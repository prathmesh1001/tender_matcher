"""Rule comparator: one atomic requirement against one manufacturer's catalogue fields.

Rules only (CLAUDE.md section 7, "rules before LLM"); the ontology is used when the item's
category has one and is never required.

1. Like-for-like. A field counts only if its label names the requirement's attribute: with
   an ontology, the field label resolves to the same attribute ("Disc Materials" is never
   body_material); without one, every key word of the requirement label (generic words such
   as "valve", "moc" removed) appears in the field label. An unlabelled line counts only if
   those words come before the value on the line. "WCB" in a Disc row never satisfies Body.
2. Numbers are compared only in a common unit: pint via the ontology, inch sizes via the NPS
   table, Class <-> PN via the EN 1759 table, or the same printed unit without an ontology.
   Bare numbers and dimension-table rows never decide anything.
3. `missing_capability` only when a field states the same attribute with a comparable value
   that fails. A label without a usable value, or values that cannot be compared, give
   `unsupported` + needs_review. Free text and unlabelled statements are unsupported too
   (they need the LLM judge, not enabled).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.core.config import Settings
from app.core.exceptions import UnitConversionError
from app.matching.fields import FieldOptions, LocatedField
from app.matching.values import Quantity, code_tokens, quantities
from app.ontology.schema import Attribute, Ontology
from app.ontology.units import class_to_pn, convert, nps_to_dn
from app.retrieval.text_normalizer import SearchVocabulary, fold_text, is_code, stem_token
from app.schemas.enums import DecisionSource, MatchStatus, Operator
from app.schemas.matching import Comparison, Evidence, LabelBasis
from app.schemas.requirements import Requirement, ValueKind
from app.tender.normalizer import ParsedValue, resolve_attribute, stems

_RANK: Final = {
    MatchStatus.FULL_MATCH: 3,
    MatchStatus.PARTIAL_MATCH: 2,
    MatchStatus.MISSING_CAPABILITY: 1,
}
_DESIGNATIONS: Final = frozenset({"class", "pn"})


@dataclass(frozen=True, slots=True)
class ComparatorOptions:
    """Comparator settings (from config), word lists stemmed once."""

    fields: FieldOptions
    generic_label_words: frozenset[str]
    code_noise_words: frozenset[str]
    designation_prefixes: tuple[str, ...]
    rel_tolerance: float
    max_evidence: int
    value_max_words: int

    @classmethod
    def from_settings(cls, settings: Settings) -> ComparatorOptions:
        """Build options from application settings."""
        return cls(
            fields=FieldOptions.from_settings(settings),
            generic_label_words=frozenset(
                stem_token(w) for w in settings.COMPARATOR_GENERIC_LABEL_WORDS
            ),
            code_noise_words=frozenset(stem_token(w) for w in settings.COMPARATOR_CODE_NOISE_WORDS),
            designation_prefixes=tuple(settings.COMPARATOR_DESIGNATION_PREFIXES),
            rel_tolerance=settings.COMPARATOR_REL_TOLERANCE,
            max_evidence=settings.COMPARATOR_MAX_EVIDENCE,
            value_max_words=settings.COMPARATOR_VALUE_MAX_WORDS,
        )


@dataclass(frozen=True, slots=True)
class MatchContext:
    """The item's ontology (or None) and its compiled code rules."""

    ontology: Ontology | None = None
    vocabulary: SearchVocabulary | None = None

    @classmethod
    def for_ontology(cls, ontology: Ontology | None) -> MatchContext:
        """Context for one ontology; without one, rules run on printed labels and units."""
        if ontology is None:
            return cls()
        return cls(ontology, SearchVocabulary.from_ontologies([ontology]))

    def attribute(self, requirement: Requirement) -> Attribute | None:
        """The requirement's ontology attribute, if both are known."""
        if self.ontology is None or requirement.attribute is None:
            return None
        return self.ontology.attribute(requirement.attribute)


@dataclass(frozen=True, slots=True)
class _Outcome:
    """What one field decides: a status, or None with the reason it decides nothing."""

    status: MatchStatus | None
    reason: str


# ---------------------------------------------------------------- like-for-like labels


def key_words(label: str, options: ComparatorOptions) -> frozenset[str]:
    """Stemmed label words that name the attribute ("VALVE BODY MOC" -> {"bodi"})."""
    return frozenset(stems(label)) - options.generic_label_words


def _shape(requirement: Requirement) -> ParsedValue:
    """The requirement's value shape, so a catalogue label resolves among the same attributes."""
    return ParsedValue(
        kind=requirement.kind,
        operator=requirement.operator,
        unit=requirement.unit,
        canonical_unit=requirement.canonical_unit,
    )


def label_names(
    requirement: Requirement, label: str, ctx: MatchContext, options: ComparatorOptions
) -> bool:
    """True if a catalogue label names the requirement's attribute (rule 1)."""
    if ctx.ontology is not None and requirement.attribute is not None:
        found = resolve_attribute(label, _shape(requirement), ctx.ontology)
        return found == requirement.attribute
    keys = key_words(requirement.label or "", options)
    return bool(keys) and keys <= frozenset(stems(label))


def _inline_value(
    requirement: Requirement, line: str, ctx: MatchContext, options: ComparatorOptions
) -> str | None:
    """For an unlabelled line: the number after the label words, if they lead the line.

    Only numeric requirements are read this way ("Size range 80 mm to 2500 mm"); the label
    is the digit-free start of the line. A code needs a labelled field: in a sentence
    ("Ends shall be ...") the words after a label are not its value. A label ending in a
    designation keeps it with the number ("ASME Class 150 to 1500" -> "class 150 to 1500").
    """
    if requirement.kind is not ValueKind.NUMERIC:
        return None
    tokens = line.split()  # verbatim: the value is cited as printed
    for end in range(1, min(len(tokens), options.fields.label_max_words) + 1):
        prefix = " ".join(tokens[:end])
        if any(ch.isdigit() for ch in prefix):
            return None
        if label_names(requirement, prefix, ctx, options):
            last = fold_text(tokens[end - 1]).strip()
            start = end - 1 if last in options.designation_prefixes else end
            rest = " ".join(tokens[start:])
            return rest if rest != tokens[end - 1] else None
    return None


def field_value(
    requirement: Requirement, located: LocatedField, ctx: MatchContext, options: ComparatorOptions
) -> str | None:
    """The field's value if the field states the requirement's attribute, else None."""
    field = located.field
    if field.label:
        return field.value if label_names(requirement, field.label, ctx, options) else None
    return _inline_value(requirement, field.value, ctx, options)


# ---------------------------------------------------------------- numbers


def _physical(number: float, unit: str | None, attr: Attribute, ontology: Ontology) -> float | None:
    """A number in the attribute's canonical unit, or None if it cannot be converted."""
    if unit is None or attr.canonical_unit is None:
        return None
    expr = ontology.units.get(unit) or ontology.prefix_units.get(unit)
    if expr is None:
        return None
    try:
        if attr.nominal_pipe_size and expr == "inch":
            return float(nps_to_dn(ontology, f"{number:g}"))
        return convert(number, expr, attr.canonical_unit)
    except UnitConversionError:
        return None


def _designation(number: float, unit: str, target: str, ontology: Ontology | None) -> float | None:
    """A Class or PN number in the `target` system (EN 1759 table only, never via bar)."""
    if unit == target:
        return number
    if ontology is None or not number.is_integer():
        return None
    if unit == "class" and target == "pn":
        pn = class_to_pn(ontology, int(number))
        return float(pn) if pn is not None else None
    if unit == "pn" and target == "class":
        found = [c for c, p in ontology.class_to_pn.items() if p == int(number)]
        return float(found[0]) if found else None
    return None


def _to_common(
    number: float, unit: str | None, requirement: Requirement, ctx: MatchContext
) -> float | None:
    """Put a printed number into the requirement's comparison space (rule 2)."""
    if requirement.unit in _DESIGNATIONS:
        if unit not in _DESIGNATIONS:
            return None
        return _designation(number, unit, requirement.unit, ctx.ontology)
    attr = ctx.attribute(requirement)
    if attr is not None and ctx.ontology is not None and attr.canonical_unit is not None:
        return _physical(number, unit, attr, ctx.ontology)
    return number if unit is not None and unit == requirement.unit else None


def _wanted(requirement: Requirement, ctx: MatchContext) -> tuple[float, float] | None:
    """The tender's number (or range) in the comparison space, or None if not convertible."""
    if requirement.value_min is not None and requirement.value_max is not None:
        low, high = requirement.value_min, requirement.value_max
    elif requirement.value_num is not None:
        low = high = requirement.value_num
    else:
        return None
    common = [_to_common(n, requirement.unit, requirement, ctx) for n in (low, high)]
    if common[0] is None or common[1] is None:
        return None
    return common[0], common[1]


def _effective_operator(requirement: Requirement, attr: Attribute | None) -> Operator:
    """Operator to apply: "=" against a rating means "reaches it" (16 bar meets 10 bar)."""
    if requirement.operator is Operator.EQ and attr is not None:
        if attr.limit == "max":
            return Operator.GTE
        if attr.limit == "min":
            return Operator.LTE
    return requirement.operator


def _judge_range(
    op: Operator, want: tuple[float, float], have: tuple[float, float], exact: bool, tol: float
) -> MatchStatus | None:
    """Status of one comparable catalogue value or range against the tender's value."""
    (w_lo, w_hi), (h_lo, h_hi) = want, have
    if op is Operator.GTE:
        return MatchStatus.FULL_MATCH if h_hi >= w_lo - tol else MatchStatus.MISSING_CAPABILITY
    if op is Operator.LTE:
        return MatchStatus.FULL_MATCH if h_lo <= w_hi + tol else MatchStatus.MISSING_CAPABILITY
    if math.isinf(h_lo) and w_hi <= h_hi + tol:
        return MatchStatus.PARTIAL_MATCH  # "up to 2500 mm": the smallest size is not printed
    if h_lo - tol <= w_lo and w_hi <= h_hi + tol:
        return MatchStatus.FULL_MATCH
    if op is Operator.RANGE and w_lo <= h_hi and h_lo <= w_hi:
        return MatchStatus.PARTIAL_MATCH
    if op is Operator.EQ and h_lo == h_hi and not exact:
        return None  # one printed value, direction unknown: it may be a limit
    return MatchStatus.MISSING_CAPABILITY


def judge_numeric(
    requirement: Requirement,
    value: str,
    in_dimension_table: bool,
    ctx: MatchContext,
    options: ComparatorOptions,
) -> _Outcome:
    """What one field's value decides for a numeric requirement (rules 2 and 3)."""
    want = _wanted(requirement, ctx)
    if in_dimension_table:
        return _Outcome(None, "dimension table: its numbers do not state the attribute")
    if want is None:
        return _Outcome(None, "requirement has no number in a convertible unit")
    attr = ctx.attribute(requirement)
    op = _effective_operator(requirement, attr)
    exact = attr is not None or requirement.unit in _DESIGNATIONS
    tol = options.rel_tolerance * max(abs(want[0]), abs(want[1]), 1.0)
    statuses = [
        _judge_range(op, want, have, exact, tol)
        for have in _comparable(quantities(value, options.designation_prefixes), requirement, ctx)
    ]
    decided = [s for s in statuses if s is not None]
    if decided:
        return _Outcome(max(decided, key=_RANK.__getitem__), "")
    if statuses:
        return _Outcome(None, "single value of unknown direction (no ontology limit)")
    return _Outcome(None, "label found but no value in a comparable unit")


def _comparable(
    found: Sequence[Quantity], requirement: Requirement, ctx: MatchContext
) -> list[tuple[float, float]]:
    """Catalogue quantities converted into the requirement's space; others dropped."""
    out: list[tuple[float, float]] = []
    for q in found:
        low = -math.inf if q.is_open else _to_common(q.low, q.unit, requirement, ctx)
        high = _to_common(q.high, q.unit, requirement, ctx)
        if low is not None and high is not None:
            out.append((min(low, high), max(low, high)))
    return out


# ---------------------------------------------------------------- codes


def _coded(tokens: frozenset[str]) -> bool:
    """True if any token is a code or a number ("a216_wcb", "125"), not only words."""
    return any(is_code(t) or t.isdigit() for t in tokens)


def judge_code(
    requirement: Requirement, value: str, ctx: MatchContext, options: ComparatorOptions
) -> _Outcome:
    """What one field's value decides for a code requirement.

    Every token of an option must appear ("ASTM A216 Gr. WCB" needs a216 and wcb). With
    options ("CA15/CF8M") all found is full, some found is partial. A value that states
    something else (a code, or a short value such as "Flanged") is missing. An empty value,
    bare numbers ("5" in a "No." column, B4) or a sentence without codes decide nothing.
    """
    have = code_tokens(value, options.code_noise_words, ctx.vocabulary)
    if not any(not t.isdigit() for t in have):
        return _Outcome(None, "label found but no value")
    wanted = [
        code_tokens(o, options.code_noise_words, ctx.vocabulary)
        for o in (requirement.alternatives or [requirement.value_text])
    ]
    wanted = [w for w in wanted if w]
    if not wanted:
        return _Outcome(None, "requirement has no code")
    found = [w for w in wanted if w <= have]
    if len(found) == len(wanted):
        return _Outcome(MatchStatus.FULL_MATCH, "")
    if found:
        return _Outcome(MatchStatus.PARTIAL_MATCH, f"{len(found)} of {len(wanted)} options found")
    if not any(_coded(w) for w in wanted) and _coded(have):
        return _Outcome(None, "tender names words, catalogue names codes: not like-for-like")
    if any(w & have for w in wanted):
        return _Outcome(MatchStatus.PARTIAL_MATCH, "near miss: only part of the code found")
    if any(is_code(t) for t in have) or len(value.split()) <= options.value_max_words:
        return _Outcome(MatchStatus.MISSING_CAPABILITY, "")
    return _Outcome(None, "value is a sentence without codes: needs the LLM judge")


# ---------------------------------------------------------------- one requirement


def _evidence(located: LocatedField, value: str, outcome: _Outcome) -> Evidence:
    doc, chunk = located.source.document, located.source.chunk
    return Evidence(
        document=doc.file,
        sha256=doc.sha256,
        title=doc.title,
        page_number=chunk.page_number,
        label=located.field.label,
        value=value,
        quote=located.field.quote,
        quote_parts=list(located.field.parts),
        status=outcome.status,
        reason=outcome.reason,
    )


def _unsupported(requirement: Requirement, reason: str, basis: LabelBasis) -> Comparison:
    return Comparison(
        requirement_id=requirement.id,
        status=MatchStatus.UNSUPPORTED,
        needs_review=True,
        decided_by=DecisionSource.NONE,
        basis=basis,
        reason=reason,
    )


def _not_checkable(requirement: Requirement) -> str | None:
    """Why rules cannot check this requirement at all, or None."""
    if requirement.label is None:
        return "statement without a label: needs the LLM judge (not enabled)"
    if requirement.kind is ValueKind.TEXT:
        return "free text: needs the LLM judge (not enabled)"
    return None


def compare(
    requirement: Requirement,
    fields: Sequence[LocatedField],
    ctx: MatchContext,
    options: ComparatorOptions,
) -> Comparison:
    """Decide one requirement against one manufacturer's fields (see module doc).

    Args:
        requirement: An atomic requirement.
        fields: The manufacturer's fields for the item's product type.
        ctx: The item's ontology context (may be empty).
        options: Comparator settings.

    Returns:
        The best status any field supports, with the fields behind it as evidence.
    """
    basis = LabelBasis.ATTRIBUTE if ctx.attribute(requirement) else LabelBasis.LABEL_WORDS
    reason = _not_checkable(requirement)
    if reason:
        return _unsupported(requirement, reason, LabelBasis.NONE)
    judged: list[Evidence] = []
    for located in fields:
        value = field_value(requirement, located, ctx, options)
        if value is None:
            continue
        if requirement.kind is ValueKind.NUMERIC:
            outcome = judge_numeric(
                requirement, value, located.field.in_dimension_table, ctx, options
            )
        else:
            outcome = judge_code(requirement, value, ctx, options)
        judged.append(_evidence(located, value, outcome))
    return _decide(requirement, judged, basis, options)


def _decide(
    requirement: Requirement,
    judged: Sequence[Evidence],
    basis: LabelBasis,
    options: ComparatorOptions,
) -> Comparison:
    """Best decided status over all judged fields; unsupported if none decided."""
    if not judged:
        return _unsupported(requirement, "no catalogue field names this attribute", LabelBasis.NONE)
    decided = [e for e in judged if e.status is not None]
    if not decided:
        result = _unsupported(requirement, judged[0].reason, basis)
        return result.model_copy(update={"evidence": list(judged[: options.max_evidence])})
    best = max(decided, key=lambda e: _RANK[e.status] if e.status else 0)
    status = best.status or MatchStatus.UNSUPPORTED
    support = [e for e in decided if e.status is status]
    return Comparison(
        requirement_id=requirement.id,
        status=status,
        needs_review=status is MatchStatus.PARTIAL_MATCH,
        decided_by=DecisionSource.RULE,
        basis=basis,
        reason=best.reason,
        evidence=support[: options.max_evidence],
    )
