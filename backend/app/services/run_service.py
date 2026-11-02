"""Use case: start a run, execute it outside the request, and say how it went.

The run keeps its own transactions: its row is committed before the job is queued, and a failure
is recorded in a new transaction after the failed one rolled back. Whatever goes wrong, including
a refused save, the run ends `failed` with the reason.
"""

import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from functools import cache
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.exceptions import AppError, InvalidInputError
from app.core.logging import log_context
from app.db.models import Run, Tender
from app.db.repositories import RunRepository, TenderRepository
from app.db.session import session_scope
from app.schemas.enums import RunStatus
from app.schemas.report import TenderReport
from app.schemas.runs import RunResponse, StartRunRequest
from app.services.ingestion_service import build_ingestion_service
from app.services.judge_service import build_judge_service
from app.services.manufacturer_service import ManufacturerService, build_manufacturer_service
from app.services.match_service import build_match_service
from app.services.report_service import build_report_service
from app.tender.link_fetcher import tender_folder
from app.workers.tasks import JobQueue

logger = logging.getLogger(__name__)

_INTERRUPTED = "The server restarted before this run finished"


class Matcher(Protocol):
    """What a run needs from matching."""

    def match_tender(
        self,
        tender: str,
        *,
        use_ontology: bool = True,
        manufacturers: Sequence[str] | None = None,
        unfetched: Sequence[str] = (),
    ) -> TenderReport:
        """Match one tender folder."""
        ...


def _response(run: Run) -> RunResponse:
    return RunResponse(
        run_id=run.id,
        tender_id=run.tender_id,
        category=run.category,
        manufacturers=run.manufacturers,
        status=run.status,
        error=run.error,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _folder(tender: Tender) -> str:
    """The tender revision's folder name.

    Raises:
        InvalidInputError: If the tender has no bid number, so it has no folder.
    """
    if tender.bid_number is None:
        raise InvalidInputError(f"Tender {tender.id} has no bid number")
    return tender_folder(tender.bid_number, tender.revision)


def failure_reason(exc: Exception, max_chars: int) -> str:
    """What a reader is told about a failed run.

    Args:
        exc: What went wrong.
        max_chars: Longest reason to keep.

    Returns:
        The message and its details for an expected error; the type and message otherwise.
    """
    if isinstance(exc, AppError):
        text = f"{exc}: {exc.details}" if exc.details else str(exc)
    else:
        text = f"{type(exc).__name__}: {exc}"
    return text[:max_chars]


class RunService:
    """Start, execute and report on runs."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        queue: JobQueue,
        matcher: Callable[[], Matcher],
        fetch_links: Callable[[str], list[str]],
        manufacturers: Callable[[], ManufacturerService],
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._queue = queue
        self._matcher = matcher
        self._fetch_links = fetch_links
        self._manufacturers = manufacturers

    def start(self, request: StartRunRequest) -> RunResponse:
        """Queue a run of a stored tender.

        Args:
            request: The tender, category and manufacturers.

        Returns:
            The queued run.

        Raises:
            NotFoundError: If the tender does not exist.
            InvalidInputError: If a manufacturer has no catalogues in the category.
        """
        self._manufacturers().require(request.category, request.manufacturers)
        with session_scope(self._session_factory) as session:
            tender = TenderRepository(session).get_or_raise(request.tender_id)
            run = RunRepository(session).add(
                Run(
                    tender_id=tender.id,
                    category=request.category,
                    manufacturers=request.manufacturers,
                    status=RunStatus.QUEUED,
                )
            )
            response = _response(run)
        self._queue.enqueue(self.execute, response.run_id)
        return response

    def get(self, run_id: uuid.UUID) -> RunResponse:
        """Return one run.

        Args:
            run_id: The run.

        Returns:
            Its status, and the reason if it failed.

        Raises:
            NotFoundError: If it does not exist.
        """
        with session_scope(self._session_factory) as session:
            return _response(RunRepository(session).get_or_raise(run_id))

    def list_recent(self) -> list[RunResponse]:
        """Return recent runs, newest first."""
        limit = self._settings.RUNS_LIST_LIMIT
        with session_scope(self._session_factory) as session:
            return [_response(r) for r in RunRepository(session).list_recent(limit=limit)]

    def execute(self, run_id: uuid.UUID) -> None:
        """Do one run; never raises for a run that fails, it records the reason on the run.

        Args:
            run_id: The queued run.
        """
        with log_context(run_id=str(run_id)):
            try:
                self._execute(run_id)
            except Exception as exc:
                logger.exception("run failed")
                self._finish_failed(run_id, failure_reason(exc, self._settings.RUN_ERROR_MAX_CHARS))

    def _execute(self, run_id: uuid.UUID) -> None:
        with session_scope(self._session_factory) as session:
            run = RunRepository(session).get_or_raise(run_id)
            run.status = RunStatus.RUNNING
            run.started_at = datetime.now(UTC)
            tender = TenderRepository(session).get_or_raise(run.tender_id)
            folder = _folder(tender)
            used = {"tender_revision": tender.revision, "bid_sha256": tender.sha256}
            names = run.manufacturers
        unfetched = self._fetch_links(folder)
        report = self._matcher().match_tender(folder, manufacturers=names, unfetched=unfetched)
        report = report.model_copy(update=used)
        with session_scope(self._session_factory) as session:
            build_report_service(session, self._settings).save(run_id, report)
            run = RunRepository(session).get_or_raise(run_id)
            run.status = RunStatus.DONE
            run.finished_at = datetime.now(UTC)
        logger.info("run done")

    def _finish_failed(self, run_id: uuid.UUID, reason: str) -> None:
        with session_scope(self._session_factory) as session:
            run = RunRepository(session).get_or_raise(run_id)
            run.status = RunStatus.FAILED
            run.error = reason
            run.finished_at = datetime.now(UTC)

    def fail_interrupted(self) -> int:
        """Mark runs a restart lost as failed, so none stays `running` forever.

        Returns:
            How many were marked.
        """
        with session_scope(self._session_factory) as session:
            lost = RunRepository(session).list_unfinished()
            for run in lost:
                run.status = RunStatus.FAILED
                run.error = _INTERRUPTED
                run.finished_at = datetime.now(UTC)
            return len(lost)


def build_run_service(
    settings: Settings, session_factory: sessionmaker[Session], queue: JobQueue
) -> RunService:
    """Wire the run service; matching and link fetching are only built by the first run.

    Args:
        settings: Application settings.
        session_factory: Opens the run's transactions.
        queue: Where runs wait.

    Returns:
        A ready service.
    """
    matcher = cache(
        lambda: build_match_service(
            settings,
            judge=build_judge_service(settings, session_factory)
            if settings.LLM_JUDGE_ENABLED
            else None,
        )
    )
    ingestion = cache(lambda: build_ingestion_service(settings))

    def fetch_links(folder: str) -> list[str]:
        """Fetch a tender's missing linked files; return what could not be fetched."""
        result = ingestion().fetch_links(folder)
        failed = [what for _, report in result.succeeded for what, _ in report.failed]
        return [*failed, *(what for what, _ in result.failed)]

    return RunService(
        session_factory,
        settings,
        queue,
        matcher,
        fetch_links,
        lambda: build_manufacturer_service(settings),
    )
