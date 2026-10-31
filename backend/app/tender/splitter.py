"""Split a line item's description into atomic requirements, by printed line (case T4, D2).

Tender specs print one "KEY : value" per line, so a line with a short key before ":" starts a
requirement and a line without one continues it (a wrapped value: "... NON" / "RUBBING").
An empty line (a paragraph break from the PDF layout) ends it, and a paragraph that does not
start with a key is a statement ("THE VALVE SHALL BE TORQUE SEATED, METAL SEATED, ...").

A value listing several things ("A479 Type SS 410/17-4PH SS,SINGLE PIECE") becomes a parent
with one child per comma part; "/" stays inside a part as alternatives of one requirement.

A key can run straight into its value ("Pump Shaft Carbon Steel"): attribute words followed by a
material name are a key and a value, whatever ":" comes later ("impeller BRONZE IS:318").
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final

from app.ontology.schema import Ontology
from app.retrieval.text_normalizer import fold_text
from app.schemas.enums import Operator
from app.schemas.requirements import LineItem, Requirement, ValueKind
from app.tender.normalizer import (
    has_code,
    parse_value,
    resolve_attribute,
    size_form,
    split_size_rating,
    stems,
    temperature_form,
)

SIZE_LABEL: Final = "SIZE"
DEFAULT_INLINE_KEY_MAX_WORDS: Final = 3
RATING_LABEL: Final = "CLASS"
_LABEL_LINE: Final = re.compile(r"^(?P<label>[^:]+?)\s*:\s*(?P<value>.*)$")
# Parts of a list: split on commas that are not inside a number ("1,000").
_PART_SPLIT: Final = re.compile(r",\s*(?!\d{3}\b)")
# "THE VALVE SHALL BE TORQUE SEATED" -> "TORQUE SEATED" (the value; the quote keeps it all).
_STATEMENT_LEAD: Final = re.compile(
    r"^(?:the\s+\w+\s+)?(?:shall|should|must|will)\s+(?:be\s+)?", re.IGNORECASE
)


@dataclass(slots=True)
class LineGroup:
    """The printed lines of one requirement: its label (None for a statement) and value."""

    label: str | None
    lines: list[str] = field(default_factory=list)
    merged: bool = False  # the key ran into the value with nothing between them

    @property
    def value(self) -> str:
        """The value lines joined with single spaces (a wrapped value reads as one)."""
        return " ".join(line.strip() for line in self.lines if line.strip())


def label_of(line: str, max_words: int) -> tuple[str, str] | None:
    """`(label, value)` if the line is "KEY : value" with a short key holding letters."""
    match = _LABEL_LINE.match(line.strip())
    if not match:
        return None
    label = match.group("label").strip()
    if not any(ch.isalpha() for ch in label) or len(label.split()) > max_words or "," in label:
        return None  # a comma means a list that merely has a key inside it
    return label, match.group("value").strip()


def inline_key(label: str, value: str, max_words: int) -> tuple[str, str]:
    """The inner key of "MATERIAL: Body: ASTM A216": it names the value, the outer one a group.

    Args:
        label: The line's key.
        value: The rest of the line.
        max_words: Longest inner key, in words; a longer one is a sentence with a colon in it.

    Returns:
        The inner key and its value, or the line's own if the value has no key. A key with a
        digit is not one ("EN 1561 NOMINAL SIZE: 50 MM"), nor is a web address.
    """
    inner = label_of(value, max_words)
    if inner is None or any(ch.isdigit() for ch in inner[0]) or inner[1].startswith("/"):
        return label, value
    return inner


def group_lines(
    lines: Sequence[str],
    title: str,
    max_words: int,
    inline_max_words: int = DEFAULT_INLINE_KEY_MAX_WORDS,
    line_key: Callable[[str], tuple[str, str] | None] | None = None,
) -> list[LineGroup]:
    """Group description lines into requirements (see module doc).

    Args:
        lines: Description lines, "" marking a paragraph break.
        title: The item title; a first line repeating it is not a requirement.
        max_words: Longest key, in words.
        inline_max_words: Longest key inside a value, in words.
        line_key: Finds a key merged into its value; it goes before a ":" on the line.

    Returns:
        One group per requirement, in printed order.
    """
    groups: list[LineGroup] = []
    current: LineGroup | None = None
    for i, line in enumerate(lines):
        if not line.strip():
            current = None
            continue
        if i == 0 and fold_text(line).strip() == fold_text(title).strip():
            continue
        merged = line_key(line) if line_key else None
        if merged is not None:
            current = LineGroup(label=merged[0], lines=[merged[1]], merged=True)
            groups.append(current)
            continue
        labelled = label_of(line, max_words)
        if labelled is not None:
            key, value = inline_key(labelled[0], labelled[1], inline_max_words)
            current = LineGroup(label=key, lines=[value])
            groups.append(current)
        elif current is not None:
            current.lines.append(line)
        else:
            current = LineGroup(label=None, lines=[line])
            groups.append(current)
    return groups


def split_parts(value: str) -> list[str]:
    """Comma-separated parts of a value, empty parts dropped."""
    return [p.strip() for p in _PART_SPLIT.split(value) if p.strip()]


@dataclass(frozen=True, slots=True)
class SplitOptions:
    """Splitting thresholds (built from settings by the tender pipeline)."""

    label_max_words: int
    enum_max_words: int
    inline_key_max_words: int = DEFAULT_INLINE_KEY_MAX_WORDS


def _starts_with_material(text: str, ontology: Ontology) -> bool:
    """True if the text's first words are one of the ontology's material names."""
    found = stems(text)
    return any(found[: len(m)] == m for m in (stems(n) for n in ontology.material_names))


def _is_material(text: str, ontology: Ontology | None) -> bool:
    """True if the text names a material or holds a code."""
    return has_code(text) or (ontology is not None and _starts_with_material(text, ontology))


def merged_line_key(line: str, ontology: Ontology | None, max_words: int) -> tuple[str, str] | None:
    """`(key, value)` of a line whose key runs into a material: "Pump Shaft Carbon Steel".

    Needs words that are exactly a synonym of a non-numeric attribute, then a material name;
    "Shaft Nut - 1 No." and "Stem Length 220 mm" stay as they are.

    Args:
        line: One printed line.
        ontology: Category ontology, or None.
        max_words: Longest key, in words.

    Returns:
        The key and the value, or None.
    """
    tokens = line.split()
    if ontology is None:
        return None
    for end in range(min(max_words, len(tokens) - 1), 0, -1):
        key, value = " ".join(tokens[:end]), " ".join(tokens[end:])
        if not (key[-1].isalnum() and value[0].isalnum()):
            continue  # "Casing : Cast Iron" and "Casing - Cast Iron" have a separator already
        if ":" in key or not _starts_with_material(value, ontology):
            continue
        wanted = stems(key)
        if any(
            attribute.dimension == "none" and wanted in (stems(s) for s in attribute.synonyms)
            for attribute in ontology.attributes
        ):
            return key, value
    return None


def implicit_label(
    part: str, ontology: Ontology | None, options: SplitOptions
) -> tuple[str, str] | None:
    """`(label, value)` of a part with no ":" whose first words name an attribute before a code.

    "BODY A351CF8" -> ("BODY", "A351CF8"). Needs an ontology, words that are exactly one of the
    attribute's synonyms, and a code in the value: otherwise the first words of a sentence
    are not a label. A value with a unit is a measurement: "STEM LENGTH 220 MM" names no stem.

    Args:
        part: One part of a compound value.
        ontology: Category ontology, or None.
        options: Splitting thresholds.

    Returns:
        The label and value, or None.
    """
    tokens = part.split()
    if ontology is None or len(tokens) < 2:
        return None
    measured = any(t.lower() in ontology.units for t in tokens)
    for end in range(min(options.label_max_words, len(tokens) - 1), 0, -1):
        prefix, value = " ".join(tokens[:end]), " ".join(tokens[end:])
        readable = temperature_form(value) is not None or (has_code(value) and not measured)
        if any(ch.isdigit() for ch in prefix) or not readable:
            continue
        parsed = parse_value(prefix, value, ontology, options.enum_max_words)
        name = resolve_attribute(prefix, parsed, ontology)
        attribute = ontology.attribute(name) if name else None
        if attribute and stems(prefix) in {stems(s) for s in attribute.synonyms}:
            return prefix, value
    return None


def _part_fields(
    label: str | None,
    part: str,
    numeric_line: bool,
    after_title: bool,
    ontology: Ontology | None,
    options: SplitOptions,
) -> tuple[str | None, str]:
    """A compound part's label and value.

    Its own key wins. A size or rating is labelled by what it is, whatever the line's label says
    ("SIZE:2", PN50" gives a class). On a numeric line, a part without a number is not that
    attribute ("SIZE:2", BW ENDS"), so it does not inherit the label. Otherwise the line's label,
    or the attribute words the part starts with. Bare millimetres right after the item's name
    are its size ("GLOBE VALVE, 15MM"); anywhere else they may measure anything.
    """
    own = label_of(part, options.label_max_words)
    if own is not None and not (ontology and _starts_with_material(own[0], ontology)):
        return own  # "BRONZE IS:318" is a value with a colon in it, not a key
    parsed = parse_value(label, part, ontology, options.enum_max_words)
    if size_form(part) is not None:
        return label or SIZE_LABEL, part
    if after_title and label is None and parsed.unit == "mm":
        return SIZE_LABEL, part
    if parsed.is_designation:
        keeps = label is not None and resolve_attribute(label, parsed, ontology) is not None
        return label if keeps else RATING_LABEL, part
    inherited = None if numeric_line and parsed.kind is not ValueKind.NUMERIC else label
    implicit = implicit_label(part, ontology, options) if inherited is None else None
    return implicit or (inherited, part)


def _size_rating_parts(parts: Sequence[str]) -> list[str]:
    """Parts with a size + rating value cut in two ("6 inch # 300 Class")."""
    out: list[str] = []
    for part in parts:
        pair = split_size_rating(part)
        out.extend(pair if pair else (part,))
    return out


def _requirement(
    item: LineItem,
    req_id: str,
    position: int,
    label: str | None,
    text: str,
    ontology: Ontology | None,
    options: SplitOptions,
    parent_id: str | None = None,
    printed: str | None = None,
) -> Requirement:
    """One requirement; for a statement the value drops its "THE VALVE SHALL BE" lead."""
    value = _STATEMENT_LEAD.sub("", text).strip() if label is None else text
    parsed = parse_value(label, value, ontology, options.enum_max_words)
    return Requirement(
        id=req_id,
        parent_id=parent_id,
        item_no=item.item_no,
        position=position,
        label=label,
        attribute=resolve_attribute(label, parsed, ontology),
        kind=parsed.kind,
        operator=parsed.operator,
        value_text=value,
        value_num=parsed.number if parsed.number_max is None else None,
        value_min=parsed.number if parsed.number_max is not None else None,
        value_max=parsed.number_max,
        unit=parsed.unit,
        canonical_unit=parsed.canonical_unit,
        alternatives=list(parsed.alternatives),
        any_alternative=parsed.any_alternative,
        # Verbatim: "KEY : value" as printed; a part of a compound value is quoted alone.
        raw_text=printed or (text if label is None or parent_id else f"{label} : {text}"),
        document=item.document,
        page_number=item.page_number,
    )


def item_requirements(
    item: LineItem, ontology: Ontology | None, options: SplitOptions
) -> list[Requirement]:
    """All requirements of one line item: parents first, then their atomic parts.

    Args:
        item: The line item.
        ontology: Category ontology (unit spellings, attribute names), or None.
        options: Splitting thresholds.

    Returns:
        Requirements in printed order. A compound value gives a TEXT parent followed by its
        children (`parent_id` set); only requirements without children are checked.
    """
    out: list[Requirement] = []
    groups = group_lines(
        item.description_lines,
        item.title,
        options.label_max_words,
        options.inline_key_max_words,
        lambda line: merged_line_key(line, ontology, options.label_max_words),
    )
    for n, group in enumerate(groups, start=1):
        req_id = f"{item.item_no}.{n}"
        parts = _size_rating_parts(split_parts(group.value))
        printed = f"{group.label} {group.value}" if group.merged else None
        parent = _requirement(
            item, req_id, len(out), group.label, group.value, ontology, options, printed=printed
        )
        if len(parts) <= 1:
            out.append(parent)
            continue
        out.append(
            parent.model_copy(
                update={
                    "kind": ValueKind.TEXT,
                    "operator": Operator.TEXT,
                    "attribute": None,
                    "alternatives": [],
                    "any_alternative": False,
                }
            )
        )
        first = parse_value(group.label, parts[0], ontology, options.enum_max_words)
        numeric_line = group.label is not None and first.kind is ValueKind.NUMERIC
        names_item = fold_text(parts[0]).strip() == fold_text(item.title).strip()
        for m, part in enumerate(parts, start=1):
            after_title = names_item and m == 2
            # A merged key was recognised by its material: it does not name "Type -enclosed".
            names_part = not group.merged or _is_material(part, ontology)
            label, value = _part_fields(
                group.label if names_part else None,
                part,
                numeric_line,
                after_title,
                ontology,
                options,
            )
            out.append(
                _requirement(
                    item, f"{req_id}.{m}", len(out), label, value, ontology, options, req_id, part
                )
            )
    return out


def leaves(requirements: Sequence[Requirement]) -> list[Requirement]:
    """Requirements that have no children: the atomic ones a comparator checks."""
    parents = {r.parent_id for r in requirements if r.parent_id}
    return [r for r in requirements if r.id not in parents]
