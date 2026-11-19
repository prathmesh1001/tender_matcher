"""Searching GeM by a tender's items: search page parsing, category filter, product ranking."""

from app.discovery.gem_cards import ListingCard
from app.discovery.gem_config import load_gem_config
from app.discovery.gem_parser import CategoryLink, listing_slug_words, search_categories
from app.discovery.gem_search import (
    fitting_categories,
    rank_cards,
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
    ranked = rank_cards([ListingCard(u) for u in urls], words, check)  # no title: URL name
    assert [c.url for c in ranked] == [urls[1], urls[2], urls[0]]


def test_listing_slug_words_are_the_product_name() -> None:
    url = "https://mkp.gem.gov.in/mineral-insulated-thermocouple/k-type-thermocouple/p-1-2-cat.html"
    assert listing_slug_words(url) == "k type thermocouple"
    assert listing_slug_words("https://mkp.gem.gov.in/p-1-2-cat.html") == ""


def _names(*names: str) -> list[CategoryLink]:
    return [CategoryLink(n, n.lower().replace(" ", "-")) for n in names]


def test_a_category_names_the_product_before_with(check: ListingCheck) -> None:
    links = _names(
        "Ball Valve with Gear Operator",  # the valve, with an accessory
        "Gear Operator with Ball Valve",  # the operator
        "Temperature Gauge with Thermowell",  # 7865644: a gauge, not a thermowell
    )
    assert [c.name for c in fitting_categories(links, "ball_valve", check)] == [
        "Ball Valve with Gear Operator"
    ]
    assert fitting_categories(links, "thermowell", check) == []


def test_a_part_tool_or_service_category_is_not_the_product(check: ListingCheck) -> None:
    links = _names(
        "Ball Valve Repair Kit",
        "Centrifugal Pump Spare Parts",
        "Thermocouple Calibration Furnace",  # 7865644
        "Control Valve Pneumatic",  # a qualifier after the type is fine
        "Centrifugal Pump for Water",
    )
    assert fitting_categories(links, "ball_valve", check) == []
    assert [c.name for c in fitting_categories(links, "centrifugal_pump", check)] == [
        "Centrifugal Pump for Water"
    ]
    assert fitting_categories(links, "thermocouple", check) == []
    assert [c.name for c in fitting_categories(links, "control_valve", check)] == [
        "Control Valve Pneumatic"
    ]


def test_a_list_of_types_still_names_each(check: ListingCheck) -> None:
    # The main-name rule keeps lists; the category is still dropped as naming globe valve too.
    assert check.names_type_in("Gate and Globe Valves", "gate_valve")


def test_a_product_for_the_type_is_not_the_type(check: ListingCheck) -> None:
    links = _names(
        "Submersible Pump Starters (Motor starter controls)",
        "PVC Insulated Cable For Submersible Pumps ISI Marked To IS 694",
        "Submersible Pumps for Slurry Application",
    )
    assert [c.name for c in fitting_categories(links, "submersible_pump", check)] == [
        "Submersible Pumps for Slurry Application"
    ]


def test_type_words_apart_or_reordered_still_name_the_type(check: ListingCheck) -> None:
    links = _names(
        "Submersible Sewerage Pump",
        "Submersible Pumpsets (Three Phase) Conforming To IS 8034 (V2)",
        "Centrifugal Cooling Water Pump (IOCL)",
        "Pump Centrifugal Horizontal",
        "Submersible Pump Cable Starter",  # a part after the type words
    )
    assert [c.name for c in fitting_categories(links, "submersible_pump", check)] == [
        "Submersible Sewerage Pump",
        "Submersible Pumpsets (Three Phase) Conforming To IS 8034 (V2)",
    ]
    assert [c.name for c in fitting_categories(links, "centrifugal_pump", check)] == [
        "Centrifugal Cooling Water Pump (IOCL)",
        "Pump Centrifugal Horizontal",
    ]


def test_type_words_apart_never_beat_another_type_named_whole(check: ListingCheck) -> None:
    # "ball" and "valve" are both here, but the name is a gate valve with ball-type words.
    links = _names("Ball Type Gate Valve")
    assert fitting_categories(links, "ball_valve", check) == []
