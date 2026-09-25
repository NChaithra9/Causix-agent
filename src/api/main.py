"""Causix FastAPI app.  Run:  uvicorn src.api.main:app --reload"""
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import RedirectResponse

from src.api.jobs import JobStore
from src.api.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    IndexRequest,
    IndexResponse,
    JobState,
    SearchRequest,
    StatusResponse,
)
from src.reasoning.agents.issue_agent import IssueAgent
from src.reasoning.facts import StubFactsProvider
from src.reasoning.llm import get_llm
from src.reasoning.orchestrator import Orchestrator
from src.reasoning.retrieval.chunker import chunk_repository
from src.reasoning.retrieval.embeddings import get_embedder
from src.reasoning.retrieval.graph import StubGraphSearch
from src.reasoning.retrieval.hybrid import HybridRetriever
from src.reasoning.retrieval.vector_store import get_vector_store
from src.reasoning.schemas import AnalysisResult, Evidence

app = FastAPI(title="Causix API", version="0.1.0")
store = JobStore()

_embedder = get_embedder()
_retriever = HybridRetriever(_embedder, get_vector_store(_embedder.dim), StubGraphSearch())


def get_retriever() -> HybridRetriever:
    return _retriever


def get_orchestrator(retriever: HybridRetriever = Depends(get_retriever)) -> Orchestrator:
    return Orchestrator(IssueAgent(get_llm()), StubFactsProvider(), retriever)


def run_job(job_id: str, request: AnalyzeRequest, orchestrator: Orchestrator) -> None:
    store.update(job_id, state=JobState.RUNNING)
    try:
        result = orchestrator.analyze(request)
        store.update(job_id, state=JobState.COMPLETED, result=result)
    except Exception as exc:  # job must never be left stuck in "running"
        store.update(job_id, state=JobState.FAILED, error=str(exc))


def _get_job_or_404(job_id: str):
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/causix/analyze", response_model=AnalyzeResponse, status_code=202)
def analyze(request: AnalyzeRequest, background_tasks: BackgroundTasks,
            orchestrator: Orchestrator = Depends(get_orchestrator)) -> AnalyzeResponse:
    job = store.create()
    background_tasks.add_task(run_job, job.job_id, request, orchestrator)
    return AnalyzeResponse(job_id=job.job_id, state=job.state)


@app.get("/api/causix/status/{job_id}", response_model=StatusResponse)
def status(job_id: str) -> StatusResponse:
    job = _get_job_or_404(job_id)
    return StatusResponse(job_id=job.job_id, state=job.state, error=job.error)


@app.get("/api/causix/result/{job_id}", response_model=AnalysisResult)
def result(job_id: str) -> AnalysisResult:
    job = _get_job_or_404(job_id)
    if job.state != JobState.COMPLETED:
        raise HTTPException(status_code=409, detail=f"Job is {job.state.value}, result not ready")
    return job.result


@app.post("/api/causix/index", response_model=IndexResponse)
def index_repository(request: IndexRequest,
                     retriever: HybridRetriever = Depends(get_retriever)) -> IndexResponse:
    root = Path(request.repo_path).expanduser()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {request.repo_path}")
    indexed = retriever.index(chunk_repository(root))
    return IndexResponse(chunks_indexed=indexed, total_chunks=retriever.store.count())


@app.post("/api/causix/search", response_model=list[Evidence])
def search(request: SearchRequest,
           retriever: HybridRetriever = Depends(get_retriever)) -> list[Evidence]:
    return retriever.search(request.query, request.top_k)
