"""Scores and ranking. Comparisons are synthetic."""

import pytest

from app.matching.scorer import rank, score, top_vendors, vendor_level
from app.schemas.enums import DecisionSource, MatchStatus, VendorMatchLevel, VendorSource
from app.schemas.matching import Comparison, Evidence, LabelBasis, SubTypeFit
from app.schemas.report import CandidateSummary, ManufacturerItemReport

SCORES = {
    MatchStatus.FULL_MATCH: 1.0,
    MatchStatus.PARTIAL_MATCH: 0.5,
    MatchStatus.MISSING_CAPABILITY: 0.0,
}
FULL, PARTIAL = MatchStatus.FULL_MATCH, MatchStatus.PARTIAL_MATCH
MISSING, UNSUPPORTED = MatchStatus.MISSING_CAPABILITY, MatchStatus.UNSUPPORTED


def _c(status: MatchStatus, review: bool = False) -> Comparison:
    return Comparison(
        requirement_id="1.1",
        status=status,
        needs_review=review,
        decided_by=DecisionSource.RULE,
        basis=LabelBasis.ATTRIBUTE,
        reason="",
    )


def test_compatibility_excludes_unsupported_and_coverage_counts_it() -> None:
    s = score([_c(FULL), _c(PARTIAL, True), _c(MISSING), _c(UNSUPPORTED, True)], SCORES)
    assert s.compatibility_pct == pytest.approx(50.0)  # (1 + 0.5 + 0) / 3
    assert s.coverage_pct == pytest.approx(75.0)  # 3 of 4 decided
    assert (s.decided, s.total, s.needs_review) == (3, 4, 2)
    assert s.counts == {FULL: 1, PARTIAL: 1, MISSING: 1, UNSUPPORTED: 1}


def test_nothing_decided_has_no_compatibility() -> None:
    s = score([_c(UNSUPPORTED, True)] * 3, SCORES)
    assert (s.compatibility_pct, s.coverage_pct) == (None, 0.0)
    assert score([], SCORES).coverage_pct == 0.0


def test_ranking_by_compatibility_then_coverage_none_last() -> None:
    ranking = rank(
        {
            "Alpha": [_c(FULL), _c(UNSUPPORTED)],  # 100 %, coverage 50
            "Beta": [_c(FULL), _c(FULL)],  # 100 %, coverage 100
            "Gamma": [_c(UNSUPPORTED)],  # nothing decided
            "Delta": [_c(MISSING), _c(FULL)],  # 50 %
        },
        {"Alpha": 1, "Beta": 1, "Delta": 1},
        SCORES,
        0.0,
    )
    assert [(r.rank, r.manufacturer) for r in ranking] == [
        (1, "Beta"), (2, "Alpha"), (3, "Delta"), (4, "Gamma"),
    ]  # fmt: skip
    assert ranking[-1].items_with_catalogue == 0
    assert not any(r.low_coverage for r in ranking)


def test_low_coverage_ranks_after_the_others_and_is_marked() -> None:
    ranking = rank(
        {
            "Thin": [_c(FULL), _c(UNSUPPORTED), _c(UNSUPPORTED)],  # 100 %, coverage 33
            "Solid": [_c(MISSING), _c(FULL)],  # 50 %, coverage 100
            "Border": [_c(MISSING), _c(UNSUPPORTED)],  # 0 %, coverage exactly 50
            "Empty": [_c(UNSUPPORTED)],  # nothing decided
        },
        {},
        SCORES,
        50.0,
    )
    assert [(r.manufacturer, r.low_coverage) for r in ranking] == [
        ("Solid", False), ("Border", False), ("Thin", True), ("Empty", True),
    ]  # fmt: skip
    assert ranking[2].score.coverage_pct == pytest.approx(100 / 3)  # coverage is always reported


def test_everyone_below_the_minimum_still_ranks_by_compatibility() -> None:
    ranking = rank(
        {"Low": [_c(MISSING), _c(UNSUPPORTED)], "High": [_c(FULL), _c(UNSUPPORTED)]},
        {},
        SCORES,
        75.0,
    )
    assert [(r.manufacturer, r.low_coverage) for r in ranking] == [("High", True), ("Low", True)]


# ---------------------------------------------------------------- top vendors per item


def _vendor(
    name: str, statuses: list[MatchStatus], gem: bool = False, document: bool = True
) -> ManufacturerItemReport:
    results = [_c(s) for s in statuses]
    if gem:
        results = [
            r.model_copy(update={"evidence": [_gem_evidence()]}) if i == 0 else r
            for i, r in enumerate(results)
        ]
    chosen = CandidateSummary(
        document=f"{name}.pdf",
        sha256="0" * 64,
        title=f"{name} listing" if gem else f"{name} catalogue",
        sub_type_fit=SubTypeFit.UNKNOWN,
        seller_declared=gem,
        fit_score=0.0,
        decided=0,
    )
    return ManufacturerItemReport(
        manufacturer=name,
        chosen=chosen if document else None,
        candidates=[chosen] if document else [],
        results=results,
        score=score(results, SCORES),
    )


def _gem_evidence() -> Evidence:
    return Evidence(
        document="x.html",
        sha256="0" * 64,
        title="GeM listing",
        page_number=1,
        label="Material",
        value="SS316",
        quote="Material SS316",
        quote_parts=["Material", "SS316"],
        source_url="https://mkp.gem.gov.in/x/p-1-1-cat.html",
        seller_declared=True,
    )


def test_top_vendors_are_the_best_three_for_the_item() -> None:
    vendors = [
        _vendor("KSB", [FULL, FULL, PARTIAL]),  # 83 %, coverage 100
        _vendor("Kirloskar", [FULL, FULL]),  # 100 %
        _vendor("ZOLOTO", [FULL, MISSING], gem=True),  # 50 %, from a GeM listing
        _vendor("Lowara", [MISSING, MISSING]),  # 0 %
        _vendor("DAB", [UNSUPPORTED, UNSUPPORTED]),  # nothing decided: never listed
    ]
    top = top_vendors(vendors, min_coverage_pct=50.0, limit=3)
    assert [(v.rank, v.manufacturer) for v in top] == [
        (1, "Kirloskar"),
        (2, "KSB"),
        (3, "Lowara"),
        (1, "ZOLOTO"),  # GeM vendors are ranked on their own, after the catalogue ones
    ]
    gem = top[3]
    assert gem.source is VendorSource.GEM_LISTING
    assert gem.source_url == "https://mkp.gem.gov.in/x/p-1-1-cat.html"
    assert top[0].source is VendorSource.CATALOGUE
    assert top[0].source_url is None


def test_low_coverage_vendors_come_after_well_covered_ones() -> None:
    vendors = [
        _vendor("Narrow", [FULL, UNSUPPORTED, UNSUPPORTED, UNSUPPORTED]),  # 100 %, 25 % covered
        _vendor("Broad", [FULL, PARTIAL, MISSING, FULL]),  # 62.5 %, 100 % covered
    ]
    top = top_vendors(vendors, min_coverage_pct=50.0, limit=3)
    assert [v.manufacturer for v in top] == ["Broad", "Narrow"]
    assert top[1].low_coverage is True


def test_a_vendor_without_a_document_for_the_item_is_not_listed() -> None:
    vendors = [_vendor("NoDoc", [FULL], document=False), _vendor("KSB", [PARTIAL])]
    assert [v.manufacturer for v in top_vendors(vendors, 50.0, 3)] == ["KSB"]


def test_each_source_keeps_its_own_best_vendors() -> None:
    vendors = [
        _vendor("Alpha", [FULL, FULL], gem=True),
        _vendor("Beta", [FULL, PARTIAL], gem=True),
        _vendor("Gamma", [PARTIAL, PARTIAL], gem=True),
        _vendor("Zeta", [MISSING, PARTIAL]),
    ]
    top = top_vendors(vendors, min_coverage_pct=50.0, limit=2)
    assert [(v.rank, v.manufacturer, v.source) for v in top] == [
        (1, "Zeta", VendorSource.CATALOGUE),  # a weaker catalogue vendor is still listed
        (1, "Alpha", VendorSource.GEM_LISTING),
        (2, "Beta", VendorSource.GEM_LISTING),
    ]


def _with_fit(vendor: ManufacturerItemReport, fit: SubTypeFit) -> ManufacturerItemReport:
    assert vendor.chosen is not None
    chosen = vendor.chosen.model_copy(update={"sub_type_fit": fit})
    return vendor.model_copy(update={"chosen": chosen, "candidates": [chosen]})


def test_gem_listing_of_the_item_sub_type_goes_before_a_better_scored_other() -> None:
    # 8210284 "KNIFE EDGE GATE VALVE 150MM": a sluice valve at 50 % came before knife listings.
    sluice = _with_fit(_vendor("ajanta", [FULL, MISSING], gem=True), SubTypeFit.GENERAL)
    knife = _with_fit(_vendor("getway", [FULL, MISSING, MISSING], gem=True), SubTypeFit.MATCH)
    wedge = _with_fit(_vendor("XEN", [FULL, FULL], gem=True), SubTypeFit.MISMATCH)
    top = top_vendors([sluice, wedge, knife], min_coverage_pct=50.0, limit=3)
    assert [v.manufacturer for v in top] == ["getway", "ajanta", "XEN"]


# ---------------------------------------------------------------- match level per vendor

LEVEL = VendorMatchLevel


def _level(vendor: ManufacturerItemReport) -> tuple[VendorMatchLevel, str]:
    return vendor_level(vendor, min_coverage_pct=50.0)


def test_every_checked_requirement_met_by_rules_is_a_full_match() -> None:
    assert _level(_vendor("KSB", [FULL, FULL, UNSUPPORTED])) == (LEVEL.FULL_MATCH, "")


def test_a_mix_is_a_partial_match_even_though_partial_rows_are_flagged() -> None:
    vendor = _vendor("KSB", [FULL, MISSING, UNSUPPORTED])
    assert _level(vendor) == (LEVEL.PARTIAL_MATCH, "")
    flagged = _vendor("KSB", [FULL, FULL])
    flagged = flagged.model_copy(update={"results": [_c(FULL), _c(PARTIAL, review=True)]})
    flagged = flagged.model_copy(update={"score": score(flagged.results, SCORES)})
    assert _level(flagged) == (LEVEL.PARTIAL_MATCH, "")


def test_nothing_met_does_not_meet_the_spec() -> None:
    assert _level(_vendor("KSB", [MISSING, MISSING])) == (LEVEL.DOES_NOT_MEET, "")


def test_too_few_checked_needs_review_whatever_the_score() -> None:
    vendor = _vendor("KSB", [FULL, UNSUPPORTED, UNSUPPORTED, UNSUPPORTED])  # 100 %, 25 % checked
    assert _level(vendor) == (LEVEL.NEEDS_REVIEW, "only 1 of 4 requirements checked")


def test_an_ai_judge_decision_needs_review() -> None:
    vendor = _vendor("KSB", [FULL, FULL])
    judged = _c(FULL).model_copy(update={"decided_by": DecisionSource.LLM})
    vendor = vendor.model_copy(update={"results": [_c(FULL), judged]})
    assert _level(vendor) == (LEVEL.NEEDS_REVIEW, "1 decided by the AI judge")


def test_tied_catalogues_need_review() -> None:
    vendor = _vendor("KSB", [FULL]).model_copy(update={"tied_documents": ["a.pdf", "b.pdf"]})
    assert _level(vendor)[0] is LEVEL.NEEDS_REVIEW


def test_nothing_decided_is_not_checked() -> None:
    assert _level(_vendor("KSB", [UNSUPPORTED]))[0] is LEVEL.NOT_CHECKED


def test_top_vendors_carry_their_level() -> None:
    top = top_vendors([_vendor("KSB", [FULL, FULL]), _vendor("DAB", [FULL, MISSING])], 50.0, 6)
    assert [(v.manufacturer, v.match_level) for v in top] == [
        ("KSB", LEVEL.FULL_MATCH),
        ("DAB", LEVEL.PARTIAL_MATCH),
    ]


def test_a_gem_seller_with_tied_listings_stays_a_gem_vendor() -> None:
    # 8210284: BVI has two "Sluice Valve 100mm" listings nothing tells apart.
    bvi = _vendor("BVI", [FULL], gem=True)
    assert bvi.chosen is not None
    second = bvi.chosen.model_copy(update={"document": "BVI-2.pdf"})
    tied = bvi.model_copy(
        update={
            "chosen": None,
            "candidates": [bvi.chosen, second],
            "tied_documents": [bvi.chosen.document, second.document],
        }
    )
    [vendor] = top_vendors([tied], 50.0, 6)
    assert vendor.source is VendorSource.GEM_LISTING
    assert vendor.match_level is VendorMatchLevel.NEEDS_REVIEW  # tied: a reviewer picks one
