"""Use case: check GeM Marketplace for a run's product types, and add the listing vendors.

Two ways in: a check asked for by hand for one uncovered type of a finished run (below), or
the GeM branch of a run (GEM_IN_RUN, `start_branch`), which checks every item type while the
catalogues are matched and adds the listing vendors to the run's own report (`gem_branch`).

Behind DISCOVERY_ENABLED (off by default). A check is a job in the run queue: it searches GeM
with the words of the run's items of that type, keeps at most GEM_MAX_CATEGORIES fitting
categories (the hand-listed ones of gem_marketplace.yaml only if the search finds none), reads at
most GEM_MAX_LISTINGS listings (robots.txt respected, a pause before every request, no login),
stores the ones that pass the checks as auto-sourced documents, and re-matches only the items
of that product type, by rules. The result is a new run whose parent is the checked run; the
checked run is never changed. No LLM is called.
"""

import logging
import multiprocessing
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import cache
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.exceptions import (
    DiscoveryDisabledError,
    ExternalServiceError,
    InvalidInputError,
)
from app.core.logging import log_context
from app.db.models import DiscoveryJob, Run
from app.db.repositories import (
    DiscoveryJobRepository,
    DocumentRepository,
    RunRepository,
)
from app.db.session import session_scope
from app.discovery.card_fit import item_wants
from app.discovery.category_fit import FitWords
from app.discovery.gem_config import GemMarketplaceConfig, load_gem_config
from app.discovery.gem_parser import GemListing
from app.discovery.gem_search import rank_words, search_queries, search_url
from app.discovery.listing_check import ListingCheck
from app.discovery.pipeline import (
    DiscoveryDeps,
    DiscoveryRequest,
    ListingOutcome,
    WordFit,
    discover,
    find_category_pages,
    page_url,
    recheck_stored,
)
from app.discovery.polite_fetcher import PageFetcher, PoliteFetcher
from app.discovery.recommend import buyer_key, recommendations, with_recommendations
from app.ingestion.gem_listing import process_listing
from app.ingestion.ocr import build_ocr_engine
from app.ingestion.pipeline import IngestOutcome, ListingSource, ingest_listing
from app.ingestion.processing import ProcessingOptions
from app.ingestion.product_tagger import ProductMatcher
from app.ontology.loader import load_ontologies
from app.retrieval.text_normalizer import SearchVocabulary, normalize_for_search
from app.schemas.discovery import (
    DiscoveryJobResponse,
    DiscoveryOverview,
    ListingResult,
    UncoveredTypeResponse,
)
from app.schemas.enums import DiscoveryStatus, RunStatus
from app.schemas.report import TenderReport
from app.services.gem_branch import GemBranch
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

    def item_types(self, tender: str) -> list[UncoveredType]:
        """Every product type (or title key) of a tender's items."""
        ...

    def branch_types(
        self,
        tender: str,
        manufacturers: Sequence[str] | None = None,
        *,
        title_keys: bool = True,
    ) -> list[UncoveredType]:
        """The types a run's GeM branch searches, those with nothing in the database first."""
        ...

    def add_listing_vendors(
        self, previous: TenderReport, brands: Mapping[str, Sequence[str]]
    ) -> TenderReport:
        """Add listing brands, matched by rules, to the items of their type."""
        ...


@dataclass(frozen=True, slots=True)
class DiscoveryTools:
    """What reading and storing listings needs (built on first use)."""

    fetcher: PageFetcher
    store: FileStore
    check: ListingCheck
    processing: ProcessingOptions
    # Every ontology's one-word abbreviations ("nrv" -> "check valve"), for the category fit.
    abbreviations: Mapping[str, str] = field(default_factory=dict)


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
        return DiscoveryOverview(
            enabled=True,
            uncovered=[
                UncoveredTypeResponse(
                    product_type=u.product_type,
                    category=u.category,
                    item_nos=u.item_nos,
                    gem_configured=True,  # GeM is searched by the items' words
                    by_title=u.by_title,
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
            DiscoveryDisabledError: If discovery is off.
            NotFoundError: If the run does not exist.
            InvalidInputError: If the run has no report or the type is not uncovered in it.
        """
        self._require_enabled()
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
        uncovered = self._matcher().uncovered_types(report, manufacturers)
        titles = _item_titles(report, product_type, uncovered)
        brands = self._check(job_id, product_type, category, titles, report.buyer_gem_categories)
        if not brands:
            return
        new_report = self._matcher().rematch_items(
            report, [product_type], sorted({*manufacturers, *brands})
        )
        result_run = self._save_run(run_id, new_report, brands)
        self._finish(job_id, DiscoveryStatus.DONE, result_run=result_run)

    def _check(
        self,
        job_id: uuid.UUID,
        product_type: str,
        category: str,
        titles: list[str],
        buyer_categories: Sequence[str] = (),
        trusted: bool = False,
    ) -> list[str]:
        """Search GeM for one type and store the listings that pass; record them on the job.

        Returns:
            Brands of every stored listing of the type; empty when nothing is kept, and then
            the job is finished here with the reason.
        """
        check = self._tools().check.for_type(product_type, self._settings.GEM_TITLE_STOP_WORDS)
        request = self._request(product_type, category, titles, check, buyer_categories)
        if trusted:  # a buyer category: its own page only, its own listings kept as they are
            request = replace(request, trusted=True, search_urls=[], page_urls=[])
        pages = find_category_pages(request, self._deps(check))
        logger.info(
            "gem categories",
            extra={"source": pages.source, "categories": pages.names, "type": product_type},
        )
        if pages.source == "fitted":  # its listings are judged by the chosen categories
            request = replace(request, fitted=True, page_urls=pages.page_urls)
        # A buyer category's stored listings are read again from its page, never withdrawn here.
        withdrawn = [] if trusted else self._withdraw_stale(request, check)
        if not pages.page_urls:
            self._record(job_id, withdrawn)
            label = product_type.replace("_", " ")
            self._finish(
                job_id, DiscoveryStatus.DONE, error=f"GeM search found no category for {label}"
            )
            return []
        gone = {o.url for o in withdrawn}  # reported once, as withdrawn
        outcomes = [
            o
            for o in discover(replace(request, page_urls=pages.page_urls), self._deps(check))
            if o.url not in gone
        ]
        self._record(job_id, [*withdrawn, *outcomes])
        brands = self._listing_brands(product_type)
        if not brands:
            self._finish(job_id, DiscoveryStatus.DONE, error="No listing was kept")
        return brands

    def start_branch(self, run_id: uuid.UUID, tender: str) -> GemBranch | None:
        """Start the GeM branch of a run: every item type checked while catalogues are matched.

        Args:
            run_id: The run that is starting.
            tender: Its tender folder.

        Returns:
            The started branch, or None when discovery or GEM_IN_RUN is off or no item has a
            type.
        """
        settings = self._settings
        if not (settings.DISCOVERY_ENABLED and settings.GEM_IN_RUN):
            return None
        started = time.monotonic()
        with session_scope(self._session_factory) as session:
            manufacturers = list(RunRepository(session).get_or_raise(run_id).manufacturers or [])
        # Every item, typed or not (GEM_IN_RUN_TITLE_KEYS): an item the database has nothing
        # for gets at least its GeM vendors; those types are checked first. A mis-read title
        # key finds no fitting listing (`ListingCheck.for_type`), so it adds nothing.
        types = self._matcher().branch_types(
            tender, manufacturers or None, title_keys=settings.GEM_IN_RUN_TITLE_KEYS
        )
        buyer = _buyer_types(types, settings.GEM_UNTYPED_CATEGORY)
        types = [*buyer, *types]
        if not types:
            return None
        with session_scope(self._session_factory) as session:
            jobs = DiscoveryJobRepository(session)
            queued = [
                (
                    jobs.add(
                        DiscoveryJob(
                            run_id=run_id,
                            product_type=t.product_type,
                            category=t.category,
                            status=DiscoveryStatus.QUEUED,
                            listings=[],
                        )
                    ).id,
                    t,
                )
                for t in types
            ]
        branch = GemBranch(
            run_id=str(run_id),
            jobs=queued,
            deadline=started + settings.GEM_RUN_BUDGET_SECONDS,
            check=self._check_in_process if settings.GEM_CHECK_IN_PROCESS else self.check_in_run,
            late=lambda late: self._late_types(run_id, late, [j for j, _ in queued]),
            recommend=lambda report: self.recommend(run_id, report),
        )
        branch.start()
        logger.info("gem branch started", extra={"types": [t.product_type for t in types]})
        return branch

    def recommend(self, run_id: uuid.UUID, report: TenderReport) -> TenderReport:
        """The report with its buyer categories' GeM listings (`recommend.with_recommendations`).

        Args:
            run_id: The run whose discovery jobs hold the listings.
            report: Its report, GeM listing vendors already added.

        Returns:
            The report with recommended (tender) and related (item) GeM vendors.
        """
        categories = report.buyer_gem_categories
        if not categories:
            return report
        keys = {buyer_key(c) for c in categories}
        with session_scope(self._session_factory) as session:
            jobs = DiscoveryJobRepository(session).list_for_run(run_id)
            listings = {j.product_type: list(j.listings) for j in jobs if j.product_type in keys}
        settings = self._settings
        recs = recommendations(categories, listings, settings.GEM_RECOMMENDED_PER_CATEGORY)
        ignore = [*settings.GEM_TITLE_STOP_WORDS, *settings.GEM_RELATED_IGNORE_WORDS]
        return with_recommendations(report, recs, ignore, settings.GEM_RELATED_PER_ITEM)

    def _check_in_process(self, job_id: uuid.UUID, item_type: UncoveredType) -> list[str]:
        """`check_in_run` in a spawned child process; the brands are read from its job.

        Raises:
            ExternalServiceError: If the child process failed (its job is marked failed).
        """
        from app.workers.gem_check import run_gem_check  # the child imports it again

        process = multiprocessing.get_context("spawn").Process(
            target=run_gem_check,
            args=(
                str(job_id),
                item_type.product_type,
                item_type.category,
                item_type.titles,
                item_type.buyer_categories,
                item_type.trusted,
            ),
            name=f"gem-{item_type.product_type}",
            daemon=True,
        )
        process.start()
        process.join()
        with session_scope(self._session_factory) as session:
            job = DiscoveryJobRepository(session).get_or_raise(job_id)
            status, error = job.status, job.error
        if process.exitcode != 0 and status is not DiscoveryStatus.FAILED:
            reason = f"GeM check process ended with exit code {process.exitcode}"
            self._finish(job_id, DiscoveryStatus.FAILED, error=reason)
            raise ExternalServiceError(reason, details={"type": item_type.product_type})
        if status is DiscoveryStatus.DONE and error is None:
            return self._listing_brands(item_type.product_type)
        return []

    def check_in_run(self, job_id: uuid.UUID, item_type: UncoveredType) -> list[str]:
        """One type of a run's GeM branch; the job is finished here, even on failure.

        Args:
            job_id: The type's discovery job.
            item_type: The product type, category and item titles.

        Returns:
            Brands of every stored listing of the type; empty when none is kept.
        """
        with session_scope(self._session_factory) as session:
            DiscoveryJobRepository(session).get_or_raise(job_id).status = DiscoveryStatus.RUNNING
        try:
            brands = self._check(
                job_id,
                item_type.product_type,
                item_type.category,
                list(item_type.titles),
                item_type.buyer_categories,
                item_type.trusted,
            )
        except Exception as exc:
            reason = failure_reason(exc, self._settings.RUN_ERROR_MAX_CHARS)
            self._finish(job_id, DiscoveryStatus.FAILED, error=reason)
            raise
        if brands:
            self._finish(job_id, DiscoveryStatus.DONE)
        return brands

    def _late_types(
        self,
        run_id: uuid.UUID,
        brands: Mapping[str, list[str]],
        job_ids: Sequence[uuid.UUID],
    ) -> None:
        """Types the run did not wait for: add their vendors in a child run of the run."""
        with session_scope(self._session_factory) as session:
            report = _report(RunRepository(session).get_or_raise(run_id))
        new_report = self._matcher().add_listing_vendors(report, brands)
        every = sorted({b for names in brands.values() for b in names})
        child = self._save_run(run_id, new_report, every)
        with session_scope(self._session_factory) as session:
            for job_id in job_ids:
                job = DiscoveryJobRepository(session).get_or_raise(job_id)
                if job.product_type in brands:
                    job.result_run_id = child
        logger.info("gem late types saved", extra={"types": sorted(brands), "child": str(child)})

    def _request(
        self,
        product_type: str,
        category: str,
        titles: list[str],
        check: ListingCheck,
        buyer_categories: Sequence[str] = (),
    ) -> DiscoveryRequest:
        """What to look for: search pages from the item titles, hand-listed pages as fallback."""
        settings, config = self._settings, self._config()
        base = settings.GEM_BASE_URL
        queries = search_queries(product_type, titles, check)
        return DiscoveryRequest(
            product_type=product_type,
            category=category,
            page_urls=[page_url(base, s) for s in config.categories_for(product_type)],
            max_listings=settings.GEM_MAX_LISTINGS,
            snapshot_prefix=settings.GEM_SNAPSHOT_PREFIX,
            empty_values=config.empty_values,
            excludes=config.excludes_for(product_type),
            search_urls=[search_url(base, q) for q in queries],
            max_categories=settings.GEM_MAX_CATEGORIES,
            rank_words=rank_words(product_type, titles, check, config.rank_ignore_words),
            item_wants=item_wants(
                product_type,
                titles,
                check,
                lambda title: rank_words(product_type, [title], check, config.rank_ignore_words),
            ),
            base_url=base,
            max_pages=settings.GEM_MAX_CATEGORY_PAGES,
            max_per_brand=settings.GEM_MAX_LISTINGS_PER_BRAND,
            seller_rank=config.seller_rank,
            buyer_categories=list(buyer_categories),
            title_words=frozenset(
                w for t in titles for w in normalize_for_search(t, check.vocabulary).split()
            ),
            tail_words=frozenset(
                w
                for t in [*settings.GEM_TITLE_STOP_WORDS, *settings.GEM_CATEGORY_TAIL_WORDS]
                for w in normalize_for_search(t, check.vocabulary).split()
            ),
            fit=WordFit(
                self._fit_words(check),
                list(titles),
                settings.GEM_FIT_MIN_COVERAGE,
                settings.GEM_FIT_MIN_PRECISION,
            ),
            min_spec_rows_buyer=settings.GEM_MIN_SPEC_ROWS_BUYER,
            filler_words=frozenset(w.casefold() for w in config.category_filler_words),
        )

    def _fit_words(self, check: ListingCheck) -> FitWords:
        """Word rules of the category fit: config equivalents over the ontology abbreviations."""
        config = self._config()
        equivalents = {**self._tools().abbreviations, **config.word_equivalents}
        return FitWords.build(
            check.vocabulary,
            equivalents,
            config.category_cut_words,
            [*config.category_filler_words, *self._settings.GEM_TITLE_STOP_WORDS],
        )

    def _record(self, job_id: uuid.UUID, outcomes: Sequence[ListingOutcome]) -> None:
        """Store what happened to each listing on the job."""
        with session_scope(self._session_factory) as session:
            DiscoveryJobRepository(session).get_or_raise(job_id).listings = [
                o.as_json() for o in outcomes
            ]

    def _withdraw_stale(
        self, request: DiscoveryRequest, check: ListingCheck
    ) -> list[ListingOutcome]:
        """Withdraw stored listings of the type that today's check rejects (never deleted).

        A listing stored by an earlier, looser check (or before a look-alike was configured)
        must not stay evidence. Its frozen HTML is checked again; a rejected one gets
        `withdrawn_reason` and is left out of matching from now on.

        Args:
            request: The product type, empty values and look-alikes of this check.
            check: Today's listing check for the type.

        Returns:
            One outcome per withdrawn listing.
        """
        tools = self._tools()
        out: list[ListingOutcome] = []
        with session_scope(self._session_factory) as session:
            for doc in DocumentRepository(session).list_auto_sourced():
                if request.product_type not in doc.product_types or not doc.url:
                    continue
                with tools.store.local_path(doc.file_path) as path:
                    html = path.read_text(encoding="utf-8", errors="replace")
                reason = recheck_stored(html, doc.url, request, check)
                if reason is None:
                    continue
                doc.withdrawn_reason = reason
                out.append(
                    ListingOutcome(
                        doc.url,
                        kept=False,
                        reason=f"withdrawn (stored earlier): {reason}",
                        title=doc.title,
                        brand=doc.manufacturer,
                    )
                )
                logger.info("gem listing withdrawn", extra={"url": doc.url, "reason": reason})
        return out

    def _deps(self, check: ListingCheck) -> DiscoveryDeps:
        """Fetcher, store, the check for this type, stored-URL check and ingestion."""
        tools = self._tools()
        return DiscoveryDeps(
            fetcher=tools.fetcher,
            store=tools.store,
            check=check,
            known=self._known,
            # The check's matcher also knows a title key, so the tagger can tag by it.
            save=lambda source, listing: self._save(
                source, listing, replace(tools.processing, product_matcher=check.matcher)
            ),
        )

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


def _buyer_types(types: Sequence[UncoveredType], category: str) -> list[UncoveredType]:
    """One trusted GeM check per category the buyer selected for the bid, in bid order."""
    names = list(dict.fromkeys(c for t in types for c in t.buyer_categories))
    return [
        UncoveredType(
            buyer_key(name),
            category,
            [],
            by_title=True,
            titles=[name],
            buyer_categories=[name],
            trusted=True,
        )
        for name in names
    ]


def _item_titles(
    report: TenderReport, product_type: str, uncovered: Sequence[UncoveredType]
) -> list[str]:
    """Titles of the report's items of an uncovered product type, each once."""
    item_nos = {n for u in uncovered if u.product_type == product_type for n in u.item_nos}
    titles = [i.item.title for i in report.items if i.item.item_no in item_nos]
    return list(dict.fromkeys(titles))


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
        check=ListingCheck.from_ontologies(
            ontologies, vocabulary, matcher, settings.GEM_MIN_SPEC_ROWS
        ),
        processing=ProcessingOptions.from_settings(
            settings, vocabulary, matcher, build_ocr_engine(settings)
        ),
        abbreviations={k: v for o in ontologies for k, v in o.abbreviations.items()},
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
    runs.use_gem_branch(service.start_branch)
    return service
