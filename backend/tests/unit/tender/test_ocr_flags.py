"""Requirements read from low-confidence OCR are flagged for review. Rows are synthetic."""

from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType
from app.schemas.requirements import LineItem, Requirement
from app.tender.items import ItemSource, TenderRequirements
from app.tender.ocr_flags import LOW_OCR_FLAG, flag_low_ocr, is_low_ocr, low_confidence_rows
from app.tender.pipeline import ItemRequirements
from app.tender.splitter import SplitOptions, item_requirements, leaves

SPLIT = SplitOptions(label_max_words=6, enum_max_words=2)
ITEM = LineItem(item_no="1", title="Pump", description_lines=[], document="spec.pdf", page_number=1)


def _row(cells: list[str], confidence: float | None, page: int = 1) -> ChunkDraft:
    meta: dict[str, object] = {"header": ["", ""], "cells": cells}
    if confidence is not None:
        meta["ocr_confidence"] = confidence
    return ChunkDraft(
        page_number=page,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=" | ".join(cells),
        token_count=1,
        meta=meta,
    )


def _requirements(*lines: str, page: int = 1) -> list[Requirement]:
    item = ITEM.model_copy(update={"description_lines": list(lines), "page_number": page})
    return leaves(item_requirements(item, None, SPLIT))


def test_only_rows_read_below_the_confidence_are_low() -> None:
    chunks = [
        _row(["Motor Rated Capacity", "35HP/26.1K W"], 0.4),
        _row(["RPM", "2900 RPM"], 0.95),
        _row(["Body", "Cast iron"], None),  # a vector table row: no OCR confidence
    ]
    assert low_confidence_rows("spec.pdf", chunks, 0.6) == {
        ("spec.pdf", 1, "motor rated capacity : 35hp/26.1k w")
    }


def test_requirement_from_a_low_row_or_low_page_is_low() -> None:
    rows = {("spec.pdf", 1, "motor rated capacity : 35hp/26.1k w")}
    [low] = _requirements("Motor Rated Capacity : 35HP/26.1K W")
    [fine] = _requirements("RPM : 2900 RPM")
    [other_page] = _requirements("Motor Rated Capacity : 35HP/26.1K W", page=2)
    assert is_low_ocr(low, set(), rows)
    assert not is_low_ocr(fine, set(), rows)
    assert not is_low_ocr(other_page, set(), rows)
    assert is_low_ocr(other_page, {("spec.pdf", 2)}, set())


def test_flag_low_ocr_flags_items_and_general_lines_once() -> None:
    requirements = _requirements("Motor Rated Capacity : 35HP/26.1K W", "RPM : 2900 RPM")
    found = TenderRequirements(
        [ItemRequirements(ITEM, None, requirements)],
        _requirements("Motor Rated Capacity : 35HP/26.1K W"),
        ItemSource.SPEC_SCHEDULE,
    )
    rows = {("spec.pdf", 1, "motor rated capacity : 35hp/26.1k w")}
    flagged = flag_low_ocr(flag_low_ocr(found, set(), rows), set(), rows)
    assert [r.flags for r in flagged.items[0].requirements] == [[LOW_OCR_FLAG], []]
    assert flagged.general[0].flags == [LOW_OCR_FLAG]


def test_nothing_low_returns_the_same_requirements() -> None:
    found = TenderRequirements([], _requirements("RPM : 2900 RPM"), ItemSource.SPEC_SCHEDULE)
    assert flag_low_ocr(found, set(), set()) is found
