"""In-memory job store. Good enough for Phase 1; swap for Redis/DB later."""
import threading
import uuid

from src.api.models import Job, JobState


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self) -> Job:
        job = Job(job_id=uuid.uuid4().hex, state=JobState.QUEUED)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **changes) -> Job:
        with self._lock:
            job = self._jobs[job_id].model_copy(update=changes)
            self._jobs[job_id] = job
            return job
