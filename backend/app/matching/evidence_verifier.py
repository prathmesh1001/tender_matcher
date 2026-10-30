"""Re-check every cited quote against its page (CLAUDE.md section 7, evidence_verifier).

Each evidence row's printed pieces are verified on the cited page. A decision keeps only
verified evidence; if none of its evidence verifies, it is downgraded to `unsupported` +
needs_review, with the failed rows kept (marked unverified) so a reviewer can look.
"""

from typing import Protocol

from app.ingestion.quote_verifier import verify_quote
from app.schemas.enums import DecisionSource, MatchStatus
from app.schemas.matching import Comparison, Evidence


class PageTexts(Protocol):
    """Anything that returns a document page's text (a `ChunkSource` does)."""

    def page_text(self, sha256: str, page_number: int) -> str | None:
        """Text of one page, or None if the page is unknown."""
        ...


def verify_evidence(evidence: Evidence, pages: PageTexts, threshold: float) -> Evidence:
    """The evidence row with `verified` and `match_score` set."""
    check = verify_quote(
        evidence.quote_parts, pages.page_text(evidence.sha256, evidence.page_number), threshold
    )
    return evidence.model_copy(update={"verified": check.verified, "match_score": check.score})


def verify_comparison(comparison: Comparison, pages: PageTexts, threshold: float) -> Comparison:
    """Verify a comparison's evidence; downgrade a decision none of whose quotes verify.

    Args:
        comparison: A comparator result.
        pages: Page text lookup.
        threshold: Minimum fuzzy score (config `QUOTE_MATCH_THRESHOLD`).

    Returns:
        The comparison with checked evidence. A decided status whose evidence all fails
        becomes `unsupported` + needs_review.
    """
    checked = [verify_evidence(e, pages, threshold) for e in comparison.evidence]
    if comparison.status is MatchStatus.UNSUPPORTED:
        return comparison.model_copy(update={"evidence": checked})
    kept = [e for e in checked if e.verified]
    if kept:
        return comparison.model_copy(update={"evidence": kept})
    return comparison.model_copy(
        update={
            "status": MatchStatus.UNSUPPORTED,
            "needs_review": True,
            "decided_by": DecisionSource.NONE,
            "reason": f"quote not found on the cited page (was {comparison.status.value})",
            "evidence": checked,
        }
    )
