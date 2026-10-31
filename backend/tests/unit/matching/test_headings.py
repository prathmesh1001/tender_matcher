"""Evidence inside one PDF holding several models. Documents and rows are synthetic."""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.ingestion.chunker import table_row_text
from app.matching.chunk_source import CatalogueChunk, CatalogueDocument
from app.matching.comparator import MatchContext
from app.matching.documents import ItemQuery, SelectionOptions, evaluate_item
from app.matching.headings import group_by_product
from app.ontology.loader import load_ontologies
from app.ontology.schema import Ontology
from app.retrieval.text_normalizer import SearchVocabulary
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType, DocType, MatchStatus
from app.schemas.matching import DocumentResult
from app.schemas.requirements import LineItem
from app.tender.splitter import SplitOptions, item_requirements, leaves

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"
OPTIONS = SelectionOptions.from_settings(
    Settings(_env_file=None, DATABASE_URL="postgresql+psycopg://u:p@localhost/db")  # type: ignore[call-arg]
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
    [ontology] = load_ontologies(ONTOLOGY_DIR)
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
    groups = group_by_product(chunks, ontology, vocabulary, OPTIONS.overview_min_headings)
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


def test_evidence_of_a_sibling_model_is_unsupported(valves: Ontology) -> None:
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
    assert seat.status is MatchStatus.UNSUPPORTED
    assert seat.needs_review is True
    assert ALPHA in seat.reason
    assert seat.evidence[0].page_number == 2  # shown, so a reviewer can look


def test_evidence_no_heading_owns_is_capped_at_partial(valves: Ontology) -> None:
    chunks = [*TWO_MODELS, _row("Stem", "ASTM A479 Type 410", "Materials", 3)]
    chunks[-1] = _row("Stem", "ASTM A479 Type 410", "Butterfly & Check Valves", 3)
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
