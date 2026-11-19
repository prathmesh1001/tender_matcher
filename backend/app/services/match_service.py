"""Use case: match one tender folder against the registered catalogues.

In-memory path, no database: tender files listed in `tender.yaml` are processed, their
schedule tables give line items and atomic requirements, and catalogue chunks come from a
`ChunkSource` (`InMemoryChunkSource` with the sha256 cache for now). The ontology is used
when an item's category has one, unless the run is told not to use any. Rules decide first; an
optional judge then handles what they cannot read.
"""

import hashlib
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.exceptions import InvalidInputError, NotFoundError
from app.core.logging import log_context
from app.ingestion.ocr import build_ocr_engine
from app.ingestion.processing import (
    ProcessedDocument,
    ProcessingOptions,
    is_supported,
    process_file,
)
from app.ingestion.product_tagger import ProductMatcher
from app.ingestion.registry import load_registry
from app.matching.chunk_source import (
    ChunkSource,
    InMemoryChunkSource,
    ProcessedCache,
    catalogue_processor,
    processing_fingerprint,
)
from app.matching.comparator import MatchContext
from app.matching.confidence import ConfidenceOptions, items_with_confidence
from app.matching.documents import SelectionOptions
from app.matching.engine import (
    ItemInput,
    for_review,
    general_result,
    item_for_review,
    match_item,
)
from app.matching.listing_source import WithListings, load_listings
from app.matching.llm_judge import ItemJudge
from app.matching.scorer import rank, score_explanation, top_vendors
from app.ontology.loader import load_ontologies
from app.ontology.schema import Ontology
from app.reporting.status_lists import status_lists
from app.reporting.warnings import single_firm_warning
from app.retrieval.text_normalizer import SearchVocabulary
from app.schemas.enums import ExtractionStatus
from app.schemas.matching import Comparison
from app.schemas.report import (
    ExtractionQuality,
    ItemReport,
    RankingEntry,
    SingleFirmWarning,
    TenderReport,
)
from app.schemas.requirements import Requirement
from app.schemas.sections import EvidenceSource
from app.storage.file_store import FileStore, LocalFileStore, build_file_store
from app.tender.items import ItemSource, TenderFile, TenderRequirements, parse_tender_files
from app.tender.link_fetcher import MANIFEST_FILENAME, read_manifest
from app.tender.ocr_flags import LowRow, flag_low_ocr, low_confidence_rows
from app.tender.pipeline import (
    ItemRequirements,
    RequirementOptions,
    ontology_for,
    product_type_for,
)
from app.tender.quality import ItemFacts, extraction_quality, low_confidence_pages
from app.tender.splitter import leaves

logger = logging.getLogger(__name__)

# Parsed tender files live next to the catalogue cache, under their own fingerprint: tender
# processing options derive from the same settings and code (`ProcessingOptions.for_tender`).
TENDER_CACHE_SUBDIR = "tenders"
_HASH_BLOCK_BYTES = 1 << 20


@dataclass(frozen=True, slots=True)
class UncoveredType:
    """A product type of a tender's items that no stored document declares."""

    product_type: str
    category: str
    item_nos: list[str]


@dataclass(slots=True)
class ParsedTender:
    """One tender read from its files, before any matching."""

    bid_number: str
    files: list[str] = field(default_factory=list)
    items: list[ItemInput] = field(default_factory=list)
    general: list[Requirement] = field(default_factory=list)
    item_source: ItemSource = ItemSource.NONE
    extraction_quality: ExtractionQuality = field(default_factory=ExtractionQuality)

    @property
    def named_makes(self) -> list[str]:
        """Every make the tender names, anywhere in it."""
        return sorted(
            {make for r in self.every for make in (r.named_make, *r.acceptable_makes) if make}
        )

    @property
    def every(self) -> list[Requirement]:
        """Every requirement of the tender, items first."""
        return [*(r for i in self.items for r in i.requirements), *self.general]

    @property
    def single_firm(self) -> SingleFirmWarning | None:
        """The warning if the tender says only one firm is acceptable."""
        return single_firm_warning(self.every)


class MatchService:
    """Match tenders against catalogues read through a `ChunkSource`."""

    def __init__(
        self,
        source: ChunkSource,
        tender_options: ProcessingOptions,
        requirement_options: RequirementOptions,
        selection: SelectionOptions,
        confidence: ConfidenceOptions,
        ontologies: Sequence[Ontology],
        tender_root: Path,
        judge: ItemJudge | None = None,
        store: FileStore | None = None,
        tender_cache: ProcessedCache | None = None,
    ) -> None:
        self._source = source
        self._tender_cache = tender_cache
        self._tender_options = tender_options
        self._requirement_options = requirement_options
        self._selection = selection
        self._confidence = confidence
        self._ontologies = list(ontologies)
        self._tender_root = tender_root
        self._judge = judge
        self._store = store or LocalFileStore(tender_root.parent)

    def parse_tender(
        self, tender: str, *, use_ontology: bool = True, unfetched: Sequence[str] = ()
    ) -> ParsedTender:
        """Read one tender's files into line items and general requirements.

        Args:
            tender: Tender folder name under the tender data root ("GEM_2026_B_8119613").
            use_ontology: False runs without any ontology (labels and units as printed).
            unfetched: Linked files that could not be fetched or read.

        Returns:
            The parsed tender, in manifest order.

        Raises:
            NotFoundError: If the tender folder has no tender.yaml.
            DocumentParseError: If a tender file cannot be parsed.
        """
        tender_dir = self._tender_root / tender
        if not (tender_dir / MANIFEST_FILENAME).is_file():
            raise NotFoundError(
                f"No {MANIFEST_FILENAME} for tender {tender}", details={"tender": tender}
            )
        manifest = read_manifest(tender_dir / MANIFEST_FILENAME)
        ontologies = self._ontologies if use_ontology else []
        files: list[TenderFile] = []
        low_ocr: set[tuple[str, int]] = set()
        low_rows: set[LowRow] = set()
        quality = self._requirement_options.quality
        for entry in manifest.stored_files():
            path = tender_dir / (entry.file or "")
            ref = self._store.ref_for(path)
            if is_supported(path) and self._store.exists(ref):
                with self._store.local_path(ref) as local:
                    processed = self._process_tender_file(local)
                files.append(TenderFile(path.name, entry.role, processed.chunks))
                low_ocr |= low_confidence_pages(
                    path.name, processed.pages, quality.ocr_min_confidence
                )
                low_rows |= low_confidence_rows(
                    path.name, processed.chunks, quality.ocr_row_min_confidence
                )
        started = time.monotonic()
        found = flag_low_ocr(
            parse_tender_files(files, self._requirement_options, ontologies), low_ocr, low_rows
        )
        logger.info(
            "tender requirements extracted",
            extra={
                "items": len(found.items),
                "general": len(found.general),
                "seconds": round(time.monotonic() - started, 2),
            },
        )
        return ParsedTender(
            bid_number=manifest.bid_number,
            files=[f.name for f in files],
            items=[
                ItemInput(
                    e.item,
                    e.requirements,
                    MatchContext.for_ontology(ontology_for(e.item.title, ontologies)),
                )
                for e in found.items
            ],
            general=found.general,
            item_source=found.source,
            extraction_quality=self._quality(found, low_ocr, unfetched, files),
        )

    def _match_timed(self, entry: ItemInput, manufacturers: Sequence[str] | None) -> ItemReport:
        """`match_item`, logging how long the item took."""
        started = time.monotonic()
        report = match_item(entry, self._source, self._selection, manufacturers)
        logger.info(
            "item matched",
            extra={
                "item": entry.item.item_no,
                "requirements": len(entry.requirements),
                "seconds": round(time.monotonic() - started, 2),
            },
        )
        return report

    def _process_tender_file(self, path: Path) -> ProcessedDocument:
        """One tender file's pages and chunks: the tender cache by sha256, else processing.

        Reruns, the GeM re-match and runs after an upload's background parse read the cache.
        """
        started = time.monotonic()
        sha256 = _file_sha256(path)
        cache = self._tender_cache
        processed = cache.get(sha256) if cache else None
        cached = processed is not None
        if processed is None:
            processed = process_file(path, self._tender_options)
            if cache:
                cache.put(sha256, processed)
        logger.info(
            "tender file read",
            extra={
                "file": path.name,
                "pages": len(processed.pages),
                "cached": cached,
                "seconds": round(time.monotonic() - started, 2),
            },
        )
        return processed

    def _item_facts(self, entry: ItemRequirements, low_ocr: set[tuple[str, int]]) -> ItemFacts:
        """What the extraction check reads about one item."""
        atomic = leaves(entry.requirements)
        return ItemFacts(
            item_no=entry.item.item_no,
            title=entry.item.title,
            names_product=ontology_for(entry.item.title, self._ontologies) is not None,
            requirements=len(atomic),
            low_ocr_requirements=sum((r.document, r.page_number) in low_ocr for r in atomic),
        )

    def _quality(
        self,
        found: TenderRequirements,
        low_ocr: set[tuple[str, int]],
        unfetched: Sequence[str],
        files: Sequence[TenderFile] = (),
    ) -> ExtractionQuality:
        """Judge the extraction; titles are read with every ontology, however the run is set."""
        spec_roles = self._requirement_options.spec_roles
        return extraction_quality(
            [self._item_facts(entry, low_ocr) for entry in found.items],
            found.source is ItemSource.ITEM_CATEGORY,
            self._requirement_options.quality,
            unfetched,
            spec_outside=sum(r.evidence is EvidenceSource.CATALOGUE for r in found.general),
            spec_files=[f.name for f in files if f.role in spec_roles],
        )

    def warm_catalogues(self) -> int:
        """Read every registered catalogue into the cache now, and load the judge's models.

        Runs queued after it wait for neither.

        Returns:
            How many catalogues are loaded.
        """
        loaded = self._source.warm()
        logger.info("catalogues warm", extra={"documents": loaded})
        if self._judge is not None:
            self._judge.warm_up()
        return loaded

    def match_tender(
        self,
        tender: str,
        *,
        use_ontology: bool = True,
        manufacturers: Sequence[str] | None = None,
        unfetched: Sequence[str] = (),
    ) -> TenderReport:
        """Match every line item of one tender; report its other requirements alongside.

        Args:
            tender: Tender folder name under the tender data root ("GEM_2026_B_8119613").
            use_ontology: False runs without any ontology (labels and units as printed).
            manufacturers: Only these; None for every registered one.
            unfetched: Linked files that could not be fetched or read.

        Returns:
            The report.

        Raises:
            NotFoundError: If the tender folder has no tender.yaml.
            DocumentParseError: If a tender file cannot be parsed.
        """
        with log_context(tender_id=tender):
            self._source.refresh()
            parsed = self.parse_tender(tender, use_ontology=use_ontology, unfetched=unfetched)
            started = time.monotonic()
            reports = [self._match_timed(entry, manufacturers) for entry in parsed.items]
            logger.info(
                "tender matched",
                extra={
                    "items": len(reports),
                    "files": len(parsed.files),
                    "seconds": round(time.monotonic() - started, 2),
                },
            )
            quality = parsed.extraction_quality
            decided_by = "rules"
            # Items that cannot be trusted are not worth paying to judge.
            if self._judge is not None and quality.status is not ExtractionStatus.UNRELIABLE:
                started = time.monotonic()
                judged = self._judge.judge_items(parsed.items, reports, self._source)
                reports, decided_by = judged.reports, judged.decided_by
                logger.info(
                    "judge step done", extra={"seconds": round(time.monotonic() - started, 2)}
                )
            reports = items_with_confidence(
                reports, self._source, self._confidence, self._selection.status_scores
            )
        general = [general_result(r) for r in parsed.general]
        if quality.status is ExtractionStatus.UNRELIABLE:
            note = "extraction unreliable: " + ", ".join(r.code.value for r in quality.reasons)
            reports = [item_for_review(r, note, self._selection) for r in reports]
            general = for_review(general, note)
        reports = [self._with_top_vendors(r) for r in reports]
        return TenderReport(
            tender=tender,
            bid_number=parsed.bid_number,
            extraction_quality=quality,
            single_firm=parsed.single_firm,
            tender_files=parsed.files,
            generated_at=datetime.now(UTC),
            ontology_used=use_ontology,
            decided_by=decided_by,
            items=reports,
            ranking=self._ranking(reports),
            score_explanation=score_explanation(
                self._selection.status_scores, self._selection.min_coverage_pct
            ),
            status_scores=self._selection.status_scores,
            status_lists=status_lists(reports),
            general_requirements=parsed.general,
            general_results=general,
            named_makes=parsed.named_makes,
            item_source=parsed.item_source.value,
        )

    def uncovered_types(
        self, report: TenderReport, manufacturers: Sequence[str] | None = None
    ) -> list[UncoveredType]:
        """Product types of a report's items that no document of the run's makers declares.

        Read from the stored report (item titles), so it needs no tender parsing. A type is
        covered only by a document of `manufacturers`: a GeM listing stored by another run's
        check does not hide the check from a run that never matched against it.

        Args:
            report: A run's report.
            manufacturers: The run's manufacturers; None counts every stored document.

        Returns:
            One entry per type, in first-item order, with its category and item numbers.
        """
        self._source.refresh()
        declared = self._source.product_types()
        found: dict[str, UncoveredType] = {}
        for item in report.items:
            ontology = ontology_for(item.item.title, self._ontologies)
            product_type = product_type_for(item.item.title, ontology)
            if (
                ontology is None
                or product_type is None
                or self._covered(product_type, declared, manufacturers)
            ):
                continue
            uncovered = found.setdefault(
                product_type, UncoveredType(product_type, ontology.category, [])
            )
            uncovered.item_nos.append(item.item.item_no)
        return list(found.values())

    def _covered(
        self,
        product_type: str,
        declared: Mapping[str, frozenset[str]],
        manufacturers: Sequence[str] | None,
    ) -> bool:
        """True if a stored document of one of `manufacturers` (None: any) declares the type."""
        categories = declared.get(product_type, frozenset())
        if manufacturers is None or not categories:
            return bool(categories)
        return any(
            self._source.documents(m, c, product_type) for m in manufacturers for c in categories
        )

    def rematch_items(
        self,
        previous: TenderReport,
        product_types: Sequence[str],
        manufacturers: Sequence[str] | None,
    ) -> TenderReport:
        """Re-match, by rules only, the items of these product types; keep every other item.

        Used after GeM listings were stored: only the affected items can change, so only they
        are matched again (no LLM judge). The ranking is recomputed over all items.

        Args:
            previous: The run's report.
            product_types: Product types whose items are matched again.
            manufacturers: Only these (None: every manufacturer and listing brand).

        Returns:
            A new report.

        Raises:
            InvalidInputError: If the tender now parses into other items than the report has.
        """
        with log_context(tender_id=previous.tender):
            self._source.refresh()
            parsed = self.parse_tender(previous.tender)
            if len(parsed.items) != len(previous.items):
                raise InvalidInputError(
                    "The tender no longer parses into the report's items; start a new run",
                    details={"tender": previous.tender},
                )
            wanted = set(product_types)
            quality = previous.extraction_quality
            note = (
                "extraction unreliable: " + ", ".join(r.code.value for r in quality.reasons)
                if quality.status is ExtractionStatus.UNRELIABLE
                else None
            )
            items = [
                (
                    self._rematched(entry, manufacturers, note)
                    if product_type_for(entry.item.title, entry.ctx.ontology) in wanted
                    else old
                )
                for entry, old in zip(parsed.items, previous.items, strict=True)
            ]
        changed = sum(new is not old for new, old in zip(items, previous.items, strict=True))
        return previous.model_copy(
            update={
                "generated_at": datetime.now(UTC),
                "decided_by": f"{previous.decided_by} + GeM listings (rules, {changed} items)",
                "items": items,
                "ranking": self._ranking(items),
                "status_lists": status_lists(items),
            }
        )

    def _rematched(
        self, entry: ItemInput, manufacturers: Sequence[str] | None, review_note: str | None
    ) -> ItemReport:
        """One item matched again by rules, with confidence; all to review if the note says so."""
        report = match_item(entry, self._source, self._selection, manufacturers)
        report = items_with_confidence(
            [report], self._source, self._confidence, self._selection.status_scores
        )[0]
        return item_for_review(report, review_note, self._selection) if review_note else report

    def _with_top_vendors(self, report: ItemReport) -> ItemReport:
        """The item with its best vendors listed (after review notes changed the scores)."""
        best = top_vendors(
            report.manufacturers,
            self._selection.min_coverage_pct,
            self._selection.top_vendors_per_item,
        )
        return report.model_copy(update={"top_vendors": best})

    def _ranking(self, reports: Sequence[ItemReport]) -> list[RankingEntry]:
        """Tender-level ranking over all items' results."""
        by_manufacturer: dict[str, list[Comparison]] = {}
        with_catalogue: dict[str, int] = {}
        for report in reports:
            for m in report.manufacturers:
                by_manufacturer.setdefault(m.manufacturer, []).extend(m.results)
                with_catalogue[m.manufacturer] = with_catalogue.get(m.manufacturer, 0) + bool(
                    m.chosen or m.tied_documents
                )
        return rank(
            by_manufacturer,
            with_catalogue,
            self._selection.status_scores,
            self._selection.min_coverage_pct,
        )


def build_match_service(
    settings: Settings,
    store: FileStore | None = None,
    judge: ItemJudge | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> MatchService:
    """Wire the in-memory match service from settings.

    Args:
        settings: Application settings.
        store: File store (built from settings if omitted).
        judge: LLM judge for what rules leave open; None for rules only.
        session_factory: Reads stored GeM listings; with DISCOVERY_ENABLED they are matched
            next to the catalogues (None: catalogues only).

    Returns:
        A ready service.
    """
    ontologies = load_ontologies(settings.ONTOLOGY_DIR)
    vocabulary = SearchVocabulary.from_ontologies(ontologies)
    matcher = ProductMatcher.from_ontologies(ontologies, vocabulary)
    options = ProcessingOptions.from_settings(
        settings, vocabulary, matcher, build_ocr_engine(settings)
    )
    files = store or build_file_store(settings)
    catalogue_fingerprint = processing_fingerprint(settings, Path(__file__).resolve().parents[2])
    cache = ProcessedCache(settings.CHUNK_CACHE_DIR, catalogue_fingerprint)
    tender_cache = ProcessedCache(
        settings.CHUNK_CACHE_DIR / TENDER_CACHE_SUBDIR,
        hashlib.sha256(f"tender:{catalogue_fingerprint}".encode()).hexdigest(),
    )
    source: ChunkSource = InMemoryChunkSource(
        load_registry(settings.MANUFACTURER_DATA_DIR), catalogue_processor(files, options), cache
    )
    if settings.DISCOVERY_ENABLED and session_factory is not None:
        factory = session_factory
        source = WithListings(source, lambda: load_listings(factory))
    return MatchService(
        source=source,
        tender_options=options.for_tender(),
        requirement_options=RequirementOptions.from_settings(settings),
        selection=SelectionOptions.from_settings(settings),
        confidence=ConfidenceOptions.from_settings(settings),
        ontologies=ontologies,
        tender_root=settings.TENDER_DATA_DIR,
        judge=judge,
        store=files,
        tender_cache=tender_cache,
    )


def _file_sha256(path: Path) -> str:
    """sha256 of a file, read in blocks (tender attachments reach 60 MB)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_HASH_BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()
