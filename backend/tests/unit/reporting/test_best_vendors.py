"""The tender's best vendors list (user decisions A1/B1/C1)."""

from datetime import UTC, datetime

from app.matching.scorer import NAME_ONLY_REASON
from app.reporting.best_vendors import best_vendors
from app.schemas.enums import VendorMatchLevel, VendorSource
from app.schemas.report import (
    GemRecommendation,
    ItemReport,
    ItemVendor,
    Score,
    TenderReport,
)
from app.schemas.requirements import LineItem

LEVEL = VendorMatchLevel


def _vendor(
    name: str,
    level: VendorMatchLevel,
    pct: float | None = 100.0,
    source: VendorSource = VendorSource.CATALOGUE,
    reason: str = "",
) -> ItemVendor:
    score = Score(
        compatibility_pct=pct, coverage_pct=50.0, decided=1, total=2, counts={}, needs_review=0
    )
    return ItemVendor(
        rank=1,
        manufacturer=name,
        score=score,
        low_coverage=False,
        source=source,
        document=f"{name} doc",
        match_level=level,
        level_reason=reason,
    )


def _item(no: str, vendors: list[ItemVendor]) -> ItemReport:
    line = LineItem(
        item_no=no,
        title=f"Item {no}",
        description_lines=[],
        description_header="",
        page_number=1,
        document="bid.pdf",
    )
    return ItemReport(
        item=line,
        category=None,
        product_type=None,
        requirements=[],
        manufacturers=[],
        top_vendors=vendors,
    )


def _report(items: list[ItemReport], recs: list[GemRecommendation] | None = None) -> TenderReport:
    return TenderReport(
        tender="GEM_2026_B_1",
        bid_number="GEM/2026/B/1",
        tender_files=["bid.pdf"],
        generated_at=datetime(2026, 11, 22, tzinfo=UTC),
        ontology_used=True,
        items=items,
        ranking=[],
        recommended_gem_vendors=recs or [],
    )


def _rec(brand: str) -> GemRecommendation:
    return GemRecommendation(
        brand=brand,
        product=f"{brand} Spares for Motor Vehicles",
        url=f"https://mkp.gem.gov.in/oem-spares/{brand}/p-1-1-cat.html",
        category="OEM Spares for Automobiles",
    )


def test_full_then_partial_best_first_counted_over_items() -> None:
    report = _report(
        [
            _item("1", [_vendor("KSB", LEVEL.FULL_MATCH), _vendor("DAB", LEVEL.PARTIAL_MATCH, 60)]),
            _item("2", [_vendor("KSB", LEVEL.PARTIAL_MATCH, 50), _vendor("L&T", LEVEL.FULL_MATCH)]),
        ]
    )
    best = best_vendors(report, min_count=3, max_count=10)
    assert [(v.rank, v.name, v.match_level) for v in best] == [
        (1, "KSB", LEVEL.FULL_MATCH),  # full on one item, partial on another
        (2, "L&T", LEVEL.FULL_MATCH),
        (3, "DAB", LEVEL.PARTIAL_MATCH),
    ]
    assert (best[0].items_full, best[0].items_partial, best[0].items_listed) == (1, 1, 2)


def test_fewer_than_three_matches_are_filled_with_their_true_level() -> None:
    report = _report(
        [
            _item(
                "1",
                [
                    _vendor("KSB", LEVEL.PARTIAL_MATCH, 50),
                    _vendor("Kirloskar", LEVEL.NEEDS_REVIEW, 100),
                    _vendor("DAB", LEVEL.DOES_NOT_MEET, 0),
                ],
            )
        ],
        recs=[_rec("Mahindra")],
    )
    best = best_vendors(report, min_count=3, max_count=10)
    # never raised: needs review stays needs review; the buyer-category seller is not added
    assert [(v.name, v.match_level) for v in best] == [
        ("KSB", LEVEL.PARTIAL_MATCH),
        ("Kirloskar", LEVEL.NEEDS_REVIEW),
        ("DAB", LEVEL.DOES_NOT_MEET),
    ]


def test_more_than_the_minimum_matched_are_all_shown_up_to_the_maximum() -> None:
    vendors = [_vendor(f"M{n}", LEVEL.PARTIAL_MATCH, 50 + n) for n in range(5)]
    report = _report([_item("1", [*vendors, _vendor("X", LEVEL.NEEDS_REVIEW)])])
    assert len(best_vendors(report, min_count=3, max_count=10)) == 5  # no filler needed
    assert len(best_vendors(report, min_count=3, max_count=4)) == 4


def test_a_name_only_match_goes_after_a_decided_partial_match() -> None:
    gem = VendorSource.GEM_LISTING
    report = _report(
        [
            _item(
                "1",
                [
                    _vendor("Champion", LEVEL.PARTIAL_MATCH, None, gem, NAME_ONLY_REASON),
                    _vendor("DENSO", LEVEL.PARTIAL_MATCH, 40, gem),
                ],
            )
        ]
    )
    best = best_vendors(report, min_count=3, max_count=10)
    assert [v.name for v in best] == ["DENSO", "Champion"]
    assert best[1].level_reason == NAME_ONLY_REASON


def test_buyer_category_sellers_are_never_listed() -> None:
    gem = VendorSource.GEM_LISTING
    report = _report(
        [_item("1", [_vendor("Mahindra", LEVEL.PARTIAL_MATCH, 50, gem)])],
        recs=[_rec("Mahindra"), _rec("Tata Motors")],
    )
    names = [v.name for v in best_vendors(report, min_count=3, max_count=10)]
    assert names == ["Mahindra"]


def test_no_vendor_at_all_gives_an_empty_list() -> None:
    report = _report([_item("1", [])], recs=[_rec("Tata Motors")])
    assert best_vendors(report, min_count=3, max_count=10) == []
