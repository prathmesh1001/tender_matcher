"""Confidence of decided results. Documents, pages and results are synthetic."""

from app.matching.confidence import ConfidenceOptions, items_with_confidence, with_confidence
from app.matching.scorer import score
from app.schemas.enums import DecisionSource, DocType, MatchStatus, ReviewFlag
from app.schemas.matching import Comparison, Evidence, LabelBasis
from app.schemas.report import ItemReport, ManufacturerItemReport, TiedDocumentRows
from app.schemas.requirements import LineItem
from tests.conftest import make_test_settings

DATASHEET, BROCHURE = "d" * 64, "b" * 64
OPTIONS = ConfidenceOptions.from_settings(make_test_settings())
SCORES = {
    MatchStatus.FULL_MATCH: 1.0,
    MatchStatus.PARTIAL_MATCH: 0.5,
    MatchStatus.MISSING_CAPABILITY: 0.0,
}


class _Facts:
    def __init__(self, ocr_pages: frozenset[tuple[str, int]] = frozenset()) -> None:
        self.ocr_pages = ocr_pages

    def doc_type(self, sha256: str) -> DocType | None:
        return {DATASHEET: DocType.DATASHEET, BROCHURE: DocType.BROCHURE}.get(sha256)

    def page_ocr(self, sha256: str, page_number: int) -> bool:
        return (sha256, page_number) in self.ocr_pages


def _evidence(sha256: str = DATASHEET, page: int = 3) -> Evidence:
    return Evidence(
        document="x.pdf",
        sha256=sha256,
        title="X",
        page_number=page,
        label="Body",
        value="WCB",
        quote="Body WCB",
        quote_parts=["Body WCB"],
        status=MatchStatus.FULL_MATCH,
    )


def _result(
    status: MatchStatus = MatchStatus.FULL_MATCH,
    decided_by: DecisionSource = DecisionSource.RULE,
    evidence: list[Evidence] | None = None,
    flags: list[ReviewFlag] | None = None,
    needs_review: bool = False,
) -> Comparison:
    return Comparison(
        requirement_id="10.1",
        status=status,
        needs_review=needs_review,
        decided_by=decided_by,
        basis=LabelBasis.ATTRIBUTE,
        reason="",
        evidence=[_evidence()] if evidence is None else evidence,
        flags=flags or [],
    )


def test_defaults_are_the_agreed_weights() -> None:
    assert (OPTIONS.llm, OPTIONS.brochure, OPTIONS.ocr) == (0.7, 0.85, 0.8)
    assert (OPTIONS.code_only, OPTIONS.flag, OPTIONS.review_below) == (0.8, 0.8, 0.75)


def test_a_rule_decision_on_a_datasheet_is_fully_trusted() -> None:
    result = with_confidence(_result(), _Facts(), OPTIONS)
    assert result.confidence == 1.0
    assert result.confidence_factors == []
    assert not result.needs_review


def test_an_llm_decision_falls_below_the_threshold() -> None:
    result = with_confidence(_result(decided_by=DecisionSource.LLM), _Facts(), OPTIONS)
    assert result.confidence == 0.7
    assert result.confidence_factors == ["LLM decision x0.7"]
    assert result.needs_review


def test_one_weakness_other_than_llm_stays_above_the_threshold() -> None:
    result = with_confidence(_result(evidence=[_evidence(BROCHURE)]), _Facts(), OPTIONS)
    assert result.confidence == 0.85
    assert not result.needs_review


def test_brochure_counts_only_when_every_citation_is_a_brochure() -> None:
    mixed = _result(evidence=[_evidence(BROCHURE), _evidence(DATASHEET)])
    assert with_confidence(mixed, _Facts(), OPTIONS).confidence == 1.0


def test_two_weaknesses_send_a_rule_decision_to_review() -> None:
    facts = _Facts(frozenset({(BROCHURE, 3)}))
    result = with_confidence(_result(evidence=[_evidence(BROCHURE)]), facts, OPTIONS)
    assert result.confidence == 0.68
    assert result.confidence_factors == ["brochure only x0.85", "OCR page x0.8"]
    assert result.needs_review


def test_rule_flags_count_once_together_and_code_only_on_its_own() -> None:
    flags = [ReviewFlag.NEAR_MISS, ReviewFlag.MISREAD, ReviewFlag.CODE_ONLY_HIT]
    result = with_confidence(_result(flags=flags), _Facts(), OPTIONS)
    assert result.confidence == 0.64
    assert result.confidence_factors == [
        "found only by a code x0.8",
        "rule flag: misread, near miss x0.8",
    ]


def test_undecided_results_get_no_confidence_and_keep_their_review() -> None:
    for status in (MatchStatus.UNSUPPORTED, MatchStatus.NOT_APPLICABLE):
        result = with_confidence(_result(status, needs_review=True), _Facts(), OPTIONS)
        assert result.confidence is None
        assert result.needs_review


def test_confidence_never_clears_an_existing_review() -> None:
    result = with_confidence(_result(needs_review=True), _Facts(), OPTIONS)
    assert result.confidence == 1.0
    assert result.needs_review


def test_items_get_confidence_on_results_and_tied_rows_and_rescored() -> None:
    llm = _result(decided_by=DecisionSource.LLM)
    report = ItemReport(
        item=LineItem(
            item_no="10", title="Valve", description_lines=[], document="b.pdf", page_number=1
        ),
        category="valves",
        product_type="gate_valve",
        requirements=[],
        manufacturers=[
            ManufacturerItemReport(
                manufacturer="Acme",
                chosen=None,
                candidates=[],
                tied_rows=[TiedDocumentRows(document="x.pdf", results=[llm])],
                results=[llm],
                score=score([llm], SCORES),
            )
        ],
    )
    [out] = items_with_confidence([report], _Facts(), OPTIONS, SCORES)
    m = out.manufacturers[0]
    assert m.results[0].confidence == 0.7
    assert m.tied_rows[0].results[0].confidence == 0.7
    assert m.score.needs_review == 1


def test_a_low_score_hit_lowers_an_llm_decision_further() -> None:
    result = _result(decided_by=DecisionSource.LLM, flags=[ReviewFlag.LOW_SCORE_HIT])
    judged = with_confidence(result, _Facts(), OPTIONS)
    assert judged.confidence == 0.56
    assert judged.confidence_factors == ["LLM decision x0.7", "search score below threshold x0.8"]
    assert judged.needs_review
