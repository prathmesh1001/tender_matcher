"""Shared fixtures for GeM Marketplace discovery: saved real pages, ontology tools."""

from pathlib import Path

import pytest

from app.discovery.gem_parser import GemListing, parse_listing
from app.discovery.listing_check import ListingCheck
from app.ingestion.processing import ProcessingOptions
from app.ingestion.product_tagger import ProductMatcher
from app.ontology.loader import load_ontologies
from app.retrieval.text_normalizer import SearchVocabulary
from tests.conftest import make_test_settings

BACKEND = Path(__file__).resolve().parents[3]
FIXTURES = BACKEND / "tests" / "fixtures" / "gem"
LISTING_URL = (
    "https://mkp.gem.gov.in/control-valve-pneumatic/"
    "festo-one-way-flow-control-valve-grla-3/p-5116877-49600927150-cat.html"
)
CATEGORY_URL = (
    "https://mkp.gem.gov.in/measuring-and-observing-and-testing-instruments-temperature-and-"
    "heat-measuring-instruments-mineral-insulated-thermocouple/search"
)
EMPTY_VALUES = ["NA", "N/A", "-"]


def fixture_text(name: str) -> str:
    """A saved GeM page."""
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def listing_html() -> str:
    return fixture_text("control_valve_listing.html")


@pytest.fixture(scope="session")
def category_html() -> str:
    return fixture_text("thermocouple_category.html")


@pytest.fixture(scope="session")
def listing(listing_html: str) -> GemListing:
    return parse_listing(listing_html, LISTING_URL + "#variant_id=1", EMPTY_VALUES)


@pytest.fixture(scope="session")
def tools() -> tuple[SearchVocabulary, ProductMatcher]:
    ontologies = load_ontologies(BACKEND / "ontology")
    vocabulary = SearchVocabulary.from_ontologies(ontologies)
    return vocabulary, ProductMatcher.from_ontologies(ontologies, vocabulary)


@pytest.fixture(scope="session")
def check(tools: tuple[SearchVocabulary, ProductMatcher]) -> ListingCheck:
    vocabulary, matcher = tools
    ontologies = load_ontologies(BACKEND / "ontology")
    return ListingCheck.from_ontologies(ontologies, vocabulary, matcher, min_spec_rows=5)


@pytest.fixture(scope="session")
def processing(tools: tuple[SearchVocabulary, ProductMatcher]) -> ProcessingOptions:
    vocabulary, matcher = tools
    return ProcessingOptions.from_settings(make_test_settings(), vocabulary, matcher, None)
