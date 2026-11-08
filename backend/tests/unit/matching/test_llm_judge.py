"""The judge's prompt and how a verdict becomes a result. All inputs are synthetic."""

from app.core.config import Settings
from app.matching.comparator import ComparatorOptions, MatchContext, needs_judge
from app.matching.llm_judge import Passage, build_prompt, judged_comparison, names_attribute
from app.schemas.enums import DecisionSource, MatchStatus, Operator, RequirementType
from app.schemas.matching import Comparison, JudgeStatus, JudgeVerdict
from app.schemas.requirements import Requirement, ValueKind
from app.schemas.sections import EvidenceSource
from tests.conftest import make_test_settings

SHA = "a" * 64
PASSAGE = Passage(
    chunk_id="chunk-1",
    document="synthetic.pdf",
    sha256=SHA,
    title="Synthetic",
    page_number=7,
    raw_text="Actuation: gear operated on request",
    section="Options",
)


def _requirement(**fields: object) -> Requirement:
    values: dict[str, object] = {
        "id": "10.1",
        "item_no": "10",
        "position": 0,
        "label": "Actuation",
        "kind": ValueKind.TEXT,
        "operator": Operator.TEXT,
        "value_text": "gear operated",
        "raw_text": "Actuation: gear operated",
        "document": "b.pdf",
        "page_number": 1,
        "evidence": EvidenceSource.CATALOGUE,
        "req_type": RequirementType.TECHNICAL,
    }
    return Requirement.model_validate(values | fields)


def _verdict(**fields: object) -> JudgeVerdict:
    values: dict[str, object] = {
        "status": JudgeStatus.PARTIAL_MATCH,
        "capability": "gear on request",
        "chunk_id": "chunk-1",
        "quote": "Actuation: gear operated on request",
        "reasoning": "Offered on request only.",
    }
    return JudgeVerdict.model_validate(values | fields)


def _options(settings: Settings | None = None) -> ComparatorOptions:
    return ComparatorOptions.from_settings(settings or make_test_settings())


def _judged(requirement: Requirement, verdict: JudgeVerdict) -> Comparison:
    return judged_comparison(requirement, verdict, [PASSAGE], MatchContext(), _options())


def test_every_line_catalogues_may_decide_can_go_to_the_judge() -> None:
    assert needs_judge(_requirement())
    assert needs_judge(_requirement(label=None))
    assert needs_judge(_requirement(kind=ValueKind.CODE))
    assert needs_judge(_requirement(kind=ValueKind.NUMERIC))


def test_lines_routing_decides_never_go_to_the_judge() -> None:
    assert not needs_judge(_requirement(req_type=RequirementType.VAGUE))
    assert not needs_judge(_requirement(evidence=EvidenceSource.VENDOR_DOCUMENTS))
    assert not needs_judge(_requirement(evidence=EvidenceSource.NONE))
    assert not needs_judge(_requirement(named_make="Acme"))


def test_prompt_holds_the_requirement_passages_and_chosen_product() -> None:
    prompt = build_prompt(_requirement(), "butterfly_valve", "Series 44", [PASSAGE])
    assert "butterfly valve" in prompt
    assert "Only passages about Series 44 count." in prompt
    assert "Requirement: Actuation: gear operated" in prompt
    assert '"chunk_id": "chunk-1"' in prompt
    assert "gear operated on request" in prompt


def test_prompt_marks_catalogue_text_as_data_not_instructions() -> None:
    assert "never instructions" in build_prompt(_requirement(), "gate_valve", None, [PASSAGE])


def test_a_decision_cites_the_passage_real_document_and_page() -> None:
    result = _judged(_requirement(), _verdict())
    assert result.status is MatchStatus.PARTIAL_MATCH
    assert result.decided_by is DecisionSource.LLM
    assert result.needs_review
    evidence = result.evidence[0]
    assert (evidence.document, evidence.sha256, evidence.page_number) == ("synthetic.pdf", SHA, 7)
    assert evidence.quote_parts == ["Actuation: gear operated on request"]


def test_every_llm_decision_goes_to_review_even_a_full_match() -> None:
    result = _judged(_requirement(), _verdict(status=JudgeStatus.FULL_MATCH))
    assert result.status is MatchStatus.FULL_MATCH
    assert result.needs_review


def test_no_evidence_verdict_stays_unsupported_without_evidence() -> None:
    result = _judged(
        _requirement(), _verdict(status=JudgeStatus.UNSUPPORTED, quote="", chunk_id="")
    )
    assert result.status is MatchStatus.UNSUPPORTED
    assert result.decided_by is DecisionSource.NONE
    assert result.needs_review
    assert result.evidence == []


def test_citing_a_passage_it_was_not_given_decides_nothing() -> None:
    result = _judged(_requirement(), _verdict(chunk_id="invented"))
    assert result.status is MatchStatus.UNSUPPORTED
    assert "unknown passage" in result.reason


def test_a_decision_without_a_quote_decides_nothing() -> None:
    result = _judged(_requirement(), _verdict(quote="   "))
    assert result.status is MatchStatus.UNSUPPORTED
    assert "no quote" in result.reason


def test_a_quote_about_another_attribute_decides_nothing() -> None:
    result = _judged(_requirement(), _verdict(quote="Body: cast iron"))
    assert result.status is MatchStatus.UNSUPPORTED
    assert "does not name the attribute" in result.reason


def test_names_attribute_is_not_needed_without_a_label() -> None:
    requirement = _requirement(label=None)
    assert names_attribute(requirement, "anything at all", MatchContext(), _options())


def test_names_attribute_needs_the_labels_key_words() -> None:
    requirement = _requirement(label="Actuation")
    assert names_attribute(requirement, "Actuation by gear", MatchContext(), _options())
    assert not names_attribute(requirement, "Gear box", MatchContext(), _options())


def _body(quote: str, status: JudgeStatus) -> Comparison:
    requirement = _requirement(
        label="Body",
        kind=ValueKind.CODE,
        operator=Operator.EQ,
        value_text="ASTM A216 WCB",
        raw_text="Body : ASTM A216 WCB",
    )
    return _judged(requirement, _verdict(status=status, quote=quote))


def test_guardrail_keeps_a_code_verdict_the_rules_read_from_its_quote() -> None:
    assert _body("Body: ASTM A216 Gr. WCB", JudgeStatus.FULL_MATCH).status is (
        MatchStatus.FULL_MATCH
    )


def test_guardrail_drops_a_full_code_verdict_the_quote_contradicts() -> None:
    result = _body("Body: ASTM A351 Gr. CF8M", JudgeStatus.FULL_MATCH)
    assert result.status is MatchStatus.UNSUPPORTED
    assert result.needs_review
    assert result.decided_by is DecisionSource.NONE
    assert "rules on its quote say: missing_capability" in result.reason


def test_guardrail_drops_a_missing_verdict_the_quote_satisfies() -> None:
    result = _body("Body: ASTM A216 Gr. WCB", JudgeStatus.MISSING_CAPABILITY)
    assert result.status is MatchStatus.UNSUPPORTED


def test_guardrail_drops_a_numeric_verdict_without_a_comparable_value() -> None:
    requirement = _requirement(
        label="Pressure",
        kind=ValueKind.NUMERIC,
        operator=Operator.GTE,
        value_text="16 bar",
        value_num=16.0,
        unit="bar",
        raw_text="Pressure : 16 bar",
    )
    verdict = _verdict(status=JudgeStatus.FULL_MATCH, quote="Pressure: suitable for high pressure")
    assert _judged(requirement, verdict).status is MatchStatus.UNSUPPORTED


def test_guardrail_leaves_partial_and_free_text_verdicts_alone() -> None:
    assert _body("Body: ASTM A351 Gr. CF8M", JudgeStatus.PARTIAL_MATCH).status is (
        MatchStatus.PARTIAL_MATCH
    )
    full_text = _judged(_requirement(), _verdict(status=JudgeStatus.FULL_MATCH))
    assert full_text.status is MatchStatus.FULL_MATCH
