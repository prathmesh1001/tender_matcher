"""Catalogue chunks for matching, read through a small interface (`ChunkSource`).

Matching asks one question per tender item and manufacturer: which chunks can be evidence
for this product type (CLAUDE.md section 7, step 1), and what does a cited page say (for
quote verification). `InMemoryChunkSource` answers from registered catalogue files that it
processes itself, caching each processed file on disk by its sha256; a Postgres source
(`ChunkRepository.list_for_product` + pages) is one more class with the same methods.

Product filter: with chunk tags, a document's chunks of the type plus its untagged chunks
when it declares the type, exactly like `ChunkRepository.list_for_product`. Without an
ontology there are no tags to trust, so every chunk of a document declaring the type is
returned and the comparator's label rules do the narrowing.
"""

import hashlib
import json
import logging
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, Protocol, TypedDict, cast

from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.ingestion.ocr import build_ocr_engine
from app.ingestion.processing import (
    ProcessedDocument,
    ProcessedPage,
    ProcessingOptions,
    process_file,
    set_product_family,
    tag_products,
)
from app.ingestion.product_tagger import Declared, ProductMatcher
from app.ingestion.registry import verify_source_file
from app.ontology.loader import load_ontologies
from app.retrieval.text_normalizer import SearchVocabulary
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import DocType
from app.schemas.sources import RegisteredSource
from app.storage.file_store import FileStore
from app.tender.normalizer import words

logger = logging.getLogger(__name__)

CACHE_SUFFIX: Final = ".json"


@dataclass(frozen=True, slots=True)
class CatalogueDocument:
    """What matching needs to know about one catalogue (from sources.yaml)."""

    manufacturer: str
    category: str
    title: str
    doc_type: DocType
    file: str
    sha256: str
    product_types: tuple[str, ...]
    sub_types: tuple[str, ...] = ()
    publish_date: date | None = None
    url: str | None = None
    fetched_at: datetime | None = None
    auto_sourced: bool = False  # a GeM listing: seller-declared, not checked by hand

    @classmethod
    def from_source(cls, source: RegisteredSource) -> CatalogueDocument:
        """Build from a registry entry."""
        return cls(
            manufacturer=source.manufacturer,
            category=source.category,
            title=source.title,
            doc_type=source.doc_type,
            file=source.path.name,
            sha256=source.sha256,
            product_types=tuple(source.product_types),
            sub_types=tuple(source.sub_types),
            publish_date=source.publish_date,
            url=str(source.url),
            fetched_at=source.fetched_at,
        )


class Provenance(TypedDict):
    """Where a cited document came from, as `Evidence` fields."""

    source_url: str | None
    fetched_at: datetime | None
    seller_declared: bool


def provenance(document: CatalogueDocument) -> Provenance:
    """Evidence fields of a document's origin: a GeM listing's URL and fetch date.

    Registered catalogues keep these empty: their file name and page are the citation.
    """
    if not document.auto_sourced:
        return Provenance(source_url=None, fetched_at=None, seller_declared=False)
    return Provenance(source_url=document.url, fetched_at=document.fetched_at, seller_declared=True)


@dataclass(frozen=True, slots=True)
class CatalogueChunk:
    """One chunk with the document it came from (for citations)."""

    document: CatalogueDocument
    chunk: ChunkDraft


class ChunkSource(Protocol):
    """Read access to catalogue chunks and page text."""

    def manufacturers(self, category: str) -> list[str]:
        """Manufacturers with at least one document in the category, sorted."""
        ...

    def product_types(self) -> dict[str, frozenset[str]]:
        """Every declared product type and the categories of the documents declaring it."""
        ...

    def documents(
        self, manufacturer: str, category: str, product_type: str
    ) -> list[CatalogueDocument]:
        """The manufacturer's documents that declare the product type."""
        ...

    def chunks_for_product(
        self, manufacturer: str, category: str, product_type: str, *, use_tags: bool
    ) -> list[CatalogueChunk]:
        """Chunks that can be evidence for the product type (see module doc)."""
        ...

    def page_text(self, sha256: str, page_number: int) -> str | None:
        """Text of one page of a document, or None if the page is unknown."""
        ...

    def doc_type(self, sha256: str) -> DocType | None:
        """A document's type, or None if it is not registered."""
        ...

    def page_ocr(self, sha256: str, page_number: int) -> bool:
        """True if a page's text came from OCR."""
        ...

    def warm(self) -> int:
        """Process every registered document now, so runs only read the cache."""
        ...

    def refresh(self) -> None:
        """Re-read documents that can change while the service lives (GeM listings)."""
        ...


def declared_types_named(title: str, product_types: Iterable[str]) -> set[str]:
    """Declared product types whose name words all appear, in order, in the item title.

    Used when no ontology covers the item: "butterfly_valve" is named by "TRIPLE OFFSET
    BUTTERFLY VALVE,250MM". Only the type names written in sources.yaml are used, never
    synonyms, so a title naming the product differently finds nothing (unsupported).

    Args:
        title: Tender item title.
        product_types: Product type names declared by catalogues.

    Returns:
        The named types.
    """
    title_words = words(title)
    named: set[str] = set()
    for product_type in product_types:
        name = words(product_type.replace("_", " "))
        if any(title_words[i : i + len(name)] == name for i in range(len(title_words))):
            named.add(product_type)
    return named


def evidence_chunks(
    document: CatalogueDocument,
    chunks: Sequence[ChunkDraft],
    product_type: str,
    *,
    use_tags: bool,
) -> list[CatalogueChunk]:
    """One document's chunks that can be evidence for `product_type` (see module doc).

    Args:
        document: The document.
        chunks: Its chunks in reading order.
        product_type: Product type of the tender item.
        use_tags: Filter by chunk product tags (needs the ontology that made them).

    Returns:
        The chunks, in reading order.
    """
    declares = product_type in document.product_types
    if not use_tags:
        return [CatalogueChunk(document, c) for c in chunks] if declares else []
    return [
        CatalogueChunk(document, c)
        for c in chunks
        if c.product_type == product_type or (c.product_type is None and declares)
    ]


class ProcessedCache:
    """Processed files on disk, one JSON file per sha256.

    Each file records the fingerprint of the code and settings that produced it; a different
    fingerprint is a miss, so a chunking change never serves stale chunks.
    """

    def __init__(self, root: Path, fingerprint: str) -> None:
        self._root = root
        self._fingerprint = fingerprint

    def _path(self, sha256: str) -> Path:
        return self._root / f"{sha256}{CACHE_SUFFIX}"

    def get(self, sha256: str) -> ProcessedDocument | None:
        """The cached document, or None if absent, unreadable or made by other code."""
        path = self._path(sha256)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("chunk cache unreadable", extra={"file": path.name, "error": str(exc)})
            return None
        if data.get("fingerprint") != self._fingerprint:
            return None
        return ProcessedDocument(
            pages=[ProcessedPage(**p) for p in data["pages"]],
            chunks=[ChunkDraft.model_validate(c) for c in data["chunks"]],
        )

    def put(self, sha256: str, processed: ProcessedDocument) -> None:
        """Store a processed document under its sha256."""
        self._root.mkdir(parents=True, exist_ok=True)
        data = {
            "fingerprint": self._fingerprint,
            "pages": [
                {
                    "page_number": p.page_number,
                    "text": p.text,
                    "ocr": p.ocr,
                    "ocr_confidence": p.ocr_confidence,
                }
                for p in processed.pages
            ],
            "chunks": [c.model_dump(mode="json") for c in processed.chunks],
        }
        self._path(sha256).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def fingerprint(paths: Iterable[Path], settings_json: str, base: Path | None = None) -> str:
    """sha256 over the given source files (sorted, by content) and the settings dump.

    Line endings are folded (CRLF -> LF) and each file is named by its path relative to
    `base`, so a checkout that only rewrites line endings, or the same code installed in
    another folder (Windows checkout vs Docker image), keeps the cache.

    Args:
        paths: Files whose content decides the processing output (code, ontology, config).
        settings_json: The settings, serialized.
        base: Folder the file names are taken relative to; a file outside it is named by
            its file name alone. None names every file by its file name.

    Returns:
        Hex digest.
    """
    digest = hashlib.sha256(settings_json.encode("utf-8"))
    for name, path in sorted((_file_label(p, base), p) for p in paths):
        digest.update(name.encode("utf-8"))
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def _file_label(path: Path, base: Path | None) -> str:
    """A file's name for the fingerprint: relative to `base` when inside it."""
    if base is not None:
        try:
            return path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            pass
    return path.name


Processor = Callable[[RegisteredSource], ProcessedDocument]
# Code whose output the cache holds, relative to the backend folder.
_FINGERPRINT_GLOBS: Final = (
    "app/ingestion/*.py",
    "app/retrieval/text_normalizer.py",
    "app/schemas/chunking.py",
    "app/schemas/parsing.py",
)


class _ReadRecorder:
    """Settings stand-in that records which settings are read."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.read: set[str] = set()

    def __getattr__(self, name: str) -> object:
        self.read.add(name)
        return getattr(self._settings, name)


def processing_settings(settings: Settings) -> dict[str, Any]:
    """The settings catalogue processing reads, found by building its options once.

    Only these decide a processed file, so changing any other setting (upload limits, the LLM,
    report paths) keeps the cache. Recording the reads keeps the list complete without a hand-
    kept copy that could go stale.

    Args:
        settings: Application settings.

    Returns:
        Setting name -> value (JSON-ready), for every setting processing reads.
    """
    recorder = _ReadRecorder(settings)
    as_settings = cast(Settings, recorder)
    ontologies = load_ontologies(settings.ONTOLOGY_DIR)
    vocabulary = SearchVocabulary.from_ontologies(ontologies)
    matcher = ProductMatcher.from_ontologies(ontologies, vocabulary)
    ProcessingOptions.from_settings(as_settings, vocabulary, matcher, build_ocr_engine(as_settings))
    dumped = settings.model_dump(mode="json")
    return {name: dumped[name] for name in sorted(recorder.read) if name in dumped}


def processing_fingerprint(settings: Settings, backend_dir: Path) -> str:
    """Fingerprint of everything that decides a processed file: code, ontology, settings.

    Args:
        settings: Application settings (ontology folder, section categories, thresholds).
        backend_dir: The backend folder, where the code globs are resolved.

    Returns:
        Hex digest for `ProcessedCache`.
    """
    files = [p for g in _FINGERPRINT_GLOBS for p in backend_dir.glob(g)]
    files += [*sorted(settings.ONTOLOGY_DIR.glob("*.yaml")), settings.SECTION_CATEGORIES_FILE]
    return fingerprint(
        files, json.dumps(processing_settings(settings), sort_keys=True), base=backend_dir
    )


def catalogue_processor(store: FileStore, options: ProcessingOptions) -> Processor:
    """The ingestion steps for one catalogue, without the database.

    Same order as `ingest_manufacturer_source`: check the sha256 against sources.yaml,
    parse, tag products by the declared types, put every chunk in the catalogue's family.

    Args:
        store: File store holding the catalogues.
        options: Catalogue processing options.

    Returns:
        A function from a registry entry to its processed document.

    Raises:
        SourceRegistryError: (from the returned function) if the file is missing or changed.
    """

    def process(source: RegisteredSource) -> ProcessedDocument:
        verify_source_file(source)
        with store.local_path(store.ref_for(source.path)) as path:
            processed = process_file(path, options)
        processed = tag_products(
            processed, options, Declared(tuple(source.product_types), tuple(source.sub_types))
        )
        return set_product_family(processed, source.title)

    return process


@dataclass(slots=True)
class InMemoryChunkSource:
    """Chunks of registered catalogues, processed on first use and kept in memory.

    Args:
        sources: Registered catalogues.
        process: Turns one source into tagged chunks (parse, tag products, set family).
        cache: Disk cache by sha256, or None to always process.
    """

    sources: Sequence[RegisteredSource]
    process: Processor
    cache: ProcessedCache | None = None
    _loaded: dict[str, ProcessedDocument] = field(default_factory=dict)
    _locks: dict[str, threading.Lock] = field(default_factory=dict)
    _locks_guard: threading.Lock = field(default_factory=threading.Lock)

    def manufacturers(self, category: str) -> list[str]:
        """Manufacturers with at least one document in the category, sorted."""
        return sorted({s.manufacturer for s in self.sources if s.category == category})

    def product_types(self) -> dict[str, frozenset[str]]:
        """Every declared product type and the categories of the documents declaring it."""
        found: dict[str, set[str]] = {}
        for source in self.sources:
            for product_type in source.product_types:
                found.setdefault(product_type, set()).add(source.category)
        return {t: frozenset(c) for t, c in found.items()}

    def _matching(self, manufacturer: str, category: str) -> list[RegisteredSource]:
        return [
            s for s in self.sources if s.manufacturer == manufacturer and s.category == category
        ]

    def documents(
        self, manufacturer: str, category: str, product_type: str
    ) -> list[CatalogueDocument]:
        """The manufacturer's documents that declare the product type."""
        return [
            CatalogueDocument.from_source(s)
            for s in self._matching(manufacturer, category)
            if product_type in s.product_types
        ]

    def _load(self, source: RegisteredSource) -> ProcessedDocument:
        """Processed document for a source: memory, then disk cache, then processing.

        Thread-safe: a background warm-up and a run never process the same document twice;
        the second caller waits for that one document only.
        """
        loaded = self._loaded.get(source.sha256)
        if loaded is not None:
            return loaded
        with self._locks_guard:
            lock = self._locks.setdefault(source.sha256, threading.Lock())
        with lock:
            loaded = self._loaded.get(source.sha256)
            return loaded if loaded is not None else self._read_or_process(source)

    def _read_or_process(self, source: RegisteredSource) -> ProcessedDocument:
        """Disk cache, else process and cache; kept in memory. Caller holds the doc's lock."""
        processed = self.cache.get(source.sha256) if self.cache else None
        if processed is None:
            logger.info("processing catalogue", extra={"file": source.path.name})
            processed = self.process(source)
            if self.cache:
                self.cache.put(source.sha256, processed)
        self._loaded[source.sha256] = processed
        return processed

    def chunks_for_product(
        self, manufacturer: str, category: str, product_type: str, *, use_tags: bool
    ) -> list[CatalogueChunk]:
        """Chunks that can be evidence for the product type, by document then reading order.

        Documents that neither declare the type nor could hold chunks tagged with it are
        not processed. Tagged chunks of a type the document does not declare cannot exist:
        the tagger only assigns declared types.
        """
        out: list[CatalogueChunk] = []
        for source in self._matching(manufacturer, category):
            if product_type not in source.product_types:
                continue
            processed = self._load(source)
            document = CatalogueDocument.from_source(source)
            out.extend(evidence_chunks(document, processed.chunks, product_type, use_tags=use_tags))
        return out

    def page_text(self, sha256: str, page_number: int) -> str | None:
        """Text of one page of a document, or None if the page is unknown.

        Raises:
            NotFoundError: If `sha256` is not a registered document.
        """
        source = next((s for s in self.sources if s.sha256 == sha256), None)
        if source is None:
            raise NotFoundError("Unknown catalogue for page lookup", details={"sha256": sha256})
        pages = self._load(source).pages
        return next((p.text for p in pages if p.page_number == page_number), None)

    def doc_type(self, sha256: str) -> DocType | None:
        """A document's type, or None if it is not registered."""
        return next((s.doc_type for s in self.sources if s.sha256 == sha256), None)

    def warm(self) -> int:
        """Load every registered document (disk cache, else process and cache it).

        Returns:
            How many documents are loaded.
        """
        for source in self.sources:
            self._load(source)
        return len(self._loaded)

    def refresh(self) -> None:
        """Nothing to re-read: registered catalogues change only with sources.yaml."""

    def page_ocr(self, sha256: str, page_number: int) -> bool:
        """True if a page's text came from OCR; False for an unknown document or page."""
        source = next((s for s in self.sources if s.sha256 == sha256), None)
        if source is None:
            return False
        pages = self._load(source).pages
        return any(p.ocr for p in pages if p.page_number == page_number)
