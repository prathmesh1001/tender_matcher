"""Requirements outside the item schedule: bid rows, terms, warranty, certificates, ...

Every chunk that is not a schedule row becomes requirements: a table row gives one, prose gives
one per sentence. Only the English part of a bilingual line is read (A8, T7); `raw_text` stays
as printed. Lines with nothing to read (page numbers, Hindi only, a lone "Download") are dropped.
"""

import re
from collections.abc import Sequence
from typing import Final

from app.retrieval.text_normalizer import fold_text
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType, Operator
from app.schemas.requirements import Requirement, ValueKind
from app.tender.classifier import RequirementClassifier

GENERAL_ITEM: Final = "general"
_DEVANAGARI: Final = re.compile(r"[ऀ-ॿ]")
_PLACEHOLDER: Final = re.compile(r"^Column \d+$")
_PAGE_MARK: Final = re.compile(r"^\d+\s*/\s*\d+$")
_WORD: Final = re.compile(r"[A-Za-z]{3,}")
_SENTENCE_END: Final = re.compile(r"(?<=[.;])\s+(?=[A-Z(0-9])")
# Fewer readable words than this is not a requirement ("Download", "Yes").
_MIN_WORDS: Final = 2


def english_part(text: str) -> str:
    """The text without its Hindi: a word holding Devanagari keeps only its "/" parts without it.

    Args:
        text: A printed line ("बड सं?या/Bid Number: GEM/2026").

    Returns:
        The English part ("Bid Number: GEM/2026").
    """
    kept: list[str] = []
    for token in text.split():
        if _DEVANAGARI.search(token):
            token = "/".join(p for p in token.split("/") if p and not _DEVANAGARI.search(p))
        if token:
            kept.append(token)
    return " ".join(kept).lstrip("/ ").strip()


def _readable(english: str) -> bool:
    """False for page numbers and lines without enough words to require anything."""
    return not _PAGE_MARK.match(english) and len(_WORD.findall(english)) >= _MIN_WORDS


def row_text(chunk: ChunkDraft) -> tuple[str | None, str]:
    """Label and value of a table row; several cells read as "label: value; ...", no label."""
    header: list[str] = chunk.meta.get("header", [])
    cells: list[str] = chunk.meta.get("cells", [])
    pairs = [
        ("" if _PLACEHOLDER.match(h) else english_part(h), english_part(c))
        for h, c in zip(header, cells, strict=False)
    ]
    pairs = [(h, c) for h, c in pairs if c]
    if len(pairs) == 1 and pairs[0][0]:
        return pairs[0]
    if len(pairs) == 2 and not pairs[0][0] and not pairs[1][0]:
        return pairs[0][1], pairs[1][1]  # GeM bid layout: a label cell, then a value cell
    if not pairs:
        return None, english_part(chunk.raw_text)
    return None, "; ".join(f"{h}: {c}" if h else c for h, c in pairs)


def split_sentences(text: str, abbreviations: Sequence[str]) -> list[str]:
    """Sentences of a prose chunk; a "." after a known abbreviation ("M/s.") does not end one.

    Args:
        text: Prose.
        abbreviations: Lower-case words that end in "." without ending a sentence.

    Returns:
        The non-empty sentences, in order, as printed.
    """
    sentences: list[str] = []
    for piece in _SENTENCE_END.split(text):
        previous = sentences[-1].split()[-1].rstrip(".").lower() if sentences else ""
        if sentences and sentences[-1].endswith(".") and previous in abbreviations:
            sentences[-1] = f"{sentences[-1]} {piece}"
        elif piece.strip():
            sentences.append(piece)
    return sentences


def _lines(chunk: ChunkDraft, abbreviations: Sequence[str]) -> list[tuple[str | None, str, str]]:
    """(label, English value, raw text) of every requirement in one chunk."""
    if chunk.chunk_type is ChunkType.TABLE_ROW:
        label, value = row_text(chunk)
        return [(label, value, chunk.raw_text)]
    return [
        (None, english_part(sentence), sentence)
        for sentence in split_sentences(chunk.raw_text, abbreviations)
    ]


def general_requirements(
    chunks: Sequence[ChunkDraft], document: str, classifier: RequirementClassifier
) -> list[Requirement]:
    """Requirements of the given non-schedule chunks of one tender file.

    Args:
        chunks: The file's chunks without its schedule rows.
        document: File name, for ids and citations.
        classifier: The line classifier.

    Returns:
        One text requirement per readable line, repeats within the file removed.
    """
    out: list[Requirement] = []
    seen: set[str] = set()
    abbreviations = classifier.rules.sentence_abbreviations
    for chunk in chunks:
        for label, value, raw in _lines(chunk, abbreviations):
            key = fold_text(f"{label or ''} {value}")
            if key in seen or not _readable(f"{label or ''} {value}"):
                continue
            seen.add(key)
            line = classifier.classify(f"{label}: {value}" if label else value, chunk.section)
            out.append(
                Requirement(
                    id=f"{document}#{len(out) + 1}",
                    item_no=GENERAL_ITEM,
                    position=len(out),
                    label=label,
                    req_type=line.req_type,
                    category=line.category,
                    evidence=line.evidence,
                    mandatory=line.mandatory,
                    named_make=line.named_make,
                    or_equivalent=line.or_equivalent,
                    kind=ValueKind.TEXT,
                    operator=Operator.TEXT,
                    value_text=value,
                    raw_text=raw,
                    document=document,
                    page_number=chunk.page_number,
                )
            )
    return out
