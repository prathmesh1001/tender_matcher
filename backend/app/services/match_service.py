"""Use case: match one tender folder against the registered catalogues (rules only).

In-memory path, no database: tender files listed in `tender.yaml` are processed, their
schedule tables give line items and atomic requirements, and catalogue chunks come from a
`ChunkSource` (`InMemoryChunkSource` with the sha256 cache for now). The ontology is used
when an item's category has one, unless the run is told not to use any.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.core.logging import log_context
from app.ingestion.ocr import build_ocr_engine
from app.ingestion.processing import ProcessingOptions, is_supported, process_file
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
from app.matching.documents import SelectionOptions
from app.matching.engine import ItemInput, general_result, match_item
from app.matching.scorer import rank
from app.ontology.loader import load_ontologies
from app.ontology.schema import Ontology
from app.reporting.warnings import single_firm_warning
from app.retrieval.text_normalizer import SearchVocabulary
from app.schemas.matching import Comparison
from app.schemas.report import ItemReport, RankingEntry, SingleFirmWarning, TenderReport
from app.schemas.requirements import Requirement
from app.storage.file_store import FileStore, build_file_store
from app.tender.items import ItemSource, TenderFile, parse_tender_files
from app.tender.link_fetcher import MANIFEST_FILENAME, read_manifest
from app.tender.pipeline import RequirementOptions, ontology_for

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ParsedTender:
    """One tender read from its files, before any matching."""

    bid_number: str
    files: list[str] = field(default_factory=list)
    items: list[ItemInput] = field(default_factory=list)
    general: list[Requirement] = field(default_factory=list)
    item_source: ItemSource = ItemSource.NONE

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
        ontologies: Sequence[Ontology],
        tender_root: Path,
    ) -> None:
        self._source = source
        self._tender_options = tender_options
        self._requirement_options = requirement_options
        self._selection = selection
        self._ontologies = list(ontologies)
        self._tender_root = tender_root

    def parse_tender(self, tender: str, *, use_ontology: bool = True) -> ParsedTender:
        """Read one tender's files into line items and general requirements.

        Args:
            tender: Tender folder name under the tender data root ("GEM_2026_B_8119613").
            use_ontology: False runs without any ontology (labels and units as printed).

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
        for entry in manifest.stored_files():
            path = tender_dir / (entry.file or "")
            if is_supported(path) and path.is_file():
                chunks = process_file(path, self._tender_options).chunks
                files.append(TenderFile(path.name, entry.role, chunks))
        found = parse_tender_files(files, self._requirement_options, ontologies)
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
        )

    def match_tender(self, tender: str, *, use_ontology: bool = True) -> TenderReport:
        """Match every line item of one tender; report its other requirements alongside.

        Args:
            tender: Tender folder name under the tender data root ("GEM_2026_B_8119613").
            use_ontology: False runs without any ontology (labels and units as printed).

        Returns:
            The report.

        Raises:
            NotFoundError: If the tender folder has no tender.yaml.
            DocumentParseError: If a tender file cannot be parsed.
        """
        with log_context(tender_id=tender):
            parsed = self.parse_tender(tender, use_ontology=use_ontology)
            reports = [match_item(entry, self._source, self._selection) for entry in parsed.items]
            logger.info("tender matched", extra={"items": len(reports), "files": len(parsed.files)})
        return TenderReport(
            tender=tender,
            bid_number=parsed.bid_number,
            single_firm=parsed.single_firm,
            tender_files=parsed.files,
            generated_at=datetime.now(UTC),
            ontology_used=use_ontology,
            items=reports,
            ranking=self._ranking(reports),
            general_requirements=parsed.general,
            general_results=[general_result(r) for r in parsed.general],
            named_makes=parsed.named_makes,
            item_source=parsed.item_source.value,
        )

    def _ranking(self, reports: Sequence[ItemReport]) -> list[RankingEntry]:
        """Tender-level ranking over all items' results."""
        by_manufacturer: dict[str, list[Comparison]] = {}
        with_catalogue: dict[str, int] = {}
        for report in reports:
            for m in report.manufacturers:
                by_manufacturer.setdefault(m.manufacturer, []).extend(m.results)
                with_catalogue[m.manufacturer] = with_catalogue.get(m.manufacturer, 0) + bool(
                    m.chosen
                )
        return rank(
            by_manufacturer,
            with_catalogue,
            self._selection.status_scores,
            self._selection.min_coverage_pct,
        )


def build_match_service(settings: Settings, store: FileStore | None = None) -> MatchService:
    """Wire the in-memory match service from settings.

    Args:
        settings: Application settings.
        store: File store (built from settings if omitted).

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
    cache = ProcessedCache(
        settings.CHUNK_CACHE_DIR,
        processing_fingerprint(settings, Path(__file__).resolve().parents[2]),
    )
    source = InMemoryChunkSource(
        load_registry(settings.MANUFACTURER_DATA_DIR), catalogue_processor(files, options), cache
    )
    return MatchService(
        source=source,
        tender_options=options.for_tender(),
        requirement_options=RequirementOptions.from_settings(settings),
        selection=SelectionOptions.from_settings(settings),
        ontologies=ontologies,
        tender_root=settings.TENDER_DATA_DIR,
    )
