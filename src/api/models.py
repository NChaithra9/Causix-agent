"""Request/response models for the Causix HTTP API."""
from enum import Enum

from pydantic import BaseModel, Field

from src.reasoning.schemas import AnalysisResult


class AnalyzeRequest(BaseModel):
    issue: str = Field(..., min_length=1, description="What is going wrong, in plain language")
    repository: str | None = None
    stack_trace: str | None = None
    logs: str | None = None


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(BaseModel):
    job_id: str
    state: JobState
    error: str | None = None
    result: AnalysisResult | None = None


class AnalyzeResponse(BaseModel):
    job_id: str
    state: JobState


class StatusResponse(BaseModel):
    job_id: str
    state: JobState
    error: str | None = None


class IndexRequest(BaseModel):
    repo_path: str = Field(..., min_length=1, description="Local path of the repository to index")


class IndexResponse(BaseModel):
    chunks_indexed: int
    total_chunks: int


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(5, ge=1, le=50)
