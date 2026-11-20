"""GeM cards picked per tender item: sub-type, then size, then title words; items take turns."""

from app.discovery.card_fit import (
    ItemWant,
    card_fit,
    item_wants,
    pick_for_items,
    rank_for_item,
    sizes_mm,
)
from app.discovery.gem_cards import ListingCard, known_brand
from app.discovery.gem_config import load_gem_config
from app.discovery.gem_parser import parse_listing
from app.discovery.gem_search import rank_words
from app.discovery.listing_check import ListingCheck
from app.discovery.pipeline import listing_sub_types
from tests.unit.discovery.conftest import BACKEND

CONFIG = load_gem_config(BACKEND / "config" / "gem_marketplace.yaml")
GATE = "gate_valve"


def _card(n: int, title: str, brand: str = "") -> ListingCard:
    return ListingCard(f"https://mkp.gem.gov.in/x/p-{n}-{n}-cat.html", title, brand or f"B{n}")


def _wants(titles: list[str], check: ListingCheck) -> list[ItemWant]:
    return item_wants(
        GATE,
        titles,
        check,
        lambda t: rank_words(GATE, [t], check, CONFIG.rank_ignore_words),
    )


def _never(_url: str) -> bool:
    return False


def test_sizes_in_mm_dn_nb_and_inches(check: ListingCheck) -> None:
    table = check.nps_to_dn
    assert sizes_mm("Knife Edge Gate Valve, Size 100 millimeter", table) == {100}
    assert sizes_mm("GATE VALVE 80MM", table) == {80}
    assert sizes_mm("Gate Valve DN 150", table) == {150}
    assert sizes_mm("Valve NB-200", table) == {200}
    assert sizes_mm('Ball Valve 1-1/2" Screwed', table) == {40}
    assert sizes_mm("Butterfly valve 8 inch", table) == {200}


def test_a_bare_number_is_no_size(check: ListingCheck) -> None:
    # Real card: "ZOLOTO Butterfly Valves 125 Flangeless Wafer Valve" (unit not printed).
    assert sizes_mm("ZOLOTO Butterfly Valves 125 Flangeless Wafer Valve", check.nps_to_dn) == set()


def test_each_8210284_gate_item_wants_its_sub_type_and_size(check: ListingCheck) -> None:
    plain, knife = _wants(["GATE VALVE 80MM", "KNIFE GATE EDGE VALVE 200MM"], check)
    assert plain.sub_types == set()
    assert plain.sizes_mm == {80}
    assert knife.sub_types == {"knife_gate"}
    assert knife.sizes_mm == {200}


def test_same_wish_is_kept_once(check: ListingCheck) -> None:
    assert len(_wants(["GATE VALVE 80MM", "Gate Valve 80 mm"], check)) == 1


def test_knife_item_puts_knife_listings_first_and_its_size_before_others(
    check: ListingCheck,
) -> None:
    [knife] = _wants(["KNIFE EDGE GATE VALVE 200MM"], check)
    wedge_200 = _card(1, "Wedge Gate Valve, Size 200 millimeter")
    knife_80 = _card(2, "Manual C.I Knife Edge Gate Valve, Size 80 millimeter")
    knife_200 = _card(3, "Manual C.I Knife Edge Gate Valve, Size 200 millimeter")
    plain = _card(4, "Cast Iron Gate Valve")
    ranked = rank_for_item([wedge_200, knife_80, plain, knife_200], knife, GATE, check)
    assert ranked == [knife_200, knife_80, plain, wedge_200]


def test_plain_gate_item_puts_knife_listings_last(check: ListingCheck) -> None:
    [plain] = _wants(["GATE VALVE 80MM"], check)
    knife_80 = _card(1, "Manual C.I Knife Edge Gate Valve, Size 80 millimeter")
    gate_150 = _card(2, "Cast Iron Gate Valve, Size 150 millimeter")
    gate_80 = _card(3, "Cast Iron Gate Valve, Size 80 millimeter")
    assert card_fit(knife_80, plain, GATE, check)[0] == -1
    ranked = rank_for_item([knife_80, gate_150, gate_80], plain, GATE, check)
    assert ranked == [gate_80, gate_150, knife_80]


def test_items_take_turns_so_each_gets_its_best_card(check: ListingCheck) -> None:
    plain, knife = _wants(["GATE VALVE 80MM", "KNIFE EDGE GATE VALVE 200MM"], check)
    knives = [_card(n, f"Knife Edge Gate Valve, Size {n}0 millimeter") for n in range(1, 21)]
    gate_80 = _card(99, "Cast Iron Gate Valve, Size 80 millimeter")
    cards = [*knives, gate_80]
    ranked = [rank_for_item(cards, w, GATE, check) for w in (plain, knife)]
    picked = pick_for_items(ranked, 2, 2, _never)
    assert picked == [gate_80, knives[19]]  # 200 mm knife listing for the knife item


def test_a_stored_card_serves_its_item_without_a_read(check: ListingCheck) -> None:
    cards = [_card(n, f"Gate Valve {n}") for n in range(1, 5)]
    stored = {cards[0].url}
    picked = pick_for_items([cards], 2, 5, lambda url: url in stored)
    assert picked == cards[:3]  # the stored one plus two new reads


def test_brand_cap_holds_across_items(check: ListingCheck) -> None:
    cards = [_card(n, f"Gate Valve {n}", brand="getway") for n in range(1, 6)]
    picked = pick_for_items([cards, list(reversed(cards))], 10, 2, _never)
    assert len(picked) == 2


def test_placeholder_brand_is_no_brand() -> None:
    assert known_brand("NA", CONFIG.empty_values) is None
    assert known_brand("n/a", CONFIG.empty_values) is None
    assert known_brand("ZOLOTO", CONFIG.empty_values) == "ZOLOTO"
    html = "<h1 itemprop='name'>Gate Valve<div class='brand'>NA</div></h1>"
    listing = parse_listing(html, "https://mkp.gem.gov.in/x/p-1-2-cat.html", CONFIG.empty_values)
    assert listing.brand is None


def test_stored_listing_declares_the_sub_type_its_title_names(check: ListingCheck) -> None:
    html = "<h1 itemprop='name'>getway Manual WCB Knife Edge Gate Valve, Size 150 millimeter</h1>"
    knife = parse_listing(html, "https://mkp.gem.gov.in/x/p-1-2-cat.html")
    sluice = parse_listing(
        "<h1 itemprop='name'>ajanta Sluice Valve of nominal size 150 mm</h1>",
        "https://mkp.gem.gov.in/x/p-3-4-cat.html",
    )
    assert listing_sub_types(knife, GATE, check) == ("knife_gate",)
    assert listing_sub_types(sluice, GATE, check) == ()
