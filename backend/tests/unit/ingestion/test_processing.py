"""Processing stage: file -> pages + chunks with search_text. Synthetic PDFs only."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest

from app.core.exceptions import DocumentParseError
from app.ingestion.ocr import OcrLine
from app.ingestion.processing import (
    ProcessingOptions,
    drop_duplicate_rule_rows,
    is_supported,
    process_file,
    set_product_family,
)
from app.ingestion.product_tagger import ProductMatcher
from app.ontology.loader import load_ontologies
from app.retrieval.text_normalizer import SearchVocabulary
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType
from tests.conftest import make_test_settings
from tests.unit.ingestion.conftest import FakeOcrEngine, write_catalogue_pdf

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"


@pytest.fixture(scope="module")
def options() -> ProcessingOptions:
    settings = make_test_settings()
    ontologies = load_ontologies(ONTOLOGY_DIR)
    vocabulary = SearchVocabulary.from_ontologies(ontologies)
    matcher = ProductMatcher.from_ontologies(ontologies, vocabulary)
    return ProcessingOptions.from_settings(settings, vocabulary, matcher, ocr_engine=None)


def test_pdf_chunks_carry_raw_and_search_text(tmp_path: Path, options: ProcessingOptions) -> None:
    processed = process_file(write_catalogue_pdf(tmp_path / "c.pdf"), options)
    assert [p.page_number for p in processed.pages] == [1]
    rows = [c for c in processed.chunks if c.chunk_type == ChunkType.TABLE_ROW]
    assert rows[0].raw_text.startswith("ETN SERIES PERFORMANCE DATA | Model: ETN 50")
    assert rows[0].search_text.startswith("etn seri perform data model etn 50")
    assert all(c.search_text for c in processed.chunks)


def _ruled_table(page: pymupdf.Page, top: float, rows: list[list[str]]) -> None:
    cols = [72, 272, 472]
    ys = [top + 20 * i for i in range(len(rows) + 1)]
    for y in ys:
        page.draw_line((cols[0], y), (cols[-1], y))
    for x in cols:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            page.insert_text((cols[j] + 4, ys[i] + 14), cell, fontsize=9)


def test_table_continuing_on_next_page_keeps_its_header(
    tmp_path: Path, options: ProcessingOptions
) -> None:
    # C1: page 2 starts with data rows and no header (like a GeM spec table split at p3/p4).
    doc = pymupdf.open()
    first = doc.new_page()
    _ruled_table(first, 640, [["Parameter", "Value"], ["Type", "Monoblock"], ["Stages", "1"]])
    second = doc.new_page()
    _ruled_table(second, 60, [["Head", "14.0 (meter)"], ["Power", "2.0 (horsepower)"]])
    path = tmp_path / "split.pdf"
    doc.save(path)
    doc.close()

    processed = process_file(path, options)
    page2 = [c for c in processed.chunks if c.page_number == 2]
    assert [c.raw_text for c in page2] == [
        "Parameter: Head; Value: 14.0 (meter)",
        "Parameter: Power; Value: 2.0 (horsepower)",
    ]
    assert all(c.meta["continued_from_page"] == 1 for c in page2)


def test_processed_page_keeps_its_ocr_confidence(
    scanned_pdf: Path,
    options: ProcessingOptions,
    fake_ocr: Callable[[list[OcrLine]], FakeOcrEngine],
) -> None:
    line = OcrLine(text="Body: SS316", left=300, top=600, right=900, bottom=660, confidence=0.42)
    processed = process_file(scanned_pdf, replace(options, ocr_engine=fake_ocr([line])))
    assert processed.pages[0].ocr is True
    assert processed.pages[0].ocr_confidence == pytest.approx(0.42)


def test_unsupported_type(tmp_path: Path, options: ProcessingOptions) -> None:
    path = tmp_path / "x.html"
    path.write_text("<html/>", encoding="utf-8")
    assert not is_supported(path)
    with pytest.raises(DocumentParseError):
        process_file(path, options)


def test_every_catalogue_chunk_gets_the_product_family(
    tmp_path: Path, options: ProcessingOptions
) -> None:
    processed = process_file(write_catalogue_pdf(tmp_path / "c.pdf"), options)
    family = set_product_family(processed, "ETN Series")
    assert {c.product_family for c in family.chunks} == {"ETN Series"}
    assert len(family.chunks) == len(processed.chunks)


def test_tender_options_read_pairs_and_never_split_material_versions(
    options: ProcessingOptions,
) -> None:
    tender = options.for_tender()
    assert tender.key_value_tables
    assert tender.chunker.variant_header_keywords == ()
    assert options.chunker.variant_header_keywords  # catalogues keep splitting
    assert tender.table_cleanup.max_label_words == 0  # no caption headers in tenders
    assert options.table_cleanup.max_label_words > 0
    assert tender.table_cleanup.units == options.table_cleanup.units  # split values still join
    assert tender.rule_grid_tables  # tables drawn with thin rectangles (T1)
    assert not options.rule_grid_tables  # catalogue drawings stay as they are


def _draft(page: int, position: int, text: str, *, rule_grid: bool) -> ChunkDraft:
    return ChunkDraft(
        page_number=page,
        position=position,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=text,
        search_text=text.lower(),
        token_count=1,
        meta={"rule_grid": True} if rule_grid else {},
    )


def test_duplicate_rule_grid_rows_are_dropped() -> None:
    # Synthetic: two overlapping grids on page 1 read the same row twice.
    chunks = [
        _draft(1, 0, "Duration: 17", rule_grid=True),
        _draft(1, 1, "duration: 17", rule_grid=True),  # same normalized text
        _draft(1, 2, "EMD: 5000", rule_grid=True),
        _draft(2, 0, "Duration: 17", rule_grid=True),  # another page: kept
    ]
    kept = drop_duplicate_rule_rows(chunks)
    assert [(c.page_number, c.position, c.raw_text) for c in kept] == [
        (1, 0, "Duration: 17"),
        (1, 1, "EMD: 5000"),
        (2, 0, "Duration: 17"),
    ]


def test_duplicates_found_the_usual_way_are_kept() -> None:
    # Catalogue rows repeated on one page (two sizes with equal values) are not touched.
    chunks = [_draft(1, 0, "Size: 50", rule_grid=False), _draft(1, 1, "Size: 50", rule_grid=False)]
    assert len(drop_duplicate_rule_rows(chunks)) == 2
