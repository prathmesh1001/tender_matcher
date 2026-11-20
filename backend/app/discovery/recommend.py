"""GeM vendors recommended for a tender: listings of the GeM categories the buyer selected.

Every GeM bid names the GeM categories its buyer selected for notification; GeM tells those
categories' sellers about the bid. Their listings are therefore the tender's recommended GeM
vendors even when no item could be matched to a listing (spare parts listed by part number,
items GeM sells under a wider name). They are shown as offered, never as checked against the
specification:

- for the tender: every kept listing of every buyer category (`recommendations`);
- for an item that has no GeM vendor of its own: the listings of a buyer category whose name
  shares a product word with the item title ("IMPELLER MOC BRONZE" -> "Stainless Steel Pump
  Impeller"), with the shared word as the reason (`with_recommendations`).

Pure functions; the discovery service reads the jobs and the run applies the result.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from app.retrieval.text_normalizer import fold_text
from app.schemas.enums import VendorSource
from app.schemas.report import GemRecommendation, ItemReport, TenderReport

# Discovery jobs of buyer categories carry this prefix before the category's words.
BUYER_KEY_PREFIX: Final = "buyer_category_"
_MAX_KEY_LENGTH: Final = 120
_WORD = re.compile(r"[a-z0-9]+")
_MIN_SHARED_WORD: Final = 4
# Listings kept by this check, or kept earlier and only reported as already stored.
_STORED_REASON: Final = "already stored"


def buyer_key(category: str) -> str:
    """The discovery job key of a buyer category ("buyer_category_stainless_steel_...")."""
    words = "_".join(_WORD.findall(fold_text(category)))
    return f"{BUYER_KEY_PREFIX}{words}"[:_MAX_KEY_LENGTH]


def _usable(listing: Mapping[str, Any]) -> bool:
    """True for a listing kept by the check, or kept by an earlier one (already stored)."""
    return bool(listing.get("kept")) or str(listing.get("reason", "")).startswith(_STORED_REASON)


def recommendations(
    categories: Sequence[str],
    listings_by_key: Mapping[str, Sequence[Mapping[str, Any]]],
    per_category: int,
) -> list[GemRecommendation]:
    """The listings of each buyer category, in category order, at most `per_category` each.

    Args:
        categories: The buyer's GeM categories.
        listings_by_key: Job key -> the job's listing outcomes (`ListingOutcome.as_json`).
        per_category: Most listings shown per category.

    Returns:
        One recommendation per listing, each URL once.
    """
    out: list[GemRecommendation] = []
    seen: set[str] = set()
    for category in categories:
        shown = 0
        for listing in listings_by_key.get(buyer_key(category), ()):
            url = str(listing.get("url", ""))
            if shown >= per_category or not url or url in seen or not _usable(listing):
                continue
            seen.add(url)
            shown += 1
            out.append(
                GemRecommendation(
                    brand=str(listing.get("brand") or "Unbranded"),
                    product=str(listing.get("title") or url),
                    url=url,
                    category=category,
                    sold_as=listing.get("sold_as"),
                )
            )
    return out


def _words(text: str, ignore: frozenset[str]) -> set[str]:
    """Folded words long enough to name a product, filler left out."""
    return {
        w for w in _WORD.findall(fold_text(text)) if len(w) >= _MIN_SHARED_WORD and w not in ignore
    }


def _has_gem_vendor(item: ItemReport) -> bool:
    """True if a GeM listing is already among the item's top vendors."""
    return any(v.source is VendorSource.GEM_LISTING for v in item.top_vendors)


def _related(
    item: ItemReport, recs: Iterable[GemRecommendation], ignore: frozenset[str], limit: int
) -> list[GemRecommendation]:
    """Recommendations of a buyer category sharing a product word with the item title."""
    title = _words(item.item.title, ignore)
    out: list[GemRecommendation] = []
    for rec in recs:
        shared = sorted(title & _words(rec.category, ignore))
        if shared and len(out) < limit:
            reason = f"GeM category '{rec.category}' shares '{shared[0]}' with the item title"
            out.append(rec.model_copy(update={"reason": reason}))
    return out


def with_recommendations(
    report: TenderReport,
    recs: Sequence[GemRecommendation],
    ignore_words: Iterable[str],
    per_item: int,
) -> TenderReport:
    """The report with the tender's recommendations and related ones on items without GeM.

    Args:
        report: A run's report (GeM listing vendors already added).
        recs: The buyer categories' listings (`recommendations`).
        ignore_words: Filler words never shared (GEM_TITLE_STOP_WORDS).
        per_item: Most related listings per item.

    Returns:
        A new report; unchanged when there is nothing to recommend.
    """
    if not recs:
        return report
    ignore = frozenset(w for word in ignore_words for w in _WORD.findall(fold_text(word)))
    items = [
        (
            i
            if _has_gem_vendor(i)
            else i.model_copy(update={"related_gem_vendors": _related(i, recs, ignore, per_item)})
        )
        for i in report.items
    ]
    return report.model_copy(update={"recommended_gem_vendors": list(recs), "items": items})
