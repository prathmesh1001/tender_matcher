from pathlib import Path

import pymupdf
import pytest

from app.core.config import Settings
from app.ingestion.chunker import (
    ChunkerOptions,
    chunk_document,
    chunk_page,
    estimate_tokens,
    find_model_column,
    is_heading,
    split_long_text,
    table_chunks,
    table_row_text,
)
from app.ingestion.pdf_parser import ParserOptions, parse_pdf
from app.ingestion.table_extractor import extract_page_tables
from app.schemas.chunking import ExtractedTable, TableRow
from app.schemas.enums import ChunkType
from app.schemas.parsing import BBox, ParsedPage, TextBlock

OPTIONS = ChunkerOptions(
    target_tokens=400,
    max_tokens=500,
    overlap_tokens=50,
    heading_max_words=10,
    model_header_keywords=("model", "type", "size"),
)
PARSER = ParserOptions(ocr_min_text_chars=50, ocr_min_image_coverage=0.5, ocr_dpi=300)


def _box(y: float, h: float = 10) -> BBox:
    return BBox(x0=72, y0=y, x1=400, y1=y + h)


def _page(*texts: str, number: int = 1) -> ParsedPage:
    blocks = [TextBlock(text=t, bbox=_box(60 + 30 * i)) for i, t in enumerate(texts)]
    return ParsedPage(
        page_number=number,
        text="\n".join(texts),
        blocks=blocks,
        width=595,
        height=842,
        ocr=False,
    )


# ---------------------------------------------------------------- helpers


def test_estimate_tokens_counts_words_and_punctuation() -> None:
    assert estimate_tokens("Head: 45 m") == 4
    assert estimate_tokens("") == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ETN SERIES PERFORMANCE DATA", True),
        ("3.2 Materials of construction", True),
        ("Shaft Seal", True),
        ("Materials", True),
        ("Casing in grey cast iron, impeller in bronze.", False),  # sentence
        ("Max head: 45 m", False),  # key: value spec line
        ("1450", False),  # no letters
        ("Pump\nModel", False),  # multi-line
        ("This Is A Very Long Title Case Line That Goes On And On", False),  # too long
    ],
)
def test_is_heading(text: str, expected: bool) -> None:
    assert is_heading(text, max_words=10) is expected


def test_find_model_column_matches_whole_words() -> None:
    assert find_model_column(["Flow", "Pump Model", "Head"], ("model",)) == 1
    assert find_model_column(["Flow", "Head"], ("model",)) is None
    assert find_model_column(["Typeface"], ("type",)) is None  # not a whole word


def test_table_row_text_pairs_header_with_cells_and_skips_blanks() -> None:
    text = table_row_text(["Model", "Flow", "Note"], ["ETN 50", "120", ""], "ETN SERIES")
    assert text == "ETN SERIES | Model: ETN 50; Flow: 120"
    assert table_row_text(["A"], ["1"], None) == "A: 1"


# ---------------------------------------------------------------- table chunks


def _table(rows: list[list[str]]) -> ExtractedTable:
    return ExtractedTable(
        page_number=3,
        index=0,
        bbox=_box(100, 100),
        header=["Model", "Flow (m3/h)", "Power (kW)"],
        rows=[TableRow(cells=r, bbox=_box(120 + 20 * i, 20)) for i, r in enumerate(rows)],
    )


def test_each_row_becomes_a_chunk_with_header_model_and_raw_cells() -> None:
    chunks = table_chunks(
        _table([["ETN 50", "120", "7.5"], ["ETN 65", "200", "15"]]), "ETN", OPTIONS
    )
    assert len(chunks) == 2
    first = chunks[0]
    assert first.chunk_type is ChunkType.TABLE_ROW
    assert first.page_number == 3
    assert first.model == "ETN 50"
    assert first.section == "ETN"
    assert first.raw_text == "ETN | Model: ETN 50; Flow (m3/h): 120; Power (kW): 7.5"
    assert first.meta["raw_row"] == "ETN 50 120 7.5"
    assert first.meta["header"] == ["Model", "Flow (m3/h)", "Power (kW)"]
    assert first.meta["row_index"] == 0


def test_row_without_model_column_has_no_model() -> None:
    table = _table([["x", "1", "2"]]).model_copy(update={"header": ["A", "B", "C"]})
    assert table_chunks(table, None, OPTIONS)[0].model is None


def test_blank_model_cell_gives_no_model() -> None:
    assert table_chunks(_table([["", "120", "7.5"]]), None, OPTIONS)[0].model is None


# ---------------------------------------------------------------- splitting


def test_split_long_text_respects_max_and_overlaps() -> None:
    text = " ".join(f"w{i}" for i in range(100))
    pieces = split_long_text(text, max_tokens=30, overlap_tokens=5)
    assert all(estimate_tokens(p) <= 30 for p in pieces)
    assert pieces[0].split()[-5:] == pieces[1].split()[:5]
    assert pieces[-1].split()[-1] == "w99"
    covered = {w for p in pieces for w in p.split()}
    assert covered == set(text.split())


def test_split_short_text_is_unchanged() -> None:
    assert split_long_text("a b c", max_tokens=30, overlap_tokens=5) == ["a b c"]


def test_split_handles_a_single_word_longer_than_max() -> None:
    assert split_long_text("x" * 10, max_tokens=1, overlap_tokens=0) == ["x" * 10]


# ---------------------------------------------------------------- pages


def test_headings_set_section_and_are_not_chunks() -> None:
    result = chunk_page(_page("Materials", "Casing in cast iron."), [], OPTIONS)
    [chunk] = result.chunks
    assert chunk.raw_text == "Casing in cast iron."
    assert chunk.section == "Materials"
    assert result.section == "Materials"


def test_small_blocks_of_one_section_merge_into_one_chunk() -> None:
    result = chunk_page(
        _page("Materials", "Casing in cast iron.", "Impeller in bronze."), [], OPTIONS
    )
    assert [c.raw_text for c in result.chunks] == ["Casing in cast iron.\nImpeller in bronze."]
    assert result.chunks[0].meta["bbox"]["y0"] == 90


def test_new_heading_starts_a_new_chunk_without_overlap() -> None:
    page = _page("Materials", "Casing in cast iron.", "Shaft Seal", "Mechanical seal.")
    chunks = chunk_page(page, [], OPTIONS).chunks
    assert [(c.section, c.raw_text) for c in chunks] == [
        ("Materials", "Casing in cast iron."),
        ("Shaft Seal", "Mechanical seal."),
    ]


def test_chunks_over_target_overlap_within_a_section() -> None:
    options = ChunkerOptions(
        target_tokens=10, max_tokens=20, overlap_tokens=2, heading_max_words=10,
        model_header_keywords=("model",),
    )  # fmt: skip
    page = _page("one two three four five six seven eight nine", "ten eleven twelve thirteen")
    first, second = chunk_page(page, [], options).chunks
    assert first.raw_text == "one two three four five six seven eight nine"
    assert second.raw_text == "eight nine\nten eleven twelve thirteen"


def test_section_carries_over_to_the_next_page() -> None:
    chunks = chunk_document(
        [_page("Materials", "Cast iron."), _page("Bronze impeller.", number=2)], {}, OPTIONS
    )
    assert [(c.page_number, c.section) for c in chunks] == [(1, "Materials"), (2, "Materials")]


def test_positions_restart_per_page() -> None:
    chunks = chunk_document([_page("a text."), _page("b text.", number=2)], {}, OPTIONS)
    assert [(c.page_number, c.position) for c in chunks] == [(1, 0), (2, 0)]


# ---------------------------------------------------------------- end to end on a PDF


def test_catalogue_page_end_to_end(catalogue_pdf: Path) -> None:
    page = parse_pdf(catalogue_pdf, PARSER, engine=None).pages[0]
    with pymupdf.open(catalogue_pdf) as doc:
        tables = extract_page_tables(doc[0])
    chunks = chunk_page(page, tables, OPTIONS).chunks

    assert [c.chunk_type for c in chunks] == [
        ChunkType.TABLE_ROW,
        ChunkType.TABLE_ROW,
        ChunkType.TABLE_ROW,
        ChunkType.PROSE,
        ChunkType.PROSE,
    ]
    assert [c.position for c in chunks] == [0, 1, 2, 3, 4]
    rows = chunks[:3]
    assert all(c.section == "ETN SERIES PERFORMANCE DATA" for c in rows)
    assert [c.model for c in rows] == ["ETN 50", "ETN 50", "ETN 65"]
    assert "Power (kW): 7.5" in rows[1].raw_text
    # Table text is not duplicated as prose.
    assert not any("120" in c.raw_text for c in chunks[3:])
    assert [(c.section, c.raw_text) for c in chunks[3:]] == [
        ("Materials", "Casing in grey cast iron, impeller in bronze."),
        ("Shaft Seal", "Mechanical seal as standard."),
    ]


def test_options_come_from_settings(settings: Settings) -> None:
    custom = settings.model_copy(update={"CHUNK_MODEL_HEADER_KEYWORDS": ["Model", "TYPE"]})
    options = ChunkerOptions.from_settings(custom)
    assert options.model_header_keywords == ("model", "type")
    assert options.target_tokens == settings.CHUNK_TARGET_TOKENS


def test_block_longer_than_max_is_split_into_several_prose_chunks() -> None:
    options = ChunkerOptions(
        target_tokens=10, max_tokens=12, overlap_tokens=2, heading_max_words=10,
        model_header_keywords=("model",),
    )  # fmt: skip
    long_block = " ".join(f"word{i}" for i in range(30))
    chunks = chunk_page(_page(long_block), [], options).chunks
    assert len(chunks) > 1
    assert all(c.token_count <= 12 for c in chunks)
    assert [c.position for c in chunks] == list(range(len(chunks)))
    assert chunks[-1].raw_text.endswith("word29")
