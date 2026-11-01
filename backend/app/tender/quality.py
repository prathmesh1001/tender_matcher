"""How far a tender's extracted items can be trusted. Pure functions.

A wrong schedule gives confident-looking results about the wrong things, and no comparison can
notice that. So the extraction itself is judged, by what it looks like and never by which
tender it is: titles that name no product and read as fragments or sentences, an item holding
far more requirements than one product has, items that only come from the bid's Item Category
row, requirements read from low-confidence OCR. Any of these marks the tender unreliable; that
sends every result to review and changes no status.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.core.config import Settings
from app.ingestion.processing import ProcessedPage
from app.schemas.enums import ExtractionIssueCode, ExtractionStatus
from app.schemas.report import ExtractionIssue, ExtractionQuality
from app.tender.normalizer import words

_EXAMPLES: Final = 3
_EXAMPLE_CHARS: Final = 40


@dataclass(frozen=True, slots=True)
class QualityRules:
    """Thresholds of the extraction check (built once from settings)."""

    fragment_max_words: int
    sentence_min_words: int
    sentence_words: frozenset[str]
    bad_title_share: float
    max_item_requirements: int
    ocr_min_confidence: float
    low_ocr_share: float

    @classmethod
    def from_settings(cls, settings: Settings) -> QualityRules:
        """Build the rules from application settings."""
        return cls(
            fragment_max_words=settings.EXTRACTION_TITLE_FRAGMENT_MAX_WORDS,
            sentence_min_words=settings.EXTRACTION_TITLE_SENTENCE_MIN_WORDS,
            sentence_words=frozenset(settings.EXTRACTION_TITLE_SENTENCE_WORDS),
            bad_title_share=settings.EXTRACTION_BAD_TITLE_SHARE,
            max_item_requirements=settings.EXTRACTION_MAX_ITEM_REQUIREMENTS,
            ocr_min_confidence=settings.EXTRACTION_OCR_MIN_CONFIDENCE,
            low_ocr_share=settings.EXTRACTION_LOW_OCR_SHARE,
        )


@dataclass(frozen=True, slots=True)
class ItemFacts:
    """What the check needs to know about one extracted item."""

    item_no: str
    title: str
    names_product: bool
    requirements: int
    low_ocr_requirements: int = 0


def low_confidence_pages(
    document: str, pages: Sequence[ProcessedPage], min_confidence: float
) -> set[tuple[str, int]]:
    """OCR pages of one file read below the confidence (an unmeasured OCR page counts as low).

    Args:
        document: File name, as requirements cite it.
        pages: The file's pages.
        min_confidence: Lowest mean OCR confidence to trust.

    Returns:
        (file name, page number) of each such page.
    """
    return {
        (document, page.page_number)
        for page in pages
        if page.ocr and (page.ocr_confidence is None or page.ocr_confidence < min_confidence)
    }


def title_reads_as_text(title: str, rules: QualityRules) -> bool:
    """True if a title looks like a piece of text rather than the name of a thing.

    Args:
        title: The item title.
        rules: Word limits and the words only sentences use.

    Returns:
        Whether it is a few plain words with no code or size, has an unclosed bracket, is very
        long, or uses a word like "shall".
    """
    title_words = words(title)
    few_plain_words = len(title_words) <= rules.fragment_max_words and not any(
        c.isdigit() for c in title
    )
    return (
        few_plain_words
        or title.count("(") != title.count(")")
        or len(title_words) >= rules.sentence_min_words
        or not rules.sentence_words.isdisjoint(title_words)
    )


def _examples(titles: Sequence[str]) -> str:
    """The first few titles, shortened, for a reason a person reads."""
    return "; ".join(f'"{t[:_EXAMPLE_CHARS]}"' for t in titles[:_EXAMPLES])


def _title_issue(items: Sequence[ItemFacts], rules: QualityRules) -> ExtractionIssue | None:
    """Most titles name no product and read as text."""
    bad = [i.title for i in items if not i.names_product and title_reads_as_text(i.title, rules)]
    if not bad or len(bad) / len(items) < rules.bad_title_share:
        return None
    return ExtractionIssue(
        code=ExtractionIssueCode.TITLES_NOT_PRODUCTS,
        reason=f"{len(bad)} of {len(items)} item titles name no product and read as fragments "
        f"or sentences: {_examples(bad)}",
    )


def _size_issue(items: Sequence[ItemFacts], rules: QualityRules) -> ExtractionIssue | None:
    """An item holds more requirements than one product plausibly has."""
    big = [i for i in items if i.requirements > rules.max_item_requirements]
    if not big:
        return None
    worst = max(big, key=lambda i: i.requirements)
    return ExtractionIssue(
        code=ExtractionIssueCode.OVERSIZED_ITEM,
        reason=f"item {worst.item_no} has {worst.requirements} requirements "
        f"(more than {rules.max_item_requirements}): specification text is probably merged",
    )


def _ocr_issue(items: Sequence[ItemFacts], rules: QualityRules) -> ExtractionIssue | None:
    """Enough item requirements were read from low-confidence OCR."""
    total = sum(i.requirements for i in items)
    low = sum(i.low_ocr_requirements for i in items)
    if not low or low / total < rules.low_ocr_share:
        return None
    return ExtractionIssue(
        code=ExtractionIssueCode.LOW_CONFIDENCE_OCR,
        reason=f"{low} of {total} item requirements come from pages with OCR confidence below "
        f"{rules.ocr_min_confidence:.0%}",
    )


def _fallback_issue(from_item_category: bool) -> ExtractionIssue | None:
    """No schedule was found, so the bid's Item Category row gave the items."""
    if not from_item_category:
        return None
    return ExtractionIssue(
        code=ExtractionIssueCode.ITEM_CATEGORY_FALLBACK,
        reason="no item schedule found: items come from the bid's Item Category row, so "
        "nothing tells which specification line belongs to which product",
    )


def extraction_quality(
    items: Sequence[ItemFacts], from_item_category: bool, rules: QualityRules
) -> ExtractionQuality:
    """Judge one tender's extraction.

    Args:
        items: The extracted items.
        from_item_category: No schedule was found: the bid's Item Category row gave the items.
        rules: Thresholds.

    Returns:
        `unreliable` with every reason found, else `ok` with none.
    """
    found = (
        _title_issue(items, rules),
        _size_issue(items, rules),
        _fallback_issue(from_item_category),
        _ocr_issue(items, rules),
    )
    reasons = [issue for issue in found if issue is not None]
    status = ExtractionStatus.UNRELIABLE if reasons else ExtractionStatus.OK
    return ExtractionQuality(status=status, reasons=reasons)
