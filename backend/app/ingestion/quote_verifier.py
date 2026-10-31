"""Check that a quote is printed on its cited page (CLAUDE.md section 6: fuzzy match >= 0.9).

Both sides are normalized the same way: unicode folding (ligatures, curly quotes, dashes),
lower case, and every run of whitespace as one space, so a cell wrapped over two printed
lines still matches. The score is a partial ratio: the best `difflib` similarity between the
quote and a window of the page of the same length, with windows aligned on exact 4-grams
the two share. Numbers and codes are not fuzzy: every one in the quote must appear as a
whole token in the window, so a ratio >= 0.9 never hides "175 psi" printed as "125 psi".
Pure functions, standard library only.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Final

from app.retrieval.text_normalizer import fold_text, fold_unicode

_SPACE: Final = re.compile(r"\s+")
# Window anchors: exact character n-grams shared by the quote and the page.
_NGRAM: Final = 4
# Punctuation around a number or code ("(80", "WCB/", "1500,") is not part of it.
_EDGE_PUNCTUATION: Final = "()[]{},;:.!?/|\"'*"
# "WCB", "LCC", "EPDM": capitals this long count as a code; "A" or "B" alone are words.
_MIN_CAPS_CODE: Final = 2


def normalize_quote(text: str) -> str:
    """Folded, lower-cased text with single spaces."""
    return _SPACE.sub(" ", fold_text(text)).strip()


def exact_tokens(text: str) -> tuple[str, ...]:
    """Numbers and codes of a quote, normalized: they must be on the page exactly.

    A token with a digit ("150", "a216", "60-40-18", "16.34") or an all-capitals code of
    letters ("WCB", "EPDM") as printed. Fuzzy matching may forgive a word's spacing, case or
    OCR noise; it never forgives "Class 150" for "Class 300".

    Args:
        text: A verbatim quote piece.

    Returns:
        The tokens, lower-cased, without surrounding punctuation.
    """
    out: list[str] = []
    for raw in fold_unicode(text).split():
        token = raw.strip(_EDGE_PUNCTUATION)
        if any(ch.isdigit() for ch in token) or (
            len(token) >= _MIN_CAPS_CODE and token.isalpha() and token.isupper()
        ):
            out.append(token.lower())
    return tuple(out)


def _has_tokens(window: str, tokens: Sequence[str]) -> bool:
    """True if every token is a whole token of the window (not part of a longer one)."""
    words = {w.strip(_EDGE_PUNCTUATION) for w in window.split()}
    return all(t in words for t in tokens)


def partial_ratio(needle: str, haystack: str, exact: Sequence[str] = (), slack: int = 0) -> float:
    """Best similarity (0..1) of `needle` against any same-length window of `haystack`.

    Args:
        needle: Normalized quote.
        haystack: Normalized page text.
        exact: Normalized tokens that must appear whole in the window (see `exact_tokens`).
        slack: Characters the window may grow by on each side when looking for them: the
            fuzzy match forgives a few inserted characters (a label printed inside a wrapped
            cell), which shift the last tokens past a same-length window.

    Returns:
        1.0 for an exact substring; 0.0 if either side is empty or no window holds the
        exact tokens.
    """
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    if len(needle) >= len(haystack):
        if not _has_tokens(haystack, exact):
            return 0.0
        return SequenceMatcher(None, needle, haystack, autojunk=False).ratio()
    best = 0.0
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(needle)
    for start in _window_starts(needle, haystack):
        end = start + len(needle)
        matcher.set_seq1(haystack[start:end])
        if matcher.quick_ratio() > best and _has_tokens(
            _widened(haystack, start - slack, end + slack), exact
        ):
            best = max(best, matcher.ratio())
    return best


def _widened(text: str, start: int, end: int) -> str:
    """`text[start:end]` (clamped) widened to whole tokens: a window may cut its edge ones."""
    start, end = max(start, 0), min(end, len(text))
    while start > 0 and text[start - 1] != " ":
        start -= 1
    while end < len(text) and text[end] != " ":
        end += 1
    return text[start:end]


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
        slack = math.ceil(len(piece) * (1 - threshold))
        score = partial_ratio(piece, page, exact_tokens(raw), slack)
        if score < worst:
            worst = score
        if score < threshold and failed is None:
            failed = raw
    return QuoteCheck(verified=failed is None, score=worst, failed_part=failed)
