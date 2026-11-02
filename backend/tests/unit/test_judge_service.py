"""Retrieve -> judge -> verify over one item. The LLM, retrieval and pages are fakes."""

from collections.abc import Sequence

from pydantic import BaseModel

from app.core.exceptions import ExternalServiceError
from app.matching.comparator import ComparatorOptions, MatchContext
from app.matching.engine import ItemInput
from app.matching.llm_judge import JudgedItems, Passage
from app.matching.scorer import score
from app.schemas.enums import (
    DecisionSource,
    MatchStatus,
    Operator,
    RequirementType,
    ReviewFlag,
)
from app.schemas.matching import Comparison, JudgeStatus, JudgeVerdict, LabelBasis, SubTypeFit
from app.schemas.report import CandidateSummary, ItemReport, ManufacturerItemReport
from app.schemas.requirements import LineItem, Requirement, ValueKind
from app.schemas.retrieval import RetrievalOutcome, RetrievalRequest
from app.schemas.sections import EvidenceSource
from app.services.judge_service import FoundEvidence, JudgeOptions, JudgeService
from tests.conftest import make_test_settings

SHA = "a" * 64
QUOTE = "Actuation: gear operated"
SCORES = {
    MatchStatus.FULL_MATCH: 1.0,
    MatchStatus.PARTIAL_MATCH: 0.5,
    MatchStatus.MISSING_CAPABILITY: 0.0,
}
PASSAGE = Passage("chunk-1", "s.pdf", SHA, "Synthetic", 7, f"{QUOTE} as standard", "Options")


class _Llm:
    model_name = "fake-model"

    def __init__(self, verdict: JudgeVerdict | Exception | None = None) -> None:
        self.verdict = verdict or _verdict()
        self.prompts: list[str] = []

    def generate_json[T: BaseModel](self, prompt: str, schema: type[T]) -> T:
        self.prompts.append(prompt)
        if isinstance(self.verdict, Exception):
            raise self.verdict
        assert schema is JudgeVerdict
        return self.verdict  # type: ignore[return-value]


class _Finder:
    def __init__(self, found: FoundEvidence | Exception | None = None) -> None:
        self.found = found or FoundEvidence(RetrievalOutcome.EVIDENCE, (PASSAGE,))
        self.requests: list[RetrievalRequest] = []

    def find(self, request: RetrievalRequest) -> FoundEvidence:
        self.requests.append(request)
        if isinstance(self.found, Exception):
            raise self.found
        return self.found


class _Pages:
    def __init__(self, text: str = f"Options\n{QUOTE} as standard") -> None:
        self.text = text

    def page_text(self, sha256: str, page_number: int) -> str | None:
        return self.text if (sha256, page_number) == (SHA, 7) else None


def _verdict(**fields: object) -> JudgeVerdict:
    values: dict[str, object] = {
        "status": JudgeStatus.FULL_MATCH,
        "capability": "gear operated",
        "chunk_id": "chunk-1",
        "quote": QUOTE,
        "reasoning": "Stated as standard.",
    }
    return JudgeVerdict.model_validate(values | fields)


def _requirement(
    req_id: str = "10.1", raw_text: str = "Actuation: gear", **fields: object
) -> Requirement:
    values: dict[str, object] = {
        "id": req_id,
        "item_no": "10",
        "position": 0,
        "label": "Actuation",
        "kind": ValueKind.TEXT,
        "operator": Operator.TEXT,
        "value_text": "gear",
        "raw_text": raw_text,
        "document": "b.pdf",
        "page_number": 1,
        "evidence": EvidenceSource.CATALOGUE,
        "req_type": RequirementType.TECHNICAL,
    }
    return Requirement.model_validate(values | fields)


def _open(requirement: Requirement, status: MatchStatus = MatchStatus.UNSUPPORTED) -> Comparison:
    return Comparison(
        requirement_id=requirement.id,
        status=status,
        needs_review=True,
        decided_by=DecisionSource.NONE,
        basis=LabelBasis.NONE,
        reason="free text: needs the LLM judge",
    )


def _chosen() -> CandidateSummary:
    return CandidateSummary(
        document="s.pdf",
        sha256=SHA,
        title="Synthetic",
        sub_type_fit=SubTypeFit.UNKNOWN,
        fit_score=0.0,
        decided=0,
    )


def _inputs(
    requirements: Sequence[Requirement], chosen: bool = True, category: str = "valves"
) -> tuple[list[ItemInput], list[ItemReport]]:
    results = [_open(r) for r in requirements]
    report = ItemReport(
        item=LineItem(
            item_no="10", title="Gate valve", description_lines=[], document="b.pdf", page_number=1
        ),
        category=category or None,
        product_type="gate_valve",
        requirements=list(requirements),
        manufacturers=[
            ManufacturerItemReport(
                manufacturer="Acme",
                chosen=_chosen() if chosen else None,
                candidates=[],
                results=results,
                score=score(results, SCORES),
            )
        ],
    )
    entry = ItemInput(report.item, list(requirements), MatchContext())
    return [entry], [report]


def _service(
    llm: _Llm, finder: _Finder, max_calls: int = 10, max_failures: int = 2
) -> JudgeService:
    return JudgeService(
        llm,
        finder,
        JudgeOptions(
            comparator=ComparatorOptions.from_settings(make_test_settings()),
            status_scores=SCORES,
            quote_threshold=0.9,
            max_calls=max_calls,
            max_failures=max_failures,
        ),
    )


def _results(judged: JudgedItems) -> list[Comparison]:
    return judged.reports[0].manufacturers[0].results


def test_a_verified_verdict_becomes_a_scored_llm_decision() -> None:
    inputs, reports = _inputs([_requirement()])
    judged = _service(_Llm(), _Finder()).judge_items(inputs, reports, _Pages())
    result = _results(judged)[0]
    assert result.status is MatchStatus.FULL_MATCH
    assert result.decided_by is DecisionSource.LLM
    assert result.needs_review
    assert result.evidence[0].verified is True
    assert judged.reports[0].manufacturers[0].score.compatibility_pct == 100.0
    assert judged.decided_by == "rules + fake-model (1 calls, 0 cached, 0 failed)"


def test_search_stays_inside_the_chosen_catalogue() -> None:
    finder = _Finder()
    inputs, reports = _inputs([_requirement()])
    _service(_Llm(), finder).judge_items(inputs, reports, _Pages())
    assert finder.requests[0].document_sha256 == SHA
    assert finder.requests[0].manufacturer == "Acme"
    assert finder.requests[0].product_type == "gate_valve"


def test_no_evidence_hit_skips_the_llm() -> None:
    llm = _Llm()
    finder = _Finder(FoundEvidence(RetrievalOutcome.BELOW_THRESHOLD))
    inputs, reports = _inputs([_requirement()])
    judged = _service(llm, finder).judge_items(inputs, reports, _Pages())
    assert llm.prompts == []
    assert _results(judged)[0].status is MatchStatus.UNSUPPORTED
    assert "below_threshold" in _results(judged)[0].reason
    assert judged.decided_by == "rules"


def test_a_quote_not_on_the_page_is_downgraded() -> None:
    inputs, reports = _inputs([_requirement()])
    judged = _service(_Llm(), _Finder()).judge_items(inputs, reports, _Pages("Something else"))
    result = _results(judged)[0]
    assert result.status is MatchStatus.UNSUPPORTED
    assert result.needs_review
    assert "quote not found" in result.reason
    assert result.evidence[0].verified is False


def test_an_llm_failure_leaves_the_requirement_unsupported_and_the_run_alive() -> None:
    llm = _Llm(ExternalServiceError("Gemini call failed after retries"))
    inputs, reports = _inputs([_requirement()])
    judged = _service(llm, _Finder()).judge_items(inputs, reports, _Pages())
    assert _results(judged)[0].status is MatchStatus.UNSUPPORTED
    assert "LLM judge failed" in _results(judged)[0].reason
    assert judged.decided_by == "rules + fake-model (1 calls, 0 cached, 1 failed)"


def test_a_retrieval_failure_leaves_the_requirement_unsupported() -> None:
    llm = _Llm()
    inputs, reports = _inputs([_requirement()])
    finder = _Finder(ExternalServiceError("Cannot load embedding model"))
    judged = _service(llm, finder).judge_items(inputs, reports, _Pages())
    assert llm.prompts == []
    assert "Cannot load embedding model" in _results(judged)[0].reason


def test_repeated_failures_stop_the_calls() -> None:
    llm = _Llm(ExternalServiceError("HTTP 429"))
    lines = [_requirement(f"10.{i}", f"Actuation: line {i}") for i in range(5)]
    inputs, reports = _inputs(lines)
    judged = _service(llm, _Finder(), max_failures=2).judge_items(inputs, reports, _Pages())
    assert len(llm.prompts) == 2
    assert "too many failures" in _results(judged)[4].reason


def test_the_call_limit_is_respected() -> None:
    llm = _Llm()
    lines = [_requirement(f"10.{i}", f"Actuation: line {i}") for i in range(3)]
    inputs, reports = _inputs(lines)
    judged = _service(llm, _Finder(), max_calls=2).judge_items(inputs, reports, _Pages())
    assert len(llm.prompts) == 2
    assert _results(judged)[1].status is MatchStatus.FULL_MATCH
    assert "call limit" in _results(judged)[2].reason


def test_a_repeated_line_is_judged_once_under_each_id() -> None:
    llm = _Llm()
    inputs, reports = _inputs([_requirement("10.1"), _requirement("10.2")])
    judged = _service(llm, _Finder()).judge_items(inputs, reports, _Pages())
    assert len(llm.prompts) == 1
    assert [r.requirement_id for r in _results(judged)] == ["10.1", "10.2"]
    assert all(r.status is MatchStatus.FULL_MATCH for r in _results(judged))


def test_a_rule_result_is_left_alone() -> None:
    llm = _Llm()
    requirement = _requirement(kind=ValueKind.CODE)
    inputs, reports = _inputs([requirement])
    judged = _service(llm, _Finder()).judge_items(inputs, reports, _Pages())
    assert llm.prompts == []
    assert _results(judged) == reports[0].manufacturers[0].results


def test_a_tie_or_missing_catalogue_is_not_judged() -> None:
    llm = _Llm()
    inputs, reports = _inputs([_requirement()], chosen=False)
    judged = _service(llm, _Finder()).judge_items(inputs, reports, _Pages())
    assert llm.prompts == []
    assert judged.reports[0] == reports[0]
    assert judged.decided_by == "rules"


class _Cache:
    def __init__(self) -> None:
        self.stored: dict[str, JudgeVerdict] = {}

    def get(self, key: str) -> JudgeVerdict | None:
        return self.stored.get(key)

    def put(self, key: str, model: str, verdict: JudgeVerdict) -> None:
        self.stored[key] = verdict


def _cached_service(llm: _Llm, cache: _Cache) -> JudgeService:
    service = _service(llm, _Finder())
    return JudgeService(llm, _Finder(), service._options, cache)


def test_a_rerun_answers_from_the_cache_without_calling() -> None:
    cache, llm = _Cache(), _Llm()
    inputs, reports = _inputs([_requirement()])
    first = _cached_service(llm, cache).judge_items(inputs, reports, _Pages())
    second = _cached_service(llm, cache).judge_items(inputs, reports, _Pages())
    assert len(llm.prompts) == 1
    assert _results(second) == _results(first)
    assert second.decided_by == "rules + fake-model (0 calls, 1 cached, 0 failed)"


def test_a_failure_is_not_cached() -> None:
    cache = _Cache()
    inputs, reports = _inputs([_requirement()])
    _cached_service(_Llm(ExternalServiceError("HTTP 429")), cache).judge_items(
        inputs, reports, _Pages()
    )
    assert cache.stored == {}


def test_another_model_or_prompt_gets_another_key() -> None:
    from app.matching.llm_judge import cache_key

    assert cache_key("prompt", "model-a") != cache_key("prompt", "model-b")
    assert cache_key("prompt", "model-a") != cache_key("prompt 2", "model-a")
    assert cache_key("prompt", "model-a") == cache_key("prompt", "model-a")


def test_low_score_passages_reach_the_judge_and_are_flagged() -> None:
    llm = _Llm()
    weak = Passage("chunk-1", "s.pdf", SHA, "Synthetic", 7, f"{QUOTE} as standard", low_score=True)
    finder = _Finder(FoundEvidence(RetrievalOutcome.BELOW_THRESHOLD, (weak,)))
    inputs, reports = _inputs([_requirement()])
    judged = _service(llm, finder).judge_items(inputs, reports, _Pages())
    result = _results(judged)[0]
    assert len(llm.prompts) == 1
    assert result.status is MatchStatus.FULL_MATCH
    assert ReviewFlag.LOW_SCORE_HIT in result.flags
    assert result.needs_review


def test_a_low_score_quote_off_the_page_stays_unsupported() -> None:
    weak = Passage("chunk-1", "s.pdf", SHA, "Synthetic", 7, f"{QUOTE} as standard", low_score=True)
    finder = _Finder(FoundEvidence(RetrievalOutcome.BELOW_THRESHOLD, (weak,)))
    inputs, reports = _inputs([_requirement()])
    judged = _service(_Llm(), finder).judge_items(inputs, reports, _Pages("Something else"))
    assert _results(judged)[0].status is MatchStatus.UNSUPPORTED
    assert _results(judged)[0].needs_review
