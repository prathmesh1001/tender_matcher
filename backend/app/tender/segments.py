"""Cut specification text at item boundaries (T1), so each line reaches only its own items.

A specification file may describe items outside any schedule row, in two ways:

- one item at a time: "Item 4 - CS010810077 - Cast Steel Globe Valve ...", which can start in
  the middle of a paragraph or in a table header;
- a block shared by several items: a "For Item Sl. No.: 1, 2 & 3" row and the rows after it.
  A row in it can differ per item ("# 300 for Item 4, 5 & 6 & # 800 for Item 7"); only the
  clause naming the item is kept, so no item is given another item's value.

Text belongs to the last boundary before it, until an end marker. Item numbers only go up: a
number seen again means the spec has ended and another section repeats the list (delivery
periods). Text before the first boundary and after the end is left for general requirements.
All patterns live in `config/requirement_rules.yaml`.
"""

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType
from app.schemas.requirements import RequirementRules
from app.tender.general import english_part

_NUMBER = re.compile(r"\d+")


@dataclass(frozen=True, slots=True)
class SegmentRules:
    """Compiled segmentation patterns."""

    item_start: re.Pattern[str]
    item_group: re.Pattern[str]
    item_qualifier: re.Pattern[str]
    clause_split: re.Pattern[str]
    end: re.Pattern[str]
    cut: re.Pattern[str]

    @classmethod
    def from_rules(cls, rules: RequirementRules) -> SegmentRules:
        """Compile the patterns of the rules file."""
        flags = re.IGNORECASE
        return cls(
            item_start=re.compile(rules.item_start_pattern, flags),
            item_group=re.compile(rules.item_group_pattern, flags),
            item_qualifier=re.compile(rules.item_qualifier_pattern, flags),
            clause_split=re.compile(rules.item_clause_split_pattern, flags),
            end=re.compile(rules.segment_end_pattern, flags),
            cut=re.compile(rules.segment_cut_pattern, flags),
        )


@dataclass(frozen=True, slots=True)
class SegmentLine:
    """One line of specification text and where it is printed."""

    text: str
    page_number: int
    unreadable: bool = False  # the scan lost the value (name_segments); never guessed


@dataclass(slots=True)
class Segment:
    """Lines that apply to the same items."""

    item_nos: tuple[str, ...]
    lines: list[SegmentLine] = field(default_factory=list)


@dataclass(slots=True)
class FileSegments:
    """The segments of one file and the chunks they use up."""

    segments: list[Segment] = field(default_factory=list)
    used_chunks: set[int] = field(default_factory=set)


def row_line(chunk: ChunkDraft) -> str:
    """A table row as one line: "label : value" for a label/value row, else its longest cell."""
    header: list[str] = chunk.meta.get("header", [])
    cells = [english_part(c) for c in chunk.meta.get("cells", [])]
    filled = [c for c in cells if c]
    labelled = any(english_part(h) and not h.startswith("Column ") for h in header)
    if not labelled and len(filled) == 2:
        return f"{filled[0]} : {filled[1]}"
    return max(filled, key=len, default="")


def item_clause(line: str, item_no: str, rules: SegmentRules) -> str | None:
    """The line as it applies to one item.

    Args:
        line: "label : value", where the value may have clauses naming items.
        item_no: The item.
        rules: Compiled patterns.

    Returns:
        The line unchanged if no clause names an item; with only this item's clause if one
        does; None if clauses name only other items, so the line does not apply to it.
    """
    if not rules.item_qualifier.search(line):
        return line
    label, separator, value = line.rpartition(" : ")
    for clause in rules.clause_split.split(value):
        found = rules.item_qualifier.search(clause)
        if found and item_no in _NUMBER.findall(found.group("nos")):
            kept = (clause[: found.start()] + " " + clause[found.end() :]).strip(" :/&")
            return f"{label}{separator}{' '.join(kept.split())}"
    return None


class _Walker:
    """Walks one file's chunks in order, tracking which items the text belongs to."""

    def __init__(self, item_nos: Collection[str], rules: SegmentRules) -> None:
        self.known = {n for n in item_nos if n.isdigit()}
        self.rules = rules
        self.result = FileSegments()
        self.current: Segment | None = None
        self.highest = 0
        self.ended = False

    def _start(self, numbers: Sequence[str]) -> bool:
        """Open a segment for these items; numbers going back end the spec. False if ignored."""
        known = [n for n in numbers if n in self.known]
        if not known:
            return False
        if min(int(n) for n in known) <= self.highest:
            self.ended = True
            return True
        self.highest = max(int(n) for n in known)
        self.current = Segment(tuple(known))
        self.result.segments.append(self.current)
        return True

    def _emit(self, text: str, page: int) -> None:
        """Add text to the open segment, one line each, without form boilerplate."""
        if self.current is None:
            return
        kept = self.rules.cut.split(text, maxsplit=1)[0]
        for raw in kept.splitlines():
            line = english_part(raw)
            if line:
                self.current.lines.append(SegmentLine(line, page))

    def _walk_text(self, text: str, page: int) -> bool:
        """Split text at item starts. True if all of it went to segments."""
        whole = self.current is not None
        position = 0
        for match in self.rules.item_start.finditer(text):
            if match.group("no") not in self.known:
                continue
            before = text[position : match.start()]
            whole = whole or not before.strip()
            self._emit(before, page)  # the text before a boundary is the previous item's
            self._start([match.group("no")])
            if self.ended:
                return False
            position = match.end()
        self._emit(text[position:], page)
        return whole

    def _walk_row(self, chunk: ChunkDraft, previous_header: str) -> bool:
        """One table row; a header naming a new item opens it. True if the chunk is used up."""
        header = " ".join(chunk.meta.get("header", []))
        line = row_line(chunk)
        group = self.rules.item_group.fullmatch(line.strip())
        if group:
            self._start(_NUMBER.findall(group.group("nos")))
            return not self.ended and self.current is not None
        if header != previous_header:
            found = self.rules.item_start.search(header)
            if found:
                self._start([found.group("no")])
                if self.ended:
                    return False
        return self._walk_text(line, chunk.page_number)

    def walk(self, chunks: Sequence[ChunkDraft]) -> FileSegments:
        """Assign every chunk's text to a segment, until the spec ends."""
        previous_header = ""
        for index, chunk in enumerate(chunks):
            at_end = self.current is not None and (
                self.rules.end.search(chunk.section or "")
                or self.rules.end.search(chunk.raw_text.lstrip())
            )
            if at_end:
                break
            if chunk.chunk_type is ChunkType.TABLE_ROW:
                used = self._walk_row(chunk, previous_header)
                previous_header = " ".join(chunk.meta.get("header", []))
            else:
                used = self._walk_text(chunk.raw_text, chunk.page_number)
            if self.ended:
                break
            if self.current is not None:
                # A chunk ends a requirement: lines of two chunks never read as one.
                self.current.lines.append(SegmentLine("", chunk.page_number))
            if used and self.current is not None:
                self.result.used_chunks.add(index)
        return self.result


def segment_file(
    chunks: Sequence[ChunkDraft], item_nos: Collection[str], rules: SegmentRules
) -> FileSegments:
    """Cut one specification file at the boundaries of the given items.

    Args:
        chunks: The file's chunks, in order.
        item_nos: The tender's item numbers; other numbers are not boundaries.
        rules: Compiled patterns.

    Returns:
        The segments, and the chunks whose text they hold completely.
    """
    return _Walker(item_nos, rules).walk(chunks)
