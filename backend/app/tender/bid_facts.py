"""Facts a GeM bid states about the whole tender: its BOQ title and the buyer's GeM categories.

GeM bids print, in their two-column "label: value" table:

- "BOQ Title": the buyer's own name for the purchase ("Spares parts for various pump model of
  Kirloskar brother limited", 8081591);
- "Relevant Categories selected for notification": GeM categories the buyer picked, one per
  printed line ("Stainless Steel Pump Impeller", "Shaft and Shaft Assembly as per Drawing
  (RCF)").

The categories are real GeM category names, so GeM can be searched by them directly. A BOQ
title naming spare parts and a maker says who usually supplies them (`spare_parts_notice`).
Labels and spare-part words come from settings. Pure functions.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.retrieval.text_normalizer import fold_text
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType
from app.schemas.report import SparePartsNotice

_WORD = re.compile(r"[a-z0-9]+")
_MIN_MAKER_WORD = 3


@dataclass(frozen=True, slots=True)
class BidFacts:
    """What the bid says about the whole tender."""

    boq_title: str | None = None
    gem_categories: list[str] = field(default_factory=list)


def _cell_value(chunk: ChunkDraft, labels: Sequence[str]) -> tuple[str, list[str]] | None:
    """(value, its printed lines) of a two-cell row whose label holds one of `labels`."""
    cells = chunk.meta.get("cells") or []
    if chunk.chunk_type is not ChunkType.TABLE_ROW or len(cells) != 2:
        return None
    label = fold_text(cells[0])
    if not any(fold_text(wanted) in label for wanted in labels):
        return None
    lines = chunk.meta.get("cell_lines") or []
    printed = list(lines[1]) if len(lines) > 1 else [cells[1]]
    return " ".join(cells[1].split()), printed


def category_lines(printed: Sequence[str]) -> list[str]:
    """One GeM category per printed line; a wrapped line (lower case, "(") joins the one before.

    Args:
        printed: The cell's printed lines.

    Returns:
        The category names, each once, bullets dropped.
    """
    out: list[str] = []
    for raw in printed:
        line = " ".join(raw.replace("•", " ").split())
        if not line:
            continue
        if out and (line[0].islower() or line[0] == "("):
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
    return list(dict.fromkeys(out))


def bid_facts(
    bid_chunks: Sequence[ChunkDraft],
    title_labels: Sequence[str],
    category_labels: Sequence[str],
) -> BidFacts:
    """The BOQ title and the buyer's GeM categories, from the bid's own chunks.

    Args:
        bid_chunks: Chunks of the bid document.
        title_labels: Labels of the BOQ title row ("BOQ Title").
        category_labels: Labels of the categories row ("Relevant Categories selected ...").

    Returns:
        The facts; empty when the bid prints neither.
    """
    title: str | None = None
    categories: list[str] = []
    for chunk in bid_chunks:
        if title is None and (found := _cell_value(chunk, title_labels)):
            title = found[0] or None
        if not categories and (found := _cell_value(chunk, category_labels)):
            categories = category_lines(found[1])
    return BidFacts(title, categories)


def _words(text: str) -> list[str]:
    """Folded words, a trailing plural "s" dropped ("Brothers" -> "brother")."""
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in _WORD.findall(fold_text(text))]


def _maker_words(name: str) -> set[str]:
    """A maker's name words of at least three letters ("NA" or "L&T" alone say too little)."""
    return {w for w in _words(name) if len(w) >= _MIN_MAKER_WORD}


def spare_parts_notice(
    boq_title: str | None,
    spare_words: Sequence[str],
    makers: Sequence[str],
) -> SparePartsNotice | None:
    """A notice when the BOQ title buys spare parts, with the maker it names if one is known.

    Args:
        boq_title: The bid's BOQ title.
        spare_words: Words that mark spare parts ("spare", "spares").
        makers: Manufacturer names in the database.

    Returns:
        The notice, or None when the title names no spare parts.
    """
    if not boq_title:
        return None
    words = _words(boq_title)
    if not set(_words(" ".join(spare_words))) & set(words):
        return None
    named = [m for m in makers if (own := _maker_words(m)) and own <= set(words)]
    # The fullest name the title holds: "Kirloskar Brothers" before a GeM brand "Kirloskar".
    oem = max(named, key=lambda m: len(_maker_words(m)), default=None)
    return SparePartsNotice(boq_title=boq_title, oem=oem)
