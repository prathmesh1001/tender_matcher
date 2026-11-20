from dataclasses import replace

from app.discovery.gem_parser import GemListing, SpecRow
from app.discovery.listing_check import ListingCheck


def test_listing_naming_the_type_is_kept(check: ListingCheck, listing: GemListing) -> None:
    assert check.rejection(listing, "control_valve") is None


def test_wrong_product_is_rejected(check: ListingCheck, listing: GemListing) -> None:
    reason = check.rejection(listing, "thermocouple")
    assert reason is not None
    assert reason == "names control valve, not thermocouple"


def test_listing_with_too_few_rows_is_rejected(check: ListingCheck, listing: GemListing) -> None:
    short = replace(listing, specs=listing.specs[:3])
    reason = check.rejection(short, "control_valve")
    assert reason == "only 3 specification rows (need 5)"


def test_category_alone_proves_nothing(check: ListingCheck, listing: GemListing) -> None:
    # A gate valve page sitting in a control-valve category is still a gate valve.
    other = replace(listing, title="Cast Iron Gate Valve 50 mm", specs=listing.specs[:6])
    assert check.rejection(other, "control_valve") is not None


GEM_EXCLUDES = ["flow control valve", "throttle valve", "pneumatic fitting", "push in fitting"]


def test_model_naming_a_look_alike_is_rejected(check: ListingCheck, listing: GemListing) -> None:
    # Saved page: title "Unbranded Control Valve Pneumatic Size 15" comes from the GeM category;
    # the model "Festo Flow Control Valve GRLA-3/..." is a pneumatic fitting.
    reason = check.rejection(listing, "control_valve", GEM_EXCLUDES)
    assert reason == "names flow control valve, not control valve"


def test_model_naming_another_ontology_type_is_rejected(
    check: ListingCheck, listing: GemListing
) -> None:
    gate = replace(listing, model="XYZ Cast Iron Gate Valve 50 mm")
    assert check.rejection(gate, "control_valve") == "names gate valve, not control valve"


def test_model_naming_a_product_without_type_is_rejected(
    check: ListingCheck, listing: GemListing
) -> None:
    solenoid = replace(listing, model="ABC Solenoid Valve 24 V DC")
    assert check.rejection(solenoid, "control_valve") == "names solenoid valve, not control valve"


def test_wanted_type_outside_the_foreign_name_is_kept(
    check: ListingCheck, listing: GemListing
) -> None:
    globe = replace(listing, model="Pneumatic Globe Control Valve 25 mm")
    assert check.rejection(globe, "control_valve", GEM_EXCLUDES) is None


def test_thermocouple_with_thermowell_is_a_thermocouple(
    check: ListingCheck, listing: GemListing
) -> None:
    tc = replace(listing, title="K Type Thermocouple with Thermowell", model="TC-100")
    assert check.foreign_product(tc, "thermocouple") is None


def test_thermowell_alone_is_not_a_thermocouple(check: ListingCheck, listing: GemListing) -> None:
    well = replace(
        listing, title="Unbranded Mineral Insulated Thermocouple", model="SS316 Thermowell"
    )
    assert check.foreign_product(well, "thermocouple") == "thermowell"


def test_a_listing_naming_the_type_only_as_a_part_is_rejected(
    check: ListingCheck, listing: GemListing
) -> None:
    kit = replace(listing, model="Control Valve Repair Kit 25 mm")
    assert check.rejection(kit, "control_valve") == (
        "names control valve accessory or part, not control valve"
    )


def test_a_listing_naming_the_type_only_after_with_is_rejected(
    check: ListingCheck, listing: GemListing
) -> None:
    actuator = replace(listing, model="Pneumatic Actuator with Control Valve")
    assert check.rejection(actuator, "control_valve") is not None


def _phosphide(url: str) -> GemListing:
    rows = tuple(SpecRow(None, f"Row {n}", "x") for n in range(10))
    return GemListing(url, "QUICKPHOS Aluminium Phosphide 56% Tab 1 Kg", "QUICKPHOS", None, rows)


def test_a_title_key_listing_is_named_by_its_own_gem_category(check: ListingCheck) -> None:
    """Trial 8054155: sellers write "Aluminium Phosphide 56% Tab", never "formulation"."""
    keyed = check.for_type("aluminium_phosphide_formulation")
    inside = _phosphide(
        "https://mkp.gem.gov.in/aluminium-phosphide-formulation-v3-conforming-6438/"
        "quickphos-aluminium-phosphide-56-tablet/p-5116877-15809662103-cat.html"
    )
    assert keyed.rejection(inside, "aluminium_phosphide_formulation") is None
    elsewhere = _phosphide("https://mkp.gem.gov.in/fumigants/quickphos/p-1-2-cat.html")
    assert keyed.rejection(elsewhere, "aluminium_phosphide_formulation") is not None
