"""In-memory registries for the local app: pipeline jobs and edit proposals.

Both live only as long as the server process. A job is created by ``POST /api/jobs`` and
advanced by a background thread; a proposal is kept until the teacher accepts or discards it.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from classdeck.editing import EditProposal

STAGES: tuple[str, ...] = ("ingest", "outline", "draft", "pictures", "export")
"""The five stages a new lesson goes through, in order."""

RUNNING = "running"
DONE = "done"
ERROR = "error"


@dataclass
class Job:
    """One pipeline run. Fields change only through the methods, which hold the lock."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = RUNNING
    stage: str | None = None
    stages_done: list[str] = field(default_factory=list)
    stages_skipped: list[str] = field(default_factory=list)
    detail: str | None = None
    lesson_id: str | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def begin(self, stage: str, detail: str | None = None) -> None:
        with self._lock:
            self.stage = stage
            self.detail = detail

    def note(self, detail: str | None) -> None:
        with self._lock:
            self.detail = detail

    def finish_stage(self, stage: str, *, skipped: bool = False) -> None:
        with self._lock:
            self.stages_done.append(stage)
            if skipped:
                self.stages_skipped.append(stage)
            self.detail = None

    def warn(self, message: str) -> None:
        with self._lock:
            self.warnings.append(message)

    def succeed(self, lesson_id: str) -> None:
        with self._lock:
            self.status = DONE
            self.lesson_id = lesson_id
            self.stage = None
            self.detail = None

    def fail(self, message: str) -> None:
        with self._lock:
            self.status = ERROR
            self.error = message
            self.detail = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "stage": self.stage,
                "stages": list(STAGES),
                "stages_done": list(self.stages_done),
                "stages_skipped": list(self.stages_skipped),
                "detail": self.detail,
                "lesson_id": self.lesson_id,
                "error": self.error,
                "warnings": list(self.warnings),
            }


class JobRegistry:
    """Jobs by id; each runs in its own daemon thread."""

    def __init__(self, keep: int = 100) -> None:
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._keep = keep

    def start(self, work: Callable[[Job], None]) -> Job:
        """Register a job and run ``work(job)`` in a thread; an uncaught error fails the job."""
        job = Job()
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > self._keep:
                self._jobs.popitem(last=False)

        def runner() -> None:
            try:
                work(job)
            except Exception as exc:  # the thread must never die silently
                job.fail(str(exc) or exc.__class__.__name__)

        threading.Thread(target=runner, name=f"classdeck-job-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)


@dataclass
class StoredProposal:
    lesson_id: str
    proposal: EditProposal


class ProposalRegistry:
    """Proposals awaiting a decision, oldest dropped first beyond ``keep``."""

    def __init__(self, keep: int = 50) -> None:
        self._items: OrderedDict[str, StoredProposal] = OrderedDict()
        self._lock = threading.Lock()
        self._keep = keep

    def add(self, lesson_id: str, proposal: EditProposal) -> str:
        key = uuid.uuid4().hex[:12]
        with self._lock:
            self._items[key] = StoredProposal(lesson_id, proposal)
            while len(self._items) > self._keep:
                self._items.popitem(last=False)
        return key

    def get(self, key: str, lesson_id: str) -> StoredProposal | None:
        with self._lock:
            stored = self._items.get(key)
        return stored if stored is not None and stored.lesson_id == lesson_id else None

    def discard(self, key: str) -> bool:
        with self._lock:
            return self._items.pop(key, None) is not None
