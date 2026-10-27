"""File -> pages + chunks: the pure part of ingestion, shared by catalogues and tender files.

PDF: parse pages (OCR when scanned) -> find tables -> link tables across page breaks ->
section-aware chunks. DOCX: one virtual page, same chunker. CSV/XLSX: one page per sheet, one
chunk per row. Every chunk then gets
`search_text = normalize_for_search(raw_text)`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.core.config import Settings
from app.core.exceptions import DocumentParseError
from app.ingestion.chunker import ChunkerOptions, chunk_document
from app.ingestion.docx_reader import docx_page, read_docx_items
from app.ingestion.ocr import OcrEngine
from app.ingestion.pdf_parser import ParserOptions, open_pdf, parse_pdf
from app.ingestion.table_extractor import extract_page_tables, merge_continued_tables
from app.ingestion.tabular import Sheet, read_csv, read_xlsx, sheet_chunks
from app.retrieval.text_normalizer import SearchVocabulary, normalize_for_search
from app.schemas.chunking import ChunkDraft, ExtractedTable
from app.schemas.parsing import ParsedPage, count_text_chars

PDF_SUFFIX: Final = ".pdf"
DOCX_SUFFIX: Final = ".docx"
SHEET_READERS: Final = {".csv": read_csv, ".xlsx": read_xlsx}
SUPPORTED_SUFFIXES: Final = frozenset({PDF_SUFFIX, DOCX_SUFFIX, *SHEET_READERS})


@dataclass(frozen=True, slots=True)
class ProcessingOptions:
    """Everything the processing stage needs (built once from settings)."""

    parser: ParserOptions
    chunker: ChunkerOptions
    table_edge_fraction: float
    vocabulary: SearchVocabulary
    ocr_engine: OcrEngine | None

    @classmethod
    def from_settings(
        cls, settings: Settings, vocabulary: SearchVocabulary, ocr_engine: OcrEngine | None
    ) -> ProcessingOptions:
        """Build options from settings plus the loaded vocabulary and OCR engine."""
        return cls(
            parser=ParserOptions.from_settings(settings),
            chunker=ChunkerOptions.from_settings(settings),
            table_edge_fraction=settings.TABLE_CONTINUATION_EDGE_FRACTION,
            vocabulary=vocabulary,
            ocr_engine=ocr_engine,
        )


@dataclass(frozen=True, slots=True)
class ProcessedPage:
    """Text of one page (or sheet) as stored in the `pages` table."""

    page_number: int
    text: str
    ocr: bool

    @property
    def char_count(self) -> int:
        """Non-whitespace characters in `text`."""
        return count_text_chars(self.text)


@dataclass(frozen=True, slots=True)
class ProcessedDocument:
    """All pages and chunks of one file."""

    pages: list[ProcessedPage]
    chunks: list[ChunkDraft]


def is_supported(path: Path) -> bool:
    """True if the file type can be parsed into pages and chunks."""
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def add_search_text(chunks: Sequence[ChunkDraft], vocabulary: SearchVocabulary) -> list[ChunkDraft]:
    """Fill `search_text` of every chunk from its verbatim `raw_text`."""
    return [
        c.model_copy(update={"search_text": normalize_for_search(c.raw_text, vocabulary)})
        for c in chunks
    ]


def extract_tables(
    path: Path, pages: Sequence[ParsedPage], edge: float
) -> dict[int, list[ExtractedTable]]:
    """Find vector tables on every non-OCR page and link those that continue across pages.

    Args:
        path: The PDF.
        pages: Its parsed pages (OCR pages are skipped: they have no vector tables).
        edge: Page-edge fraction for continuation detection.

    Returns:
        Tables keyed by 1-based page number.
    """
    text_pages = {p.page_number for p in pages if not p.ocr}
    with open_pdf(path) as doc:
        found = {
            page.number + 1: extract_page_tables(page)
            for page in doc
            if page.number + 1 in text_pages
        }
    heights = {p.page_number: p.height for p in pages}
    return merge_continued_tables(found, heights, edge)


def process_pdf(path: Path, options: ProcessingOptions) -> ProcessedDocument:
    """Parse, extract tables, and chunk a PDF.

    Raises:
        DocumentParseError: If the PDF cannot be opened.
        ExternalServiceError: If OCR is needed and fails.
    """
    parsed = parse_pdf(path, options.parser, options.ocr_engine)
    tables = extract_tables(path, parsed.pages, options.table_edge_fraction)
    chunks = chunk_document(parsed.pages, tables, options.chunker)
    pages = [ProcessedPage(page_number=p.page_number, text=p.text, ocr=p.ocr) for p in parsed.pages]
    return ProcessedDocument(pages=pages, chunks=add_search_text(chunks, options.vocabulary))


def process_docx(path: Path, options: ProcessingOptions) -> ProcessedDocument:
    """Chunk a Word document as one virtual page (paragraphs, headings, table rows).

    Raises:
        DocumentParseError: If the file is not a readable .docx.
    """
    page, tables = docx_page(read_docx_items(path))
    chunks = chunk_document([page], {page.page_number: tables}, options.chunker)
    pages = [ProcessedPage(page_number=page.page_number, text=page.text, ocr=False)]
    return ProcessedDocument(pages=pages, chunks=add_search_text(chunks, options.vocabulary))


def process_sheets(sheets: Sequence[Sheet], options: ProcessingOptions) -> ProcessedDocument:
    """Turn sheets of a CSV/XLSX file into pages and row chunks."""
    pages = [ProcessedPage(page_number=s.page_number, text=s.text, ocr=False) for s in sheets]
    chunks = [c for s in sheets for c in sheet_chunks(s, options.chunker)]
    return ProcessedDocument(pages=pages, chunks=add_search_text(chunks, options.vocabulary))


def process_file(path: Path, options: ProcessingOptions) -> ProcessedDocument:
    """Process any supported file.

    Args:
        path: Local file path; check `is_supported` first.
        options: Processing options.

    Returns:
        Pages and chunks.

    Raises:
        DocumentParseError: If the file cannot be parsed or its type is unsupported.
    """
    suffix = path.suffix.lower()
    if suffix == PDF_SUFFIX:
        return process_pdf(path, options)
    if suffix == DOCX_SUFFIX:
        return process_docx(path, options)
    if suffix in SHEET_READERS:
        return process_sheets(SHEET_READERS[suffix](path), options)
    raise DocumentParseError(f"Unsupported file type: {path.name}", details={"path": str(path)})
