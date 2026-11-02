"""How far a decided result can be trusted (CLAUDE.md section 7, confidence).

Confidence starts at 1.0 and each weakness found in the facts multiplies it down: an LLM
decision, evidence only from brochures, an OCR page, a search hit accepted only by a code, a
rule flag (near miss, misread, unclear heading, tied catalogues). A weakness is counted only
when the facts show it, never guessed. Below the threshold the result goes to review.
`unsupported` and `not_applicable` decide nothing, so they get no confidence.

Conflicting documents would lower it too, but no conflicts are detected yet (`spec_facts`
is empty), so that factor never applies.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from math import prod
from typing import Protocol

from app.core.config import Settings
from app.matching.scorer import score
from app.schemas.enums import DecisionSource, DocType, MatchStatus
from app.schemas.matching import Comparison
from app.schemas.report import ItemReport, ManufacturerItemReport

_UNDECIDED = frozenset({MatchStatus.UNSUPPORTED, MatchStatus.NOT_APPLICABLE})


class SourceFacts(Protocol):
    """What the catalogues tell about a citation's source."""

    def doc_type(self, sha256: str) -> DocType | None:
        """The document's type, or None if unknown."""
        ...

    def page_ocr(self, sha256: str, page_number: int) -> bool:
        """True if the page's text came from OCR."""
        ...


@dataclass(frozen=True, slots=True)
class ConfidenceOptions:
    """The factor of each weakness and the review threshold (from config)."""

    llm: float
    brochure: float
    ocr: float
    code_only: float
    flag: float
    review_below: float

    @classmethod
    def from_settings(cls, settings: Settings) -> ConfidenceOptions:
        """Build options from application settings."""
        return cls(
            llm=settings.CONFIDENCE_LLM_FACTOR,
            brochure=settings.CONFIDENCE_BROCHURE_FACTOR,
            ocr=settings.CONFIDENCE_OCR_FACTOR,
            code_only=settings.CONFIDENCE_CODE_ONLY_FACTOR,
            flag=settings.CONFIDENCE_FLAG_FACTOR,
            review_below=settings.CONFIDENCE_REVIEW_THRESHOLD,
        )


def weaknesses(
    comparison: Comparison, facts: SourceFacts, options: ConfidenceOptions
) -> list[tuple[str, float]]:
    """The weaknesses of one decided result, each with its factor.

    Args:
        comparison: A decided result.
        facts: Document types and OCR pages.
        options: The factors.

    Returns:
        `(what, factor)` pairs, empty for a result with no weakness.
    """
    found: list[tuple[str, float]] = []
    if comparison.decided_by is DecisionSource.LLM:
        found.append(("LLM decision", options.llm))
    cited = comparison.evidence
    if cited and all(facts.doc_type(e.sha256) is DocType.BROCHURE for e in cited):
        found.append(("brochure only", options.brochure))
    if any(facts.page_ocr(e.sha256, e.page_number) for e in cited):
        found.append(("OCR page", options.ocr))
    flags = sorted(f.value for f in comparison.flags)
    if "code_only_hit" in flags:
        flags.remove("code_only_hit")
        found.append(("found only by a code", options.code_only))
    if flags:
        found.append((f"rule flag: {', '.join(f.replace('_', ' ') for f in flags)}", options.flag))
    return found


def with_confidence(
    comparison: Comparison, facts: SourceFacts, options: ConfidenceOptions
) -> Comparison:
    """The result with its confidence, sent to review if it is below the threshold.

    Args:
        comparison: Any result.
        facts: Document types and OCR pages.
        options: Factors and threshold.

    Returns:
        A copy; undecided results get no confidence.
    """
    if comparison.status in _UNDECIDED:
        return comparison.model_copy(update={"confidence": None, "confidence_factors": []})
    found = weaknesses(comparison, facts, options)
    value = round(prod(factor for _, factor in found), 4)
    return comparison.model_copy(
        update={
            "confidence": value,
            "confidence_factors": [f"{what} x{factor:g}" for what, factor in found],
            "needs_review": comparison.needs_review or value < options.review_below,
        }
    )


def _manufacturer(
    m: ManufacturerItemReport,
    facts: SourceFacts,
    options: ConfidenceOptions,
    status_scores: dict[MatchStatus, float],
) -> ManufacturerItemReport:
    """One manufacturer's results and tied rows with confidence; its score recounted."""
    results = [with_confidence(c, facts, options) for c in m.results]
    tied_rows = [
        row.model_copy(
            update={"results": [with_confidence(c, facts, options) for c in row.results]}
        )
        for row in m.tied_rows
    ]
    return m.model_copy(
        update={
            "results": results,
            "tied_rows": tied_rows,
            "score": score(results, status_scores),
        }
    )


def items_with_confidence(
    reports: Sequence[ItemReport],
    facts: SourceFacts,
    options: ConfidenceOptions,
    status_scores: dict[MatchStatus, float],
) -> list[ItemReport]:
    """Every item's results with confidence; review counts in the scores follow.

    Args:
        reports: Item reports, after every decision (rules and judge).
        facts: Document types and OCR pages.
        options: Factors and threshold.
        status_scores: Score of each decided status.

    Returns:
        Updated copies, same order.
    """
    return [
        r.model_copy(
            update={
                "manufacturers": [
                    _manufacturer(m, facts, options, status_scores) for m in r.manufacturers
                ]
            }
        )
        for r in reports
    ]
