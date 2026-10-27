"""Processing stage: file -> pages + chunks with search_text. Synthetic PDFs only."""

from pathlib import Path

import pymupdf
import pytest

from app.core.exceptions import DocumentParseError
from app.ingestion.processing import ProcessingOptions, is_supported, process_file
from app.ontology.loader import load_ontologies
from app.retrieval.text_normalizer import SearchVocabulary
from app.schemas.enums import ChunkType
from tests.conftest import make_test_settings
from tests.unit.ingestion.conftest import write_catalogue_pdf

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"


@pytest.fixture(scope="module")
def options() -> ProcessingOptions:
    settings = make_test_settings()
    vocabulary = SearchVocabulary.from_ontologies(load_ontologies(ONTOLOGY_DIR))
    return ProcessingOptions.from_settings(settings, vocabulary, ocr_engine=None)


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


def test_unsupported_type(tmp_path: Path, options: ProcessingOptions) -> None:
    path = tmp_path / "x.html"
    path.write_text("<html/>", encoding="utf-8")
    assert not is_supported(path)
    with pytest.raises(DocumentParseError):
        process_file(path, options)
