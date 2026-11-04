"""Lines that only name the product ("Valve Type : Globe Valve"). Documents are synthetic."""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.ingestion.chunker import table_row_text
from app.matching.chunk_source import CatalogueChunk, CatalogueDocument
from app.matching.comparator import MatchContext
from app.matching.documents import ItemQuery, SelectionOptions, evaluate_item
from app.matching.product_identity import names_only_product
from app.ontology.loader import load_ontologies
from app.ontology.schema import Ontology
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType, DocType, MatchStatus
from app.schemas.matching import Comparison
from app.schemas.requirements import LineItem, Requirement
from app.tender.splitter import SplitOptions, item_requirements, leaves

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"
SETTINGS = Settings(_env_file=None, DATABASE_URL="postgresql+psycopg://u:p@localhost/db")  # type: ignore[arg-type]
OPTIONS = SelectionOptions.from_settings(SETTINGS)
LABEL_WORDS = OPTIONS.product_label_words


@pytest.fixture(scope="module")
def valves() -> Ontology:
    [ontology] = load_ontologies(ONTOLOGY_DIR)
    return ontology


def _doc(product_type: str) -> CatalogueDocument:
    return CatalogueDocument(
        manufacturer="Acme",
        category="valves_pumps",
        title="Synthetic catalogue",
        doc_type=DocType.CATALOGUE,
        file="acme.pdf",
        sha256="a" * 64,
        product_types=(product_type,),
        sub_types=(),
    )


def _chunk(doc: CatalogueDocument, text: str, page: int = 2) -> CatalogueChunk:
    chunk = ChunkDraft(
        page_number=page, position=0, chunk_type=ChunkType.PROSE, raw_text=text, token_count=1
    )
    return CatalogueChunk(doc, chunk)


def _requirement(line: str, title: str, ontology: Ontology | None) -> Requirement:
    item = LineItem(
        item_no="1", title=title, description_lines=[line], document="t.pdf", page_number=1
    )
    [requirement] = leaves(item_requirements(item, ontology, SplitOptions(6, 2)))
    return requirement


class _Pages:
    def __init__(self, chunks: list[CatalogueChunk]) -> None:
        self._text = {(c.document.sha256, c.chunk.page_number): c.chunk.raw_text for c in chunks}

    def page_text(self, sha256: str, page_number: int) -> str | None:
        return self._text.get((sha256, page_number))


def _result(
    line: str, title: str, product: str, texts: list[str], ontology: Ontology | None
) -> Comparison:
    doc = _doc(product)
    chunks = [_chunk(doc, t, page=i + 2) for i, t in enumerate(texts)]
    result = evaluate_item(
        ItemQuery("Acme", "1", title, product),
        [_requirement(line, title, ontology)],
        chunks,
        MatchContext.for_ontology(ontology),
        OPTIONS,
        _Pages(chunks),
    )
    [document] = result.documents
    [comparison] = document.comparisons
    return comparison


def test_product_line_is_met_by_the_line_naming_it(valves: Ontology) -> None:
    found = _result(
        "Valve Type. : Globe Valve",
        "Globe valve as per BS-1873",
        "globe_valve",
        ["Introduction to our range", "Cast Steel Globe Valves\nClass 150 to 600"],
        valves,
    )
    assert found.status is MatchStatus.FULL_MATCH
    [evidence] = found.evidence
    assert evidence.quote == "Cast Steel Globe Valves"
    assert evidence.page_number == 3
    assert evidence.verified is True


def test_catalogue_that_never_prints_the_name_decides_nothing(valves: Ontology) -> None:
    found = _result(
        "Valve Type : Globe Valve",
        "Globe valve",
        "globe_valve",
        ["Class 150 to 600", "Body: ASTM A216 WCB"],
        valves,
    )
    assert found.status is MatchStatus.UNSUPPORTED
    assert found.evidence == []


def test_without_an_ontology_the_rule_is_off() -> None:
    found = _result(
        "Valve Type : Globe Valve", "Globe valve", "globe_valve", ["Globe Valves"], None
    )
    assert found.status is MatchStatus.UNSUPPORTED


@pytest.mark.parametrize(
    ("line", "title", "product"),
    [
        # More than the product's name: the other words are other requirements.
        ("Valve Type : Globe Valve with Bolted Bonnet", "Globe valve", "globe_valve"),
        # A sub-type needs the sub-type checks (B2).
        (
            "Valve Type : Triple Offset Butterfly Valve",
            "Butterfly valve",
            "butterfly_valve",
        ),
        # Another product than the item's.
        ("Valve Type : Gate Valve", "Globe valve", "globe_valve"),
        # The label does not ask for the type.
        ("Service : Globe Valve", "Globe valve", "globe_valve"),
    ],
)
def test_rule_does_not_apply(valves: Ontology, line: str, title: str, product: str) -> None:
    requirement = _requirement(line, title, valves)
    assert not names_only_product(
        requirement, product, MatchContext.for_ontology(valves), LABEL_WORDS
    )


def test_plain_product_line_applies(valves: Ontology) -> None:
    requirement = _requirement("Type of Valve : GLOBE VALVES", "Globe valve", valves)
    assert names_only_product(
        requirement, "globe_valve", MatchContext.for_ontology(valves), LABEL_WORDS
    )


def test_line_naming_several_products_is_not_evidence(valves: Ontology) -> None:
    found = _result(
        "Valve Type : Globe Valve",
        "Globe valve",
        "globe_valve",
        ["Cast Steel Gate, Globe and Check Valves", "Globe Valves\nClass 150 to 600"],
        valves,
    )
    assert found.status is MatchStatus.FULL_MATCH
    [evidence] = found.evidence
    assert (evidence.quote, evidence.page_number) == ("Globe Valves", 3)


def test_table_row_cites_the_printed_cell(valves: Ontology) -> None:
    doc = _doc("gate_valve")
    header, cells = ["Valve Type", "Valve Design"], ["Gate Valves", "API 602"]
    row = ChunkDraft(
        page_number=3,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=table_row_text(header, cells, "Compliance Standards"),
        token_count=1,
        meta={"header": header, "cells": cells},
    )
    chunks = [CatalogueChunk(doc, row)]
    pages = {(doc.sha256, 3): "Compliance Standards\nValve Type Valve Design\nGate Valves API 602"}

    class _Printed:
        def page_text(self, sha256: str, page_number: int) -> str | None:
            return pages.get((sha256, page_number))

    result = evaluate_item(
        ItemQuery("Acme", "1", "Gate valve", "gate_valve"),
        [_requirement("Valve Type : Gate Valve", "Gate valve", valves)],
        chunks,
        MatchContext.for_ontology(valves),
        OPTIONS,
        _Printed(),
    )
    [comparison] = result.documents[0].comparisons
    assert comparison.status is MatchStatus.FULL_MATCH
    assert comparison.evidence[0].quote == "Gate Valves"
