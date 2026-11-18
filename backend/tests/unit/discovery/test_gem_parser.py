from app.discovery.gem_parser import GemListing, SpecRow, listing_urls, parse_listing
from tests.unit.discovery.conftest import CATEGORY_URL, LISTING_URL


def test_listing_title_brand_and_model_are_read(listing: GemListing) -> None:
    assert listing.title == "Unbranded Control Valve Pneumatic Size 15 with Warranty 6 month"
    assert listing.brand == "Unbranded"
    assert listing.model == "Festo Flow Control Valve GRLA-3/6-QS-10-D-193151"
    assert listing.url == LISTING_URL  # the #variant fragment is dropped


def test_spec_rows_keep_label_value_and_group(listing: GemListing) -> None:
    assert SpecRow("Valve", "Pressure Rating", "ASME Class 150") in listing.specs
    assert SpecRow("Valve", "Body & Bonnet Material", "WCB") in listing.specs  # &amp; decoded
    assert SpecRow("Actuator", "Actuator Pneumatic Connection", "1/4 inch BSP") in listing.specs
    assert listing.specs[0] == SpecRow("Valve", "Pressure Rating", "ASME Class 150")


def test_rows_stating_nothing_are_dropped(listing_html: str, listing: GemListing) -> None:
    every = parse_listing(listing_html, LISTING_URL)
    assert any(r.label == "Valve Kv" and r.value == "NA" for r in every.specs)
    assert all(r.value != "NA" for r in listing.specs)
    assert len(every.specs) > len(listing.specs)


def test_page_without_spec_table_has_no_rows() -> None:
    listing = parse_listing("<html><h1 itemprop='name'>Something</h1></html>", LISTING_URL)
    assert listing.title == "Something"
    assert listing.specs == ()
    assert listing.brand is None


def test_category_page_gives_each_product_link_once(category_html: str) -> None:
    urls = listing_urls(category_html, CATEGORY_URL)
    assert len(urls) == 4  # each product is linked twice on the page
    assert all(u.startswith("https://mkp.gem.gov.in/") for u in urls)
    assert all(u.endswith("-cat.html") and "#" not in u for u in urls)
    assert not any("category_report" in u for u in urls)
