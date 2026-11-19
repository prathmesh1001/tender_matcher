"""Category-page cards, vendor ranking and brand variety (GeM step 5)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.exceptions import LinkFetchError
from app.discovery.gem_cards import ListingCard, clean_brand, listing_cards, total_results
from app.discovery.gem_config import load_gem_config
from app.discovery.gem_parser import parse_listing
from app.discovery.gem_search import pick_cards, rank_cards
from app.discovery.listing_check import ListingCheck
from app.discovery.pipeline import DiscoveryDeps, category_cards
from app.ingestion.pipeline import IngestOutcome, IngestStatus
from app.schemas.tender_files import FetchedFile
from app.storage.file_store import LocalFileStore
from tests.unit.discovery.conftest import BACKEND, fixture_text

PAGE = "https://mkp.gem.gov.in/valves-butterfly-valves-v2-as-per-is-13095/search"
SELLER_RANK = load_gem_config(BACKEND / "config" / "gem_marketplace.yaml").seller_rank


def test_cards_hold_title_brand_seller_rating_and_assessment() -> None:
    cards = listing_cards(fixture_text("butterfly_category_cards.html"), PAGE)
    assert len(cards) == 2
    first = cards[0]
    assert first.url == (
        "https://mkp.gem.gov.in/butterfly-valves-v2-per-13095/"
        "butterfly-valve-wafer-type-pn-1-6-with/p-5116877-16653259417-cat.html"
    )
    assert first.title == "ZOLOTO Butterfly Valves 40 Flangeless Wafer Valve"
    assert first.brand == "ZOLOTO"
    assert first.sold_as == ("resellers", "authorized")
    assert first.rating == 4.0
    assert first.assessed is False


def test_the_page_states_how_many_products_the_category_has() -> None:
    assert total_results(fixture_text("butterfly_category_cards.html")) == 12
    assert total_results("<html></html>") is None


@pytest.mark.parametrize(
    ("printed", "brand"),
    [
        ("Brand: ZOLOTO", "ZOLOTO"),
        ("AAVAD®", "AAVAD"),
        ("Kirloskar (R)", "Kirloskar"),
        ("ROCKWELL PUMPS--ROCKWELL PUMPS AND MOTORS PRIVATE LIMITED", "ROCKWELL PUMPS"),
        ("Brand:  ", None),
    ],
)
def test_brand_loses_its_label_and_trade_mark(printed: str, brand: str | None) -> None:
    assert clean_brand(printed) == brand


def test_product_page_brand_leaves_out_the_superscript_mark() -> None:
    # Real markup (7865644 thermocouple listing): <span class="brand-name">AAVAD<sup>R</sup>.
    html = (
        "<h1 itemprop='name'>AAVAD Mineral Insulated Thermocouple"
        "<div class='brand'><div><span class=\"brand-name\">AAVAD<sup>R</sup></span></div></div>"
        "<span class='model'>(AJWS-6X500)</span></h1>"
    )
    listing = parse_listing(html, "https://mkp.gem.gov.in/x/p-1-2-cat.html")
    assert listing.brand == "AAVAD"
    assert listing.model == "AJWS-6X500"


def _card(n: int, brand: str, title: str, sold: tuple[str, ...] = (), **kw: object) -> ListingCard:
    return ListingCard(f"https://mkp.gem.gov.in/x/p-{n}-{n}-cat.html", title, brand, sold, **kw)  # type: ignore[arg-type]


def test_product_fit_comes_before_how_it_is_sold(check: ListingCheck) -> None:
    fits = _card(1, "A", "Wafer Butterfly Valve PN16", ("resellers",))
    sold_well = _card(2, "B", "Butterfly Valve", ("oem",))
    ranked = rank_cards([sold_well, fits], frozenset({"wafer", "pn16"}), check, SELLER_RANK)
    assert ranked == [fits, sold_well]


def test_oem_then_authorised_then_rating_then_assessed(check: ListingCheck) -> None:
    reseller = _card(1, "A", "Butterfly Valve", ("resellers",), rating=5.0)
    authorised = _card(2, "B", "Butterfly Valve", ("resellers", "authorized"), rating=3.0)
    oem_low = _card(3, "C", "Butterfly Valve", ("oem",), rating=3.0)
    oem_high = _card(4, "D", "Butterfly Valve", ("oem",), rating=4.5)
    oem_high_assessed = _card(5, "E", "Butterfly Valve", ("oem",), rating=4.5, assessed=True)
    cards = [reseller, authorised, oem_low, oem_high, oem_high_assessed]
    ranked = rank_cards(cards, frozenset(), check, SELLER_RANK)
    assert ranked == [oem_high_assessed, oem_high, oem_low, authorised, reseller]


def test_one_brand_cannot_fill_every_slot() -> None:
    # A real IS 13095 page: 12 cards, all ZOLOTO in different sizes.
    zoloto = [_card(n, "ZOLOTO", f"ZOLOTO Butterfly Valves {n}") for n in range(1, 13)]
    others = [_card(20, "Kirloskar", "KBL Butterfly"), _card(21, "zoloto", "ZOLOTO Wafer")]
    picked = pick_cards([*zoloto, *others], max_listings=10, max_per_brand=2)
    assert [c.brand for c in picked] == ["ZOLOTO", "ZOLOTO", "Kirloskar"]


def test_cards_without_a_brand_are_not_capped() -> None:
    cards = [_card(n, "", f"Valve {n}") for n in range(1, 5)]
    assert len(pick_cards(cards, max_listings=10, max_per_brand=1)) == 4


@dataclass
class _Pages:
    pages: dict[str, str]
    asked: list[str] = field(default_factory=list)

    def get(self, url: str) -> FetchedFile:
        self.asked.append(url)
        if url not in self.pages:
            raise LinkFetchError(f"Cannot fetch {url}: 404", details={"url": url})
        return FetchedFile(
            url=url,
            content=self.pages[url].encode("utf-8"),
            content_type="text/html",
            fetched_at=datetime(2026, 11, 19, tzinfo=UTC),
        )


def _cards_page(numbers: range, total: int) -> str:
    cards = "".join(
        f'<div class="variant-wrapper"><span class="variant-title">'
        f'<a href="/c/p-{n}-{n}-cat.html#variant_id=1" title="Valve {n}">Valve</a></span>'
        f'<div class="variant-brand"> Brand: B{n}</div></div>'
        for n in numbers
    )
    return f'{cards}<script>x({{"total_results":{total}}})</script>'


def _deps(pages: _Pages, tmp_path: Path, check: ListingCheck) -> DiscoveryDeps:
    return DiscoveryDeps(
        fetcher=pages,
        store=LocalFileStore(tmp_path),
        check=check,
        known=lambda _: False,
        save=lambda s, _: IngestOutcome(s.snapshot_ref, IngestStatus.INGESTED),
    )


def test_later_result_pages_are_read_until_the_total(tmp_path: Path, check: ListingCheck) -> None:
    pages = _Pages(
        {
            PAGE: _cards_page(range(1, 13), total=20),
            f"{PAGE}?page=2": _cards_page(range(13, 21), total=20),
        }
    )
    cards = category_cards(PAGE, max_pages=5, deps=_deps(pages, tmp_path, check))
    assert len(cards) == 20
    assert pages.asked == [PAGE, f"{PAGE}?page=2"]  # 20 of 20 read: no page 3


def test_an_unreadable_later_page_ends_the_reading(tmp_path: Path, check: ListingCheck) -> None:
    pages = _Pages({PAGE: _cards_page(range(1, 13), total=40)})
    cards = category_cards(PAGE, max_pages=3, deps=_deps(pages, tmp_path, check))
    assert len(cards) == 12
