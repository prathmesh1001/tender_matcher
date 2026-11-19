"""Values printed in catalogue fields: quantities (a number or range with a unit) and codes.

Pure text parsing, with or without an ontology. A quantity keeps the unit as printed
(folded: "mm", '"', "bar") or its designation prefix ("pn", "class", "dn"); the comparator
decides whether it can be converted. Bare numbers keep `unit=None` and are never compared
(CLAUDE.md B4).
"""

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from app.ontology.units import spell_fractions
from app.retrieval.text_normalizer import SearchVocabulary, fold_text, search_tokens, stem_token

_NUM: Final = r"-?\d+(?:\.\d+)?"
# A unit right after a number: '"', or a word starting with a letter or "°" ("mm", "m3/hr").
_UNIT: Final = r"""(?:"|''|(?!to\b|thru\b|through\b|and\b|or\b)[a-z°][a-z0-9°/²³]*)"""
# "50 to 600", "2" thru 12"" (Mueller), "50 - 600": one range, never a list of two values.
_RANGE_SEP: Final = r"\s*(?:to|thru|through|-|~)\s*"
# A size printed in both units, one in brackets: "50 mm (2")" or "2" (50 mm)". The mm value is
# kept, so "from 50 mm (2") to 600 mm (24")" is one range, not four sizes, and "up to 100"
# (2500 mm)" keeps 2500 mm even where 100" has no DN in the NPS table.
_INCH: Final = r"""(?:"|''|inch(?:es)?\b)"""
_MM_THEN_INCH: Final = re.compile(
    rf"(\d+(?:\.\d+)?\s*mm|\b(?:dn|nb)\s*-?\s*\d+)\s*\(\s*\d+(?:\.\d+)?\s*{_INCH}\s*\)"
)
_INCH_THEN_MM: Final = re.compile(rf"\d+(?:\.\d+)?\s*{_INCH}\s*\(\s*(\d+(?:\.\d+)?\s*mm)\s*\)")
# An edition or revision number ("5TH EDITION", "2nd"): never part of a code.
_ORDINAL: Final = re.compile(r"\b\d+(?:st|nd|rd|th)\b", re.IGNORECASE)
_NUMBER_VALUE: Final = re.compile(
    rf"(?<![\w.])(?P<a>{_NUM})\s*(?P<ua>{_UNIT})?(?:{_RANGE_SEP}(?P<b>{_NUM})\s*(?P<ub>{_UNIT})?)?"
)
_THOUSANDS: Final = re.compile(r"(?<=\d),(?=\d{3}\b)")
# An inch size printed as a fraction: 3/8" is 0.375 inch, never 8 inch; 1-1/2" is 1.5 inch.
_INCH_FRACTION: Final = re.compile(
    r"(?<![\w./-])(?:(?P<whole>\d+)\s*-\s*)?(?P<top>\d+)/(?P<bottom>[1-9]\d*)(?=\s*(?:\"|''|inch))"
)
# "up to 100" (2500 mm)": an upper limit with no printed lower end.
_UP_TO: Final = re.compile(r"\b(?:up\s*to|upto)\s*$")
_WORD: Final = re.compile(r"[a-z0-9_]+")
# "a 216" -> "a216", "ss-316" -> "ss316": a short letter run joined to the number after it.
_LETTERS_NUMBER: Final = re.compile(r"\b([a-z]{1,4})[\s-]+(?=\d)")


@dataclass(frozen=True, slots=True)
class Quantity:
    """A printed number or range: `low == high` for a single value, `low = -inf` for "up to"."""

    low: float
    high: float
    unit: str | None
    prefixed: bool = False  # the unit is a designation written before the number

    @property
    def is_open(self) -> bool:
        """True for an upper limit without a printed lower end ("up to 2500 mm")."""
        return math.isinf(self.low)


@lru_cache(maxsize=16)
def _prefix_pattern(prefixes: tuple[str, ...]) -> re.Pattern[str]:
    """Pattern for designations: "PN 10/16", "Class 150 to 1500", "DN50 - DN600"."""
    names = "|".join(re.escape(p) for p in sorted(prefixes, key=len, reverse=True))
    return re.compile(
        rf"\b(?P<p>{names})\s*#?\s*(?P<a>\d+(?:\.\d+)?)"
        rf"(?:{_RANGE_SEP}(?:(?P=p)\s*#?\s*)?(?P<b>\d+(?:\.\d+)?)"
        rf"|(?P<more>(?:\s*[/,&]\s*\d+(?:\.\d+)?)+))?"
    )


def _prefixed(text: str, prefixes: tuple[str, ...]) -> tuple[list[Quantity], str]:
    """Designation quantities, and the text with them blanked out."""
    found: list[Quantity] = []
    for m in _prefix_pattern(prefixes).finditer(text):
        unit, a = m.group("p"), float(m.group("a"))
        if m.group("b"):
            found.append(Quantity(a, float(m.group("b")), unit, prefixed=True))
            continue
        found.append(Quantity(a, a, unit, prefixed=True))
        for extra in re.findall(r"\d+(?:\.\d+)?", m.group("more") or ""):
            found.append(Quantity(float(extra), float(extra), unit, prefixed=True))
    blanked = _prefix_pattern(prefixes).sub(lambda m: " " * len(m.group(0)), text)
    return found, blanked


def _decimal_inches(match: re.Match[str]) -> str:
    """A printed inch fraction as a decimal number."""
    whole = int(match.group("whole") or 0)
    return f"{whole + int(match.group('top')) / int(match.group('bottom')):g}"


def _joined_units(text: str, unit_phrases: Sequence[str]) -> str:
    """Multi-word unit spellings right after a number written as one word ("degc")."""
    for phrase in sorted(unit_phrases, key=len, reverse=True):
        pattern = rf"(?<=\d)(\s*){re.escape(phrase)}(?![a-z0-9])"
        joined = phrase.replace(" ", "").replace("\\", "\\\\")
        text = re.sub(pattern, rf"\g<1>{joined}", text)
    return text


def quantities(
    text: str, prefixes: Sequence[str], unit_phrases: Sequence[str] = ()
) -> list[Quantity]:
    """Every quantity printed in `text`, in order (designations first).

    Args:
        text: A field value as printed.
        prefixes: Designations written before their number ("pn", "class", "dn").
        unit_phrases: Folded multi-word unit spellings ("degree celsius", "deg c"); after a
            number they become one unit word with the spaces removed ("degreecelsius").

    Returns:
        The quantities. A range's unit is the unit printed with either end.
    """
    spelled = _joined_units(fold_text(spell_fractions(text)), unit_phrases)
    folded = _INCH_FRACTION.sub(_decimal_inches, _THOUSANDS.sub("", spelled))
    folded = _INCH_THEN_MM.sub(r"\1", _MM_THEN_INCH.sub(r"\1", folded))
    found, rest = _prefixed(folded, tuple(p.lower() for p in prefixes))
    open_end = -1  # end of the last "up to" value: its bracketed equivalent is open too
    for m in _NUMBER_VALUE.finditer(rest):
        a = float(m.group("a"))
        before = rest[: m.start()]
        bracketed = open_end >= 0 and rest[open_end : m.start()].strip() == "("
        if m.group("b") is None and (_UP_TO.search(before) or bracketed):
            found.append(Quantity(-math.inf, a, m.group("ua")))
            open_end = m.end()
        elif m.group("b") is None:
            found.append(Quantity(a, a, m.group("ua")))
        else:
            b = float(m.group("b"))
            found.append(Quantity(min(a, b), max(a, b), m.group("ub") or m.group("ua")))
    return found


def compact_codes(text: str, noise: Iterable[str] = ()) -> str:
    """Fold and join short letter runs to the number after them ("A 216" -> "a216").

    Noise words are not joined ("to 600" stays two words).
    """
    skip = frozenset(noise)

    def join(m: re.Match[str]) -> str:
        return m.group(0) if m.group(1) in skip else m.group(1)

    return _LETTERS_NUMBER.sub(join, fold_text(text))


def code_tokens(
    text: str, noise: frozenset[str], vocabulary: SearchVocabulary | None
) -> frozenset[str]:
    """Stemmed tokens of a code value, noise words removed.

    With a vocabulary the ontology's code rules apply ("ASTM A216 Gr. WCB" -> a216_wcb);
    without one the folded words stay ("a216", "wcb").

    Args:
        text: A value as printed.
        noise: Stemmed words to drop ("astm", "gr").
        vocabulary: Ontology code rules, or None.

    Returns:
        The tokens.
    """
    compacted = compact_codes(_ORDINAL.sub(" ", text), noise)
    raw = search_tokens(compacted, vocabulary) if vocabulary else _WORD.findall(compacted)
    return frozenset(t for t in (stem_token(r) for r in raw) if t not in noise)
