"""A row whose cells the page prints interleaved with other columns. Rows are synthetic."""

from app.matching.fields import FieldOptions, table_fields
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType

OPTIONS = FieldOptions(
    label_max_words=6, dimension_words=(), dimension_min_letter_columns=3, line_y_tolerance=2.0
)
HEADER = ["Valve Type", "End Connection", "Disc"]
KEY_LINES = ["3 Pressure Seal", "Bonnet / Cap"]
END_LINES = ["U Hard-faced", "Butt-weld", "Flanged RF"]


def _row(
    lines: list[list[str]],
    ys: list[list[float | None]],
    interleaved: list[int],
    cells: list[str] | None = None,
) -> ChunkDraft:
    cells = cells or [" ".join(t for t in c if t) for c in lines]
    shown = "; ".join(f"{h}: {c}" for h, c in zip(HEADER, cells, strict=True) if c)
    return ChunkDraft(
        page_number=1,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=f"Ordering Information | {shown}",
        token_count=10,
        meta={
            "header": HEADER,
            "cells": cells,
            "cell_lines": lines,
            "cell_line_ys": ys,
            "interleaved_cells": interleaved,
        },
    )


def _legend() -> ChunkDraft:
    return _row(
        [KEY_LINES, END_LINES, []],
        [[100.0, 130.0], [100.5, 110.0, 120.0], []],
        [0, 1],
    )


def test_each_line_of_an_interleaved_cell_is_its_own_field() -> None:
    fields = [f for f in table_fields(_legend(), OPTIONS) if f.label.endswith("End Connection")]
    assert [f.value for f in fields] == END_LINES
    assert all(f.quote == f.value for f in fields)


def test_a_line_keeps_the_key_only_beside_a_key_line() -> None:
    fields = {f.value: f for f in table_fields(_legend(), OPTIONS)}
    beside = fields["U Hard-faced"]
    assert (beside.label, beside.key, beside.parts) == (
        "3 Pressure Seal End Connection",
        "3 Pressure Seal",
        ("3 Pressure Seal", "U Hard-faced"),
    )
    alone = fields["Flanged RF"]
    assert (alone.label, alone.key, alone.parts) == ("End Connection", "", ("Flanged RF",))


def test_the_interleaved_key_cell_gives_its_own_lines() -> None:
    fields = [f for f in table_fields(_legend(), OPTIONS) if f.label == "Valve Type"]
    assert [f.value for f in fields] == KEY_LINES


def test_a_cell_filled_across_merged_columns_is_skipped() -> None:
    end = " ".join(END_LINES)
    chunk = _row(
        [KEY_LINES, END_LINES, []],
        [[100.0, 130.0], [100.5, 110.0, 120.0], []],
        [1],
        cells=[" ".join(KEY_LINES), end, end],
    )
    assert all(f.value != end for f in table_fields(chunk, OPTIONS))


def test_a_plain_cell_in_an_interleaved_row_cites_the_key_lines() -> None:
    chunk = _row(
        [KEY_LINES, END_LINES, []],
        [[100.0, 130.0], [100.5, 110.0, 120.0], []],
        [0, 1],
        cells=[" ".join(KEY_LINES), " ".join(END_LINES), "Flexible Wedge"],
    )
    disc = next(f for f in table_fields(chunk, OPTIONS) if f.value == "Flexible Wedge")
    assert disc.parts == (*KEY_LINES, "Flexible Wedge")


def test_without_line_heights_the_row_is_read_as_before() -> None:
    chunk = _row([KEY_LINES, END_LINES, []], [], [1])
    values = [f.value for f in table_fields(chunk, OPTIONS)]
    assert " ".join(END_LINES) in values


def test_a_stacked_key_pairs_each_value_line_with_its_own_part() -> None:
    chunk = _row(
        [["Disc", "Stem"], ["CA-15 overlay", "A182 F6a"], []],
        [[297.5, 308.4], [297.5, 308.4], []],
        [0],
    )
    pairs = {(f.key, f.value) for f in table_fields(chunk, OPTIONS) if f.key}
    assert pairs == {("Disc", "CA-15 overlay"), ("Stem", "A182 F6a")}
