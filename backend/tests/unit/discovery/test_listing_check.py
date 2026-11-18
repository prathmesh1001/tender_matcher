from dataclasses import replace

from app.discovery.gem_parser import GemListing
from app.discovery.listing_check import ListingCheck


def test_listing_naming_the_type_is_kept(check: ListingCheck, listing: GemListing) -> None:
    assert check.rejection(listing, "control_valve") is None


def test_wrong_product_is_rejected(check: ListingCheck, listing: GemListing) -> None:
    reason = check.rejection(listing, "thermocouple")
    assert reason is not None
    assert "does not name thermocouple" in reason


def test_listing_with_too_few_rows_is_rejected(check: ListingCheck, listing: GemListing) -> None:
    short = replace(listing, specs=listing.specs[:3])
    reason = check.rejection(short, "control_valve")
    assert reason == "only 3 specification rows (need 5)"


def test_category_alone_proves_nothing(check: ListingCheck, listing: GemListing) -> None:
    # A gate valve page sitting in a control-valve category is still a gate valve.
    other = replace(listing, title="Cast Iron Gate Valve 50 mm", specs=listing.specs[:6])
    assert check.rejection(other, "control_valve") is not None
