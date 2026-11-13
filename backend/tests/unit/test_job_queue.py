"""Job queues: the thread queue runs bound handlers, the RQ queue only sends name + argument."""

import threading

import pytest

from app.core.config import JobQueueBackend
from app.workers import worker
from app.workers.tasks import (
    RQ_JOB_FUNCTION,
    JobName,
    RqJobQueue,
    ThreadJobQueue,
    build_job_queue,
    logged,
)
from tests.conftest import make_test_settings


class _FakeRq:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def enqueue(self, f: str, *args: object, **kwargs: object) -> object:
        self.calls.append((f, args, kwargs))
        return None


def test_thread_queue_runs_the_bound_handler() -> None:
    done = threading.Event()
    seen: list[tuple[JobName, str]] = []

    def handler(job: JobName, argument: str) -> None:
        seen.append((job, argument))
        done.set()

    queue = ThreadJobQueue()
    queue.bind(handler)
    queue.submit(JobName.RUN, "abc")
    assert done.wait(timeout=5)
    queue.shutdown()
    assert seen == [(JobName.RUN, "abc")]
    assert queue.survives_restart is False


def test_thread_queue_without_a_handler_is_a_wiring_error() -> None:
    with pytest.raises(RuntimeError):
        ThreadJobQueue().submit(JobName.RUN, "abc")


def test_rq_queue_sends_only_strings_to_the_worker_function() -> None:
    fake = _FakeRq()
    queue = RqJobQueue(fake, job_timeout_s=120)
    queue.bind(lambda job, argument: None)  # ignored: the worker process has its own
    queue.submit(JobName.RUN, "run-id")
    assert fake.calls == [(RQ_JOB_FUNCTION, ("run", "run-id"), {"job_timeout": 120})]
    assert queue.survives_restart is True


def test_the_worker_function_is_importable_by_its_path() -> None:
    module, name = RQ_JOB_FUNCTION.rsplit(".", 1)
    assert module == worker.__name__
    assert getattr(worker, name) is worker.perform


def test_settings_choose_the_queue() -> None:
    settings = make_test_settings()
    assert isinstance(build_job_queue(settings), ThreadJobQueue)
    rq = settings.model_copy(update={"JOB_QUEUE_BACKEND": JobQueueBackend.RQ})
    assert isinstance(build_job_queue(rq), RqJobQueue)  # Redis is only reached on submit


def test_a_failing_job_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    def boom(job: JobName, argument: str) -> None:
        raise ValueError("lost")

    logged(boom, JobName.WARM_UP, "")
    assert "job failed" in caplog.text
