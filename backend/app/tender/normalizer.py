"""Parse one requirement value: number + unit, code, or free text; operator; attribute.

Works with or without an ontology. With one, unit spellings resolve to pint units ("m3/hr"
-> "m**3/h") and the printed label maps to a canonical attribute ("VALVE BODY MOC" ->
body_material). Without one, the unit stays as printed (folded) and the attribute is None:
the comparator then only compares the same unit spelling and matches on the label words.
Raw pint is never applied to printed units: it reads "MM" as megamolar.
"""

import re
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from typing import Final

from app.ontology.schema import Attribute, Ontology
from app.ontology.units import convert, spell_fractions, units_compatible
from app.retrieval.text_normalizer import fold_text, stem_token
from app.schemas.enums import Operator
from app.schemas.requirements import ValueKind

_NUMBER: Final = r"[+-]?\d+(?:\.\d+)?"
# "250 MM", "650 M3/HR", "CLASS #150", "PN 16", "50 to 600 mm": the whole value is a number.
_NUMERIC_VALUE: Final = re.compile(
    rf"^(?P<designation>class|pn)?\s*#?\s*(?P<num>{_NUMBER})"
    rf"(?:\s*(?:to|thru|through|-)\s*(?P<num2>{_NUMBER}))?\s*(?P<unit>[^\s\d][^\s]*)?$",
    re.IGNORECASE,
)
# A code: letters and digits in one token ("A216", "CF8M", "17-4PH"), or a standard body
# followed by its number ("API 609", "IS 13095", "SS 410").
_CODE: Final = re.compile(
    r"\b(?:(?=[A-Za-z-]*\d)(?=[\d-]*[A-Za-z])[A-Za-z0-9-]+|[A-Z]{2,}\s?\d{2,})\b"
)
_WORD: Final = re.compile(r"[a-z0-9]+")
# A bracketed part of a folded label: "nominal pressure (pn) (mpa)" -> "pn", "mpa".
_LABEL_BRACKET: Final = re.compile(r"\(([^()]+)\)")
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


_TEMPERATURE_UNIT: Final = "degc"
# A PN designation is the nominal pressure in bar (EN 1333): "Nominal Pressure (PN) (MPa) : 1.6"
# is PN 16. Only whole PN numbers count; anything else stays a pressure.
_PN_UNIT: Final = "bar"
_PN_TOLERANCE: Final = 1e-6
# Sizes and ratings are matched on folded text, so curly quotes are already straight.
# A space joins a fraction only to one digit: "1 1/2" is 1.5, "15 1/2" may be DN15 next to 1/2 inch.
_INCH_NUMBER: Final = r"\d+\s*-\s*\d+/\d+|\d\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?"
_INCH_UNIT: Final = r"(?:(?:\"|'')(?:\s*inch(?:es)?\b)?|inch(?:es)?\b|in\b)"
_SIZE_PATTERN: Final = (
    rf"(?:dn|nb)\s*-?\s*(?P<dn>\d+)(?:\s*mm\b)?|(?P<nb>\d+)\s*nb\b"
    rf"|(?P<inch>{_INCH_NUMBER})\s*{_INCH_UNIT}"
)
_SIZE_VALUE: Final = re.compile(_SIZE_PATTERN)
_RATING_VALUE: Final = re.compile(
    r"class\s*[#:]?\s*(?P<a>\d+)|#\s*(?P<b>\d+)(?:\s*class\b)?|(?P<c>\d+)\s*(?:#|lbs?\b|class\b)"
)
# The rating at the end of a size + rating value: "6 inch # 300 Class".
_RATING_SUFFIX: Final = re.compile(
    r"(?:#\s*\d+(?:\s*class)?|class\s*[#:]?\s*\d+)\s*$", re.IGNORECASE
)
# A size in an item title, where words surround it. A title's "mm" needs "size" or a comma before
# it: "STEM LENGTH 220 MM" is not a size. "(?<![\w.-])" keeps "F316-1.5 Inch" codes out.
_TITLE_SIZE: Final = re.compile(
    rf"(?<![\w.-])(?P<size>(?:dn|nb)\s*-?\s*\d+(?:\s*mm\b)?|\d+\s*nb\b"
    rf"|(?:{_INCH_NUMBER})\s*{_INCH_UNIT})"
    r"|(?:\bsize\s*:?\s*|,\s*)(?P<mm>\d+(?:\.\d+)?\s*mm\b)"
)
# Distinct printed labels per process (catalogue labels repeat on every table row).
_LABEL_CACHE_SIZE: Final = 65536
# Distinct label words stemmed per process.
_STEM_CACHE_SIZE: Final = 65536

# A temperature: "-40 Deg. C", "(-196 DEG C)", "80° C"; a range joins two with "to" or a dash.
_DEGREES: Final = r"(?:deg(?:ree)?s?\.?\s*c(?:elsius)?\b|°\s*c\b)"
_TEMPERATURE: Final = rf"\(?\s*(?P<{{name}}>[+-]?\d+(?:\.\d+)?)\s*{_DEGREES}\s*\)?"
_TEMPERATURE_VALUE: Final = re.compile(
    _TEMPERATURE.format(name="low")
    + r"(?:\s*(?:to|~|-)\s*"
    + _TEMPERATURE.format(name="high")
    + ")?"
)


def temperature_form(value: str) -> tuple[float, float | None] | None:
    """The numbers of a value that is only a temperature or a temperature range.

    Args:
        value: A value as printed ("-40 Deg. C to +40 Deg. C").

    Returns:
        `(low, high)`, `high` None for one temperature; None if the value is anything else.
    """
    found = _TEMPERATURE_VALUE.fullmatch(fold_text(value).strip())
    if found is None:
        return None
    high = found.group("high")
    return float(found.group("low")), float(high) if high else None


def _inches(text: str) -> float:
    """Inches as printed: "3/4", "1-1/2", "1 1/2" or "1.5"."""
    return float(sum(Fraction(part) for part in re.split(r"[\s-]+", text.strip())))


def size_form(value: str) -> tuple[float, str] | None:
    """The number and unit of a value that is only a pipe size.

    DN and NB are millimetres (DN25 MM -> 25 mm); inches keep their fractions (3/4" -> 0.75).

    Args:
        value: A value as printed.

    Returns:
        `(number, "mm" | "inch")`, or None if the value is anything else.
    """
    found = _SIZE_VALUE.fullmatch(fold_text(value).strip())
    if found is None:
        return None
    if found.group("inch"):
        return _inches(found.group("inch")), "inch"
    return float(found.group("dn") or found.group("nb")), "mm"


def rating_form(value: str) -> float | None:
    """The class number of a value that is only a rating ("# 300", "150#", "Class 800"), or None."""
    found = _RATING_VALUE.fullmatch(fold_text(value).strip())
    return float(found.group("a") or found.group("b") or found.group("c")) if found else None


def split_size_rating(value: str) -> tuple[str, str] | None:
    """A compound value's size and rating ("6 inch # 300 Class" -> "6 inch", "# 300 Class")."""
    found = _RATING_SUFFIX.search(value)
    if found is None:
        return None
    size, rating = value[: found.start()].strip(), value[found.start() :].strip()
    return (size, rating) if size_form(size) and rating_form(rating) else None


def title_size(title: str) -> str | None:
    """The one size an item title states, as printed; None for none or several (never guessed)."""
    found = {
        " ".join((m.group("size") or m.group("mm")).split())
        for m in _TITLE_SIZE.finditer(fold_text(spell_fractions(title)))
    }
    return next(iter(found)) if len(found) == 1 else None


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
    any_alternative: bool = False  # options written with "or" or "/": any one is enough

    @property
    def is_designation(self) -> bool:
        """True for Class / PN designations: numbers without a physical unit (B6)."""
        return self.unit in {"class", "pn"}


def words(text: str) -> list[str]:
    """Folded lower-case words of `text`."""
    return _WORD.findall(fold_text(text))


# Cached here, not in `text_normalizer`: that module is part of the catalogue cache fingerprint.
_stem = lru_cache(maxsize=_STEM_CACHE_SIZE)(stem_token)


@lru_cache(maxsize=_STEMS_CACHE_SIZE)
def stems(text: str) -> tuple[str, ...]:
    """Stemmed folded label words ("Body Materials" -> "bodi", "materi").

    A hyphenated compound stays one word ("Dead-End" -> "deadend"), so its parts never
    match on their own: "Dead-End Service" is not an end connection.
    """
    return tuple(_stem(w.replace("-", "")) for w in _COMPOUND.findall(fold_text(text)))


def has_code(text: str) -> bool:
    """True if the text holds a code ("ASTM A216 Gr. WCB", "API 609")."""
    return bool(_CODE.search(text))


def alternatives(value: str) -> tuple[str, ...]:
    """Options written with "/" ("ASTM A217 Gr. CA15/CF8M" -> "ASTM A217 Gr. CA15", "CF8M")."""
    parts = tuple(p.strip() for p in value.split("/"))
    return parts if len(parts) > 1 and all(parts) else ()


def or_options(value: str) -> tuple[str, ...]:
    """Options when the tender writes "or" ("NITRILE OR SS"); "/" inside them also separates.

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


def names_material(text: str, ontology: Ontology | None) -> bool:
    """True if the text names one of the ontology's materials anywhere ("... aluminium die ...").

    Args:
        text: A requirement's value.
        ontology: Provides the material names; None names none.

    Returns:
        Whether a material name's words appear in order in the text.
    """
    if ontology is None:
        return False
    found = stems(text)
    for name in ontology.material_names:
        wanted = stems(name)
        if any(found[i : i + len(wanted)] == wanted for i in range(len(found))):
            return True
    return False


def label_unit(label: str | None, ontology: Ontology | None) -> str | None:
    """The one known unit a label prints in brackets ("Nominal Size (DN) (mm)" -> "mm").

    GeM bid tables put the unit in the label and leave the value bare ("100"). Only a
    bracketed spelling the ontology knows counts; none or several known units give None.

    Args:
        label: The printed label, or None.
        ontology: Category ontology, or None.

    Returns:
        The folded unit spelling, or None.
    """
    if label is None or ontology is None:
        return None
    found = {
        unit
        for part in _LABEL_BRACKET.findall(fold_text(label))
        if (unit := part.strip()) in ontology.units
    }
    return next(iter(found)) if len(found) == 1 else None


def pn_from_pressure(label: str | None, number: float, canonical_unit: str | None) -> float | None:
    """The PN designation of a pressure whose label names PN, or None.

    Args:
        label: The printed label ("Nominal Pressure (PN) (MPa)").
        number: The printed number (1.6).
        canonical_unit: Its pint unit ("MPa"), or None.

    Returns:
        The whole PN number (16.0); None if the label does not name PN, the unit is not a
        pressure, or the pressure is not a whole number of bar.
    """
    if canonical_unit is None or "pn" not in words(label or ""):
        return None
    if not units_compatible(canonical_unit, _PN_UNIT):
        return None
    in_bar = convert(number, canonical_unit, _PN_UNIT)
    whole = round(in_bar)
    return float(whole) if whole > 0 and abs(in_bar - whole) < _PN_TOLERANCE else None


def _numeric(
    match: re.Match[str], label: str | None, value: str, ontology: Ontology | None
) -> ParsedValue:
    designation = match.group("designation")
    unit = fold_text(designation or match.group("unit") or "").strip() or None
    if unit and not designation and unit.startswith("(") and unit.endswith(")"):
        unit = unit[1:-1].strip() or None  # GeM prints "1.5 (horsepower)"
    if unit is None:
        unit = label_unit(label, ontology)
    canonical = None
    if unit and not designation and ontology is not None:
        canonical = ontology.units.get(unit)
    if unit is None and "class" in words(label or ""):
        unit = "class"  # "CLASS : 800 OR HIGHER": the label says what the bare number is
    number2 = match.group("num2")
    operator = Operator.RANGE if number2 else operator_for(label, value)
    pn = None if number2 else pn_from_pressure(label, float(match.group("num")), canonical)
    if pn is not None:
        return ParsedValue(kind=ValueKind.NUMERIC, operator=operator, number=pn, unit="pn")
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
    stripped = spell_fractions(value.strip().rstrip("."))
    # "PN 16 OR HIGHER": the direction words set the operator, the rest is the number.
    bare = _AT_MOST_VALUE.sub("", _AT_LEAST_VALUE.sub("", fold_text(stripped))).strip()
    size = size_form(bare)
    if size is not None:
        canonical = ontology.units.get(size[1]) if ontology is not None else None
        return ParsedValue(
            kind=ValueKind.NUMERIC,
            operator=operator_for(label, stripped),
            number=size[0],
            unit=size[1],
            canonical_unit=canonical,
        )
    temperature = temperature_form(bare)
    if temperature is not None:
        low, high = temperature
        return ParsedValue(
            kind=ValueKind.NUMERIC,
            operator=Operator.RANGE if high is not None else operator_for(label, stripped),
            number=low,
            number_max=high,
            unit=_TEMPERATURE_UNIT,
            canonical_unit=ontology.units.get(_TEMPERATURE_UNIT) if ontology is not None else None,
        )
    rating = rating_form(bare)
    if rating is not None:
        return ParsedValue(
            kind=ValueKind.NUMERIC,
            operator=operator_for(label, stripped),
            number=rating,
            unit="class",
        )
    match = _NUMERIC_VALUE.match(bare)
    if match:
        return _numeric(match, label, stripped, ontology)
    if has_code(stripped) or len(stripped.split()) <= enum_max_words:
        # "B7/2H" and "B7 OR 2H" mean the same in tenders: either option is acceptable.
        options = or_options(stripped) or alternatives(stripped)
        return ParsedValue(
            kind=ValueKind.CODE,
            operator=Operator.IN,
            alternatives=options,
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


def _label_stems(label: str, ontology: Ontology) -> tuple[str, ...]:
    """Stemmed label words; a distinct word ("Designation") keeps its printed form.

    Snowball folds "designation" into "design", a design-standard synonym; kept printed, such
    a word matches no synonym. Positions stay aligned with `stems(label)`.
    """
    distinct = {fold_text(w) for w in ontology.label_distinct_words}
    printed = _COMPOUND.findall(fold_text(label))
    return tuple(
        f"={word}" if word in distinct else stem
        for word, stem in zip(printed, stems(label), strict=True)
    )


def _rest_is_ignorable(label: str, end: int, ontology: Ontology) -> bool:
    """True if every label word after position `end` is generic, a unit or a code.

    The label's last words name the thing, so anything else after a synonym means the label
    is about something else: "Design Temp." is not a design standard, "Stem Length" is not a
    stem material. "Body Materials WCB/ WCC" keeps body: the rest is generic or a code.
    """
    printed = _COMPOUND.findall(fold_text(label))
    generic = {s for word in ontology.label_generic_words for s in stems(word)}
    for word in printed[end:]:
        if _stem(word.replace("-", "")) in generic or any(ch.isdigit() for ch in word):
            continue
        if word in ontology.units:
            continue
        if not any(re.fullmatch(rule.pattern, word) for rule in ontology.codes):
            return False
    return True


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
    seat), so the synonym starting first wins ("MOTOR BODY MATERIAL" -> motor body), then one
    ending at the label's end, then the longer synonym, and only
    generic words or codes may follow a synonym ("Design Temp." is not `design_standard`).
    The value must fit the attribute: "TORQUE AT SHAFT : 820 Nm" is not the shaft material.
    Words are stemmed on both sides, so catalogue labels ("Body Materials") resolve the same.

    Args:
        label: The printed label, or None.
        value: Its parsed value.
        ontology: Category ontology, or None.

    Returns:
        The attribute name, or None.
    """
    if label is None or ontology is None:
        return None
    word_count, candidates = _label_candidates(_SameOntology(ontology), label)
    best: tuple[int, int, int, str] | None = None
    for attribute, end, length in candidates:
        if not _fits(attribute, value):
            continue
        # Only generic words follow any candidate, so the one starting first names the
        # most: "Motor Body Material" is the motor body, not the body.
        rank = (length - end, int(end == word_count), length, attribute.name)
        if best is None or rank[:3] > best[:3]:
            best = rank
    return best[3] if best else None


class _SameOntology:
    """Cache key: one loaded ontology by identity (its dicts make it unhashable by value).

    The cache holds the key, so the ontology stays alive and its id is never reused.
    """

    __slots__ = ("ontology",)

    def __init__(self, ontology: Ontology) -> None:
        self.ontology = ontology

    def __hash__(self) -> int:
        return id(self.ontology)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _SameOntology) and other.ontology is self.ontology


@lru_cache(maxsize=_LABEL_CACHE_SIZE)
def _label_candidates(
    key: _SameOntology, label: str
) -> tuple[int, tuple[tuple[Attribute, int, int], ...]]:
    """The label's word count and every (attribute, end, length) synonym span it allows.

    Depends on the label only, not on the value, so catalogue labels seen on many rows are
    stemmed and scanned once; `resolve_attribute` then keeps the spans whose value fits.
    Attribute and span order is kept, so ties break as before.
    """
    ontology = key.ontology
    label_words = _label_stems(label, ontology)
    candidates = tuple(
        (attribute, end, length)
        for attribute in ontology.attributes
        for end, length in _synonym_spans(label_words, attribute)
        if _rest_is_ignorable(label, end, ontology)
    )
    return len(label_words), candidates
