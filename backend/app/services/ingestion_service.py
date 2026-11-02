"""Ingestion use cases: manufacturer catalogues, tender link fetching, tender documents.

Each file (or tender) is ingested in its own transaction, so one bad file does not roll back
the others; domain and database errors are recorded per item and the batch continues.
Failures are logged and returned (never swallowed) so callers can report them and exit non-zero.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.exceptions import AppError
from app.db.repositories import DocumentRepository
from app.db.session import create_db_engine, create_session_factory, session_scope
from app.ingestion.ocr import build_ocr_engine
from app.ingestion.pipeline import (
    IngestOutcome,
    ReprocessPolicy,
    ingest_manufacturer_source,
    rebuild_sections,
    retag_document,
)
from app.ingestion.processing import ProcessingOptions
from app.ingestion.product_tagger import ProductMatcher
from app.ingestion.registry import load_registry
from app.ontology.loader import load_ontologies
from app.retrieval.text_normalizer import SearchVocabulary
from app.storage.file_store import FileStore, build_file_store
from app.tender.documents import TenderIngestOutcome, ingest_tender_folder
from app.tender.link_fetcher import (
    BID_FILENAME,
    MANIFEST_FILENAME,
    CompiledLinkRules,
    CrawlReport,
    LinkCrawler,
    build_http_client,
    init_manifest,
    load_link_rules,
    read_manifest,
    write_manifest,
)

logger = logging.getLogger(__name__)

_BYTES_PER_MB = 1024 * 1024


@dataclass(slots=True)
class BatchResult[T]:
    """Successful results plus `(item, error)` pairs for the ones that failed."""

    succeeded: list[T] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class IngestionService:
    """Runs ingestion jobs; all collaborators are injected."""

    settings: Settings
    session_factory: sessionmaker[Session]
    store: FileStore
    options: ProcessingOptions
    link_rules: CompiledLinkRules
    http_client_factory: Callable[[], AbstractContextManager[httpx.Client]]

    def ingest_manufacturers(
        self, category: str | None = None, *, reprocess: bool = False
    ) -> BatchResult[IngestOutcome]:
        """Ingest every registered catalogue (already-stored ones are skipped).

        Args:
            category: Only this category, if given.
            reprocess: Re-parse catalogues that are already stored.

        Returns:
            Per-file outcomes and failures.
        """
        result: BatchResult[IngestOutcome] = BatchResult()
        policy = ReprocessPolicy(enabled=reprocess)
        for source in load_registry(self.settings.MANUFACTURER_DATA_DIR, category=category):
            try:
                self.options.product_matcher.validate(source.product_types, source.sub_types)
                with session_scope(self.session_factory) as session:
                    outcome = ingest_manufacturer_source(
                        session, self.store, source, self.options, policy
                    )
                result.succeeded.append(outcome)
            except (AppError, SQLAlchemyError) as exc:
                logger.error(
                    "manufacturer ingest failed",
                    extra={"file": str(source.path), "error": str(exc)},
                )
                result.failed.append((str(source.path), str(exc)))
        return result

    def retag_products(self) -> BatchResult[tuple[str, int]]:
        """Refresh declared product types from sources.yaml and re-tag every stored chunk.

        No re-parsing: tags are recomputed from stored sections and search text.

        Returns:
            `(file_path, chunks_changed)` per document, and failures.
        """
        declared = {s.sha256: s for s in load_registry(self.settings.MANUFACTURER_DATA_DIR)}
        with session_scope(self.session_factory) as session:
            documents = [(d.id, d.file_path) for d in DocumentRepository(session).find()]
        result: BatchResult[tuple[str, int]] = BatchResult()
        for document_id, file_path in documents:
            try:
                with session_scope(self.session_factory) as session:
                    document = DocumentRepository(session).get_or_raise(document_id)
                    source = declared.get(document.sha256)
                    if source is not None:
                        self.options.product_matcher.validate(
                            source.product_types, source.sub_types
                        )
                        document.product_types = list(source.product_types)
                        document.sub_types = list(source.sub_types)
                    changed = retag_document(session, document, self.options)
                result.succeeded.append((file_path, changed))
            except (AppError, SQLAlchemyError) as exc:
                result.failed.append((file_path, str(exc)))
        return result

    def classify_sections(self) -> BatchResult[tuple[str, int]]:
        """Rebuild sections and chunk categories of every stored document (no re-parsing).

        Run after editing `config/section_categories.yaml`.

        Returns:
            `(file_path, sections)` per document, and failures.
        """
        with session_scope(self.session_factory) as session:
            documents = [(d.id, d.file_path) for d in DocumentRepository(session).find()]
        result: BatchResult[tuple[str, int]] = BatchResult()
        for document_id, file_path in documents:
            try:
                with session_scope(self.session_factory) as session:
                    document = DocumentRepository(session).get_or_raise(document_id)
                    sections = rebuild_sections(session, document, self.options)
                result.succeeded.append((file_path, sections))
            except (AppError, SQLAlchemyError) as exc:
                result.failed.append((file_path, str(exc)))
        return result

    def tender_dirs(self, only: str | None = None) -> list[Path]:
        """Tender folders under the data root (those with bid.pdf or tender.yaml).

        Args:
            only: Folder name (e.g. "GEM_2026_B_8119613") to restrict to.

        Returns:
            Folders in name order.
        """
        root = self.settings.TENDER_DATA_DIR
        dirs = sorted(d for d in root.iterdir() if d.is_dir()) if root.is_dir() else []
        dirs = [
            d for d in dirs if (d / MANIFEST_FILENAME).is_file() or (d / BID_FILENAME).is_file()
        ]
        return [d for d in dirs if only is None or d.name == only]

    def _crawler(self, client: httpx.Client | None) -> LinkCrawler:
        """Link crawler with configured limits (offline when `client` is None)."""
        return LinkCrawler(
            store=self.store,
            rules=self.link_rules,
            max_depth=self.settings.TENDER_LINK_MAX_DEPTH,
            max_bytes=self.settings.HTTP_MAX_DOWNLOAD_MB * _BYTES_PER_MB,
            client=client,
        )

    def check_links(self, only: str | None = None) -> BatchResult[tuple[str, CrawlReport]]:
        """Offline: list links each tender should have fetched but that are not recorded.

        Args:
            only: Restrict to one tender folder.

        Returns:
            `(folder, report)` per tender; `report.missing` lists the gaps.
        """
        result: BatchResult[tuple[str, CrawlReport]] = BatchResult()
        for tender_dir in self.tender_dirs(only):
            try:
                manifest = read_manifest(tender_dir / MANIFEST_FILENAME)
                report = self._crawler(None).crawl(self.store.ref_for(tender_dir), manifest)
                result.succeeded.append((tender_dir.name, report))
            except (AppError, SQLAlchemyError) as exc:
                result.failed.append((tender_dir.name, str(exc)))
        return result

    def fetch_links(self, only: str | None = None) -> BatchResult[tuple[str, CrawlReport]]:
        """Download every missing `fetch` link and update each `tender.yaml`.

        Folders with only a bid.pdf get a new manifest first.

        Args:
            only: Restrict to one tender folder.

        Returns:
            `(folder, report)` per tender.
        """
        result: BatchResult[tuple[str, CrawlReport]] = BatchResult()
        with self.http_client_factory() as client:
            for tender_dir in self.tender_dirs(only):
                try:
                    result.succeeded.append((tender_dir.name, self._fetch_one(tender_dir, client)))
                except (AppError, SQLAlchemyError) as exc:
                    logger.error(
                        "link fetch failed", extra={"tender": tender_dir.name, "error": str(exc)}
                    )
                    result.failed.append((tender_dir.name, str(exc)))
        return result

    def _fetch_one(self, tender_dir: Path, client: httpx.Client) -> CrawlReport:
        """Crawl one tender folder and save its manifest."""
        manifest_path = tender_dir / MANIFEST_FILENAME
        manifest = (
            read_manifest(manifest_path) if manifest_path.is_file() else init_manifest(tender_dir)
        )
        new = not manifest_path.is_file()
        report = self._crawler(client).crawl(self.store.ref_for(tender_dir), manifest)
        if new or report.fetched:  # a tracked provenance file: only rewritten when it changes
            write_manifest(manifest_path, manifest)
        return report

    def ingest_tenders(
        self, only: str | None = None, *, reprocess: bool = False
    ) -> BatchResult[TenderIngestOutcome]:
        """Store every tender's files (bid.pdf and linked documents) as pages and chunks.

        Args:
            only: Restrict to one tender folder.
            reprocess: Re-parse files already stored (a shared file once per run).

        Returns:
            Per-tender outcomes and failures.
        """
        result: BatchResult[TenderIngestOutcome] = BatchResult()
        policy = ReprocessPolicy(enabled=reprocess)
        for tender_dir in self.tender_dirs(only):
            try:
                with session_scope(self.session_factory) as session:
                    outcome = ingest_tender_folder(
                        session, self.store, tender_dir, self.options, policy
                    )
                result.succeeded.append(outcome)
            except (AppError, SQLAlchemyError) as exc:
                logger.error(
                    "tender ingest failed", extra={"tender": tender_dir.name, "error": str(exc)}
                )
                result.failed.append((tender_dir.name, str(exc)))
        return result


def build_ingestion_service(settings: Settings) -> IngestionService:
    """Wire the ingestion service from settings (DB, file store, ontology, OCR, link rules).

    Args:
        settings: Application settings.

    Returns:
        A ready service.
    """
    ontologies = load_ontologies(settings.ONTOLOGY_DIR)
    vocabulary = SearchVocabulary.from_ontologies(ontologies)
    matcher = ProductMatcher.from_ontologies(ontologies, vocabulary)

    def client_factory() -> AbstractContextManager[httpx.Client]:
        """New HTTP client per fetch job (closed when the job ends)."""
        return build_http_client(settings.HTTP_TIMEOUT_S, settings.HTTP_USER_AGENT)

    return IngestionService(
        settings=settings,
        session_factory=create_session_factory(create_db_engine(settings)),
        store=build_file_store(settings),
        options=ProcessingOptions.from_settings(
            settings, vocabulary, matcher, build_ocr_engine(settings)
        ),
        link_rules=load_link_rules(settings.TENDER_LINK_RULES_FILE),
        http_client_factory=client_factory,
    )
