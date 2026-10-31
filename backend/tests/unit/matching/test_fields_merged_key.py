"""A table key cell that swallowed its row's first values. Rows are synthetic."""

from app.matching.fields import FieldOptions, merged_key, table_fields
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType

OPTIONS = FieldOptions(label_max_words=6, dimension_words=(), dimension_min_letter_columns=3)


def _row(header: list[str], cells: list[str], lines: list[list[str]]) -> ChunkDraft:
    shown = "; ".join(f"{h}: {c}" for h, c in zip(header, cells, strict=True))
    return ChunkDraft(
        page_number=1,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=f"MATERIAL OF CONSTRUCTION | {shown}",
        token_count=10,
        meta={"header": header, "cells": cells, "cell_lines": lines},
    )


def test_value_starts_at_the_line_a_sibling_cell_repeats() -> None:
    lines = ["Pump Shaft", "Carbon Steel", "Carbon Steel", "/Stainless Steel"]
    assert merged_key(lines, ["Stainless Steel", "Carbon Steel /Stainless Steel"]) == (
        "Pump Shaft",
        "Carbon Steel Carbon Steel /Stainless Steel",
    )


def test_a_wrapped_key_is_kept_whole() -> None:
    assert merged_key(["Wear", "Ring"], ["Bronze", "Cast Iron"]) is None


def test_one_line_is_never_split() -> None:
    assert merged_key(["Gate Valve"], ["Valve"]) is None


def test_a_cell_spread_over_merged_columns_proves_nothing() -> None:
    assert merged_key(["Flow", "Control Lever"], ["Flow Control Lever", "Gear Unit"]) is None


def test_bare_numbers_are_no_evidence() -> None:
    assert merged_key(["Model A", "16"], ["16", "25"]) is None
    assert merged_key(["6", "F05"], ["F05"]) is None


def test_row_gives_a_field_for_the_key_cells_value_and_clean_keys() -> None:
    chunk = _row(
        ["KDS/GMC", "KDI"],
        ["Pump Shaft Carbon Steel", "Carbon Steel"],
        [["Pump Shaft", "Carbon Steel"], ["Carbon Steel"]],
    )
    first, second = table_fields(chunk, OPTIONS)
    assert (first.label, first.key, first.value) == (
        "Pump Shaft KDS/GMC",
        "Pump Shaft",
        "Carbon Steel",
    )
    assert (second.label, second.key, second.value) == (
        "Pump Shaft KDI",
        "Pump Shaft",
        "Carbon Steel",
    )
    assert first.quote in chunk.raw_text
    assert second.quote in chunk.raw_text


def test_a_split_row_cites_each_printed_line() -> None:
    chunk = _row(
        ["KDS/GMC", "KDI"],
        ["Impeller Bronze /Noryl", "Bronze /Steel"],
        [["Impeller", "Bronze", "/Noryl"], ["Bronze", "/Steel"]],
    )
    first, second = table_fields(chunk, OPTIONS)
    assert first.parts == ("Impeller", "Bronze", "/Noryl")
    assert second.parts == ("Impeller", "Bronze", "/Steel")


def test_only_the_first_column_can_hold_a_merged_key() -> None:
    chunk = _row(
        ["No.", "Part", "KDI"],
        ["", "Shaft Steel", "Steel"],
        [[], ["Shaft", "Steel"], ["Steel"]],
    )
    assert table_fields(chunk, OPTIONS)[0].value == "Shaft Steel"


def test_a_row_without_printed_lines_stays_as_it_was() -> None:
    chunk = _row(["Part", "Material"], ["Body", "Cast Iron"], [])
    fields = table_fields(chunk, OPTIONS)
    assert [(f.label, f.value) for f in fields] == [
        ("Part", "Body"),
        ("Body Material", "Cast Iron"),
    ]
