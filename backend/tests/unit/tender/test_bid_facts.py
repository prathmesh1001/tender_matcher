"""BOQ title and buyer GeM categories from the bid; the spare-parts notice (8081591)."""

from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType
from app.tender.bid_facts import bid_facts, category_lines, spare_parts_notice

TITLE_LABELS = ["BOQ Title"]
CATEGORY_LABELS = ["Relevant Categories selected for notification"]
SPARE_WORDS = ["spare", "spares"]


def _row(label: str, value_lines: list[str]) -> ChunkDraft:
    value = " ".join(value_lines)
    return ChunkDraft(
        page_number=7,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=f"{label}: {value}",
        token_count=1,
        meta={"header": ["", ""], "cells": [label, value], "cell_lines": [[label], value_lines]},
    )


def test_bid_facts_read_the_title_and_one_category_per_line() -> None:
    chunks = [
        _row("बीओक्यू शीर्षक /BOQ Title", ["Spares parts for various pump model of", "Kirloskar"]),
        _row(
            "अधिसूचना / Relevant Categories selected for notification",
            ["Stainless Steel Pump Impeller", "Shaft and Shaft Assembly as per Drawing (RCF)"],
        ),
    ]
    facts = bid_facts(chunks, TITLE_LABELS, CATEGORY_LABELS)
    assert facts.boq_title == "Spares parts for various pump model of Kirloskar"
    assert facts.gem_categories == [
        "Stainless Steel Pump Impeller",
        "Shaft and Shaft Assembly as per Drawing (RCF)",
    ]


def test_a_wrapped_category_line_joins_the_one_before() -> None:
    printed = [
        "• Copper Alloy Gate Valve For Waterworks",
        "(V2) Conforming to IS 778",
        "Foot Valve",
    ]
    assert category_lines(printed) == [
        "Copper Alloy Gate Valve For Waterworks (V2) Conforming to IS 778",
        "Foot Valve",
    ]


def test_a_bid_without_the_rows_has_no_facts() -> None:
    facts = bid_facts([_row("Item Category", ["Gate Valve"])], TITLE_LABELS, CATEGORY_LABELS)
    assert (facts.boq_title, facts.gem_categories) == (None, [])


def test_spare_parts_notice_names_a_known_maker() -> None:
    title = "Spares parts for various pump model of Kirloskar brother limited"
    notice = spare_parts_notice(title, SPARE_WORDS, ["KSB", "Kirloskar Brothers", "NA"])
    assert notice is not None
    assert notice.oem == "Kirloskar Brothers"


def test_spare_parts_notice_without_a_known_maker() -> None:
    notice = spare_parts_notice("Spare parts of Acme pumps", SPARE_WORDS, ["KSB"])
    assert notice is not None
    assert notice.oem is None


def test_no_notice_when_the_title_buys_no_spares() -> None:
    assert spare_parts_notice("Different types of Valves", SPARE_WORDS, ["KSB"]) is None
    assert spare_parts_notice(None, SPARE_WORDS, ["KSB"]) is None
