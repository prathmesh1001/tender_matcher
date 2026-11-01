"""Where long jobs run: behind `JobQueue`, so RQ + Redis can replace the thread at Step 16.

One worker thread, so runs wait their turn: catalogue caches and OCR are not safe to run at the
same time. The queue lives in the API process, so a restart loses it; `RunService` marks those
runs failed on startup.
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

logger = logging.getLogger(__name__)


class JobQueue(Protocol):
    """Run a function later, outside the request."""

    def enqueue[T](self, job: Callable[[T], None], argument: T) -> None:
        """Queue one job.

        Args:
            job: The function; it must record its own failure.
            argument: Its one argument.
        """
        ...


class ThreadJobQueue:
    """Jobs run one at a time on a thread of this process."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="run")

    def enqueue[T](self, job: Callable[[T], None], argument: T) -> None:
        """Queue one job.

        Args:
            job: The function; it must record its own failure.
            argument: Its one argument.
        """
        self._executor.submit(_logged, job, argument)

    def shutdown(self) -> None:
        """Drop jobs that have not started and stop waiting for the current one."""
        self._executor.shutdown(wait=False, cancel_futures=True)


def _logged[T](job: Callable[[T], None], argument: T) -> None:
    """Run a job; a failure it could not record itself must still reach the log."""
    try:
        job(argument)
    except Exception:
        logger.exception("job failed and could not record it")
