"""Per item x document evaluation and choice. Documents and rows are synthetic."""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.ingestion.chunker import table_row_text
from app.matching.chunk_source import CatalogueChunk, CatalogueDocument
from app.matching.comparator import MatchContext
from app.matching.documents import (
    ItemQuery,
    SelectionOptions,
    _capped,
    _from_another_product,
    evaluate_item,
    item_sub_type,
    sub_type_fit,
)
from app.matching.engine import manufacturer_report
from app.ontology.loader import load_ontologies
from app.ontology.schema import Ontology
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType, DecisionSource, DocType, MatchStatus, ReviewFlag
from app.schemas.matching import Comparison, LabelBasis, ManufacturerItemResult, SubTypeFit
from app.schemas.requirements import LineItem, Requirement
from app.tender.splitter import SplitOptions, item_requirements, leaves

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"
OPTIONS = SelectionOptions.from_settings(
    Settings(_env_file=None, DATABASE_URL="postgresql+psycopg://u:p@localhost/db")  # type: ignore[arg-type]
)
TITLE = "TRIPLE OFFSET BUTTERFLY VALVE,250MM"
BOTH = pytest.mark.parametrize("with_ontology", [True, False], ids=["ontology", "no-ontology"])


@pytest.fixture(scope="module")
def valves() -> Ontology:
    [ontology] = load_ontologies(ONTOLOGY_DIR)
    return ontology


def _doc(
    name: str, sub_types: tuple[str, ...], product_types: tuple[str, ...] = ("butterfly_valve",)
) -> CatalogueDocument:
    return CatalogueDocument(
        manufacturer="Acme",
        category="valves_pumps",
        title=f"Synthetic {name}",
        doc_type=DocType.CATALOGUE,
        file=f"{name}.pdf",
        sha256=name[0] * 64,
        product_types=product_types,
        sub_types=sub_types,
    )


def _prose(doc: CatalogueDocument, text: str) -> CatalogueChunk:
    chunk = ChunkDraft(
        page_number=1, position=0, chunk_type=ChunkType.PROSE, raw_text=text, token_count=1
    )
    return CatalogueChunk(doc, chunk)


def _seat(doc: CatalogueDocument, material: str) -> CatalogueChunk:
    header, cells = ["Components", "Materials"], ["Seat", material]
    chunk = ChunkDraft(
        page_number=3,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=table_row_text(header, cells, "Materials"),
        token_count=1,
        meta={"header": header, "cells": cells},
    )
    return CatalogueChunk(doc, chunk)


def _requirements(ontology: Ontology | None) -> list[Requirement]:
    item = LineItem(
        item_no="10",
        title=TITLE,
        description_lines=["SIZE : 250 MM", "SEAT MOC : STELLITE 21"],
        document="tender.pdf",
        page_number=1,
    )
    return leaves(item_requirements(item, ontology, SplitOptions(6, 2)))


class _Pages:
    """Page text made of the synthetic chunks' text (each page holds its chunks)."""

    def __init__(self, chunks: list[CatalogueChunk]) -> None:
        self._text: dict[tuple[str, int], str] = {}
        for c in chunks:
            key = (c.document.sha256, c.chunk.page_number)
            self._text[key] = self._text.get(key, "") + "\n" + c.chunk.raw_text

    def page_text(self, sha256: str, page_number: int) -> str | None:
        return self._text.get((sha256, page_number))


CONCENTRIC = _doc("concentric", ("concentric",))
TRIPLE = _doc("triple", ("triple_offset",))
# The concentric catalogue prints a size range and a rubber seat; the triple-offset one only
# the seat. Pooled, the concentric seat would make the triple-offset seat look missing.
CHUNKS = [
    _prose(CONCENTRIC, "Size range: 50 mm to 600 mm"),
    _seat(CONCENTRIC, "EPDM / Nitrile"),
    _seat(TRIPLE, "Stellite 21 overlay"),
]


@BOTH
def test_documents_are_never_pooled(valves: Ontology, with_ontology: bool) -> None:
    ontology = valves if with_ontology else None
    result = evaluate_item(
        ItemQuery("Acme", "10", TITLE, "butterfly_valve"),
        _requirements(ontology),
        CHUNKS,
        MatchContext.for_ontology(ontology),
        OPTIONS,
        _Pages(CHUNKS),
    )
    by_file = {d.document: [c.status for c in d.comparisons] for d in result.documents}
    # Without equivalents a material word can't fail: EPDM could be anything.
    seat = MatchStatus.MISSING_CAPABILITY if with_ontology else MatchStatus.UNSUPPORTED
    assert by_file["concentric.pdf"] == [MatchStatus.FULL_MATCH, seat]
    assert by_file["triple.pdf"] == [MatchStatus.UNSUPPORTED, MatchStatus.FULL_MATCH]
    # Same fit score (1.0 each): the declared sub-type decides.
    assert result.item_sub_type == "triple_offset"
    assert result.chosen == TRIPLE.sha256
    assert [d.sub_type_fit for d in result.documents] == [SubTypeFit.MATCH, SubTypeFit.MISMATCH]


def test_fit_score_breaks_ties_between_general_documents(valves: Ontology) -> None:
    general_a, general_b = _doc("alpha", ()), _doc("beta", ())
    chunks = [_seat(general_a, "EPDM"), _seat(general_b, "Stellite 21")]
    result = evaluate_item(
        ItemQuery("Acme", "10", TITLE, "butterfly_valve"),
        _requirements(valves),
        chunks,
        MatchContext.for_ontology(valves),
        OPTIONS,
        _Pages(chunks),
    )
    assert [d.document for d in result.documents] == ["beta.pdf", "alpha.pdf"]
    assert [d.fit_score for d in result.documents] == [1.0, 0.0]


def test_sub_type_of_another_product_is_general(valves: Ontology) -> None:
    # Aquaseal shape: declares dual_plate_check for its check valves, none for butterfly.
    aquaseal = _doc("aquaseal", ("dual_plate_check",), ("butterfly_valve", "check_valve"))
    ctx = MatchContext.for_ontology(valves)
    assert sub_type_fit(aquaseal, "butterfly_valve", "triple_offset", ctx) is SubTypeFit.GENERAL
    # Without an ontology it cannot know whose sub-type that is: another sub-type, mismatch.
    assert (
        sub_type_fit(aquaseal, "butterfly_valve", "triple_offset", MatchContext())
        is SubTypeFit.MISMATCH
    )


def test_item_sub_type_with_and_without_ontology(valves: Ontology) -> None:
    ctx = MatchContext.for_ontology(valves)
    assert item_sub_type("TOBV 250 MM", "butterfly_valve", ctx, []) == "triple_offset"  # synonym
    assert (
        item_sub_type("TOBV 250 MM", "butterfly_valve", MatchContext(), ["triple_offset"]) is None
    )
    assert (
        item_sub_type(TITLE, "butterfly_valve", MatchContext(), ["triple_offset"])
        == "triple_offset"
    )
    assert item_sub_type("BUTTERFLY VALVE", "butterfly_valve", ctx, []) is None


def test_no_product_type_means_no_document() -> None:
    result = evaluate_item(
        ItemQuery("Acme", "10", "THERMOCOUPLE", None),
        [],
        CHUNKS,
        MatchContext(),
        OPTIONS,
        _Pages(CHUNKS),
    )
    assert (result.chosen, result.documents) == (None, [])


# ---------------------------------------------------------------- choosing by identity


def _choice(
    lines: list[str], documents: dict[str, str], valves: Ontology, title: str = "GATE VALVE"
) -> list[str]:
    item = LineItem(
        item_no="1", title=title, description_lines=lines, document="b.pdf", page_number=1
    )
    atomic = leaves(item_requirements(item, valves, SplitOptions(6, 2)))
    chunks = [_prose(_doc(name, (), ("gate_valve",)), text) for name, text in documents.items()]
    pages = _Pages(chunks)
    query = ItemQuery("Acme", "1", title, "gate_valve")
    result = evaluate_item(query, atomic, chunks, MatchContext.for_ontology(valves), OPTIONS, pages)
    return [d.document for d in result.documents]


def test_critical_requirements_beat_coverage(valves: Ontology) -> None:
    # The second catalogue decides three things, but has the wrong size range.
    documents = {
        "big": "Size: 50 mm to 600 mm",
        "small": "Size: 8 mm to 50 mm\nBody: A 216 Gr. WCB\nDisc: A 351 Gr. CF8M",
    }
    lines = ["SIZE : 150 MM", "BODY MOC : ASTM A216 Gr. WCB", "DISC MOC : CF8M"]
    assert _choice(lines, documents, valves) == ["big.pdf", "small.pdf"]


def test_stating_the_wrong_standard_ranks_below_stating_none(valves: Ontology) -> None:
    documents = {
        "alpha": "Design: API 602\nBody: A 216 Gr. WCB\nDisc: A 351 Gr. CF8M",
        "zeta": "Body: A 216 Gr. WCB",
    }
    lines = ["VALVE DESIGN : API 600", "BODY MOC : ASTM A216 Gr. WCB", "DISC MOC : CF8M"]
    assert _choice(lines, documents, valves) == ["zeta.pdf", "alpha.pdf"]


def test_with_critical_equal_coverage_decides_then_fit(valves: Ontology) -> None:
    documents = {
        "alpha": "Size: 50 mm to 600 mm",
        "zeta": "Size: 50 mm to 600 mm\nBody: A 105",
    }
    lines = ["SIZE : 150 MM", "BODY MOC : ASTM A216 Gr. WCB"]
    assert _choice(lines, documents, valves) == ["zeta.pdf", "alpha.pdf"]  # one more decided


def test_sub_type_still_comes_first(valves: Ontology) -> None:
    right = _doc("right", ("triple_offset",))
    wrong = _doc("wrong", ("concentric",))
    item = LineItem(
        item_no="1",
        title=TITLE,
        description_lines=["VALVE DESIGN : API 609"],
        document="b.pdf",
        page_number=1,
    )
    atomic = leaves(item_requirements(item, valves, SplitOptions(6, 2)))
    chunks = [_prose(right, "Body: WCB"), _prose(wrong, "Design: API 609")]
    pages = _Pages(chunks)
    query = ItemQuery("Acme", "1", TITLE, "butterfly_valve")
    result = evaluate_item(query, atomic, chunks, MatchContext.for_ontology(valves), OPTIONS, pages)
    assert [d.document for d in result.documents] == ["right.pdf", "wrong.pdf"]
    assert result.documents[1].critical_met == 1.0  # met more, still the wrong product


def test_critical_counts_are_reported(valves: Ontology) -> None:
    item = LineItem(
        item_no="1",
        title="GATE VALVE",
        description_lines=["VALVE DESIGN : API 600", "SIZE : 150 MM"],
        document="b.pdf",
        page_number=1,
    )
    atomic = leaves(item_requirements(item, valves, SplitOptions(6, 2)))
    text = "Design: API 602\nSize: 50 mm to 600 mm"
    chunks = [_prose(_doc("one", (), ("gate_valve",)), text)]
    query = ItemQuery("Acme", "1", "GATE VALVE", "gate_valve")
    result = evaluate_item(
        query, atomic, chunks, MatchContext.for_ontology(valves), OPTIONS, _Pages(chunks)
    )
    assert (result.documents[0].critical_met, result.documents[0].critical_failed) == (1.0, 1)


def test_a_catalogue_deciding_nothing_never_wins(valves: Ontology) -> None:
    documents = {"alpha": "A brochure about cryogenic service", "zeta": "Size: 8 mm to 50 mm"}
    assert _choice(["SIZE : 150 MM"], documents, valves) == ["zeta.pdf", "alpha.pdf"]


# ---------------------------------------------------------------- options list in the document


def _ordering(
    doc: CatalogueDocument, text: str, section: str = "Ordering Information"
) -> CatalogueChunk:
    chunk = ChunkDraft(
        page_number=9,
        position=0,
        chunk_type=ChunkType.PROSE,
        raw_text=text,
        section=section,
        token_count=1,
    )
    return CatalogueChunk(doc, chunk)


def _table_and_list(
    valves: Ontology, line: str, option: CatalogueChunk
) -> tuple[MatchStatus, bool]:
    doc = option.document
    chunks = [_seat(doc, "ASTM A216 Gr. WCB"), option]
    item = LineItem(
        item_no="1", title=TITLE, description_lines=[line], document="t.pdf", page_number=1
    )
    result = evaluate_item(
        ItemQuery("Acme", "1", TITLE, "butterfly_valve"),
        leaves(item_requirements(item, valves, SplitOptions(6, 2))),
        chunks,
        MatchContext.for_ontology(valves),
        OPTIONS,
        _Pages(chunks),
    )
    found = result.documents[0].comparisons[0]
    return found.status, found.needs_review


def test_an_ordering_page_of_the_whole_document_turns_a_failure_into_full(
    valves: Ontology,
) -> None:
    option = _ordering(_doc("range", ()), "LCB for ASTM A352 Gr. LCB")
    assert _table_and_list(valves, "SEAT MOC : LCB", option) == (MatchStatus.FULL_MATCH, False)


def test_an_options_list_on_another_products_page_gives_partial(valves: Ontology) -> None:
    doc = _doc("range", (), ("butterfly_valve", "check_valve"))
    option = _ordering(doc, "LCB for ASTM A352 Gr. LCB")
    check = CatalogueChunk(
        doc,
        ChunkDraft(
            page_number=9,
            position=1,
            chunk_type=ChunkType.PROSE,
            raw_text="Disc: IS 2062",
            section="Gamma Dual Plate Check Valve",
            token_count=1,
        ),
    )
    chunks = [_seat(doc, "ASTM A216 Gr. WCB"), option, check]
    item = LineItem(
        item_no="1",
        title=TITLE,
        description_lines=["SEAT MOC : LCB"],
        document="t.pdf",
        page_number=1,
    )
    result = evaluate_item(
        ItemQuery("Acme", "1", TITLE, "butterfly_valve"),
        leaves(item_requirements(item, valves, SplitOptions(6, 2))),
        chunks,
        MatchContext.for_ontology(valves),
        OPTIONS,
        _Pages(chunks),
    )
    found = result.documents[0].comparisons[0]
    assert (found.status, found.needs_review) == (MatchStatus.PARTIAL_MATCH, True)


def test_options_list_needs_the_whole_value(valves: Ontology) -> None:
    option = _ordering(_doc("range", ()), "LCB for ASTM A352 Gr. LCB")
    status, _ = _table_and_list(valves, "SEAT MOC : ASTM A352 Gr. LCC", option)
    assert status is MatchStatus.MISSING_CAPABILITY


def test_other_sections_are_not_an_options_list(valves: Ontology) -> None:
    option = _ordering(_doc("range", ()), "LCB for ASTM A352 Gr. LCB", section="Applications")
    status, _ = _table_and_list(valves, "SEAT MOC : LCB", option)
    assert status is MatchStatus.MISSING_CAPABILITY


def test_a_match_in_the_table_stays(valves: Ontology) -> None:
    option = _ordering(_doc("range", ()), "WCB for ASTM A216 Gr. WCB")
    status, _ = _table_and_list(valves, "SEAT MOC : ASTM A216 Gr. WCB", option)
    assert status is MatchStatus.FULL_MATCH  # the table itself states it


# ---------------------------------------------------------------- ties


def _tied_result(valves: Ontology, names: tuple[str, str]) -> ManufacturerItemResult:
    first, second = _doc(names[0], ()), _doc(names[1], ())
    chunks = [_seat(first, "Stellite 21"), _seat(second, "Stellite 21")]
    return evaluate_item(
        ItemQuery("Acme", "1", "BUTTERFLY VALVE", "butterfly_valve"),
        _requirements(valves),
        chunks,
        MatchContext.for_ontology(valves),
        OPTIONS,
        _Pages(chunks),
    )


def test_a_tie_chooses_no_catalogue_and_lists_them(valves: Ontology) -> None:
    result = _tied_result(valves, ("alpha", "beta"))
    assert result.chosen is None
    assert sorted(result.tied_documents) == ["alpha.pdf", "beta.pdf"]


def test_a_file_name_never_decides(valves: Ontology) -> None:
    result = _tied_result(valves, ("zulu", "alpha"))
    assert result.chosen is None
    assert sorted(result.tied_documents) == ["alpha.pdf", "zulu.pdf"]


def test_a_tie_reports_the_shared_score_and_each_documents_rows(valves: Ontology) -> None:
    requirements = _requirements(valves)
    result = _tied_result(valves, ("alpha", "beta"))
    report = manufacturer_report(result, requirements, OPTIONS)
    assert report.chosen is None  # no document is picked
    assert report.score.compatibility_pct == 100.0
    assert report.score.decided == 1
    assert sorted(t.document for t in report.tied_rows) == ["alpha.pdf", "beta.pdf"]
    for tied in report.tied_rows:
        assert all(r.needs_review for r in tied.results)
        seat = tied.results[1]
        assert seat.status is MatchStatus.FULL_MATCH
        assert seat.evidence[0].document == tied.document
        assert "catalogues tie, none picked" in seat.reason
    assert all(r.needs_review for r in report.results)


def test_a_clear_winner_is_no_tie(valves: Ontology) -> None:
    good, poor = _doc("alpha", ()), _doc("beta", ())
    chunks = [_seat(good, "Stellite 21"), _seat(poor, "EPDM")]
    result = evaluate_item(
        ItemQuery("Acme", "1", "BUTTERFLY VALVE", "butterfly_valve"),
        _requirements(valves),
        chunks,
        MatchContext.for_ontology(valves),
        OPTIONS,
        _Pages(chunks),
    )
    assert result.chosen == good.sha256
    assert result.tied_documents == []


def _tagged(doc: CatalogueDocument, text: str, product_type: str | None) -> CatalogueChunk:
    chunk = ChunkDraft(
        page_number=1,
        position=0,
        chunk_type=ChunkType.PROSE,
        raw_text=text,
        token_count=1,
        product_type=product_type,
    )
    return CatalogueChunk(doc, chunk)


def _size_result(valves: Ontology, chunks: list[CatalogueChunk]) -> tuple[MatchStatus, bool]:
    result = evaluate_item(
        ItemQuery("Acme", "10", TITLE, "butterfly_valve"),
        _requirements(valves)[:1],  # SIZE : 250 MM
        chunks,
        MatchContext.for_ontology(valves),
        OPTIONS,
        _Pages(chunks),
    )
    found = result.documents[0].comparisons[0]
    return found.status, found.needs_review


SEVERAL = ("butterfly_valve", "gate_valve")


def test_untagged_full_match_of_a_several_product_document_is_capped(valves: Ontology) -> None:
    # Velan's cover page "gate, globe, and check ... NPS 2 - 64" is about every product.
    doc = _doc("brochure", (), SEVERAL)
    chunks = [_tagged(doc, "Size range: 50 mm to 600 mm", None)]
    assert _size_result(valves, chunks) == (MatchStatus.PARTIAL_MATCH, True)


def test_tagged_full_match_stays_full(valves: Ontology) -> None:
    doc = _doc("brochure", (), SEVERAL)
    chunks = [
        _tagged(doc, "Size range: 50 mm to 600 mm", None),
        _tagged(doc, "Size range: 80 mm to 300 mm", "butterfly_valve"),
    ]
    assert _size_result(valves, chunks) == (MatchStatus.FULL_MATCH, False)


def test_a_tagged_full_match_is_cited_from_the_tagged_line(valves: Ontology) -> None:
    doc = _doc("brochure", (), SEVERAL)
    chunks = [
        _tagged(doc, "Size range: 50 mm to 600 mm", None),
        _tagged(doc, "Size range: 80 mm to 300 mm", "butterfly_valve"),
    ]
    result = evaluate_item(
        ItemQuery("Acme", "10", TITLE, "butterfly_valve"),
        _requirements(valves)[:1],
        chunks,
        MatchContext.for_ontology(valves),
        OPTIONS,
        _Pages(chunks),
    )
    quotes = [e.quote for e in result.documents[0].comparisons[0].evidence]
    assert quotes == ["Size range: 80 mm to 300 mm"]


def test_a_tagged_failure_never_replaces_an_untagged_match(valves: Ontology) -> None:
    # Velan "Sizes NPS 2-6 ... may have": only caps, never turns a match into a failure.
    doc = _doc("brochure", (), SEVERAL)
    chunks = [
        _tagged(doc, "Size range: 50 mm to 600 mm", None),
        _tagged(doc, "Size range: 50 mm to 100 mm", "butterfly_valve"),
    ]
    assert _size_result(valves, chunks) == (MatchStatus.PARTIAL_MATCH, True)


def test_untagged_line_of_a_one_product_document_still_decides(valves: Ontology) -> None:
    doc = _doc("datasheet", ())
    chunks = [_tagged(doc, "Size range: 50 mm to 600 mm", None)]
    assert _size_result(valves, chunks) == (MatchStatus.FULL_MATCH, False)


def _rule_result(flags: list[ReviewFlag]) -> Comparison:
    return Comparison(
        requirement_id="1.1",
        status=MatchStatus.FULL_MATCH,
        needs_review=False,
        decided_by=DecisionSource.RULE,
        basis=LabelBasis.ATTRIBUTE,
        reason="",
        flags=flags,
    )


def test_heading_cap_leaves_a_code_only_standard_alone() -> None:
    kept = _capped(_rule_result([ReviewFlag.CODE_ONLY_HIT]))
    assert kept.status is MatchStatus.FULL_MATCH
    capped = _capped(_rule_result([]))
    assert capped.status is MatchStatus.PARTIAL_MATCH
    assert ReviewFlag.HEADING_UNCLEAR in capped.flags


@pytest.mark.parametrize("status", [MatchStatus.FULL_MATCH, MatchStatus.PARTIAL_MATCH])
def test_a_sibling_products_value_is_partial_for_review(status: MatchStatus) -> None:
    # 8157057 "Disc : SGI": printed only for another butterfly valve of the same catalogue (B10).
    found = _from_another_product(
        _rule_result([]).model_copy(update={"status": status}), "Aquaseal 10"
    )
    assert (found.status, found.needs_review) == (MatchStatus.PARTIAL_MATCH, True)
    assert ReviewFlag.SIBLING_PRODUCT in found.flags
    assert "another product" in found.reason


def test_a_sibling_lacking_the_value_never_fails_this_product() -> None:
    missing = _rule_result([]).model_copy(update={"status": MatchStatus.MISSING_CAPABILITY})
    found = _from_another_product(missing, "Aquaseal 10")
    assert (found.status, found.needs_review) == (MatchStatus.UNSUPPORTED, True)
