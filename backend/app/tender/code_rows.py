"""An item's own specification row, found by the item's code (material / part code).

Some tenders list items by code only ("PY9756306157") and print each code's specification as a
row of a variant table elsewhere: `Var No: 15; Material Code: PY9756306157; Process Connection
Size: 1½" 300#RF; Thermowell Material: SS 316 / 446; ...` (7865644 boq_spec.pdf p22). Such a
row belongs to the item when, and only when:

- one of its cells is exactly `<label>: <the item's code>` (a trailing "(NACE)" note allowed),
- it has at least `min_cells` other labelled cells, and
- no cell names another item's code (a list of codes, a price table: not this item's row).

An item matching several such rows gets none (never guessed). Pure functions; the caller turns
the returned lines into requirements with the normal reader.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

_TOKEN_SPLIT: Final = re.compile(r"[\s,;:|()\[\]]+")
_CODE: Final = re.compile(r"[A-Za-z0-9]+")
_HAS_LETTER: Final = re.compile(r"[A-Za-z]")
_HAS_DIGIT: Final = re.compile(r"\d")
_TRAILING_NOTE: Final = re.compile(r"\s*\([^)]*\)\s*$")
_CELL_SEPARATOR: Final = "; "
_CONTEXT_SEPARATOR: Final = " | "


@dataclass(frozen=True, slots=True)
class CodeRow:
    """The row found for an item code, as reader lines without the code cell."""

    code: str
    lines: list[str]


def codes_in(texts: Iterable[str], min_length: int) -> set[str]:
    """Code-like tokens: one unbroken run of letters and digits, at least `min_length` long.

    Args:
        texts: Item title and requirement texts.
        min_length: Shortest token read as a code (short ones like "SS316" are values;
            "SS316-446-A182" is several values, not a code).

    Returns:
        The codes, upper-cased.
    """
    out: set[str] = set()
    for text in texts:
        for token in _TOKEN_SPLIT.split(text):
            if (
                len(token) >= min_length
                and _CODE.fullmatch(token)
                and _HAS_LETTER.search(token)
                and _HAS_DIGIT.search(token)
            ):
                out.add(token.upper())
    return out


def row_cells(text: str) -> list[tuple[str, str]]:
    """The `label: value` cells of a table row's text (context before " | " dropped).

    Args:
        text: e.g. "HYDERABAD | Var No: 15; Material Code: PY9756306157; Range: 0 ≤ 1200 oC".

    Returns:
        (label, value) pairs; a cell without ": " is skipped.
    """
    body = text.rsplit(_CONTEXT_SEPARATOR, 1)[-1]
    cells: list[tuple[str, str]] = []
    for cell in body.split(_CELL_SEPARATOR):
        label, sep, value = cell.partition(": ")
        if sep and label.strip() and value.strip():
            cells.append((" ".join(label.split()), " ".join(value.split())))
    return cells


def _is_code(value: str, code: str) -> bool:
    """True if a cell value is exactly the code (a trailing "(note)" allowed)."""
    return _TRAILING_NOTE.sub("", value).strip().upper() == code


def code_row_lines(
    text: str, code: str, other_codes: Iterable[str], min_cells: int
) -> list[str] | None:
    """Reader lines ("Label : value") of a row that is this code's own row, else None.

    Args:
        text: A general requirement's printed text.
        code: The item's code (upper-case).
        other_codes: Codes of every other item.
        min_cells: Fewest labelled cells besides the code cell.

    Returns:
        The other cells as lines, or None if the row is not the code's own row.
    """
    cells = row_cells(text)
    keyed = [i for i, (_, value) in enumerate(cells) if _is_code(value, code)]
    if len(keyed) != 1:
        return None
    rest = [cell for i, cell in enumerate(cells) if i != keyed[0]]
    others = {c.upper() for c in other_codes} - {code}
    if len(rest) < min_cells or any(o in value.upper() for _, value in rest for o in others):
        return None
    return [f"{label} : {value}" for label, value in rest]


def find_code_row(
    code: str, texts: Sequence[str], other_codes: Iterable[str], min_cells: int
) -> tuple[int, CodeRow] | None:
    """The one row (index into `texts`) that is the code's own row; None for none or several.

    Args:
        code: The item's code.
        texts: Printed texts of the general requirements.
        other_codes: Codes of every other item.
        min_cells: Fewest labelled cells besides the code cell.

    Returns:
        The row's index and lines, or None.
    """
    others = list(other_codes)
    found = [
        (i, lines)
        for i, text in enumerate(texts)
        if code in text.upper()
        and (lines := code_row_lines(text, code, others, min_cells)) is not None
    ]
    if len(found) != 1:
        return None
    index, lines = found[0]
    return index, CodeRow(code, lines)
