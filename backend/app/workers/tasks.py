"""Where long jobs run: behind `JobQueue`, either a thread of the API or RQ + Redis.

Jobs are a name and one string argument, so they can cross a process boundary: the thread
queue hands them to the handler `RunService` binds, the RQ worker process
(`app.workers.worker`) builds its own `RunService` and calls the same handler.

Either way one job runs at a time: catalogue caches and OCR are not safe to run concurrently.
The thread queue lives in the API process, so a restart loses it and `RunService` marks those
runs failed on startup; RQ jobs wait in Redis and survive the restart.
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from typing import Protocol

from redis import Redis
from rq import Queue

from app.core.config import JobQueueBackend, Settings

logger = logging.getLogger(__name__)

# The function the RQ worker imports and calls for every job.
RQ_JOB_FUNCTION = "app.workers.worker.perform"


class JobName(StrEnum):
    """The jobs a queue carries."""

    RUN = "run"  # argument: the run id
    WARM_UP = "warm_up"  # argument: unused


type JobHandler = Callable[[JobName, str], None]


class JobQueue(Protocol):
    """Run a named job later, outside the request."""

    @property
    def survives_restart(self) -> bool:
        """Whether queued jobs outlive the API process."""
        ...

    def bind(self, handler: JobHandler) -> None:
        """Set what executes jobs in this process (ignored where another process does)."""
        ...

    def submit(self, job: JobName, argument: str = "") -> None:
        """Queue one job.

        Args:
            job: Which job.
            argument: Its one argument.
        """
        ...

    def shutdown(self) -> None:
        """Stop taking jobs."""
        ...


class ThreadJobQueue:
    """Jobs run one at a time on a thread of this process."""

    survives_restart = False

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="run")
        self._handler: JobHandler | None = None

    def bind(self, handler: JobHandler) -> None:
        """Set the function that executes jobs."""
        self._handler = handler

    def submit(self, job: JobName, argument: str = "") -> None:
        """Queue one job.

        Args:
            job: Which job.
            argument: Its one argument.

        Raises:
            RuntimeError: If no handler was bound (a wiring bug).
        """
        if self._handler is None:
            raise RuntimeError("ThreadJobQueue has no handler bound")
        self._executor.submit(logged, self._handler, job, argument)

    def shutdown(self) -> None:
        """Drop jobs that have not started and stop waiting for the current one."""
        self._executor.shutdown(wait=False, cancel_futures=True)


class Enqueuer(Protocol):
    """The part of `rq.Queue` used here."""

    def enqueue(self, f: str, *args: object, **kwargs: object) -> object:
        """Queue a call of the function at import path `f`."""
        ...


class RqJobQueue:
    """Jobs wait in Redis; `python -m app.workers.worker` executes them."""

    survives_restart = True

    def __init__(self, queue: Enqueuer, job_timeout_s: int) -> None:
        self._queue = queue
        self._job_timeout_s = job_timeout_s

    def bind(self, handler: JobHandler) -> None:
        """Nothing to bind: the worker process builds its own handler."""

    def submit(self, job: JobName, argument: str = "") -> None:
        """Queue one job in Redis.

        Args:
            job: Which job.
            argument: Its one argument.
        """
        self._queue.enqueue(RQ_JOB_FUNCTION, job.value, argument, job_timeout=self._job_timeout_s)

    def shutdown(self) -> None:
        """Nothing to stop: the jobs stay in Redis."""


def build_job_queue(settings: Settings) -> JobQueue:
    """The queue the settings choose.

    Args:
        settings: Application settings.

    Returns:
        A thread queue, or an RQ queue on `REDIS_URL` (connects on first use).
    """
    if settings.JOB_QUEUE_BACKEND is JobQueueBackend.RQ:
        queue = Queue(settings.RQ_QUEUE_NAME, connection=Redis.from_url(settings.REDIS_URL))
        return RqJobQueue(queue, settings.RQ_JOB_TIMEOUT_S)
    return ThreadJobQueue()


def logged(handler: JobHandler, job: JobName, argument: str) -> None:
    """Run a job; a failure it could not record itself must still reach the log.

    Args:
        handler: What executes the job.
        job: Which job.
        argument: Its one argument.
    """
    try:
        handler(job, argument)
    except Exception:
        logger.exception("job failed and could not record it", extra={"job": job.value})
