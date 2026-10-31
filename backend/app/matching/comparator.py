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
from app.matching.fields import FieldOptions, LocatedField
from app.matching.values import Quantity, code_tokens, quantities
from app.ontology.schema import Attribute, Ontology
from app.ontology.units import canonical_number, class_to_pn
from app.retrieval.text_normalizer import SearchVocabulary, fold_text, is_code, stem_token
from app.schemas.enums import DecisionSource, MatchStatus, Operator, RequirementType
from app.schemas.matching import Comparison, Evidence, LabelBasis
from app.schemas.requirements import Requirement, ValueKind
from app.schemas.sections import EvidenceSource
from app.tender.normalizer import ParsedValue, resolve_attribute, stems, words

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


_UNREADABLE: Final = "printed value is impossible, so it was misread: ignored"


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
    requirement: Requirement,
    label: str,
    ctx: MatchContext,
    options: ComparatorOptions,
    key: str = "",
) -> bool:
    """True if a catalogue label names the requirement's attribute (rule 1).

    A table cell's label is its row key plus a column header, and the header can be a model or
    a heading ("Head" + "Etanorm", "End Connection" + "Standard"). So if the whole label names
    nothing, the row key alone decides; "Disc Nut" + "Material" still names no disc.
    """
    if ctx.ontology is not None and requirement.attribute is not None:
        shape = _shape(requirement)
        found = resolve_attribute(label, shape, ctx.ontology)
        if found is None and key:
            found = resolve_attribute(key, shape, ctx.ontology)
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
        named = label_names(requirement, field.label, ctx, options, field.key)
        return field.value if named else None
    return _inline_value(requirement, field.value, ctx, options)


# ---------------------------------------------------------------- numbers


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
        return canonical_number(number, unit, attr, ctx.ontology)
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


def _impossible(printed: Sequence[tuple[float, float]], attribute: Attribute) -> bool:
    """True if a printed number is below what can exist ("-460C": the degree sign read as 0)."""
    floor = attribute.physical_min
    if floor is None:
        return False
    return any(math.isfinite(end) and end < floor for pair in printed for end in pair)


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
    printed = _comparable(quantities(value, options.designation_prefixes), requirement, ctx)
    if attr is not None and _impossible(printed, attr):
        return _Outcome(None, _UNREADABLE)
    statuses = [_judge_range(op, want, have, exact, tol) for have in printed]
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


def _families(text: str, ontology: Ontology | None) -> frozenset[str]:
    """The ontology's material groups a value names ("SS ASTM A276" -> stainless_steel)."""
    if ontology is None:
        return frozenset()
    found = f" {' '.join(words(text))} "
    return frozenset(
        group.name
        for group in ontology.equivalents
        if any(f" {' '.join(words(term))} " in found for term in group.terms)
    )


def _coded(tokens: frozenset[str]) -> bool:
    """True if any token is a code or a number ("a216_wcb", "125"), not only words."""
    return any(is_code(t) or t.isdigit() for t in tokens)


def judge_code(
    requirement: Requirement, value: str, ctx: MatchContext, options: ComparatorOptions
) -> _Outcome:
    """What one field's value decides for a code requirement.

    Every token of an option must appear ("ASTM A216 Gr. WCB" needs a216 and wcb). With
    options ("CA15/CF8M") all found is full, some found is partial, unless the tender wrote
    "or" ("NITRILE OR SS"): then one found is full. A value that states
    something else (a code, or a short value such as "Flanged") is missing. The same material
    named another way is full if the tender names no grade ("CI" against "Cast Iron EN 1561"), and
    partial if the catalogue leaves its grade out ("SS ASTM A276" against "Stainless Steel"): that
    is no contradiction. Without an ontology's equivalents only a code can fail against a code:
    "CI" against "A216 WCB" may or may not be the same material. An empty value, bare numbers ("5"
    in a "No." column, B4) or a sentence without codes decide nothing.
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
    if len(found) == len(wanted) or (found and requirement.any_alternative):
        return _Outcome(MatchStatus.FULL_MATCH, "")
    if found:
        return _Outcome(MatchStatus.PARTIAL_MATCH, f"{len(found)} of {len(wanted)} options found")
    same_material = _families(requirement.value_text, ctx.ontology) & _families(value, ctx.ontology)
    if same_material and len(wanted) == 1 and not _coded(wanted[0]):
        return _Outcome(MatchStatus.FULL_MATCH, "same material, named another way")
    if not any(_coded(w) for w in wanted) and _coded(have):
        return _Outcome(None, "tender names words, catalogue names codes: not like-for-like")
    if any(w & have for w in wanted):
        return _Outcome(MatchStatus.PARTIAL_MATCH, "near miss: only part of the code found")
    if same_material:
        return _Outcome(MatchStatus.PARTIAL_MATCH, "same material, grade not printed")
    knows_materials = ctx.ontology is not None and bool(ctx.ontology.equivalents)
    if not knows_materials and not (_coded(have) and any(_coded(w) for w in wanted)):
        return _Outcome(None, "no material equivalents: cannot tell if these are the same")
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


def routed(requirement: Requirement) -> Comparison | None:
    """The result of a requirement catalogues cannot decide, or None if they may.

    Nothing is searched for these (CLAUDE.md section 7): vendor documents are not ingested, so
    they have no evidence and are not failures; a named make is recorded and never fails
    another make (D4); a buyer detail has nothing to compare.

    Args:
        requirement: An atomic requirement.

    Returns:
        The result, or None if the requirement goes on to the catalogues.
    """
    reason = None
    makes = [requirement.named_make] if requirement.named_make else []
    makes += requirement.acceptable_makes
    if makes:
        reason = f"tender names makes ({', '.join(makes)}): recorded, not judged"
    elif requirement.req_type is RequirementType.VAGUE:
        reason = "nothing to verify in this wording"
    elif requirement.evidence is EvidenceSource.VENDOR_DOCUMENTS:
        reason = "needs the vendor's own documents: none ingested, so no evidence"
    elif requirement.evidence is None:
        reason = "could not tell what kind of requirement this is"
    if reason is not None:
        return _unsupported(requirement, reason, LabelBasis.NONE)
    if requirement.evidence is EvidenceSource.NONE:
        return Comparison(
            requirement_id=requirement.id,
            status=MatchStatus.NOT_APPLICABLE,
            needs_review=False,
            decided_by=DecisionSource.NONE,
            basis=LabelBasis.NONE,
            reason="nothing to compare",
        )
    return None


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
    route = routed(requirement)
    if route is not None:
        return route
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
    result = _decide(requirement, judged, basis, options)
    return result.model_copy(update={"needs_review": True}) if requirement.flags else result


def offered_as_option(
    requirement: Requirement,
    comparison: Comparison,
    fields: Sequence[LocatedField],
    ctx: MatchContext,
    options: ComparatorOptions,
) -> Comparison:
    """Soften a failure if the document's options list offers the tender's whole value.

    A table showing the standard build fails "LCB", yet the same document may offer LCB on order.
    Positive beats negative only so far: the list does not say which part or model the value is
    for, so the result is partial + review, never full.

    Args:
        requirement: An atomic requirement.
        comparison: Its result against the document.
        fields: The document's options fields (`option_fields`).
        ctx: The item's ontology context (may be empty).
        options: Comparator settings.

    Returns:
        The comparison, changed only if it was a failure of a code value and a field offers it.
    """
    if comparison.status is not MatchStatus.MISSING_CAPABILITY:
        return comparison
    if requirement.kind is ValueKind.NUMERIC:
        return comparison
    reason = "fails in a table, but the document's options list offers it"
    outcome = _Outcome(MatchStatus.PARTIAL_MATCH, reason)
    offered = [
        _evidence(located, located.field.value, outcome)
        for located in fields
        if judge_code(requirement, located.field.value, ctx, options).status
        is MatchStatus.FULL_MATCH
    ]
    if not offered:
        return comparison
    return comparison.model_copy(
        update={
            "status": MatchStatus.PARTIAL_MATCH,
            "needs_review": True,
            "reason": reason,
            "evidence": [*offered[: options.max_evidence], *comparison.evidence],
        }
    )


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
    misread = any(e.reason == _UNREADABLE for e in judged)
    if not decided:
        reason = _UNREADABLE if misread else judged[0].reason
        result = _unsupported(requirement, reason, basis)
        return result.model_copy(update={"evidence": list(judged[: options.max_evidence])})
    best = max(decided, key=lambda e: _RANK[e.status] if e.status else 0)
    status = best.status or MatchStatus.UNSUPPORTED
    support = [e for e in decided if e.status is status]
    return Comparison(
        requirement_id=requirement.id,
        status=status,
        needs_review=misread or status is MatchStatus.PARTIAL_MATCH,
        decided_by=DecisionSource.RULE,
        basis=basis,
        reason=f"{best.reason}; {_UNREADABLE}".strip("; ") if misread else best.reason,
        evidence=support[: options.max_evidence],
    )
