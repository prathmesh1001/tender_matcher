"""Group chunks into document sections and give each section a category.

A section is a maximal run of consecutive chunks (reading order) under the same heading, as
the chunker recorded it. Chunks never mix headings, so costing text and a technical table on
adjacent pages land in different sections.

Section category: the heading's category when the heading names one; otherwise the most
common non-default category of its chunks (a GeM "Bid Details" table holds EMD, turnover and
delivery rows, each keeps its own chunk category); otherwise the default.

Hierarchy: numbered headings ("10.1 ...") hang under the latest earlier section numbered
with their prefix ("10. ..."). Unnumbered headings have no parent.
"""

import re
from collections import Counter
from collections.abc import Sequence
from typing import Final

from app.ingestion.section_classifier import SectionClassifier
from app.retrieval.text_normalizer import SearchVocabulary, normalize_for_search
from app.schemas.sections import CategorySource, ChunkCategory, SectionDraft

_NUMBER: Final = re.compile(r"^\s*\(?(\d{1,3}(?:\.\d{1,3})*)\s*[.)]?(?=\s|$)")


def section_number(title: str | None) -> str | None:
    """Leading heading number: "10.1 General" -> "10.1", "3. Warranty" -> "3", else None."""
    match = _NUMBER.match(title or "")
    return match.group(1) if match else None


def _parent_number(number: str) -> str | None:
    """Parent heading number: 10.1 -> 10; a top-level number has none."""
    head, _, _ = number.rpartition(".")
    return head or None


def _runs(titles: Sequence[str | None]) -> list[list[int]]:
    """Indexes of consecutive chunks sharing a heading."""
    runs: list[list[int]] = []
    for i, title in enumerate(titles):
        if runs and titles[runs[-1][0]] == title:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def classify_chunks(
    headings: Sequence[str | None],
    search_texts: Sequence[str],
    classifier: SectionClassifier,
    vocabulary: SearchVocabulary,
) -> list[ChunkCategory]:
    """Category of each chunk from its heading and `search_text` (see `section_classifier`).

    Args:
        headings: Section heading of each chunk, verbatim.
        search_texts: Normalized text of each chunk.
        classifier: Compiled category rules.
        vocabulary: Normalizes the headings the same way as `search_text`.

    Returns:
        One category per chunk.
    """
    normalized: dict[str | None, str] = {}
    for heading in headings:
        if heading not in normalized:
            normalized[heading] = normalize_for_search(heading or "", vocabulary)
    return [
        classifier.classify(normalized[h], text)
        for h, text in zip(headings, search_texts, strict=True)
    ]


def section_category(members: Sequence[ChunkCategory], default: str) -> tuple[str, CategorySource]:
    """Category of a section from its chunks' categories (see module docstring)."""
    if members and members[0].source is CategorySource.HEADING:
        return members[0].category, CategorySource.HEADING
    counts = Counter(m.category for m in members if m.category != default)
    if not counts:
        return default, CategorySource.DEFAULT
    return counts.most_common(1)[0][0], CategorySource.CHUNKS


def build_sections(
    titles: Sequence[str | None],
    pages: Sequence[int],
    categories: Sequence[ChunkCategory],
    default: str,
) -> list[SectionDraft]:
    """Group a document's chunks (in reading order) into sections.

    Args:
        titles: Section heading of each chunk (None before the first heading).
        pages: Page of each chunk.
        categories: Category of each chunk.
        default: The default category name.

    Returns:
        Sections in document order; every chunk index belongs to exactly one.
    """
    sections: list[SectionDraft] = []
    latest_by_number: dict[str, int] = {}
    for position, run in enumerate(_runs(titles)):
        title = titles[run[0]]
        number = section_number(title)
        parent_number = _parent_number(number) if number else None
        category, source = section_category([categories[i] for i in run], default)
        sections.append(
            SectionDraft(
                position=position,
                title=title,
                number=number,
                parent_position=latest_by_number.get(parent_number) if parent_number else None,
                category=category,
                category_source=source,
                start_page=min(pages[i] for i in run),
                end_page=max(pages[i] for i in run),
                chunk_indexes=run,
            )
        )
        if number:
            latest_by_number[number] = position
    return sections
