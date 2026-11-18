"""Use case: check GeM Marketplace for a run's uncovered product type, then re-match by rules.

Behind DISCOVERY_ENABLED (off by default). A check is a job in the run queue: it reads at most
GEM_MAX_LISTINGS listings (robots.txt respected, a pause before every request, no login),
stores the ones that pass the checks as auto-sourced documents, and re-matches only the items
of that product type, by rules. The result is a new run whose parent is the checked run; the
checked run is never changed. No LLM is called.
"""

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.exceptions import DiscoveryDisabledError, InvalidInputError
from app.core.logging import log_context
from app.db.models import DiscoveryJob, Run
from app.db.repositories import (
    DiscoveryJobRepository,
    DocumentRepository,
    RunRepository,
)
from app.db.session import session_scope
from app.discovery.gem_config import GemMarketplaceConfig, load_gem_config
from app.discovery.gem_parser import GemListing
from app.discovery.listing_check import ListingCheck
from app.discovery.pipeline import (
    DiscoveryDeps,
    DiscoveryRequest,
    ListingOutcome,
    discover,
    page_url,
)
from app.discovery.polite_fetcher import PageFetcher, PoliteFetcher
from app.ingestion.gem_listing import process_listing
from app.ingestion.ocr import build_ocr_engine
from app.ingestion.pipeline import IngestOutcome, ListingSource, ingest_listing
from app.ingestion.processing import ProcessingOptions
from app.ingestion.product_tagger import ProductMatcher
from app.ontology.loader import load_ontologies
from app.retrieval.text_normalizer import SearchVocabulary
from app.schemas.discovery import (
    DiscoveryJobResponse,
    DiscoveryOverview,
    ListingResult,
    UncoveredTypeResponse,
)
from app.schemas.enums import DiscoveryStatus, RunStatus
from app.schemas.report import TenderReport
from app.services.match_service import UncoveredType
from app.services.report_service import build_report_service
from app.services.run_service import RunService, failure_reason
from app.storage.file_store import FileStore, build_file_store
from app.tender.link_fetcher import build_http_client
from app.workers.tasks import JobName, JobQueue

logger = logging.getLogger(__name__)


class DiscoveryMatcher(Protocol):
    """What discovery needs from matching."""

    def uncovered_types(
        self, report: TenderReport, manufacturers: Sequence[str] | None = None
    ) -> list[UncoveredType]:
        """Product types of the report's items no document of `manufacturers` declares."""
        ...

    def rematch_items(
        self,
        previous: TenderReport,
        product_types: Sequence[str],
        manufacturers: Sequence[str] | None,
    ) -> TenderReport:
        """Re-match, by rules, the items of these product types."""
        ...


@dataclass(frozen=True, slots=True)
class DiscoveryTools:
    """What reading and storing listings needs (built on first use)."""

    fetcher: PageFetcher
    store: FileStore
    check: ListingCheck
    processing: ProcessingOptions


def _job_response(job: DiscoveryJob) -> DiscoveryJobResponse:
    return DiscoveryJobResponse(
        job_id=job.id,
        run_id=job.run_id,
        product_type=job.product_type,
        category=job.category,
        status=job.status,
        error=job.error,
        listings=[ListingResult.model_validate(x) for x in job.listings],
        result_run_id=job.result_run_id,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


def _report(run: Run) -> TenderReport:
    """A run's report.

    Raises:
        InvalidInputError: If the run has none yet.
    """
    if run.report is None:
        raise InvalidInputError(
            f"Run {run.id} has no report yet (status: {run.status.value})",
            details={"run_id": str(run.id)},
        )
    return TenderReport.model_validate(run.report)


class DiscoveryService:
    """Offer, queue and execute GeM Marketplace checks for uncovered product types."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        queue: JobQueue,
        matcher: Callable[[], DiscoveryMatcher],
        tools: Callable[[], DiscoveryTools],
        config: Callable[[], GemMarketplaceConfig],
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._queue = queue
        self._matcher = matcher
        self._tools = tools
        self._config = config

    def _require_enabled(self) -> None:
        if not self._settings.DISCOVERY_ENABLED:
            raise DiscoveryDisabledError("GeM Marketplace discovery is off (DISCOVERY_ENABLED)")

    def overview(self, run_id: uuid.UUID) -> DiscoveryOverview:
        """Uncovered product types of a run, and the checks already asked for each.

        Args:
            run_id: A finished run.

        Returns:
            `enabled=False` and nothing else when discovery is off.

        Raises:
            NotFoundError: If the run does not exist.
            InvalidInputError: If the run has no report yet.
        """
        if not self._settings.DISCOVERY_ENABLED:
            return DiscoveryOverview(enabled=False)
        with session_scope(self._session_factory) as session:
            run = RunRepository(session).get_or_raise(run_id)
            report, manufacturers = _report(run), list(run.manufacturers)
            jobs = [_job_response(j) for j in DiscoveryJobRepository(session).list_for_run(run_id)]
        config = self._config()
        return DiscoveryOverview(
            enabled=True,
            uncovered=[
                UncoveredTypeResponse(
                    product_type=u.product_type,
                    category=u.category,
                    item_nos=u.item_nos,
                    gem_configured=bool(config.categories_for(u.product_type)),
                    jobs=[j for j in jobs if j.product_type == u.product_type],
                )
                for u in self._matcher().uncovered_types(report, manufacturers)
            ],
        )

    def start(self, run_id: uuid.UUID, product_type: str) -> DiscoveryJobResponse:
        """Queue a GeM Marketplace check for one uncovered product type of a run.

        Args:
            run_id: A finished run.
            product_type: One of its uncovered product types.

        Returns:
            The queued job.

        Raises:
            DiscoveryDisabledError: If discovery is off or the type has no GeM categories.
            NotFoundError: If the run does not exist.
            InvalidInputError: If the run has no report or the type is not uncovered in it.
        """
        self._require_enabled()
        if not self._config().categories_for(product_type):
            raise DiscoveryDisabledError(
                f"No GeM Marketplace categories configured for {product_type}",
                details={"file": str(self._settings.GEM_MARKETPLACE_FILE)},
            )
        with session_scope(self._session_factory) as session:
            run = RunRepository(session).get_or_raise(run_id)
            report, manufacturers = _report(run), list(run.manufacturers)
            uncovered = {
                u.product_type: u for u in self._matcher().uncovered_types(report, manufacturers)
            }
            if product_type not in uncovered:
                raise InvalidInputError(
                    f"{product_type} is not an uncovered product type of run {run_id}",
                    details={"uncovered": sorted(uncovered)},
                )
            job = DiscoveryJobRepository(session).add(
                DiscoveryJob(
                    run_id=run_id,
                    product_type=product_type,
                    category=uncovered[product_type].category,
                    status=DiscoveryStatus.QUEUED,
                    listings=[],
                )
            )
            response = _job_response(job)
        self._queue.submit(JobName.DISCOVER, str(response.job_id))
        return response

    def get(self, job_id: uuid.UUID) -> DiscoveryJobResponse:
        """One check and how far it got.

        Raises:
            NotFoundError: If it does not exist.
        """
        with session_scope(self._session_factory) as session:
            return _job_response(DiscoveryJobRepository(session).get_or_raise(job_id))

    def execute(self, job_id: uuid.UUID) -> None:
        """Do one queued check; a failure is recorded on the job, never raised.

        Args:
            job_id: The job.
        """
        with session_scope(self._session_factory) as session:
            job = DiscoveryJobRepository(session).get(job_id)
            run_id = str(job.run_id) if job else ""
        if not run_id:
            logger.warning("discovery job not found", extra={"job_id": str(job_id)})
            return
        with log_context(run_id=run_id):
            try:
                self._execute(job_id)
            except Exception as exc:
                logger.exception("discovery failed", extra={"job_id": str(job_id)})
                reason = failure_reason(exc, self._settings.RUN_ERROR_MAX_CHARS)
                self._finish(job_id, DiscoveryStatus.FAILED, error=reason)

    def _execute(self, job_id: uuid.UUID) -> None:
        with session_scope(self._session_factory) as session:
            job = DiscoveryJobRepository(session).get_or_raise(job_id)
            job.status = DiscoveryStatus.RUNNING
            run = RunRepository(session).get_or_raise(job.run_id)
            product_type, category, run_id = job.product_type, job.category, run.id
            manufacturers, report = list(run.manufacturers), _report(run)
        outcomes = self._read(product_type, category)
        with session_scope(self._session_factory) as session:
            DiscoveryJobRepository(session).get_or_raise(job_id).listings = [
                o.as_json() for o in outcomes
            ]
        brands = self._listing_brands(product_type)
        if not brands:
            self._finish(job_id, DiscoveryStatus.DONE, error="No listing was kept")
            return
        new_report = self._matcher().rematch_items(
            report, [product_type], sorted({*manufacturers, *brands})
        )
        result_run = self._save_run(run_id, new_report, brands)
        self._finish(job_id, DiscoveryStatus.DONE, result_run=result_run)

    def _read(self, product_type: str, category: str) -> list[ListingOutcome]:
        """Read and store listings for the type."""
        settings, config, tools = self._settings, self._config(), self._tools()
        request = DiscoveryRequest(
            product_type=product_type,
            category=category,
            page_urls=[
                page_url(settings.GEM_BASE_URL, s) for s in config.categories_for(product_type)
            ],
            max_listings=settings.GEM_MAX_LISTINGS,
            snapshot_prefix=settings.GEM_SNAPSHOT_PREFIX,
            empty_values=config.empty_values,
        )
        deps = DiscoveryDeps(
            fetcher=tools.fetcher,
            store=tools.store,
            check=tools.check,
            known=self._known,
            save=lambda source, listing: self._save(source, listing, tools.processing),
        )
        return discover(request, deps)

    def _known(self, url: str) -> bool:
        with session_scope(self._session_factory) as session:
            return DocumentRepository(session).get_by_url(url) is not None

    def _save(
        self, source: ListingSource, listing: GemListing, options: ProcessingOptions
    ) -> IngestOutcome:
        processed = process_listing(listing, source.product_type, options)
        with session_scope(self._session_factory) as session:
            return ingest_listing(session, source, processed, options)

    def _listing_brands(self, product_type: str) -> list[str]:
        """Brands of every stored listing of the type (this check's and earlier ones)."""
        with session_scope(self._session_factory) as session:
            documents = DocumentRepository(session).list_auto_sourced()
            return sorted(
                {
                    d.manufacturer
                    for d in documents
                    if d.manufacturer and product_type in d.product_types
                }
            )

    def _save_run(self, parent_id: uuid.UUID, report: TenderReport, brands: list[str]) -> uuid.UUID:
        """Store the re-matched report as a new, finished run whose parent is the checked run."""
        now = datetime.now(UTC)
        with session_scope(self._session_factory) as session:
            runs = RunRepository(session)
            parent = runs.get_or_raise(parent_id)
            run = runs.add(
                Run(
                    tender_id=parent.tender_id,
                    category=parent.category,
                    manufacturers=sorted({*parent.manufacturers, *brands}),
                    status=RunStatus.RUNNING,
                    started_at=now,
                    parent_run_id=parent.id,
                )
            )
            build_report_service(session, self._settings).save(run.id, report)
            run.status = RunStatus.DONE
            run.finished_at = datetime.now(UTC)
            return run.id

    def _finish(
        self,
        job_id: uuid.UUID,
        status: DiscoveryStatus,
        *,
        error: str | None = None,
        result_run: uuid.UUID | None = None,
    ) -> None:
        with session_scope(self._session_factory) as session:
            job = DiscoveryJobRepository(session).get_or_raise(job_id)
            job.status = status
            job.error = error
            job.result_run_id = result_run
            job.finished_at = datetime.now(UTC)


def build_discovery_tools(settings: Settings) -> DiscoveryTools:
    """Fetcher, file store, listing check and processing options from settings."""
    ontologies = load_ontologies(settings.ONTOLOGY_DIR)
    vocabulary = SearchVocabulary.from_ontologies(ontologies)
    matcher = ProductMatcher.from_ontologies(ontologies, vocabulary)
    client = build_http_client(settings.HTTP_TIMEOUT_S, settings.HTTP_USER_AGENT)
    return DiscoveryTools(
        fetcher=PoliteFetcher(
            client=client,
            user_agent=settings.HTTP_USER_AGENT,
            delay_s=settings.GEM_REQUEST_DELAY_SECONDS,
            max_bytes=settings.HTTP_MAX_DOWNLOAD_MB * 1024 * 1024,
        ),
        store=build_file_store(settings),
        check=ListingCheck(matcher, vocabulary, settings.GEM_MIN_SPEC_ROWS),
        processing=ProcessingOptions.from_settings(
            settings, vocabulary, matcher, build_ocr_engine(settings)
        ),
    )


def build_discovery_service(
    settings: Settings,
    session_factory: sessionmaker[Session],
    queue: JobQueue,
    matcher: Callable[[], DiscoveryMatcher],
    runs: RunService,
) -> DiscoveryService:
    """Wire the discovery service and let the run queue execute its jobs.

    The fetcher and config are built by the first use.

    Args:
        settings: Application settings.
        session_factory: Opens its transactions.
        queue: The run queue (checks run one at a time with runs).
        matcher: The match service shared with runs (`build_matcher`).
        runs: The run service owning the queue's handler; discovery jobs are registered on it.

    Returns:
        A ready service.
    """
    service = DiscoveryService(
        session_factory,
        settings,
        queue,
        matcher,
        cache(lambda: build_discovery_tools(settings)),
        cache(lambda: load_gem_config(settings.GEM_MARKETPLACE_FILE)),
    )
    runs.register(JobName.DISCOVER, lambda argument: service.execute(uuid.UUID(argument)))
    return service
