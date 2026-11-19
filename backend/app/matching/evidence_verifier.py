"""Re-check every cited quote against its page (CLAUDE.md section 7, evidence_verifier).

Each evidence row's printed pieces are verified on the cited page. A decision keeps only
verified evidence; if none of its evidence verifies, it is downgraded to `unsupported` +
needs_review, with the failed rows kept (marked unverified) so a reviewer can look.

A table cell printed on two lines with other columns between them ("0.37 to 22 kW" ...
"(0.5 to 30 HP)") is not one run on the page. Such a piece also verifies when it splits into
at most `QuoteSplit.max_pieces` consecutive pieces of at least `min_words` words, each found
verbatim on the page; every piece is still printed there, so nothing is invented.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import combinations, pairwise
from typing import Protocol

from app.ingestion.quote_verifier import QuoteCheck, verify_quote
from app.schemas.enums import DecisionSource, MatchStatus
from app.schemas.matching import Comparison, Evidence


class PageTexts(Protocol):
    """Anything that returns a document page's text (a `ChunkSource` does)."""

    def page_text(self, sha256: str, page_number: int) -> str | None:
        """Text of one page, or None if the page is unknown."""
        ...


@dataclass(frozen=True, slots=True)
class QuoteSplit:
    """How a piece printed on several lines may be split (config QUOTE_SPLIT_*)."""

    max_pieces: int
    min_words: int
    max_words: int  # longer pieces are not split (the number of splits grows fast)


def _splits(words: list[str], split: QuoteSplit) -> Iterator[list[str]]:
    """Every way to cut the words into 2..max_pieces consecutive pieces of min_words or more."""
    n = len(words)
    for pieces in range(2, split.max_pieces + 1):
        for cuts in combinations(range(1, n), pieces - 1):
            bounds = [0, *cuts, n]
            if all(b - a >= split.min_words for a, b in pairwise(bounds)):
                yield [" ".join(words[a:b]) for a, b in pairwise(bounds)]


def _split_check(
    part: str, page: str | None, threshold: float, split: QuoteSplit
) -> QuoteCheck | None:
    """A verified check of `part` as consecutive pieces each printed on the page, or None."""
    words = part.split()
    if len(words) > split.max_words:
        return None
    for pieces in _splits(words, split):
        check = verify_quote(pieces, page, threshold)
        if check.verified:
            return check
    return None


def verify_evidence(
    evidence: Evidence, pages: PageTexts, threshold: float, split: QuoteSplit | None = None
) -> Evidence:
    """The evidence row with `verified` and `match_score` set."""
    page = pages.page_text(evidence.sha256, evidence.page_number)
    check = verify_quote(evidence.quote_parts, page, threshold)
    if not check.verified and split is not None and page is not None:
        checks = [
            verify_quote([p], page, threshold) if p.strip() else None for p in evidence.quote_parts
        ]
        resolved = [
            c if c is None or c.verified else _split_check(p, page, threshold, split)
            for p, c in zip(evidence.quote_parts, checks, strict=True)
        ]
        found = [c for c in resolved if c is not None]
        if len(found) == len([c for c in checks if c is not None]) and found:
            check = QuoteCheck(verified=True, score=min(c.score for c in found), failed_part=None)
    return evidence.model_copy(update={"verified": check.verified, "match_score": check.score})


def verify_comparison(
    comparison: Comparison,
    pages: PageTexts,
    threshold: float,
    split: QuoteSplit | None = None,
) -> Comparison:
    """Verify a comparison's evidence; downgrade a decision none of whose quotes verify.

    Args:
        comparison: A comparator result.
        pages: Page text lookup.
        threshold: Minimum fuzzy score (config `QUOTE_MATCH_THRESHOLD`).
        split: How a piece printed on several lines may be split; None: never.

    Returns:
        The comparison with checked evidence. A decided status whose evidence all fails
        becomes `unsupported` + needs_review.
    """
    checked = [verify_evidence(e, pages, threshold, split) for e in comparison.evidence]
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
