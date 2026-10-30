"""Check that a quote is printed on its cited page (CLAUDE.md section 6: fuzzy match >= 0.9).

Both sides are normalized the same way: unicode folding (ligatures, curly quotes, dashes),
lower case, and every run of whitespace as one space, so a cell wrapped over two printed
lines still matches. The score is a partial ratio: the best `difflib` similarity between the
quote and a window of the page of the same length, with windows aligned on exact 4-grams
the two share. Pure functions, standard library only.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Final

from app.retrieval.text_normalizer import fold_text

_SPACE: Final = re.compile(r"\s+")
# Window anchors: exact character n-grams shared by the quote and the page.
_NGRAM: Final = 4


def normalize_quote(text: str) -> str:
    """Folded, lower-cased text with single spaces."""
    return _SPACE.sub(" ", fold_text(text)).strip()


def partial_ratio(needle: str, haystack: str) -> float:
    """Best similarity (0..1) of `needle` against any same-length window of `haystack`.

    Args:
        needle: Normalized quote.
        haystack: Normalized page text.

    Returns:
        1.0 for an exact substring; 0.0 if either side is empty.
    """
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    if len(needle) >= len(haystack):
        return SequenceMatcher(None, needle, haystack, autojunk=False).ratio()
    best = 0.0
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(needle)
    for start in _window_starts(needle, haystack):
        matcher.set_seq1(haystack[start : start + len(needle)])
        if matcher.quick_ratio() > best:
            best = max(best, matcher.ratio())
    return best


def _window_starts(needle: str, haystack: str) -> list[int]:
    """Window starts that align an exact n-gram of the needle with the haystack.

    A quote that is >= 90% similar shares at least one exact n-gram with its place on the
    page, so every candidate window is found (needles shorter than the n-gram only match
    exactly, handled by the caller's substring check).
    """
    last = len(haystack) - len(needle)
    starts: set[int] = set()
    for offset in range(len(needle) - _NGRAM + 1):
        gram = needle[offset : offset + _NGRAM]
        at = haystack.find(gram)
        while at >= 0:
            starts.add(min(max(at - offset, 0), last))
            at = haystack.find(gram, at + 1)
    return sorted(starts)


@dataclass(frozen=True, slots=True)
class QuoteCheck:
    """Result of verifying the pieces of one quote on one page."""

    verified: bool
    score: float  # the lowest piece score (every piece must be on the page)
    failed_part: str | None = None


def verify_quote(parts: Sequence[str], page_text: str | None, threshold: float) -> QuoteCheck:
    """Verify every printed piece of a quote (cells or lines) on the cited page.

    Args:
        parts: Verbatim pieces; empty ones are ignored.
        page_text: The cited page's text, or None if the page is unknown.
        threshold: Minimum partial ratio per piece.

    Returns:
        Verified only if the page exists, there is a piece, and every piece reaches it.
    """
    pieces = [normalize_quote(p) for p in parts if p.strip()]
    if page_text is None or not pieces:
        return QuoteCheck(verified=False, score=0.0, failed_part=parts[0] if parts else None)
    page = normalize_quote(page_text)
    worst, failed = 1.0, None
    for raw, piece in zip((p for p in parts if p.strip()), pieces, strict=True):
        score = partial_ratio(piece, page)
        if score < worst:
            worst = score
        if score < threshold and failed is None:
            failed = raw
    return QuoteCheck(verified=failed is None, score=worst, failed_part=failed)
