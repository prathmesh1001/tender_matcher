"""File -> pages + chunks: the pure part of ingestion, shared by catalogues and tender files.

PDF: parse pages (OCR when scanned) -> drop repeated headers/footers -> find tables -> link
tables across page breaks -> section-aware chunks. DOCX: one virtual page, same chunker.
CSV/XLSX: one page per sheet, one chunk per row. Every chunk then gets
`search_text = normalize_for_search(raw_text)`.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from app.core.config import Settings
from app.core.exceptions import DocumentParseError
from app.ingestion.boilerplate import BoilerplateOptions, remove_boilerplate
from app.ingestion.chunker import ChunkerOptions, chunk_document
from app.ingestion.docx_reader import docx_page, read_docx_items
from app.ingestion.glyph_repair import RepairOptions
from app.ingestion.layout import LayoutOptions
from app.ingestion.ocr import OcrEngine
from app.ingestion.ocr_table import OcrTableOptions, ocr_tables
from app.ingestion.pdf_parser import ParserOptions, open_pdf, parse_pdf
from app.ingestion.product_tagger import (
    Declared,
    ProductMatcher,
    TagInput,
    tag_chunks,
)
from app.ingestion.section_classifier import SectionClassifier, load_section_rules
from app.ingestion.table_cleanup import TableCleanupOptions
from app.ingestion.table_extractor import extract_page_tables, merge_continued_tables
from app.ingestion.tabular import Sheet, read_csv, read_xlsx, sheet_chunks
from app.retrieval.text_normalizer import SearchVocabulary, normalize_for_search
from app.schemas.chunking import ChunkDraft, ExtractedTable
from app.schemas.enums import ChunkType
from app.schemas.parsing import ParsedPage, count_text_chars

logger = logging.getLogger(__name__)

PDF_SUFFIX: Final = ".pdf"
DOCX_SUFFIX: Final = ".docx"
SHEET_READERS: Final = {".csv": read_csv, ".xlsx": read_xlsx}
SUPPORTED_SUFFIXES: Final = frozenset({PDF_SUFFIX, DOCX_SUFFIX, *SHEET_READERS})


@dataclass(frozen=True, slots=True)
class ProcessingOptions:
    """Everything the processing stage needs (built once from settings)."""

    parser: ParserOptions
    chunker: ChunkerOptions
    boilerplate: BoilerplateOptions
    table_edge_fraction: float
    table_cleanup: TableCleanupOptions
    layout: LayoutOptions
    vocabulary: SearchVocabulary
    product_matcher: ProductMatcher
    product_page_min_tagged: int
    section_classifier: SectionClassifier
    ocr_engine: OcrEngine | None
    key_value_tables: bool = False
    # Rebuild tables from drawn rules where PyMuPDF finds none (tender files only, see for_tender).
    rule_grid_tables: bool = False
    ocr_table: OcrTableOptions | None = None
    # Rebuild tables on scanned pages from OCR word boxes (tender files only, see for_tender).
    ocr_tables: bool = False
    # Tesseract --psm for scanned tender pages (row by row reading keeps table cells).
    tender_page_segmentation: int | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        vocabulary: SearchVocabulary,
        product_matcher: ProductMatcher,
        ocr_engine: OcrEngine | None,
    ) -> ProcessingOptions:
        """Build options from settings plus the vocabulary, product matcher and OCR.

        Raises:
            ConfigFileError: If the section categories file is invalid.
        """
        return cls(
            parser=ParserOptions.from_settings(settings),
            chunker=ChunkerOptions.from_settings(settings),
            boilerplate=BoilerplateOptions.from_settings(settings),
            table_edge_fraction=settings.TABLE_CONTINUATION_EDGE_FRACTION,
            table_cleanup=TableCleanupOptions(
                units=vocabulary.unit_spellings,
                max_label_words=settings.TABLE_HEADER_MAX_WORDS,
                paragraph_gap=settings.TABLE_PARAGRAPH_GAP_FACTOR,
                glyphs=RepairOptions.from_settings(settings),
            ),
            layout=LayoutOptions.from_settings(settings),
            vocabulary=vocabulary,
            product_matcher=product_matcher,
            product_page_min_tagged=settings.PRODUCT_PAGE_MIN_TAGGED,
            section_classifier=SectionClassifier.from_rules(
                load_section_rules(settings.SECTION_CATEGORIES_FILE), vocabulary
            ),
            ocr_engine=ocr_engine,
            ocr_table=OcrTableOptions.from_settings(settings, vocabulary.code_rules),
            tender_page_segmentation=settings.OCR_TENDER_PAGE_SEGMENTATION,
        )

    def for_tender(self) -> ProcessingOptions:
        """Options for tender files: label/value tables, rule grids, no versions or captions.

        Version columns are catalogue evidence (CLAUDE.md B10). Tender tables are line items,
        where "Material Code" and "Material Description" are different fields, not versions.
        Caption headers are a catalogue layout; in tenders a long header cell with commas is
        usually a clause or an item description, not a title. Rule-grid tables are a tender
        layout (8017367 `boq_spec.pdf`); in catalogues the same rules outline dimension
        drawings, which must stay as they are.
        """
        return replace(
            self,
            key_value_tables=True,
            rule_grid_tables=True,
            ocr_tables=self.ocr_table is not None,
            parser=replace(self.parser, ocr_page_segmentation=self.tender_page_segmentation),
            chunker=replace(self.chunker, variant_header_keywords=()),
            table_cleanup=replace(self.table_cleanup, max_label_words=0),
        )


@dataclass(frozen=True, slots=True)
class ProcessedPage:
    """Text of one page (or sheet) as stored in the `pages` table."""

    page_number: int
    text: str
    ocr: bool
    ocr_confidence: float | None = None

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


def drop_duplicate_rule_rows(chunks: Sequence[ChunkDraft]) -> list[ChunkDraft]:
    """Drop rule-grid row chunks whose page and `search_text` repeat an earlier chunk.

    Overlapping boxes drawn on GeM bid pages give two grids that read the same cells, so the
    same row came out twice. Only rule-grid rows are dropped: chunks found the usual way are
    kept as they are. Positions are renumbered per page.

    Args:
        chunks: Chunks in page and position order, with `search_text` filled.

    Returns:
        The chunks without those duplicates.
    """
    seen: set[tuple[int, str]] = set()
    kept: list[ChunkDraft] = []
    for chunk in chunks:
        key = (chunk.page_number, chunk.search_text)
        if chunk.meta.get("rule_grid") and key in seen:
            continue
        seen.add(key)
        kept.append(chunk)
    positions: dict[int, int] = {}
    renumbered: list[ChunkDraft] = []
    for chunk in kept:
        position = positions.get(chunk.page_number, 0)
        positions[chunk.page_number] = position + 1
        renumbered.append(chunk.model_copy(update={"position": position}))
    return renumbered


def extract_tables(
    path: Path,
    pages: Sequence[ParsedPage],
    edge: float,
    key_value: bool = False,
    cleanup: TableCleanupOptions | None = None,
    layout: LayoutOptions | None = None,
) -> dict[int, list[ExtractedTable]]:
    """Find vector tables on every non-OCR page and link those that continue across pages.

    Args:
        path: The PDF.
        pages: Its parsed pages (OCR pages are skipped: they have no vector tables).
        edge: Page-edge fraction for continuation detection.
        key_value: Tender file: read two-column tables as label/value pairs.
        cleanup: Row and header repairs (unit spellings, caption detection).
        layout: Rule-grid thresholds, for tables drawn without stroked lines.

    Rows holding several stacked items (no dividing line) are split in every file: in
    catalogues they would otherwise mix two parts' materials ("01 02 | Body Body Seat Ring").

    Returns:
        Tables keyed by 1-based page number.
    """
    text_pages = {p.page_number for p in pages if not p.ocr}
    with open_pdf(path) as doc:
        found = {
            page.number + 1: extract_page_tables(page, key_value, True, cleanup, layout)
            for page in doc
            if page.number + 1 in text_pages
        }
    heights = {p.page_number: p.height for p in pages}
    return merge_continued_tables(found, heights, edge)


def scanned_tables(
    pages: Sequence[ParsedPage], options: OcrTableOptions
) -> dict[int, list[ExtractedTable]]:
    """Tables rebuilt from OCR words on every scanned page (CLAUDE.md C5).

    Args:
        pages: Parsed pages; only OCR pages with words are read.
        options: Thresholds and code rules.

    Returns:
        Tables keyed by 1-based page number, only for pages that have some.
    """
    found = {p.page_number: ocr_tables(p.ocr_words, p.page_number, options) for p in pages if p.ocr}
    return {page: tables for page, tables in found.items() if tables}


def process_pdf(path: Path, options: ProcessingOptions) -> ProcessedDocument:
    """Parse, extract tables, and chunk a PDF.

    Raises:
        DocumentParseError: If the PDF cannot be opened.
        ExternalServiceError: If OCR is needed and fails.
    """
    parsed = parse_pdf(path, options.parser, options.ocr_engine)
    cleaned, boilerplate = remove_boilerplate(parsed.pages, options.boilerplate)
    if boilerplate:
        logger.info(
            "Dropped boilerplate lines",
            extra={"path": str(path), "lines": sorted(boilerplate)},
        )
    tables = extract_tables(
        path,
        parsed.pages,
        options.table_edge_fraction,
        options.key_value_tables,
        options.table_cleanup,
        options.layout if options.rule_grid_tables else None,
    )
    if options.ocr_tables and options.ocr_table is not None:
        tables = {**tables, **scanned_tables(parsed.pages, options.ocr_table)}
    chunks = chunk_document(cleaned, tables, options.chunker)
    pages = [
        ProcessedPage(
            page_number=p.page_number, text=p.text, ocr=p.ocr, ocr_confidence=p.ocr_confidence
        )
        for p in parsed.pages
    ]
    searchable = add_search_text(chunks, options.vocabulary)
    return ProcessedDocument(pages=pages, chunks=drop_duplicate_rule_rows(searchable))


def process_docx(path: Path, options: ProcessingOptions) -> ProcessedDocument:
    """Chunk a Word document as one virtual page (paragraphs, headings, table rows).

    Raises:
        DocumentParseError: If the file is not a readable .docx.
    """
    page, tables = docx_page(read_docx_items(path))
    # One tall virtual page has no footer band: its last lines can be real headings.
    chunker = replace(options.chunker, footer_band=0.0)
    chunks = chunk_document([page], {page.page_number: tables}, chunker)
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


def tag_inputs(
    sections: Sequence[str | None],
    search_texts: Sequence[str],
    pages: Sequence[int],
    section_pages: Sequence[int | None],
    vocabulary: SearchVocabulary,
    chunk_types: Sequence[ChunkType] | None = None,
) -> list[TagInput]:
    """Tagger input for chunks: normalized section heading, stored search text, page, kind.

    `heading_on_page` says whether the section heading describes the chunk's page (see
    `headings_on_page`).
    """
    on_page = headings_on_page(sections, pages, section_pages)
    kinds = chunk_types or [ChunkType.PROSE] * len(sections)
    return [
        TagInput(
            section=normalize_for_search(sec or "", vocabulary),
            search_text=text,
            page=page,
            heading_on_page=local,
            is_table=kind == ChunkType.TABLE_ROW,
        )
        for sec, text, page, local, kind in zip(
            sections, search_texts, pages, on_page, kinds, strict=True
        )
    ]


def headings_on_page(
    sections: Sequence[str | None], pages: Sequence[int], section_pages: Sequence[int | None]
) -> list[bool]:
    """For chunks in reading order: does the chunk's section heading describe its page?

    A heading counts on the page where it was found, and on later pages only if it had content
    under it on its own page (a section that really continues, e.g. a long table). A heading
    with nothing under it on its page (the last line of a page, often a list item) does not
    carry onto the next page. Chunks stored before heading pages were recorded count as local.

    Args:
        sections: Section of each chunk.
        pages: Page of each chunk.
        section_pages: Page each chunk's heading was found on (None if unknown).

    Returns:
        One flag per chunk.
    """
    with_content = {
        (sec, sp)
        for sec, page, sp in zip(sections, pages, section_pages, strict=True)
        if sp == page
    }
    return [
        sp is None or sp == page or (sec, sp) in with_content
        for sec, page, sp in zip(sections, pages, section_pages, strict=True)
    ]


def tag_products(
    processed: ProcessedDocument, options: ProcessingOptions, declared: Declared
) -> ProcessedDocument:
    """Set `product_type` / `product_sub_type` on every chunk (see `product_tagger`).

    Args:
        processed: Pages and chunks of one document, chunks in reading order.
        options: Processing options (vocabulary and product matcher).
        declared: Product types the document declares (empty for tender files).

    Returns:
        The same document with tagged chunks.
    """
    chunks = processed.chunks
    items = tag_inputs(
        [c.section for c in chunks],
        [c.search_text for c in chunks],
        [c.page_number for c in chunks],
        [c.meta.get("section_page") for c in chunks],
        options.vocabulary,
        [c.chunk_type for c in chunks],
    )
    page_texts = {
        p.page_number: normalize_for_search(p.text, options.vocabulary) for p in processed.pages
    }
    tags = tag_chunks(
        items, options.product_matcher, declared, options.product_page_min_tagged, page_texts
    )
    tagged = [
        c.model_copy(update={"product_type": t.product_type, "product_sub_type": t.sub_type})
        for c, t in zip(chunks, tags, strict=True)
    ]
    return ProcessedDocument(pages=processed.pages, chunks=tagged)


def set_product_family(processed: ProcessedDocument, family: str) -> ProcessedDocument:
    """Put every chunk of a catalogue into its product family.

    One catalogue is one product family (its title in sources.yaml). Matching takes all
    evidence for a manufacturer from one family, so specs of different catalogues are never
    combined (CLAUDE.md section 7).

    Args:
        processed: Pages and chunks of one catalogue.
        family: The family name.

    Returns:
        The same document with `product_family` set on every chunk.
    """
    chunks = [c.model_copy(update={"product_family": family}) for c in processed.chunks]
    return ProcessedDocument(pages=processed.pages, chunks=chunks)
