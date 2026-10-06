"""Causix FastAPI app.  Run:  uvicorn src.api.main:app --reload"""
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse

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
from src.reasoning.agents.documentation_agent import DocumentationAgent
from src.reasoning.agents.fix_agent import FixAgent
from src.reasoning.agents.impact_agent import ImpactAgent
from src.reasoning.agents.scenario_agent import ScenarioAgent
from src.reasoning.execution import StubScenarioRunner
from src.reasoning.agents.issue_agent import IssueAgent
from src.reasoning.agents.rca_agent import RCAAgent
from src.reasoning.facts import StubFactsProvider
from src.reasoning.impact import CodeCallGraph
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


_impact_provider = CodeCallGraph()   # interim; swap for Person 1's graph impact engine


def get_retriever() -> HybridRetriever:
    return _retriever


def get_impact_provider() -> CodeCallGraph:
    return _impact_provider


def get_orchestrator(retriever: HybridRetriever = Depends(get_retriever),
                     impact_provider: CodeCallGraph = Depends(get_impact_provider)) -> Orchestrator:
    llm = get_llm()
    return Orchestrator(IssueAgent(llm), StubFactsProvider(), retriever, rca_agent=RCAAgent(llm),
                        fix_agent=FixAgent(llm), impact_agent=ImpactAgent(llm),
                        impact_provider=impact_provider, scenario_agent=ScenarioAgent(llm),
                        scenario_runner=StubScenarioRunner(),
                        documentation_agent=DocumentationAgent(llm))


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


@app.get("/api/causix/report/{job_id}", response_class=PlainTextResponse)
def report(job_id: str) -> str:
    """The incident report as Markdown (Phase 7)."""
    job = _get_job_or_404(job_id)
    if job.state != JobState.COMPLETED:
        raise HTTPException(status_code=409, detail=f"Job is {job.state.value}, result not ready")
    doc = job.result.documentation
    if doc is None or doc.status != "generated":
        raise HTTPException(status_code=404, detail=(doc.reason if doc else "No report generated"))
    return doc.markdown


@app.post("/api/causix/index", response_model=IndexResponse)
def index_repository(request: IndexRequest,
                     retriever: HybridRetriever = Depends(get_retriever),
                     impact_provider: CodeCallGraph = Depends(get_impact_provider)) -> IndexResponse:
    root = Path(request.repo_path).expanduser()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {request.repo_path}")
    chunks = chunk_repository(root)
    impact_provider.load(chunks)
    indexed = retriever.index(chunks)
    return IndexResponse(chunks_indexed=indexed, total_chunks=retriever.store.count())


@app.post("/api/causix/search", response_model=list[Evidence])
def search(request: SearchRequest,
           retriever: HybridRetriever = Depends(get_retriever)) -> list[Evidence]:
    return retriever.search(request.query, request.top_k)
