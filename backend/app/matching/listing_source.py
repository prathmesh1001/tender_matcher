"""GeM listings next to registered catalogues: one `ChunkSource` over both.

Registered catalogues come from sources.yaml and their files (`InMemoryChunkSource`). GeM
listings are found while the service runs, so they are read from the database, on `refresh()`
at the start of every match. Each listing is its own document of its brand: matching treats it
like a one-page catalogue, and its citations carry the listing's URL and fetch date.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Chunk, Document
from app.db.repositories import ChunkRepository, DocumentRepository
from app.db.session import session_scope
from app.ingestion.processing import ProcessedDocument, ProcessedPage
from app.matching.chunk_source import (
    CatalogueChunk,
    CatalogueDocument,
    ChunkSource,
    evidence_chunks,
)
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import DocType


@dataclass(frozen=True, slots=True)
class StoredListing:
    """One stored listing: what matching knows about it, and its page and chunks."""

    document: CatalogueDocument
    processed: ProcessedDocument


def _draft(chunk: Chunk) -> ChunkDraft:
    """A stored chunk as the draft matching reads."""
    return ChunkDraft(
        page_number=chunk.page_number,
        position=chunk.position,
        chunk_type=chunk.chunk_type,
        raw_text=chunk.raw_text,
        search_text=chunk.search_text,
        section=chunk.section,
        product_family=chunk.product_family,
        product_type=chunk.product_type,
        product_sub_type=chunk.product_sub_type,
        model=chunk.model,
        token_count=max(chunk.token_count, 1),
        meta=chunk.meta,
    )


def _listing(document: Document, chunks: Sequence[Chunk]) -> StoredListing:
    """A stored document as a listing matching can read."""
    return StoredListing(
        document=CatalogueDocument(
            manufacturer=document.manufacturer or "",
            category=document.category or "",
            title=document.title,
            doc_type=document.doc_type or DocType.GEM_LISTING,
            file=document.file_path.rsplit("/", 1)[-1],
            sha256=document.sha256,
            product_types=tuple(document.product_types),
            sub_types=tuple(document.sub_types),
            publish_date=document.publish_date,
            url=document.url,
            fetched_at=document.fetched_at,
            auto_sourced=True,
        ),
        processed=ProcessedDocument(
            pages=[
                ProcessedPage(page_number=p.page_number, text=p.text, ocr=p.ocr)
                for p in document.pages
            ],
            chunks=[_draft(c) for c in chunks],
        ),
    )


def load_listings(session_factory: sessionmaker[Session]) -> list[StoredListing]:
    """Every stored GeM listing with its page and chunks.

    Args:
        session_factory: Opens the read transaction.

    Returns:
        The listings, oldest first.
    """
    with session_scope(session_factory) as session:
        chunks = ChunkRepository(session)
        return [
            _listing(d, chunks.list_for_document(d.id))
            for d in DocumentRepository(session).list_auto_sourced()
        ]


@dataclass(slots=True)
class WithListings:
    """A `ChunkSource`: registered catalogues first, then GeM listings read by `load`.

    Args:
        catalogues: The registered catalogues.
        load: Reads the stored listings (called by `refresh`).
    """

    catalogues: ChunkSource
    load: Callable[[], list[StoredListing]]
    _listings: list[StoredListing] = field(default_factory=list)

    def refresh(self) -> None:
        """Re-read the stored listings (new ones appear in the next match)."""
        self.catalogues.refresh()
        self._listings = self.load()

    def _of(self, manufacturer: str, category: str) -> list[StoredListing]:
        return [
            item
            for item in self._listings
            if item.document.manufacturer == manufacturer and item.document.category == category
        ]

    def _by_sha(self, sha256: str) -> StoredListing | None:
        return next((i for i in self._listings if i.document.sha256 == sha256), None)

    def manufacturers(self, category: str) -> list[str]:
        """Manufacturers and listing brands with a document in the category, sorted."""
        listed = {
            i.document.manufacturer for i in self._listings if i.document.category == category
        }
        return sorted(set(self.catalogues.manufacturers(category)) | listed)

    def product_types(self) -> dict[str, frozenset[str]]:
        """Declared product types of catalogues and listings, with their categories."""
        found = {t: set(c) for t, c in self.catalogues.product_types().items()}
        for item in self._listings:
            for product_type in item.document.product_types:
                found.setdefault(product_type, set()).add(item.document.category)
        return {t: frozenset(c) for t, c in found.items()}

    def documents(
        self, manufacturer: str, category: str, product_type: str
    ) -> list[CatalogueDocument]:
        """Catalogues, then listings, of the manufacturer that declare the product type."""
        listed = [
            i.document
            for i in self._of(manufacturer, category)
            if product_type in i.document.product_types
        ]
        return [*self.catalogues.documents(manufacturer, category, product_type), *listed]

    def chunks_for_product(
        self, manufacturer: str, category: str, product_type: str, *, use_tags: bool
    ) -> list[CatalogueChunk]:
        """Evidence chunks of catalogues, then of listings, for the product type."""
        out = self.catalogues.chunks_for_product(
            manufacturer, category, product_type, use_tags=use_tags
        )
        for item in self._of(manufacturer, category):
            if product_type in item.document.product_types:
                out.extend(
                    evidence_chunks(
                        item.document, item.processed.chunks, product_type, use_tags=use_tags
                    )
                )
        return out

    def page_text(self, sha256: str, page_number: int) -> str | None:
        """Page text of a listing, else of a registered catalogue."""
        item = self._by_sha(sha256)
        if item is None:
            return self.catalogues.page_text(sha256, page_number)
        return next((p.text for p in item.processed.pages if p.page_number == page_number), None)

    def doc_type(self, sha256: str) -> DocType | None:
        """`gem_listing` for a listing, else the catalogue's type."""
        item = self._by_sha(sha256)
        return item.document.doc_type if item else self.catalogues.doc_type(sha256)

    def page_ocr(self, sha256: str, page_number: int) -> bool:
        """Listings are never OCR'd."""
        return False if self._by_sha(sha256) else self.catalogues.page_ocr(sha256, page_number)

    def warm(self) -> int:
        """Warm the catalogues and read the listings."""
        loaded = self.catalogues.warm()
        self._listings = self.load()
        return loaded + len(self._listings)
