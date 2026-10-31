"""Parse one requirement value: number + unit, code, or free text; operator; attribute.

Works with or without an ontology. With one, unit spellings resolve to pint units ("m3/hr"
-> "m**3/h") and the printed label maps to a canonical attribute ("VALVE BODY MOC" ->
body_material). Without one, the unit stays as printed (folded) and the attribute is None:
the comparator then only compares the same unit spelling and matches on the label words.
Raw pint is never applied to printed units: it reads "MM" as megamolar.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from app.ontology.schema import Attribute, Ontology
from app.ontology.units import units_compatible
from app.retrieval.text_normalizer import fold_text, stem_token
from app.schemas.enums import Operator
from app.schemas.requirements import ValueKind

_NUMBER: Final = r"[+-]?\d+(?:\.\d+)?"
# "250 MM", "650 M3/HR", "CLASS #150", "PN 16", "50 to 600 mm": the whole value is a number.
_NUMERIC_VALUE: Final = re.compile(
    rf"^(?P<designation>class|pn)?\s*#?\s*(?P<num>{_NUMBER})"
    rf"(?:\s*(?:to|-)\s*(?P<num2>{_NUMBER}))?\s*(?P<unit>[^\s\d][^\s]*)?$",
    re.IGNORECASE,
)
# A code: letters and digits in one token ("A216", "CF8M", "17-4PH"), or a standard body
# followed by its number ("API 609", "IS 13095", "SS 410").
_CODE: Final = re.compile(
    r"\b(?:(?=[A-Za-z-]*\d)(?=[\d-]*[A-Za-z])[A-Za-z0-9-]+|[A-Z]{2,}\s?\d{2,})\b"
)
_WORD: Final = re.compile(r"[a-z0-9]+")
_COMPOUND: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# Labels and synonyms repeat across thousands of catalogue fields; stemming is pure.
_STEMS_CACHE_SIZE: Final = 65536
# Direction words. In a label, "MAX FLOW : 650" is a duty the product must reach (>=).
_AT_LEAST_LABEL: Final = frozenset({"max", "maximum"})
_AT_MOST_LABEL: Final = frozenset({"min", "minimum"})
_AT_LEAST_VALUE: Final = re.compile(r"\b(?:or higher|or above|or more|minimum|min\.?|at least)\b")
_AT_MOST_VALUE: Final = re.compile(r"\b(?:or lower|or less|up to|upto|maximum|max\.?)\b")
# "NITRILE OR SS" names options; "WCB OR EQUIVALENT" and "PN 16 OR HIGHER" name one thing.
_OPTION_OR: Final = re.compile(
    r"\s+or\s+(?!(?:equivalent|equal|similar|better|higher|above|more|lower|less)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ParsedValue:
    """A requirement value after parsing (no attribute yet)."""

    kind: ValueKind
    operator: Operator
    number: float | None = None
    number_max: float | None = None
    unit: str | None = None  # folded spelling, or "class" / "pn" for designations
    canonical_unit: str | None = None  # pint unit via the ontology
    alternatives: tuple[str, ...] = ()
    any_alternative: bool = False  # the tender wrote "or": any one option is enough

    @property
    def is_designation(self) -> bool:
        """True for Class / PN designations: numbers without a physical unit (B6)."""
        return self.unit in {"class", "pn"}


def words(text: str) -> list[str]:
    """Folded lower-case words of `text`."""
    return _WORD.findall(fold_text(text))


@lru_cache(maxsize=_STEMS_CACHE_SIZE)
def stems(text: str) -> tuple[str, ...]:
    """Stemmed folded label words ("Body Materials" -> "bodi", "materi").

    A hyphenated compound stays one word ("Dead-End" -> "deadend"), so its parts never
    match on their own: "Dead-End Service" is not an end connection.
    """
    return tuple(stem_token(w.replace("-", "")) for w in _COMPOUND.findall(fold_text(text)))


def has_code(text: str) -> bool:
    """True if the text holds a code ("ASTM A216 Gr. WCB", "API 609")."""
    return bool(_CODE.search(text))


def alternatives(value: str) -> tuple[str, ...]:
    """Options written with "/" ("ASTM A217 Gr. CA15/CF8M" -> "ASTM A217 Gr. CA15", "CF8M")."""
    parts = tuple(p.strip() for p in value.split("/"))
    return parts if len(parts) > 1 and all(parts) else ()


def or_options(value: str) -> tuple[str, ...]:
    """Options when the tender writes "or" ("NITRILE OR SS"); "/" inside them also separates.

    "/" alone does not say whether one option is enough, so only a written "or" does.
    Qualifiers ("OR EQUIVALENT", "OR HIGHER") are not options.

    Args:
        value: The printed value.

    Returns:
        The options, or an empty tuple if the value has no "or" between options.
    """
    pieces = [p.strip() for p in _OPTION_OR.split(value)]
    if len(pieces) < 2 or not all(pieces):
        return ()
    return tuple(option for piece in pieces for option in (alternatives(piece) or (piece,)))


def operator_for(label: str | None, value: str) -> Operator:
    """Direction of a numeric requirement from its label and value words; "=" by default."""
    folded = fold_text(value)
    if _AT_LEAST_VALUE.search(folded):
        return Operator.GTE
    if _AT_MOST_VALUE.search(folded):
        return Operator.LTE
    label_words = set(words(label or ""))
    if label_words & _AT_LEAST_LABEL:
        return Operator.GTE
    if label_words & _AT_MOST_LABEL:
        return Operator.LTE
    return Operator.EQ


def _numeric(
    match: re.Match[str], label: str | None, value: str, ontology: Ontology | None
) -> ParsedValue:
    designation = match.group("designation")
    unit = fold_text(designation or match.group("unit") or "").strip() or None
    canonical = None
    if unit and not designation and ontology is not None:
        canonical = ontology.units.get(unit)
    number2 = match.group("num2")
    operator = Operator.RANGE if number2 else operator_for(label, value)
    return ParsedValue(
        kind=ValueKind.NUMERIC,
        operator=operator,
        number=float(match.group("num")),
        number_max=float(number2) if number2 else None,
        unit=unit,
        canonical_unit=canonical,
    )


def parse_value(
    label: str | None, value: str, ontology: Ontology | None, enum_max_words: int
) -> ParsedValue:
    """Classify and parse one value.

    Args:
        label: The printed label ("MAX FLOW"), or None for an unlabelled statement.
        value: The printed value ("650 M3/HR").
        ontology: Category ontology for unit spellings, or None.
        enum_max_words: Values this short with no code are options ("WAFER"), not text.

    Returns:
        The parsed value: numeric (number, unit, operator), code, or text.
    """
    stripped = value.strip().rstrip(".")
    # "PN 16 OR HIGHER": the direction words set the operator, the rest is the number.
    bare = _AT_MOST_VALUE.sub("", _AT_LEAST_VALUE.sub("", fold_text(stripped))).strip()
    match = _NUMERIC_VALUE.match(bare)
    if match:
        return _numeric(match, label, stripped, ontology)
    if has_code(stripped) or len(stripped.split()) <= enum_max_words:
        options = or_options(stripped)
        return ParsedValue(
            kind=ValueKind.CODE,
            operator=Operator.IN,
            alternatives=options or alternatives(stripped),
            any_alternative=bool(options),
        )
    return ParsedValue(kind=ValueKind.TEXT, operator=Operator.TEXT)


def _synonym_spans(label_words: tuple[str, ...], attribute: Attribute) -> list[tuple[int, int]]:
    """(end index, length) of every synonym of `attribute` found as a word run in the label."""
    spans: list[tuple[int, int]] = []
    for synonym in attribute.synonyms:
        syn = stems(synonym)
        for start in range(len(label_words) - len(syn) + 1):
            if syn and label_words[start : start + len(syn)] == syn:
                spans.append((start + len(syn), len(syn)))
    return spans


def _fits(attribute: Attribute, value: ParsedValue) -> bool:
    """True if the value could be a value of this attribute (unit dimension, designation)."""
    if value.kind is not ValueKind.NUMERIC or value.is_designation:
        return attribute.canonical_unit is None
    if attribute.canonical_unit is None or value.canonical_unit is None:
        return False
    return units_compatible(value.canonical_unit, attribute.canonical_unit)


def resolve_attribute(
    label: str | None, value: ParsedValue, ontology: Ontology | None
) -> str | None:
    """Canonical attribute for a printed label, or None (no ontology, no match, no fit).

    The label's last words name the thing ("VALVE BODY MOC" -> body moc; "BODY SEAT" ->
    seat), so a synonym ending at the label's end wins, then the longer synonym. The value
    must fit the attribute: "TORQUE AT SHAFT : 820 Nm" is not the shaft material. Words are
    stemmed on both sides, so catalogue labels ("Body Materials") resolve the same way.

    Args:
        label: The printed label, or None.
        value: Its parsed value.
        ontology: Category ontology, or None.

    Returns:
        The attribute name, or None.
    """
    if label is None or ontology is None:
        return None
    label_words = stems(label)
    best: tuple[int, int, str] | None = None
    for attribute in ontology.attributes:
        if not _fits(attribute, value):
            continue
        for end, length in _synonym_spans(label_words, attribute):
            rank = (int(end == len(label_words)), length, attribute.name)
            if best is None or rank[:2] > best[:2]:
                best = rank
    return best[2] if best else None
