"""Use case: match one tender folder against the registered catalogues (rules only).

In-memory path, no database: tender files listed in `tender.yaml` are processed, their
schedule tables give line items and atomic requirements, and catalogue chunks come from a
`ChunkSource` (`InMemoryChunkSource` with the sha256 cache for now). The ontology is used
when an item's category has one, unless the run is told not to use any.
"""

import logging
from collections.abc import Sequence
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
from app.matching.engine import ItemInput, match_item
from app.matching.scorer import rank
from app.ontology.loader import load_ontologies
from app.ontology.schema import Ontology
from app.retrieval.text_normalizer import SearchVocabulary
from app.schemas.matching import Comparison
from app.schemas.report import ItemReport, RankingEntry, TenderReport
from app.storage.file_store import FileStore, build_file_store
from app.tender.link_fetcher import MANIFEST_FILENAME, read_manifest
from app.tender.pipeline import RequirementOptions, ontology_for, tender_requirements

logger = logging.getLogger(__name__)


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

    def _items(self, tender_dir: Path, use_ontology: bool) -> tuple[list[str], list[ItemInput]]:
        """Tender files used and the line items found in them, in manifest order."""
        manifest = read_manifest(tender_dir / MANIFEST_FILENAME)
        ontologies = self._ontologies if use_ontology else []
        files: list[str] = []
        items: list[ItemInput] = []
        for entry in manifest.stored_files():
            path = tender_dir / (entry.file or "")
            if not is_supported(path) or not path.is_file():
                continue
            files.append(path.name)
            chunks = process_file(path, self._tender_options).chunks
            for found in tender_requirements(
                chunks, path.name, self._requirement_options, ontologies
            ):
                ctx = MatchContext.for_ontology(ontology_for(found.item.title, ontologies))
                items.append(ItemInput(found.item, found.requirements, ctx))
        return files, items

    def match_tender(self, tender: str, *, use_ontology: bool = True) -> TenderReport:
        """Match every line item of one tender.

        Args:
            tender: Tender folder name under the tender data root ("GEM_2026_B_8119613").
            use_ontology: False runs without any ontology (labels and units as printed).

        Returns:
            The report.

        Raises:
            NotFoundError: If the tender folder has no tender.yaml.
            DocumentParseError: If a tender file cannot be parsed.
        """
        tender_dir = self._tender_root / tender
        if not (tender_dir / MANIFEST_FILENAME).is_file():
            raise NotFoundError(
                f"No {MANIFEST_FILENAME} for tender {tender}", details={"tender": tender}
            )
        with log_context(tender_id=tender):
            files, items = self._items(tender_dir, use_ontology)
            reports = [match_item(entry, self._source, self._selection) for entry in items]
            logger.info("tender matched", extra={"items": len(reports), "files": len(files)})
        return TenderReport(
            tender=tender,
            bid_number=read_manifest(tender_dir / MANIFEST_FILENAME).bid_number,
            tender_files=files,
            generated_at=datetime.now(UTC),
            ontology_used=use_ontology,
            items=reports,
            ranking=self._ranking(reports),
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
        return rank(by_manufacturer, with_catalogue, self._selection.status_scores)


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
