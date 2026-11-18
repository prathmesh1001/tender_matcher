"""A stored GeM listing as chunks, matched next to catalogues, cited with its provenance."""

from datetime import UTC, datetime

from app.discovery.gem_parser import GemListing
from app.ingestion.gem_listing import LISTING_PAGE, listing_name, process_listing
from app.ingestion.processing import ProcessingOptions
from app.matching.chunk_source import CatalogueDocument, provenance
from app.matching.confidence import ConfidenceOptions, with_confidence
from app.matching.listing_source import StoredListing, WithListings
from app.schemas.enums import (
    DecisionSource,
    DocType,
    MatchStatus,
    ReviewFlag,
)
from app.schemas.matching import Comparison, Evidence, LabelBasis
from tests.conftest import make_test_settings
from tests.unit.discovery.conftest import LISTING_URL

SHA = "b" * 64
FETCHED = datetime(2026, 11, 18, 7, 38, tzinfo=UTC)


def test_one_chunk_per_row_with_brand_and_model_repeated(
    listing: GemListing, processing: ProcessingOptions
) -> None:
    processed = process_listing(listing, "control_valve", processing)
    assert len(processed.chunks) == len(listing.specs)
    first = processed.chunks[0]
    assert first.page_number == LISTING_PAGE
    assert first.raw_text == f"Valve | {listing_name(listing)} | Pressure Rating: ASME Class 150"
    assert first.meta["cells"] == ["Pressure Rating", "ASME Class 150"]
    assert all(listing_name(listing) in c.raw_text for c in processed.chunks)
    assert all(c.model == listing.model for c in processed.chunks)
    assert {c.product_type for c in processed.chunks} <= {"control_valve", None}
    assert all(c.product_family == listing.title for c in processed.chunks)


def test_every_row_quote_is_on_the_stored_page(
    listing: GemListing, processing: ProcessingOptions
) -> None:
    processed = process_listing(listing, "control_valve", processing)
    page = processed.pages[0].text
    assert all(f"{r.label}: {r.value}" in page for r in listing.specs)


class _Catalogues:
    """No registered catalogue covers the type."""

    def manufacturers(self, category: str) -> list[str]:
        return ["KSB"]

    def product_types(self) -> dict[str, frozenset[str]]:
        return {"gate_valve": frozenset({"valves_pumps"})}

    def documents(self, manufacturer: str, category: str, product_type: str) -> list[object]:
        return []

    def chunks_for_product(self, *args: object, use_tags: bool) -> list[object]:
        return []

    def page_text(self, sha256: str, page_number: int) -> str | None:
        raise AssertionError("not a catalogue")

    def doc_type(self, sha256: str) -> DocType | None:
        return DocType.CATALOGUE

    def page_ocr(self, sha256: str, page_number: int) -> bool:
        return False

    def warm(self) -> int:
        return 0

    def refresh(self) -> None:
        return None


def _stored(listing: GemListing, processing: ProcessingOptions) -> StoredListing:
    document = CatalogueDocument(
        manufacturer="Unbranded",
        category="valves_pumps",
        title=listing.title,
        doc_type=DocType.GEM_LISTING,
        file=f"{SHA}.html",
        sha256=SHA,
        product_types=("control_valve",),
        url=LISTING_URL,
        fetched_at=FETCHED,
        auto_sourced=True,
    )
    return StoredListing(document, process_listing(listing, "control_valve", processing))


def test_listings_join_the_catalogues_after_refresh(
    listing: GemListing, processing: ProcessingOptions
) -> None:
    source = WithListings(_Catalogues(), lambda: [_stored(listing, processing)])  # type: ignore[arg-type]
    assert "control_valve" not in source.product_types()
    source.refresh()
    assert source.product_types()["control_valve"] == frozenset({"valves_pumps"})
    assert source.manufacturers("valves_pumps") == ["KSB", "Unbranded"]
    chunks = source.chunks_for_product("Unbranded", "valves_pumps", "control_valve", use_tags=True)
    assert len(chunks) == len(listing.specs)
    assert source.doc_type(SHA) is DocType.GEM_LISTING
    assert "Pressure Rating: ASME Class 150" in (source.page_text(SHA, 1) or "")


def test_citation_carries_listing_url_and_fetch_date(
    listing: GemListing, processing: ProcessingOptions
) -> None:
    fields = provenance(_stored(listing, processing).document)
    assert fields == {"source_url": LISTING_URL, "fetched_at": FETCHED, "seller_declared": True}


def _decided(status: MatchStatus) -> Comparison:
    evidence = Evidence(
        document=f"{SHA}.html",
        sha256=SHA,
        title="listing",
        page_number=1,
        label="Body & Bonnet Material",
        value="WCB",
        quote="Body & Bonnet Material: WCB",
        quote_parts=["Body & Bonnet Material", "WCB"],
        status=status,
        source_url=LISTING_URL,
        fetched_at=FETCHED,
        seller_declared=True,
    )
    return Comparison(
        requirement_id="1.1",
        status=status,
        needs_review=False,
        decided_by=DecisionSource.RULE,
        basis=LabelBasis.ATTRIBUTE,
        reason="",
        evidence=[evidence],
    )


class _Facts:
    def doc_type(self, sha256: str) -> DocType | None:
        return DocType.GEM_LISTING

    def page_ocr(self, sha256: str, page_number: int) -> bool:
        return False


def test_seller_declared_result_is_less_confident_and_always_reviewed() -> None:
    options = ConfidenceOptions.from_settings(make_test_settings())
    result = with_confidence(_decided(MatchStatus.FULL_MATCH), _Facts(), options)
    assert result.confidence == options.gem_listing
    assert result.needs_review
    assert ReviewFlag.SELLER_DECLARED in result.flags
    assert any("GeM listing only" in f for f in result.confidence_factors)


def test_seller_flag_adds_no_second_rule_factor() -> None:
    options = ConfidenceOptions.from_settings(make_test_settings())
    once = with_confidence(_decided(MatchStatus.FULL_MATCH), _Facts(), options)
    twice = with_confidence(once, _Facts(), options)
    assert twice.confidence == options.gem_listing
    assert twice.flags.count(ReviewFlag.SELLER_DECLARED) == 1
