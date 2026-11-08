"""Cells whose lines the page prints interleaved with other columns. Pages are synthetic."""

from app.ingestion.chunker import interleaved_cells
from app.ingestion.table_extractor import positioned_lines, with_paragraph_breaks
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType


def _chunk(lines: list[list[str]]) -> ChunkDraft:
    return ChunkDraft(
        page_number=1,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text="row",
        token_count=1,
        meta={"cell_lines": lines},
    )


def test_a_cell_printed_between_other_columns_is_interleaved() -> None:
    page = "Valve Type\n3 Pressure Seal\nU Hard-faced\nButt-weld\nBonnet / Cap\nFlanged RF"
    chunk = _chunk([["3 Pressure Seal", "Bonnet / Cap"], ["U Hard-faced", "Butt-weld"]])
    assert interleaved_cells(chunk, page, 0.9) == [0]


def test_a_wrapped_cell_printed_in_order_is_not_interleaved() -> None:
    page = "Body\nASTM A216 Gr.\nWCB\nDisc\nASTM A351\nGr. CF8M"
    chunk = _chunk([["Body"], ["ASTM A216 Gr.", "WCB"]])
    assert interleaved_cells(chunk, page, 0.9) == []


def test_single_line_cells_are_never_checked() -> None:
    assert interleaved_cells(_chunk([["Flanged RF"], [""]]), "nothing here", 0.9) == []


def test_line_heights_follow_the_paragraph_breaks() -> None:
    lines = [(10.0, "a"), (18.0, "b"), (26.0, "c"), (70.0, "d")]
    assert positioned_lines(lines, 2.0) == [
        (10.0, "a"),
        (18.0, "b"),
        (26.0, "c"),
        (None, ""),
        (70.0, "d"),
    ]
    assert with_paragraph_breaks(lines, 2.0) == ["a", "b", "c", "", "d"]
