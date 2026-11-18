"""Searching GeM by a tender's items: search page parsing, category filter, product ranking."""

from app.discovery.gem_config import load_gem_config
from app.discovery.gem_parser import CategoryLink, listing_slug_words, search_categories
from app.discovery.gem_search import (
    fitting_categories,
    rank_listings,
    rank_words,
    search_queries,
    search_url,
)
from app.discovery.listing_check import ListingCheck
from tests.unit.discovery.conftest import BACKEND, fixture_text

TENDER_TITLE = '1" Pneumatic Globe Control valve ATO'  # 8063368-style item title
CONFIG = load_gem_config(BACKEND / "config" / "gem_marketplace.yaml")


def _links() -> list[CategoryLink]:
    return search_categories(fixture_text("search_globe_control_valve.html"))


def test_search_page_lists_its_categories_without_the_note() -> None:
    links = _links()
    assert len(links) == 9
    assert links[0] == CategoryLink("Directional Control Valve", "valves-directional-control-valve")
    assert links[1] == CategoryLink("Control Valve Pneumatic", "valves-control-valve-pneumatic")


def test_only_categories_of_the_type_are_kept(check: ListingCheck) -> None:
    excludes = CONFIG.excludes_for("control_valve")
    kept = fitting_categories(_links(), "control_valve", check, excludes)
    assert [c.slug for c in kept] == ["valves-control-valve-pneumatic"]


def test_globe_valve_search_keeps_the_globe_category_only(check: ListingCheck) -> None:
    kept = fitting_categories(_links(), "globe_valve", check)
    assert [c.name for c in kept] == [
        "Copper Alloy Globe Valve For Waterworks Purpose (V2) Conforming to IS 778"
    ]


def test_search_words_come_from_the_item(check: ListingCheck) -> None:
    assert search_queries("control_valve", [TENDER_TITLE], check) == [
        "pneumatic control valve",
        "control valve",
    ]
    assert search_queries("globe_valve", ["Globe valve DN50"], check) == ["globe valve"]


def test_search_url_encodes_the_words() -> None:
    assert search_url("https://mkp.gem.gov.in/", "pneumatic control valve") == (
        "https://mkp.gem.gov.in/search?q=pneumatic%20control%20valve"
    )


def test_rank_words_skip_the_type_and_filler(check: ListingCheck) -> None:
    words = rank_words("control_valve", [TENDER_TITLE], check, CONFIG.rank_ignore_words)
    assert "globe" in words
    assert "pneumat" in words or "pneumatic" in words
    assert not words & {"control", "valv", "valve"}


def test_listings_naming_more_item_words_go_first(check: ListingCheck) -> None:
    base = "https://mkp.gem.gov.in/control-valve-pneumatic"
    urls = [
        f"{base}/festo-one-way-flow-control-valve-grla-3/p-1-1-cat.html",
        f"{base}/pneumatic-globe-control-valve/p-2-2-cat.html",
        f"{base}/pneumatic-control-valve/p-3-3-cat.html",
    ]
    words = rank_words("control_valve", [TENDER_TITLE], check, CONFIG.rank_ignore_words)
    ranked = rank_listings(urls, words, check)
    assert ranked == [urls[1], urls[2], urls[0]]


def test_listing_slug_words_are_the_product_name() -> None:
    url = "https://mkp.gem.gov.in/mineral-insulated-thermocouple/k-type-thermocouple/p-1-2-cat.html"
    assert listing_slug_words(url) == "k type thermocouple"
    assert listing_slug_words("https://mkp.gem.gov.in/p-1-2-cat.html") == ""
