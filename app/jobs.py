from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Job:
    id: str
    url: str
    choice: dict
    out_dir: str
    status: str = "queued"  # queued | running | done | error
    progress: dict = field(default_factory=dict)
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "status": self.status,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "choice": {k: v for k, v in self.choice.items() if not k.startswith("_")},
            "out_dir": self.out_dir,
        }


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def create(self, url: str, choice: dict, out_dir: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], url=url, choice=choice, out_dir=out_dir)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def subscribe(self, job_id: str) -> Optional[asyncio.Queue]:
        job = self.get(job_id)
        if job is None:
            return None
        q: asyncio.Queue = asyncio.Queue()
        with job._lock:
            job.subscribers.append(q)
        # Replay current state immediately.
        q.put_nowait({"event": "snapshot", "data": job.snapshot()})
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        job = self.get(job_id)
        if job is None:
            return
        with job._lock:
            if q in job.subscribers:
                job.subscribers.remove(q)

    def publish(self, job: Job, event: str, data: dict) -> None:
        if self._loop is None:
            return
        with job._lock:
            subs = list(job.subscribers)
        for q in subs:
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, {"event": event, "data": data})
            except RuntimeError:
                pass

    def update_progress(self, job: Job, progress: dict) -> None:
        with job._lock:
            job.progress = progress
            if progress.get("status") == "downloading":
                job.status = "running"
        self.publish(job, "progress", job.snapshot())

    def mark_done(self, job: Job, result: dict) -> None:
        with job._lock:
            job.status = "done"
            job.result = result
        self.publish(job, "done", job.snapshot())

    def mark_error(self, job: Job, message: str) -> None:
        with job._lock:
            job.status = "error"
            job.error = message
        self.publish(job, "error", job.snapshot())
