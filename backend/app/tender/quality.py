"""How far a tender's extracted items can be trusted. Pure functions.

A wrong schedule gives confident-looking results about the wrong things, and no comparison can
notice that. So the extraction itself is judged, by what it looks like and never by which
tender it is: titles that name no product and read as fragments or sentences, an item holding
far more requirements than one product has, items left nearly empty while the specification's
lines sit among the general requirements, items left with only their title although
specification files are attached, a bid's Item Category row merging several products
into one item, requirements read from low-confidence OCR, linked files that could not be fetched
(the specification may be in one). Any of these marks the tender unreliable; that sends every
result to review and changes no status.
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
TITLE_PART_SEPARATOR: Final = ","


@dataclass(frozen=True, slots=True)
class QualityRules:
    """Thresholds of the extraction check (built once from settings)."""

    fragment_max_words: int
    sentence_min_words: int
    sentence_words: frozenset[str]
    merged_title_lead_words: int
    bad_title_share: float
    max_item_requirements: int
    nearly_empty_item: int
    min_spec_outside_items: int
    title_only_share: float
    ocr_min_confidence: float
    low_ocr_share: float

    @classmethod
    def from_settings(cls, settings: Settings) -> QualityRules:
        """Build the rules from application settings."""
        return cls(
            fragment_max_words=settings.EXTRACTION_TITLE_FRAGMENT_MAX_WORDS,
            sentence_min_words=settings.EXTRACTION_TITLE_SENTENCE_MIN_WORDS,
            sentence_words=frozenset(settings.EXTRACTION_TITLE_SENTENCE_WORDS),
            merged_title_lead_words=settings.EXTRACTION_MERGED_TITLE_LEAD_WORDS,
            bad_title_share=settings.EXTRACTION_BAD_TITLE_SHARE,
            max_item_requirements=settings.EXTRACTION_MAX_ITEM_REQUIREMENTS,
            nearly_empty_item=settings.EXTRACTION_NEARLY_EMPTY_ITEM,
            min_spec_outside_items=settings.EXTRACTION_MIN_SPEC_OUTSIDE_ITEMS,
            title_only_share=settings.EXTRACTION_TITLE_ONLY_SHARE,
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


def _spec_outside_issue(
    items: Sequence[ItemFacts], spec_outside: int, rules: QualityRules
) -> ExtractionIssue | None:
    """Every item is nearly empty while the specification's lines became general requirements."""
    if not items or spec_outside < rules.min_spec_outside_items:
        return None
    if any(i.requirements > rules.nearly_empty_item for i in items):
        return None
    most = max(i.requirements for i in items)
    return ExtractionIssue(
        code=ExtractionIssueCode.SPEC_OUTSIDE_ITEMS,
        reason=f"items hold at most {most} requirements while {spec_outside} lines that "
        "catalogues could check are general requirements: the specification did not reach "
        "the items",
    )


def _title_only_issue(
    items: Sequence[ItemFacts], spec_files: Sequence[str], rules: QualityRules
) -> ExtractionIssue | None:
    """Most items hold only their title although specification files are attached."""
    if not items or not spec_files:
        return None
    empty = [i for i in items if i.requirements <= rules.nearly_empty_item]
    if len(empty) / len(items) < rules.title_only_share:
        return None
    return ExtractionIssue(
        code=ExtractionIssueCode.TITLE_ONLY_ITEMS,
        reason=f"{len(empty)} of {len(items)} items hold at most {rules.nearly_empty_item} "
        f"requirements although specification files are attached ({_examples(spec_files)}): "
        f"their text did not reach the items, e.g. {_examples([i.title for i in empty])}",
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


def title_merges_products(title: str, lead_words: int) -> bool:
    """True if comma-separated parts of a title start alike ("Globe Valve 40 NB, Globe Valve 50").

    Args:
        title: The item title.
        lead_words: How many first words must be the same.

    Returns:
        Whether at least two parts share them; commas inside one product's description do not.
    """
    leads = [tuple(words(part)[:lead_words]) for part in title.split(TITLE_PART_SEPARATOR)]
    full = [lead for lead in leads if len(lead) == lead_words]
    return len(set(full)) < len(full)


def _merged_issue(
    items: Sequence[ItemFacts], from_item_category: bool, rules: QualityRules
) -> ExtractionIssue | None:
    """The Item Category row names several products, so they became one item."""
    if not from_item_category:
        return None
    merged = [
        i.title for i in items if title_merges_products(i.title, rules.merged_title_lead_words)
    ]
    if not merged:
        return None
    return ExtractionIssue(
        code=ExtractionIssueCode.MERGED_ITEM_CATEGORY,
        reason="no item schedule found and the bid's Item Category row names several products, "
        f"so they are read as one item: {_examples(merged)}",
    )


def _fetch_issue(unfetched: Sequence[str]) -> ExtractionIssue | None:
    """Linked files are missing, so requirements in them were not read."""
    if not unfetched:
        return None
    return ExtractionIssue(
        code=ExtractionIssueCode.FILES_NOT_FETCHED,
        reason=f"{len(unfetched)} linked files could not be fetched or read, so requirements "
        f"in them are missing: {_examples(unfetched)}",
    )


def extraction_quality(
    items: Sequence[ItemFacts],
    from_item_category: bool,
    rules: QualityRules,
    unfetched: Sequence[str] = (),
    spec_outside: int = 0,
    spec_files: Sequence[str] = (),
) -> ExtractionQuality:
    """Judge one tender's extraction.

    Args:
        items: The extracted items.
        from_item_category: No schedule was found: the bid's Item Category row gave the items.
        rules: Thresholds.
        unfetched: Linked files that could not be fetched or read.
        spec_outside: General requirements that catalogues could check (evidence: catalogue).
        spec_files: Specification files read for the tender (spec, BOQ and annexure roles).

    Returns:
        `unreliable` with every reason found, else `ok` with none.
    """
    found = (
        _title_issue(items, rules),
        _size_issue(items, rules),
        _spec_outside_issue(items, spec_outside, rules),
        _title_only_issue(items, spec_files, rules),
        _merged_issue(items, from_item_category, rules),
        _ocr_issue(items, rules),
        _fetch_issue(unfetched),
    )
    reasons = [issue for issue in found if issue is not None]
    status = ExtractionStatus.UNRELIABLE if reasons else ExtractionStatus.OK
    return ExtractionQuality(status=status, reasons=reasons)
