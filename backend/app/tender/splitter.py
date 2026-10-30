"""Split a line item's description into atomic requirements, by printed line (case T4, D2).

Tender specs print one "KEY : value" per line, so a line with a short key before ":" starts a
requirement and a line without one continues it (a wrapped value: "... NON" / "RUBBING").
An empty line (a paragraph break from the PDF layout) ends it, and a paragraph that does not
start with a key is a statement ("THE VALVE SHALL BE TORQUE SEATED, METAL SEATED, ...").

A value listing several things ("A479 Type SS 410/17-4PH SS,SINGLE PIECE") becomes a parent
with one child per comma part; "/" stays inside a part as alternatives of one requirement.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from app.ontology.schema import Ontology
from app.retrieval.text_normalizer import fold_text
from app.schemas.enums import Operator
from app.schemas.requirements import LineItem, Requirement, ValueKind
from app.tender.normalizer import parse_value, resolve_attribute

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
    if not any(ch.isalpha() for ch in label) or len(label.split()) > max_words:
        return None
    return label, match.group("value").strip()


def group_lines(lines: Sequence[str], title: str, max_words: int) -> list[LineGroup]:
    """Group description lines into requirements (see module doc).

    Args:
        lines: Description lines, "" marking a paragraph break.
        title: The item title; a first line repeating it is not a requirement.
        max_words: Longest key, in words.

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
        labelled = label_of(line, max_words)
        if labelled is not None:
            current = LineGroup(label=labelled[0], lines=[labelled[1]])
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


def _requirement(
    item: LineItem,
    req_id: str,
    position: int,
    label: str | None,
    text: str,
    ontology: Ontology | None,
    options: SplitOptions,
    parent_id: str | None = None,
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
        # Verbatim: "KEY : value" as printed; a part of a compound value is quoted alone.
        raw_text=text if label is None or parent_id else f"{label} : {text}",
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
    groups = group_lines(item.description_lines, item.title, options.label_max_words)
    for n, group in enumerate(groups, start=1):
        req_id = f"{item.item_no}.{n}"
        parts = split_parts(group.value)
        if len(parts) <= 1:
            out.append(
                _requirement(item, req_id, len(out), group.label, group.value, ontology, options)
            )
            continue
        parent = _requirement(item, req_id, len(out), group.label, group.value, ontology, options)
        out.append(
            parent.model_copy(
                update={
                    "kind": ValueKind.TEXT,
                    "operator": Operator.TEXT,
                    "attribute": None,
                    "alternatives": [],
                }
            )
        )
        for m, part in enumerate(parts, start=1):
            out.append(
                _requirement(
                    item, f"{req_id}.{m}", len(out), group.label, part, ontology, options, req_id
                )
            )
    return out


def leaves(requirements: Sequence[Requirement]) -> list[Requirement]:
    """Requirements that have no children: the atomic ones a comparator checks."""
    parents = {r.parent_id for r in requirements if r.parent_id}
    return [r for r in requirements if r.id not in parents]
