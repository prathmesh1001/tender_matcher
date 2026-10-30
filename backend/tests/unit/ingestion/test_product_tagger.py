"""Product-type tagging. Cases mirror what was measured on the real catalogues (synthetic text)."""

from pathlib import Path

import pytest

from app.core.exceptions import SourceRegistryError
from app.ingestion.processing import headings_on_page
from app.ingestion.product_tagger import Declared, ProductMatcher, TagInput, tag_chunks
from app.ontology.loader import load_ontologies
from app.retrieval.text_normalizer import SearchVocabulary, normalize_for_search

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"
MIN_TAGGED = 2


@pytest.fixture(scope="module")
def vocab() -> SearchVocabulary:
    return SearchVocabulary.from_ontologies(load_ontologies(ONTOLOGY_DIR))


@pytest.fixture(scope="module")
def matcher(vocab: SearchVocabulary) -> ProductMatcher:
    return ProductMatcher.from_ontologies(load_ontologies(ONTOLOGY_DIR), vocab)


def _items(
    vocab: SearchVocabulary, rows: list[tuple[str, str, int]], local: bool = True
) -> list[TagInput]:
    return [
        TagInput(
            section=normalize_for_search(sec, vocab),
            search_text=normalize_for_search(text, vocab),
            page=page,
            heading_on_page=local,
        )
        for sec, text, page in rows
    ]


def _types(
    vocab: SearchVocabulary, matcher: ProductMatcher, rows: list[tuple[str, str, int]], d: Declared
) -> list[str | None]:
    return [t.product_type for t in tag_chunks(_items(vocab, rows), matcher, d, MIN_TAGGED)]


def test_single_product_catalogue_ignores_passing_mentions(
    vocab: SearchVocabulary, matcher: ProductMatcher
) -> None:
    # Real failure: one sentence about gate valves re-labelled 97 chunks of a TOBV catalogue.
    rows = [
        ("Overview", "Triple offset design, an ideal alternative to gate valves.", 1),
        ("Dimensions", "Size 80 NB: A 120 B 210", 2),
    ]
    declared = Declared(("butterfly_valve",), ("triple_offset",))
    tags = tag_chunks(_items(vocab, rows), matcher, declared, MIN_TAGGED)
    assert [(t.product_type, t.sub_type) for t in tags] == [
        ("butterfly_valve", "triple_offset"),
        ("butterfly_valve", "triple_offset"),
    ]


def test_multi_product_catalogue_tags_by_heading_then_text(
    vocab: SearchVocabulary, matcher: ProductMatcher
) -> None:
    declared = Declared(("gate_valve", "globe_valve", "check_valve"))
    rows = [
        ("Gate Valves", "Body: A216 WCB", 1),
        ("Materials", "Swing check valve body: WCB", 2),
        ("Materials", "Disc: CF8M", 2),  # carries within the same section and page
        ("Pressure Testing", "Shell test 1.5 x rating", 2),  # new heading: no carry
        ("Introduction", "We make gate, globe and check valves.", 3),  # several types: general
    ]
    assert _types(vocab, matcher, rows, declared) == [
        "gate_valve",
        "check_valve",
        "check_valve",
        "check_valve",  # page 2 already has 2 check-valve chunks and nothing else
        None,
    ]


def test_types_outside_the_declaration_are_never_assigned(
    vocab: SearchVocabulary, matcher: ProductMatcher
) -> None:
    declared = Declared(("butterfly_valve", "check_valve"))
    rows = [("Introduction", "Our group also makes ball valves and plug valves.", 1)]
    assert _types(vocab, matcher, rows, declared) == [None]


def test_page_fill_needs_a_heading_or_two_tagged_chunks(
    vocab: SearchVocabulary, matcher: ProductMatcher
) -> None:
    declared = Declared(("gate_valve", "butterfly_valve", "ball_valve"))
    heading_page = [
        ("Aquaseal Butterfly Valve - Class 150", "Body style: wafer", 1),
        ("Dimensions - PN 16", "DN 200: A 60 B 230", 1),
    ]
    overview_page = [  # a single passing mention in body text, like the cryogenic brochure
        ("Our Range", "Including butterfly valves for LNG.", 2),
        ("Design", "Design to BS 6364 and ISO 28921.", 2),
    ]
    two_types = [
        ("Gate Valves", "Body: WCB", 3),
        ("Ball Valves", "Seat: PTFE", 3),
        ("Notes", "All dimensions in mm.", 3),
    ]
    types = _types(vocab, matcher, heading_page + overview_page + two_types, declared)
    assert types == [
        *["butterfly_valve", "butterfly_valve"],  # heading names the product: page inherits
        *["butterfly_valve", None],  # one passing mention: only that chunk is tagged
        *["gate_valve", "ball_valve", None],  # two types on the page: no inheritance
    ]


def test_sub_type_from_text_when_not_declared(
    vocab: SearchVocabulary, matcher: ProductMatcher
) -> None:
    rows = [("Check Valves", "Dual plate check valve, wafer pattern", 1)]
    [tag] = tag_chunks(_items(vocab, rows), matcher, Declared(("check_valve", "gate_valve")), 2)
    assert (tag.product_type, tag.sub_type) == ("check_valve", "dual_plate_check")


def test_tender_text_allows_every_type_and_ignores_ball_bearings(
    vocab: SearchVocabulary, matcher: ProductMatcher
) -> None:
    # B1: "ball" in "ball bearing" is not a ball valve.
    rows = [
        ("Item 1", "Butterfly valve, wafer type, 200 NB", 1),
        ("Item 2", "Pump with ball bearings, 1450 rpm", 2),
    ]
    assert _types(vocab, matcher, rows, Declared()) == ["butterfly_valve", None]


def test_inherited_heading_is_not_used(vocab: SearchVocabulary, matcher: ProductMatcher) -> None:
    declared = Declared(("gate_valve", "butterfly_valve"))
    rows = [("Triple-offset Butterfly Valves", "L&T is a global leader in cryogenic valves.", 2)]
    tags = tag_chunks(_items(vocab, rows, local=False), matcher, declared, MIN_TAGGED)
    assert tags[0].product_type is None


def test_headings_on_page() -> None:
    sections = ["Gate Valves", "Gate Valves", "Range List", "Range List"]
    pages = [1, 2, 3, 4]
    # "Gate Valves" had content on page 1, so it still counts on page 2. "Range List" was found
    # on page 2 with nothing under it there, so it describes neither page 3 nor page 4.
    section_pages: list[int | None] = [1, 1, 2, 2]
    assert headings_on_page(sections, pages, section_pages) == [True, True, False, False]
    assert headings_on_page(["X"], [5], [None]) == [True]  # stored before pages were recorded


def test_unknown_declarations_are_rejected(matcher: ProductMatcher) -> None:
    matcher.validate(["butterfly_valve"], ["triple_offset"])
    with pytest.raises(SourceRegistryError, match="butterfly_valves"):
        matcher.validate(["butterfly_valves"], [])
    with pytest.raises(SourceRegistryError, match="quadruple_offset"):
        matcher.validate(["butterfly_valve"], ["quadruple_offset"])


BOTH = Declared(("butterfly_valve", "check_valve"))


def _spread(
    vocab: SearchVocabulary, next_page_table: bool = True, next_page_title: str = ""
) -> tuple[list[TagInput], dict[int, str]]:
    """L&T Aquaseal layout: p24 introduces a check valve, p25 holds only its spec tables."""
    rows = [
        (
            "Aquaseal Chek Dual-plate Check Valve",
            "Soft-seated dual-plate check valve, API 594.",
            24,
        ),
        ("Hinge Pin", "Stainless steel hinge pin.", 24),
        ("Compliance Standards", "Parameter: Valve Design; Standard: API 594", 25),
        ("Compliance Standards", "Components: Body; Materials: Cast Iron EN1561", 25),
        ("Dimensions", "Valve Size: 100; A: 54", 25),
    ]
    items = [
        TagInput(
            section=normalize_for_search(sec, vocab),
            search_text=normalize_for_search(text, vocab),
            page=page,
            is_table=page == 25 and next_page_table,
        )
        for sec, text, page in rows
    ]
    pages = {
        24: normalize_for_search("Aquaseal Chek Dual-plate Check Valve ...", vocab),
        25: normalize_for_search(f"{next_page_title} Compliance Standards Parameter", vocab),
    }
    return items, pages


def test_spec_table_page_continues_the_product_of_the_page_before(
    vocab: SearchVocabulary, matcher: ProductMatcher
) -> None:
    items, pages = _spread(vocab)
    tags = tag_chunks(items, matcher, BOTH, MIN_TAGGED, pages)
    assert [t.product_type for t in tags] == ["check_valve"] * 5  # p25 is not butterfly evidence


def test_prose_page_does_not_inherit(vocab: SearchVocabulary, matcher: ProductMatcher) -> None:
    items, pages = _spread(vocab, next_page_table=False)
    tags = tag_chunks(items, matcher, BOTH, MIN_TAGGED, pages)
    assert [t.product_type for t in tags][2:] == [None, None, None]


def test_page_naming_another_product_does_not_inherit(
    vocab: SearchVocabulary, matcher: ProductMatcher
) -> None:
    # Real case: L&T API 602 p9 "Cryogenic Gate Valves" table after a check-valve page.
    items, pages = _spread(vocab, next_page_title="Butterfly Valve Materials")
    tags = tag_chunks(items, matcher, BOTH, MIN_TAGGED, pages)
    assert [t.product_type for t in tags][2:] == [None, None, None]


def test_without_page_texts_nothing_inherits(
    vocab: SearchVocabulary, matcher: ProductMatcher
) -> None:
    items, _ = _spread(vocab)
    tags = tag_chunks(items, matcher, BOTH, MIN_TAGGED)
    assert [t.product_type for t in tags][2:] == [None, None, None]
