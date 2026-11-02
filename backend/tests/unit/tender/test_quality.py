"""The extraction check. Items and pages are synthetic."""

import pytest

from app.ingestion.processing import ProcessedPage
from app.schemas.enums import ExtractionIssueCode, ExtractionStatus
from app.tender.quality import (
    ItemFacts,
    QualityRules,
    extraction_quality,
    low_confidence_pages,
    title_reads_as_text,
)
from tests.conftest import make_test_settings

RULES = QualityRules.from_settings(make_test_settings())
LONG_TITLE = " ".join(["word"] * 25)


def _item(title: str = "Globe valve", *, names_product: bool = True, n: int = 10) -> ItemFacts:
    return ItemFacts(item_no="1", title=title, names_product=names_product, requirements=n)


def _codes(items: list[ItemFacts], from_item_category: bool = False) -> set[ExtractionIssueCode]:
    return {r.code for r in extraction_quality(items, from_item_category, RULES).reasons}


@pytest.mark.parametrize(
    "title",
    [
        "Design",
        "Fluid service",
        "Mandatory spares (As",  # bracket never closed: the cell was cut
        "Apron shall conform to EN ISO",
        "Spares must be supplied",
        "the shall, followed by a comma",
        LONG_TITLE,
    ],
)
def test_pieces_of_text_read_as_text(title: str) -> None:
    assert title_reads_as_text(title, RULES)


@pytest.mark.parametrize(
    "title",
    [
        "RTD 3-wire with Thermowell SS316 1.5 Inch",
        "Aluminium Phosphide Formulation (V3) Conforming to IS 6438",
        "DN 25",  # short, but a digit makes it a size or code
        "Non-Return Flap Valve of size 3inches with full bore",
    ],
)
def test_names_of_things_do_not_read_as_text(title: str) -> None:
    assert not title_reads_as_text(title, RULES)


def test_a_clean_tender_is_ok() -> None:
    quality = extraction_quality([_item(), _item("Gate valve")], False, RULES)
    assert quality.status is ExtractionStatus.OK
    assert quality.reasons == []


def test_no_items_is_not_a_division_by_zero() -> None:
    assert extraction_quality([], False, RULES).status is ExtractionStatus.OK


def test_most_titles_being_fragments_marks_the_tender() -> None:
    items = [_item("Design", names_product=False), _item("Size", names_product=False), _item()]
    assert _codes(items) == {ExtractionIssueCode.TITLES_NOT_PRODUCTS}


def test_a_few_fragment_titles_do_not() -> None:
    assert _codes([_item("Design", names_product=False), _item(), _item()]) == set()


def test_a_title_naming_a_product_is_never_a_bad_title() -> None:
    assert _codes([_item("Pump", names_product=True)]) == set()


def test_a_real_product_outside_the_ontology_is_not_a_bad_title() -> None:
    assert _codes([_item("RTD 3-wire with Thermowell 1.5 Inch", names_product=False)]) == set()


def test_requirement_limit_is_exclusive() -> None:
    assert _codes([_item(n=50)]) == set()
    assert _codes([_item(n=51)]) == {ExtractionIssueCode.OVERSIZED_ITEM}


def test_the_reason_names_the_worst_item() -> None:
    items = [
        ItemFacts(item_no="7", title="Globe valve", names_product=True, requirements=566),
        ItemFacts(item_no="8", title="Gate valve", names_product=True, requirements=83),
    ]
    [issue] = extraction_quality(items, False, RULES).reasons
    assert "item 7 has 566" in issue.reason


def test_item_category_naming_one_product_is_fine() -> None:
    titles = [
        "Submersible Sewerage Pump (Q3)",
        "SINGLE STAGE WATER PUMP WITH MOTOR ON M. S. BASE PLATE, CENTRIFUGAL, BRONZE IMPELL",
    ]
    for title in titles:
        assert _codes([_item(title)], from_item_category=True) == set()


@pytest.mark.parametrize(
    "title",
    [
        "Globe Valve 40 NB with Flanges , Globe Valve 50 NB with Flanges",
        "CENTRIFUGAL HORIZONTAL PUMP FOR ACID,Centrifugal Horizontal Pump for lye",
    ],
)
def test_item_category_merging_several_products_marks_the_tender(title: str) -> None:
    assert _codes([_item(title)], from_item_category=True) == {
        ExtractionIssueCode.MERGED_ITEM_CATEGORY
    }


def test_repeated_title_parts_mean_nothing_when_a_schedule_gave_the_items() -> None:
    assert _codes([_item("Globe Valve 40, Globe Valve 50")]) == set()


def test_low_confidence_ocr_share_is_inclusive() -> None:
    at_limit = ItemFacts("1", "Globe valve", True, requirements=10, low_ocr_requirements=2)
    below = ItemFacts("1", "Globe valve", True, requirements=10, low_ocr_requirements=1)
    assert _codes([at_limit]) == {ExtractionIssueCode.LOW_CONFIDENCE_OCR}
    assert _codes([below]) == set()


def test_linked_files_that_could_not_be_fetched_mark_the_tender() -> None:
    quality = extraction_quality([_item()], False, RULES, unfetched=["https://x.invalid/spec.pdf"])
    [issue] = quality.reasons
    assert issue.code is ExtractionIssueCode.FILES_NOT_FETCHED
    assert "spec.pdf" in issue.reason


def test_every_reason_is_reported() -> None:
    items = [
        ItemFacts(
            "1",
            "Spare part shall, Spare part shall",
            False,
            requirements=60,
            low_ocr_requirements=60,
        )
    ]
    quality = extraction_quality(items, True, RULES, unfetched=["a.pdf"], spec_outside=100)
    # Nearly empty items and an oversized item exclude each other.
    expected = set(ExtractionIssueCode) - {ExtractionIssueCode.SPEC_OUTSIDE_ITEMS}
    assert {r.code for r in quality.reasons} == expected


def test_only_ocr_pages_below_the_confidence_are_low() -> None:
    pages = [
        ProcessedPage(1, "typed", ocr=False),
        ProcessedPage(2, "good scan", ocr=True, ocr_confidence=0.7),
        ProcessedPage(3, "bad scan", ocr=True, ocr_confidence=0.69),
        ProcessedPage(4, "not measured", ocr=True),
    ]
    assert low_confidence_pages("spec.pdf", pages, 0.7) == {("spec.pdf", 3), ("spec.pdf", 4)}


def _spec_codes(requirements: list[int], spec_outside: int) -> set[ExtractionIssueCode]:
    items = [_item(n=n) for n in requirements]
    return {
        r.code for r in extraction_quality(items, False, RULES, spec_outside=spec_outside).reasons
    }


def test_nearly_empty_items_with_the_spec_outside_them_mark_the_tender() -> None:
    # 7959356: one item with 1 requirement, the pump's spec rows among the general requirements.
    limit = RULES.min_spec_outside_items
    assert ExtractionIssueCode.SPEC_OUTSIDE_ITEMS in _spec_codes([1], limit)
    assert ExtractionIssueCode.SPEC_OUTSIDE_ITEMS in _spec_codes([RULES.nearly_empty_item], limit)


def test_the_reason_gives_both_counts() -> None:
    items = [_item(n=1)]
    [issue] = extraction_quality(items, False, RULES, spec_outside=64).reasons
    assert "at most 1 requirements while 64 lines" in issue.reason


def test_few_general_spec_lines_do_not_mark_the_tender() -> None:
    assert _spec_codes([1], RULES.min_spec_outside_items - 1) == set()


def test_one_item_with_real_requirements_clears_the_check() -> None:
    many = RULES.nearly_empty_item + 1
    assert _spec_codes([1, many], 100) == set()


def test_no_items_is_not_this_problem() -> None:
    assert _spec_codes([], 100) == set()
