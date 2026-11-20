"""Recommended GeM vendors: listings of the buyer's GeM categories (8081591 trial)."""

from types import SimpleNamespace
from typing import Any, cast

from app.discovery.recommend import buyer_key, recommendations, with_recommendations
from app.schemas.enums import VendorSource
from app.schemas.report import GemRecommendation, TenderReport

IMPELLER = "Stainless Steel Pump Impeller"
SHAFT = "Shaft and Shaft Assembly as per Drawing (RCF)"


def _listing(n: int, kept: bool = True, reason: str = "kept") -> dict[str, Any]:
    return {
        "url": f"https://mkp.gem.gov.in/stainless-steel-pump-impeller/x{n}/p-{n}-1-cat.html",
        "kept": kept,
        "reason": reason,
        "title": f"Brand{n} Stainless Steel Pump Impeller",
        "brand": f"Brand{n}",
        "sold_as": "oem",
    }


def test_buyer_key_is_the_category_words() -> None:
    assert buyer_key(SHAFT) == "buyer_category_shaft_and_shaft_assembly_as_per_drawing_rcf"


def test_kept_and_already_stored_listings_are_recommended_per_category() -> None:
    listings = {
        buyer_key(IMPELLER): [
            _listing(1),
            _listing(2, kept=False, reason="already stored (duplicate)"),
            _listing(3, kept=False, reason="only 2 specification rows (need 5)"),
            _listing(4),
        ]
    }
    recs = recommendations([IMPELLER, SHAFT], listings, per_category=2)
    assert [(r.brand, r.category) for r in recs] == [("Brand1", IMPELLER), ("Brand2", IMPELLER)]


def _item(no: str, title: str, gem: bool = False) -> Any:
    vendor = SimpleNamespace(source=VendorSource.GEM_LISTING)
    item = SimpleNamespace(item=SimpleNamespace(item_no=no, title=title))
    item.top_vendors = [vendor] if gem else []
    item.model_copy = lambda update: SimpleNamespace(**{**vars(item), **update})
    return item


def test_items_without_gem_get_related_listings_sharing_a_product_word() -> None:
    rec = GemRecommendation(brand="K", product="K impeller", url="u", category=IMPELLER)
    items = [
        _item("1", "IMPELLER MOC BRONZE"),
        _item("2", "BEARING HOLDER DE"),  # shares no product word
        _item("3", "IMPELLER", gem=True),  # has its own GeM vendor
    ]
    report = SimpleNamespace(items=items)
    report.model_copy = lambda update: SimpleNamespace(**{**vars(report), **update})
    out = with_recommendations(cast(TenderReport, report), [rec], ["of", "and"], per_item=3)
    assert [r.brand for r in out.recommended_gem_vendors] == ["K"]
    first, second, third = out.items
    assert [r.reason for r in first.related_gem_vendors] == [
        f"GeM category '{IMPELLER}' shares 'impeller' with the item title"
    ]
    assert second.related_gem_vendors == []
    assert not hasattr(third, "related_gem_vendors")  # left as it was


def test_nothing_to_recommend_leaves_the_report() -> None:
    report = cast(TenderReport, SimpleNamespace(items=[]))
    assert with_recommendations(report, [], [], per_item=3) is report
