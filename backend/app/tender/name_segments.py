"""Cut a specification file at blocks headed by an item's name, when no item numbers are printed.

Some annexures describe each item under its own name, not its number (8157758 Annexure A):

    SS ball valve set - 1 set shall consists of the following SS ball values
    1" - 10 nos

and a scan often loses the Sl. No column, so number boundaries (`segments`) find nothing. The
item's name is its BOQ title without "set as per Annexure A". A line is a block heading when the
name opens it, after at most a few stray words (a surviving Sl. No), and is followed by "set" or
a dash; letters OCR confuses ("Gl" for "GI") are read alike; the longest name wins, so
"SS hex head bolt" is never "Hex head bolt". Each item starts once. Inside a block, a set line
("1" - 10 nos") becomes "Size : 1"" when it holds a pipe size; a size the scan lost ("%" for
1/2" or 3/4") is kept but marked unreadable, never guessed. All patterns live in
`config/requirement_rules.yaml`.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.ontology.units import spell_fractions
from app.retrieval.text_normalizer import fold_text
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType
from app.schemas.requirements import RequirementRules
from app.tender.general import english_part
from app.tender.segments import FileSegments, Segment, SegmentLine, row_line

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class NameRules:
    """Compiled patterns for name-headed blocks and set lines."""

    title_suffix: re.Pattern[str]
    heading_follow: re.Pattern[str]
    max_lead_words: int
    set_line: re.Pattern[str]
    set_size: re.Pattern[str]
    unreadable: re.Pattern[str] | None
    size_label: str
    item_label: str
    end: re.Pattern[str]
    confusions: dict[int, str]

    @classmethod
    def from_rules(cls, rules: RequirementRules) -> NameRules | None:
        """Compile the rules; None when the rules file does not define name headings."""
        if not rules.item_title_suffix_pattern or not rules.set_line_pattern:
            return None
        flags = re.IGNORECASE
        return cls(
            title_suffix=re.compile(rules.item_title_suffix_pattern, flags),
            heading_follow=re.compile(rules.name_heading_follow_pattern, flags),
            max_lead_words=rules.name_heading_max_lead_words,
            set_line=re.compile(rules.set_line_pattern, flags),
            set_size=re.compile(rules.set_size_pattern, flags),
            unreadable=(
                re.compile(rules.set_unreadable_pattern) if rules.set_unreadable_pattern else None
            ),
            size_label=rules.set_size_label,
            item_label=rules.set_item_label,
            end=re.compile(rules.segment_end_pattern, flags),
            confusions=str.maketrans(rules.name_ocr_confusions),
        )


def item_name(title: str, rules: NameRules) -> tuple[str, ...]:
    """The words naming an item: its title without "set as per Annexure A" (folded).

    Only an item whose title points to an annexure (or is a set) is described under its name;
    any other title names nothing, so its own repeated title never opens a block.

    Args:
        title: The BOQ item title.
        rules: Compiled patterns.

    Returns:
        The name's words in matching form, e.g. ("ss", "baii", "vaive") with OCR-confused letters
        folded; empty when the title does not point to an annexure.
    """
    name = original = fold_text(title).strip().translate(rules.confusions)
    while True:
        shorter = rules.title_suffix.sub("", name).strip()
        if shorter == name:
            break
        name = shorter
    return tuple(_WORD.findall(name)) if name != original else ()


def _heading_pattern(name: tuple[str, ...], rules: NameRules) -> re.Pattern[str]:
    """The name at the start of a line, after at most `max_lead_words` stray words."""
    lead = rf"^(?:\S+\s+){{0,{rules.max_lead_words}}}?"
    return re.compile(lead + r"[^a-z0-9]*".join(re.escape(w) for w in name) + r"(?![a-z0-9])")


def heading_item(
    line: str, patterns: Sequence[tuple[str, re.Pattern[str]]], rules: NameRules
) -> str | None:
    """The item a line opens, or None.

    Args:
        line: One printed line.
        patterns: (item number, heading pattern), longest name first.
        rules: Compiled patterns.

    Returns:
        The item number whose name heads the line and is followed by "set" or a dash.
    """
    folded = fold_text(line).translate(rules.confusions)
    for item_no, pattern in patterns:
        found = pattern.search(folded)
        if found and rules.heading_follow.match(folded[found.end() :]):
            return item_no
    return None


@dataclass(frozen=True, slots=True)
class SetLine:
    """A line of a set as a requirement line, and whether the scan lost its value."""

    text: str
    unreadable: bool


def set_line(line: str, rules: NameRules) -> SetLine | None:
    """A set line as "Size : 1"" (or "Set contents : M6 x 1 x 20"); None for any other line.

    Args:
        line: One printed line.
        rules: Compiled patterns.

    Returns:
        The requirement line, without the count. Unreadable when the scan lost the value.
    """
    found = rules.set_line.match(line.strip())
    if found is None:
        return None
    value = found.group("value").strip(" -")
    if rules.unreadable is not None and rules.unreadable.search(value):
        return SetLine(f"{rules.size_label} : {value}", unreadable=True)
    if rules.set_size.match(fold_text(spell_fractions(value))):
        return SetLine(f"{rules.size_label} : {value}", unreadable=False)
    return SetLine(f"{rules.item_label} : {value}", unreadable=False)


def _lines(chunk: ChunkDraft) -> list[str]:
    """A chunk's printed lines (a table row as one line)."""
    text = row_line(chunk) if chunk.chunk_type is ChunkType.TABLE_ROW else chunk.raw_text
    return [line for raw in text.splitlines() if (line := english_part(raw))]


class _NameWalker:
    """Walks a file's lines, opening a segment at each item's name heading."""

    def __init__(self, titles: Mapping[str, str], rules: NameRules) -> None:
        names = {no: item_name(title, rules) for no, title in titles.items()}
        ordered = sorted(((no, n) for no, n in names.items() if n), key=lambda p: -len(p[1]))
        self.patterns = [(no, _heading_pattern(n, rules)) for no, n in ordered]
        self.rules = rules
        self.started: set[str] = set()
        self.result = FileSegments()
        self.current: Segment | None = None

    def _line(self, line: str, page: int) -> bool:
        """Add one line; True if it went to a segment."""
        item_no = heading_item(line, self.patterns, self.rules)
        if item_no is not None and item_no not in self.started:
            self.started.add(item_no)
            self.current = Segment((item_no,))
            self.result.segments.append(self.current)
            # The heading is the item's own text ("... of Gr8.8 TVS/UNBRAKO"): kept, and the
            # lines it wraps onto join it.
            self.current.lines.append(SegmentLine(line, page))
            return True
        if self.current is None:
            return False
        found = set_line(line, self.rules)
        if found is None:
            self.current.lines.append(SegmentLine(line, page))
        else:
            self.current.lines.append(SegmentLine(found.text, page, found.unreadable))
        return True

    def walk(self, chunks: Sequence[ChunkDraft]) -> FileSegments:
        """Assign every line to the segment of the last heading, until the spec ends."""
        for index, chunk in enumerate(chunks):
            lines = _lines(chunk)
            if self.current is not None and any(self.rules.end.search(x) for x in lines[:1]):
                break
            used = [self._line(line, chunk.page_number) for line in lines]
            if lines and all(used):
                self.result.used_chunks.add(index)
        return self.result


def segment_by_name(
    chunks: Sequence[ChunkDraft], titles: Mapping[str, str], rules: NameRules
) -> FileSegments:
    """Cut one specification file at blocks headed by the items' names.

    Args:
        chunks: The file's chunks, in order.
        titles: Item number -> BOQ title.
        rules: Compiled patterns.

    Returns:
        The segments, and the chunks whose lines they hold completely.
    """
    return _NameWalker(titles, rules).walk(chunks)
