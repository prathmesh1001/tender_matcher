"""The RQ worker process: `python -m app.workers.worker` (used when JOB_QUEUE_BACKEND=rq).

A `SimpleWorker` executes jobs in this process, one at a time and without forking, so the
catalogue cache and the loaded models stay in memory from one run to the next. Each job calls
`perform`, which hands it to the same `RunService.handle` the thread queue uses.
"""

import logging
from functools import cache

from redis import Redis
from rq import Queue, SimpleWorker

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import create_db_engine, create_session_factory
from app.services.run_service import RunService, build_run_service
from app.workers.tasks import JobName, build_job_queue

logger = logging.getLogger(__name__)


@cache
def _service() -> RunService:
    """The worker's run service, built by the first job and kept for the next ones."""
    settings = get_settings()
    session_factory = create_session_factory(create_db_engine(settings))
    # Jobs a run queues itself (none today) go back through Redis like the API's.
    return build_run_service(settings, session_factory, build_job_queue(settings))


def perform(job: str, argument: str) -> None:
    """Execute one queued job (RQ imports and calls this by its path).

    Args:
        job: A `JobName` value.
        argument: The job's argument.

    Raises:
        ValueError: If the job name is unknown.
    """
    _service().handle(JobName(job), argument)


def main() -> None:
    """Work the run queue until stopped."""
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL, json_output=settings.LOG_JSON)
    connection = Redis.from_url(settings.REDIS_URL)
    queue = Queue(settings.RQ_QUEUE_NAME, connection=connection)
    logger.info("worker started", extra={"queue": settings.RQ_QUEUE_NAME})
    SimpleWorker([queue], connection=connection).work()


if __name__ == "__main__":
    main()
