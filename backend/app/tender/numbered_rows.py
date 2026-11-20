"""An item's own description, found as the row numbered like the item in a specification table.

Some tenders list items by title only (`boq_items.csv`: "BALL VALVE 40MM") and print each item's
specification as a numbered row of a table in another file, whose header the reader did not
recognise, so the cells have no names: `Column 1: 1; Column 4: BALL VALVE 40MM-10KG/CM2 VALVE
TYPE: THREE-PIECE DESIGN, ... BODY & ENDS: CF8M, CLASS: 600, ...` (8210284 boq_spec.pdf). The
rows of one file describe the items when, and only when:

- each row's first non-empty cell is a whole number, and its longest cell has at least
  `min_words` words (a description, not a unit or a price);
- the numbers of that file's rows are exactly the item numbers, each once.

Anything else (missing or extra numbers, a number twice, two files that both fit) gives nothing:
never guessed. The description keeps its printed lines where a line opens with a label
("END STYLE: LUGGED"); other lines are wrapped text and join the line before. Pure functions;
the caller turns the lines into requirements with the normal reader.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

# A printed line that opens with a label: one to three words, then ": " ("END STYLE: LUGGED",
# "BODY & ENDS: CF8M"). "IS:318" has no space after its colon and is no label.
_LABEL_START: Final = re.compile(r"^(?:[A-Za-z][\w/.&-]*\s+|&\s+){0,2}[A-Za-z][\w/.&-]*:\s")


@dataclass(frozen=True, slots=True)
class TableRow:
    """One table row of a tender file, as its cells and each cell's printed lines."""

    document: str
    page_number: int
    cells: Sequence[str]
    cell_lines: Sequence[Sequence[str]]


@dataclass(frozen=True, slots=True)
class NumberedRow:
    """The row numbered like an item: where it is and its description lines."""

    document: str
    page_number: int
    number: str
    description: str  # the description cell as one text
    lines: list[str]  # the description as reader lines


def description_lines(printed: Sequence[str]) -> list[str]:
    """A description cell's printed lines, wrapped lines joined to the line before.

    A line opening with "LABEL: " starts a new line; any other line continues the one before.

    Args:
        printed: The cell's printed lines.

    Returns:
        The lines, none empty.
    """
    out: list[str] = []
    for line in (" ".join(p.split()) for p in printed):
        if not line:
            continue
        if out and not _LABEL_START.match(line):
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
    return out


def numbered_row(row: TableRow, min_words: int) -> NumberedRow | None:
    """The row's number and description, if it is a numbered description row.

    Args:
        row: A table row.
        min_words: Fewest words of a description cell.

    Returns:
        The numbered row, or None.
    """
    filled = [i for i, cell in enumerate(row.cells) if cell.strip()]
    if len(filled) < 2:
        return None
    number = row.cells[filled[0]].strip().rstrip(".").strip()
    if not number.isdigit():
        return None
    index = max(filled[1:], key=lambda i: len(row.cells[i]))
    description = " ".join(row.cells[index].split())
    if len(description.split()) < min_words:
        return None
    printed = row.cell_lines[index] if index < len(row.cell_lines) else []
    lines = description_lines(printed) or [description]
    return NumberedRow(row.document, row.page_number, str(int(number)), description, lines)


def rows_for_items(
    item_nos: Sequence[str], rows: Sequence[TableRow], min_words: int
) -> dict[str, NumberedRow]:
    """For each item number, its row in the one file whose numbered rows match the items.

    Args:
        item_nos: The items' numbers ("1" ... "12").
        rows: The table rows of the tender's files, in order.
        min_words: Fewest words of a description cell.

    Returns:
        Item number -> its row; empty unless exactly one file matches every item exactly once.
    """
    wanted = sorted(item_nos)
    by_file: dict[str, list[NumberedRow]] = {}
    for row in rows:
        found = numbered_row(row, min_words)
        if found is not None:
            by_file.setdefault(found.document, []).append(found)
    fitting = [found for found in by_file.values() if sorted(r.number for r in found) == wanted]
    if len(fitting) != 1:
        return {}
    return {r.number: r for r in fitting[0]}
