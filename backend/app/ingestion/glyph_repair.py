r"""Repair text whose font maps glyphs to the wrong characters (CLAUDE.md C4, A3).

Some catalogues embed a font whose character codes are shifted by a fixed amount: the L&T API 600
catalogue prints "Working temperature" as "9QTMKPI\x02VGORGTCVWTG" (every character 30 below,
the space a control character). Its degree sign is a superscript "0", so "-29 °C" reads "-290C".

Both repairs only run on pages that show the fault, so every other page stays byte for byte:
- shifted text: control characters stand between printable characters, as a space does. The
  most common one is the shifted space, which gives the shift. A control character next to a
  line break or another control character is a symbol glyph or a bullet, not a space. Text in
  a span holding a control character is decoded; a token outside one ("2CTCOGVGT", a one-word
  header) only if it has no lowercase and decodes to a word (letters, lowercase after the
  first, enough vowels). Codes such as "ASTM" or "WCB" decode to non-letters and are kept.
- degree: the page has a superscript "0" or "O" span right before a span starting with C or F;
  there, that glyph glued to a number ("4270C", "-29O C") becomes a degree sign ("427°C").
"""

import itertools
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import pymupdf

from app.core.config import Settings

_CONTROL: Final = re.compile(r"[\x01-\x08\x0b\x0c\x0e-\x1f]")
_SPACE_LIKE: Final = re.compile(r"(?<=[!-~])([\x01-\x08\x0b\x0c\x0e-\x1f])(?=[!-~])")
_TOKEN: Final = re.compile(r"[^ \t\n\r]+")  # \s would split at \x0b and \x1c-\x1f
_GLUED_DEGREE: Final = re.compile(r"(\d)[0oO] ?([CF])\b")
_SUPERSCRIPT: Final = 1  # PyMuPDF span flag bit
_VOWELS: Final = frozenset("aeiouy")
_DEGREE_GLYPHS: Final = frozenset({"0", "o", "O"})

Box = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class RepairOptions:
    """When a page counts as shifted, and when a one-word token is decoded."""

    min_control_chars: int
    min_word_letters: int
    min_vowel_share: float

    @classmethod
    def from_settings(cls, settings: Settings) -> RepairOptions:
        """Build options from application settings."""
        return cls(
            min_control_chars=settings.SHIFTED_TEXT_MIN_CONTROL_CHARS,
            min_word_letters=settings.SHIFTED_WORD_MIN_LETTERS,
            min_vowel_share=settings.SHIFTED_WORD_MIN_VOWEL_SHARE,
        )


@dataclass(frozen=True, slots=True)
class PageRepair:
    """What to repair on one page: the shift and where shifted spans lie, and the degree sign."""

    shift: int | None
    degree: bool
    options: RepairOptions
    shifted_boxes: tuple[Box, ...] = ()

    @property
    def active(self) -> bool:
        """True if anything on the page needs repair."""
        return self.shift is not None or self.degree


def detect_shift(text: str, options: RepairOptions) -> int | None:
    """The shift of a page's text, from the control character standing in for the space.

    Args:
        text: The page's plain text.
        options: Repair options.

    Returns:
        The amount to add to each shifted character, or None if the page is not shifted.
    """
    controls = Counter(_SPACE_LIKE.findall(text))
    if not controls:
        return None
    space, count = controls.most_common(1)[0]
    if count < options.min_control_chars:
        return None
    shift = ord(" ") - ord(space)
    return shift if shift > 0 else None


def _raised_degree(span: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    raised = int(span.get("flags", 0)) & _SUPERSCRIPT
    glyph = str(span.get("text", "")).strip()
    return (
        bool(raised)
        and glyph in _DEGREE_GLYPHS
        and str(after.get("text", "")).lstrip()[:1]
        in {
            "C",
            "F",
        }
    )


def has_superscript_degree(lines: Iterable[Sequence[Mapping[str, Any]]]) -> bool:
    """True if a superscript "0"/"O" span comes right before a span starting with C or F.

    Args:
        lines: Each text line's spans, as in PyMuPDF's "dict" output.

    Returns:
        Whether the page prints its degree sign as a raised zero or letter O.
    """
    return any(_raised_degree(a, b) for spans in lines for a, b in itertools.pairwise(spans))


def _box(span: Mapping[str, Any]) -> Box:
    x0, y0, x1, y1 = span["bbox"]
    return float(x0), float(y0), float(x1), float(y1)


def page_repair(page: pymupdf.Page, options: RepairOptions) -> PageRepair:
    """Detect what one page needs repaired (see module doc)."""
    lines = [
        line["spans"]
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
    ]
    shift = detect_shift(page.get_text(), options)
    boxes: tuple[Box, ...] = ()
    if shift is not None:
        spans = (s for line in lines for s in line)
        boxes = tuple(_box(s) for s in spans if _CONTROL.search(str(s.get("text", ""))))
    return PageRepair(shift, has_superscript_degree(lines), options, boxes)


def _decode(token: str, shift: int) -> str:
    """Shift every character the font moved; others (ligature glyphs) stay as printed."""
    return "".join(chr(ord(c) + shift) if 0 < ord(c) + shift < 0x7F else c for c in token)


def _reads_as_word(original: str, decoded: str, options: RepairOptions) -> bool:
    """True if a token without control characters is a shifted word ("2CTCOGVGT")."""
    if any(c.islower() for c in original) or not decoded.isalpha():
        return False
    if any(c.isupper() for c in decoded[1:]) or len(decoded) < options.min_word_letters:
        return False
    vowels = sum(1 for c in decoded.lower() if c in _VOWELS)
    return vowels / len(decoded) >= options.min_vowel_share


def _repair_token(token: str, shift: int, options: RepairOptions) -> str:
    decoded = _decode(token, shift)
    if _CONTROL.search(token) or _reads_as_word(token, decoded, options):
        return decoded
    return token


def _degree(text: str, repair: PageRepair) -> str:
    return _GLUED_DEGREE.sub(r"\1°\2", text) if repair.degree else text


def repair_text(text: str, repair: PageRepair) -> str:
    """Repair one piece of a page's text (a block).

    Args:
        text: Text as extracted.
        repair: What the page needs (see `page_repair`).

    Returns:
        The repaired text; unchanged if the page needs nothing.
    """
    if repair.shift is not None:
        shift, options = repair.shift, repair.options
        text = _TOKEN.sub(lambda m: _repair_token(m.group(0), shift, options), text)
    return _degree(text, repair)


def repair_word(word: str, box: Sequence[Any], repair: PageRepair) -> str:
    """Repair one extracted word; inside a shifted span it is decoded whole.

    Word extraction splits at the shifted space, so "VQ" ("to") has no control character left;
    its position inside a span that had one says it is shifted.

    Args:
        word: The word as extracted.
        box: Its `x0, y0, x1, y1`.
        repair: What the page needs (see `page_repair`).

    Returns:
        The repaired word.
    """
    if repair.shift is not None:
        x, y = (float(box[0]) + float(box[2])) / 2, (float(box[1]) + float(box[3])) / 2
        if any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in repair.shifted_boxes):
            return _degree(_decode(word, repair.shift), repair)
    return repair_text(word, repair)
