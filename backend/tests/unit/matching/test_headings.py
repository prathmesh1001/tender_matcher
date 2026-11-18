"""Evidence inside one PDF holding several models. Documents and rows are synthetic."""

from dataclasses import replace
from pathlib import Path

import pytest

from app.core.config import Settings
from app.ingestion.chunker import table_row_text
from app.matching.chunk_source import CatalogueChunk, CatalogueDocument
from app.matching.comparator import MatchContext
from app.matching.documents import ItemQuery, SelectionOptions, _names_this_type, evaluate_item
from app.matching.headings import ProductGroups, group_by_product, other_product_passages
from app.ontology.loader import load_ontology_file
from app.ontology.schema import Ontology
from app.retrieval.text_normalizer import SearchVocabulary
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType, DecisionSource, DocType, MatchStatus, ReviewFlag
from app.schemas.matching import Comparison, DocumentResult, Evidence, LabelBasis
from app.schemas.requirements import LineItem
from app.tender.splitter import SplitOptions, item_requirements, leaves

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"
OPTIONS = SelectionOptions.from_settings(
    Settings(_env_file=None, DATABASE_URL="postgresql+psycopg://u:p@localhost/db")  # type: ignore[arg-type]
)
TITLE = "BUTTERFLY VALVE"
ALPHA = "Alpha Butterfly Valve - PN 10"
BETA = "Beta Butterfly Valve - PN 16"
DOC = CatalogueDocument(
    manufacturer="Acme",
    category="valves_pumps",
    title="Synthetic range",
    doc_type=DocType.CATALOGUE,
    file="range.pdf",
    sha256="r" * 64,
    product_types=("butterfly_valve",),
    sub_types=(),
)


@pytest.fixture(scope="module")
def valves() -> Ontology:
    ontology = load_ontology_file(ONTOLOGY_DIR / "valves_pumps.yaml")
    return ontology


def _row(
    part: str, material: str, section: str | None, page: int, position: int = 0
) -> CatalogueChunk:
    header, cells = ["Components", "Materials"], [part, material]
    chunk = ChunkDraft(
        page_number=page,
        position=position,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=table_row_text(header, cells, section or "Materials"),
        section=section,
        token_count=1,
        meta={"header": header, "cells": cells},
    )
    return CatalogueChunk(DOC, chunk)


class _Pages:
    """Page text made of the synthetic chunks' text."""

    def __init__(self, chunks: list[CatalogueChunk]) -> None:
        self._text: dict[int, str] = {}
        for c in chunks:
            self._text[c.chunk.page_number] = (
                self._text.get(c.chunk.page_number, "") + "\n" + c.chunk.raw_text
            )

    def page_text(self, sha256: str, page_number: int) -> str | None:
        return self._text.get(page_number)


def _evaluate(lines: list[str], chunks: list[CatalogueChunk], ontology: Ontology) -> DocumentResult:
    item = LineItem(
        item_no="1", title=TITLE, description_lines=lines, document="t.pdf", page_number=1
    )
    result = evaluate_item(
        ItemQuery("Acme", "1", TITLE, "butterfly_valve"),
        leaves(item_requirements(item, ontology, SplitOptions(6, 2))),
        chunks,
        MatchContext.for_ontology(ontology),
        OPTIONS,
        _Pages(chunks),
    )
    return result.documents[0]


def _groups(chunks: list[CatalogueChunk], ontology: Ontology) -> tuple[list[str], int]:
    vocabulary = SearchVocabulary.from_ontologies([ontology])
    groups = group_by_product(
        chunks, ontology, vocabulary, OPTIONS.overview_min_headings, OPTIONS.page_title_max_words
    )
    return groups.headings_of("butterfly_valve"), len(groups.unclear)


TWO_MODELS = [
    _row("Body", "ASTM A216 Gr. WCB", ALPHA, 1),
    _row("Disc", "ASTM A351 Gr. CF8M", ALPHA, 1, 1),
    _row("Body", "ASTM A216 Gr. WCB", BETA, 2),
    _row("Seat", "EPDM", BETA, 2, 1),
]


def test_a_heading_holds_until_the_next_one(valves: Ontology) -> None:
    chunks = [
        _row("Seat", "EPDM", "Introduction", 1),
        _row("Body", "WCB", ALPHA, 2),
        _row("Disc", "CF8M", "Compliance Standards", 3),
        _row("Body", "WCB", BETA, 4),
    ]
    assert _groups(chunks, valves) == ([ALPHA, BETA], 1)


def test_a_flanged_variant_is_the_same_heading(valves: Ontology) -> None:
    chunks = [_row("Body", "WCB", ALPHA, 1), _row("Disc", "CF8M", f"{ALPHA} (Flanged)", 2)]
    assert _groups(chunks, valves) == ([ALPHA], 0)


def _text(text: str, section: str | None, page: int, position: int = 0) -> CatalogueChunk:
    chunk = ChunkDraft(
        page_number=page,
        position=position,
        chunk_type=ChunkType.PROSE,
        raw_text=text,
        section=section,
        token_count=1,
    )
    return CatalogueChunk(DOC, chunk)


RANGE = replace(DOC, product_types=("butterfly_valve", "check_valve"))


def _in_range(chunks: list[CatalogueChunk]) -> list[CatalogueChunk]:
    """The chunks moved to a document declaring several product types (page titles are read)."""
    return [CatalogueChunk(RANGE, c.chunk) for c in chunks]


def test_a_figure_caption_above_the_page_number_is_no_heading(valves: Ontology) -> None:
    chunks = [
        _row("Seat", "EPDM", "Introduction", 1),
        _text("08", "48-inch Class 150 Butterfly Valve", 1, 1),
        _row("Trim", "SS 316", "Trim Materials", 2),
    ]
    assert _groups(chunks, valves) == ([], 3)


def test_a_page_title_line_is_a_product_heading(valves: Ontology) -> None:
    chunks = [
        _row("Seat", "EPDM", "Introduction", 1),
        _text("Alpha Butterfly Valves - ASME Class 150\nStandard materials", "Stem Packing", 2),
        _row("Body", "WCB", "Stem Packing", 2, 1),
        _row("Disc", "CF8M", "BUTT-WELD END", 3),
    ]
    assert _groups(_in_range(chunks), valves) == (["Alpha Butterfly Valves - ASME Class 150"], 1)
    assert _groups(chunks, valves) == ([], 4)  # a one-product catalogue reads no page titles


def test_a_title_naming_several_products_ends_the_heading(valves: Ontology) -> None:
    chunks = [
        _row("Body", "WCB", ALPHA, 1),
        _text("Butterfly & Check Valves - ASME B16.34\nPressure seal", None, 2),
    ]
    assert _groups(_in_range(chunks), valves) == ([ALPHA], 1)


def test_a_sentence_opening_a_page_is_no_title(valves: Ontology) -> None:
    line = "The butterfly valve body is cast in one piece for every size"
    chunks = [_row("Body", "WCB", "Introduction", 1), _text(line, None, 2)]
    assert _groups(_in_range(chunks), valves) == ([], 2)


def test_a_comma_does_not_end_a_title(valves: Ontology) -> None:
    line = "Triple-offset Butterfly Valve, an inherently fire-safe valve that provides"
    chunks = [_row("Body", "WCB", "Introduction", 1), _text(line, None, 2)]
    assert _groups(_in_range(chunks), valves) == ([], 2)


def test_shared_pages_are_those_no_heading_owns_and_naming_no_other_product(
    valves: Ontology,
) -> None:
    chunks = [
        _text("Ordering Information\nTrim: 8, 12, 16", "Ordering Information", 1),
        _text("Dual plate check valves for sea water", None, 2),
        _row("Body", "WCB", ALPHA, 3),
    ]
    vocabulary = SearchVocabulary.from_ontologies([valves])
    groups = group_by_product(
        chunks, valves, vocabulary, OPTIONS.overview_min_headings, OPTIONS.page_title_max_words
    )
    assert groups.shared_pages("butterfly_valve") == frozenset({1})
    assert groups.shared_pages("check_valve") == frozenset({1, 2})


def test_a_page_naming_many_products_is_an_overview(valves: Ontology) -> None:
    gamma = "Gamma Butterfly Valve"
    chunks = [
        _row("Body", "WCB", ALPHA, 1),
        _row("Body", "WCB", BETA, 1, 1),
        _row("Body", "WCB", gamma, 1, 2),
    ]
    assert _groups(chunks, valves) == ([], 3)


def test_a_heading_naming_several_products_owns_nothing(valves: Ontology) -> None:
    chunks = [_row("Body", "WCB", "Butterfly & Check Valves", 1)]
    assert _groups(chunks, valves) == ([], 1)


def test_evidence_of_a_sibling_model_is_partial_for_review(valves: Ontology) -> None:
    # B10 (decided 2026-11-13): a sibling's value is never full for this model, but partial.
    chunks = [*TWO_MODELS, _row("Stem", "ASTM A479 Type 410", ALPHA, 1, 2)]
    lines = [
        "BODY MOC : ASTM A216 Gr. WCB",
        "DISC MOC : ASTM A351 Gr. CF8M",
        "STEM MOC : ASTM A479 Type 410",
        "SEAT MOC : EPDM",
    ]
    result = _evaluate(lines, chunks, valves)
    assert result.product_heading == ALPHA
    *own, seat = result.comparisons
    assert [c.status for c in own] == [MatchStatus.FULL_MATCH] * 3
    assert seat.status is MatchStatus.PARTIAL_MATCH
    assert seat.needs_review is True
    assert ReviewFlag.SIBLING_PRODUCT in seat.flags
    assert ALPHA in seat.reason
    assert seat.evidence[0].page_number == 2  # shown, so a reviewer can look


def test_evidence_no_heading_owns_is_capped_at_partial(valves: Ontology) -> None:
    two = replace(DOC, product_types=("butterfly_valve", "check_valve"))
    rows = [*TWO_MODELS, _row("Stem", "ASTM A479 Type 410", "Butterfly & Check Valves", 3)]
    chunks = [CatalogueChunk(two, c.chunk) for c in rows]
    result = _evaluate(
        ["DISC MOC : ASTM A351 Gr. CF8M", "STEM MOC : ASTM A479 Type 410"], chunks, valves
    )
    assert result.comparisons[0].status is MatchStatus.FULL_MATCH
    stem = result.comparisons[1]
    assert (stem.status, stem.needs_review) == (MatchStatus.PARTIAL_MATCH, True)


def test_a_tie_between_models_chooses_none_and_caps(valves: Ontology) -> None:
    result = _evaluate(["BODY MOC : ASTM A216 Gr. WCB"], TWO_MODELS, valves)
    assert result.product_heading is None
    body = result.comparisons[0]
    assert (body.status, body.needs_review) == (MatchStatus.PARTIAL_MATCH, True)


def test_the_chosen_model_decides_even_if_a_sibling_would_pass(valves: Ontology) -> None:
    chunks = [
        _row("Body", "ASTM A216 Gr. WCB", ALPHA, 1),
        _row("Disc", "ASTM A351 Gr. CF8M", ALPHA, 1, 1),
        _row("Seat", "NBR", ALPHA, 1, 2),
        _row("Seat", "EPDM", BETA, 2),
    ]
    result = _evaluate(
        ["BODY MOC : ASTM A216 Gr. WCB", "DISC MOC : ASTM A351 Gr. CF8M", "SEAT MOC : EPDM"],
        chunks,
        valves,
    )
    assert result.product_heading == ALPHA
    assert result.comparisons[2].status is MatchStatus.MISSING_CAPABILITY


@pytest.mark.parametrize(("rating", "heading"), [("PN10", ALPHA), ("PN16", BETA)])
def test_a_rating_printed_in_the_heading_chooses_the_model(
    rating: str, heading: str, valves: Ontology
) -> None:
    result = _evaluate([f"CLASS : {rating}", "BODY MOC : ASTM A216 Gr. WCB"], TWO_MODELS, valves)
    assert result.product_heading == heading
    # Choosing is all a heading does: it is not evidence for the rating.
    assert result.comparisons[0].status is MatchStatus.UNSUPPORTED


def test_one_heading_changes_nothing(valves: Ontology) -> None:
    chunks = [_row("Body", "ASTM A216 Gr. WCB", ALPHA, 1), _row("Seat", "EPDM", None, 2)]
    result = _evaluate(["BODY MOC : ASTM A216 Gr. WCB", "SEAT MOC : EPDM"], chunks, valves)
    assert result.product_heading is None
    assert [c.status for c in result.comparisons] == [MatchStatus.FULL_MATCH] * 2


# ---------------------------------------------------------------- one product in the document


UNHEADED = _row("Stem", "ASTM A479 Type 410", "Butterfly & Check Valves", 3)


def test_pages_without_a_heading_belong_to_the_only_product(valves: Ontology) -> None:
    chunks = [*TWO_MODELS, UNHEADED]
    result = _evaluate(
        ["DISC MOC : ASTM A351 Gr. CF8M", "STEM MOC : ASTM A479 Type 410"], chunks, valves
    )
    stem = result.comparisons[1]
    assert (stem.status, stem.needs_review) == (MatchStatus.FULL_MATCH, False)


def test_they_are_capped_if_the_document_declares_two_products(valves: Ontology) -> None:
    two = replace(DOC, product_types=("butterfly_valve", "check_valve"))
    chunks = [CatalogueChunk(two, c.chunk) for c in [*TWO_MODELS, UNHEADED]]
    result = _evaluate(
        ["DISC MOC : ASTM A351 Gr. CF8M", "STEM MOC : ASTM A479 Type 410"], chunks, valves
    )
    stem = result.comparisons[1]
    assert (stem.status, stem.needs_review) == (MatchStatus.PARTIAL_MATCH, True)


def test_they_are_capped_if_a_heading_names_another_product(valves: Ontology) -> None:
    chunks = [
        *TWO_MODELS,
        UNHEADED,
        _row("Disc", "ASTM A351 Gr. CF8", "Delta Check Valve", 4),
    ]
    result = _evaluate(
        ["DISC MOC : ASTM A351 Gr. CF8M", "STEM MOC : ASTM A479 Type 410"], chunks, valves
    )
    assert result.comparisons[1].status is MatchStatus.PARTIAL_MATCH


def _keys(chunks: list[CatalogueChunk]) -> frozenset[tuple[int, str]]:
    return frozenset((c.chunk.page_number, c.chunk.raw_text) for c in chunks)


def test_the_judge_may_not_read_a_sibling_model_once_one_is_chosen(valves: Ontology) -> None:
    alpha_stem = _row("Stem", "ASTM A479 Type 410", ALPHA, 1, 2)
    result = _evaluate(
        ["BODY MOC : ASTM A216 Gr. WCB", "STEM MOC : ASTM A479 Type 410"],
        [*TWO_MODELS, alpha_stem],
        valves,
    )
    assert result.product_heading == ALPHA
    assert result.other_product_passages == _keys(TWO_MODELS[2:])


def test_siblings_stay_readable_while_none_is_chosen(valves: Ontology) -> None:
    result = _evaluate(["BODY MOC : ASTM A216 Gr. WCB"], TWO_MODELS, valves)
    assert result.product_heading is None
    assert result.other_product_passages == frozenset()


def test_another_product_types_heading_is_never_the_items(valves: Ontology) -> None:
    check = _row("Disc", "IS 2062 Gr. B", "Gamma Dual Plate Check Valve", 3)
    chunks = [_row("Body", "WCB", ALPHA, 1), check]
    vocabulary = SearchVocabulary.from_ontologies([valves])
    groups = group_by_product(
        chunks, valves, vocabulary, OPTIONS.overview_min_headings, OPTIONS.page_title_max_words
    )
    assert other_product_passages(groups, "butterfly_valve", None) == _keys([check])
    assert other_product_passages(groups, "check_valve", None) == _keys(chunks[:1])


def test_the_excluded_chunks_are_not_written_to_the_report(valves: Ontology) -> None:
    result = _evaluate(["DISC MOC : ASTM A351 Gr. CF8M"], TWO_MODELS, valves)
    assert result.other_product_passages  # Alpha chosen, Beta held back
    assert "other_product_passages" not in result.model_dump()


def _found_on(*pages: int) -> Comparison:
    return Comparison(
        requirement_id="1.1",
        status=MatchStatus.FULL_MATCH,
        needs_review=False,
        decided_by=DecisionSource.RULE,
        basis=LabelBasis.ATTRIBUTE,
        reason="",
        evidence=[
            Evidence(
                document="r.pdf",
                sha256="r" * 64,
                title="t",
                page_number=p,
                label="l",
                value="v",
                quote="q",
                quote_parts=[],
            )
            for p in pages
        ],
    )


def test_a_range_page_naming_the_type_is_not_capped(valves: Ontology) -> None:
    # L&T API 600 p5: "Cast Steel Gate, Globe and Check Valves are offered in ..." (no heading).
    groups = ProductGroups(
        by_heading={},
        unclear=[],
        types={},
        unowned={5: frozenset({"gate_valve", "globe_valve", "check_valve"}), 6: frozenset()},
    )
    item = leaves(
        item_requirements(
            LineItem(
                item_no="1",
                title="GLOBE VALVE",
                description_lines=["DESIGN TEMP : 40 C"],
                document="t.pdf",
                page_number=1,
            ),
            valves,
            SplitOptions(6, 2),
        )
    )[0].model_copy(update={"product_type": "globe_valve"})
    assert _names_this_type(_found_on(5), groups, item) is True
    assert _names_this_type(_found_on(6), groups, item) is False  # names no type: stays capped
    assert _names_this_type(_found_on(5, 7), groups, item) is False  # p7 is owned elsewhere
