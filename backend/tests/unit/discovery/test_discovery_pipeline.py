from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.exceptions import LinkFetchError
from app.discovery.gem_cards import ListingCard
from app.discovery.gem_parser import GemListing
from app.discovery.listing_check import ListingCheck
from app.discovery.pipeline import (
    DiscoveryDeps,
    DiscoveryRequest,
    candidate_cards,
    discover,
    find_category_pages,
    page_url,
    recheck_stored,
    snapshot_ref,
)
from app.ingestion.pipeline import IngestOutcome, IngestStatus, ListingSource
from app.schemas.tender_files import FetchedFile
from app.storage.file_store import LocalFileStore
from tests.unit.discovery.conftest import EMPTY_VALUES, fixture_text

BASE = "https://mkp.gem.gov.in"
FETCHED_AT = datetime(2026, 11, 18, 7, 38, tzinfo=UTC)


@dataclass
class _Pages:
    """A fetcher serving fixed pages; anything else fails like a 404."""

    pages: dict[str, str]
    asked: list[str] = field(default_factory=list)

    def get(self, url: str) -> FetchedFile:
        self.asked.append(url)
        if url not in self.pages:
            raise LinkFetchError(f"Cannot fetch {url}: 404", details={"url": url})
        content = self.pages[url].encode("utf-8")
        return FetchedFile(
            url=url, content=content, content_type="text/html", fetched_at=FETCHED_AT
        )


def _category(*paths: str) -> str:
    return "".join(f'<a href="{p}#variant_id=9">x</a>' for p in paths)


def _setup(
    tmp_path: Path, check: ListingCheck, listing_paths: list[str], known: set[str] | None = None
) -> tuple[_Pages, list[ListingSource], DiscoveryDeps]:
    page = page_url(BASE, "valves-control-valve-pneumatic")
    listing = fixture_text("control_valve_listing.html")
    pages = {page: _category(*listing_paths)}
    pages |= {BASE + p: listing for p in listing_paths}
    fetcher = _Pages(pages)
    saved: list[ListingSource] = []

    def save(source: ListingSource, _: GemListing) -> IngestOutcome:
        saved.append(source)
        return IngestOutcome(source.snapshot_ref, IngestStatus.INGESTED)

    deps = DiscoveryDeps(
        fetcher=fetcher,
        store=LocalFileStore(tmp_path),
        check=check,
        known=lambda url: url in (known or set()),
        save=save,
    )
    return fetcher, saved, deps


def _request(product_type: str = "control_valve", max_listings: int = 5) -> DiscoveryRequest:
    return DiscoveryRequest(
        product_type=product_type,
        category="valves_pumps",
        page_urls=[page_url(BASE, "valves-control-valve-pneumatic")],
        max_listings=max_listings,
        snapshot_prefix="gem_snapshots",
        empty_values=EMPTY_VALUES,
    )


def test_listing_is_frozen_and_stored_with_its_provenance(
    tmp_path: Path, check: ListingCheck
) -> None:
    _, saved, deps = _setup(tmp_path, check, ["/cv/a/p-1-11-cat.html"])
    [outcome] = discover(_request(), deps)
    assert outcome.kept
    assert outcome.reason == "kept"
    [source] = saved
    assert source.manufacturer == "Unbranded"
    assert source.url == BASE + "/cv/a/p-1-11-cat.html"
    assert source.fetched_at == FETCHED_AT
    assert source.snapshot_ref == snapshot_ref("gem_snapshots", "control_valve", source.sha256)
    frozen = (tmp_path / source.snapshot_ref).read_text(encoding="utf-8")
    assert frozen == fixture_text("control_valve_listing.html")


def test_stored_url_is_skipped_without_fetching_it(tmp_path: Path, check: ListingCheck) -> None:
    url = BASE + "/cv/a/p-1-11-cat.html"
    fetcher, saved, deps = _setup(tmp_path, check, ["/cv/a/p-1-11-cat.html"], known={url})
    [outcome] = discover(_request(), deps)
    assert not outcome.kept
    assert outcome.reason == "already stored (duplicate)"
    assert url not in fetcher.asked
    assert saved == []


def test_same_brand_and_model_twice_is_kept_once(tmp_path: Path, check: ListingCheck) -> None:
    _, saved, deps = _setup(tmp_path, check, ["/cv/a/p-1-11-cat.html", "/cv/b/p-2-22-cat.html"])
    first, second = discover(_request(), deps)
    assert first.kept
    assert not second.kept
    assert second.reason.startswith("duplicate of")
    assert len(saved) == 1


def test_wrong_product_is_not_stored(tmp_path: Path, check: ListingCheck) -> None:
    _, saved, deps = _setup(tmp_path, check, ["/cv/a/p-1-11-cat.html"])
    [outcome] = discover(_request(product_type="thermocouple"), deps)
    assert not outcome.kept
    assert outcome.reason == "names control valve, not thermocouple"
    assert saved == []


def test_at_most_max_listings_are_read(tmp_path: Path, check: ListingCheck) -> None:
    paths = [f"/cv/{i}/p-{i}-{i}-cat.html" for i in range(1, 5)]
    fetcher, _, deps = _setup(tmp_path, check, paths)
    outcomes = discover(_request(max_listings=2), deps)
    assert len(outcomes) == 2
    assert len(fetcher.asked) == 3  # the category page and two listings


def test_unreadable_listing_is_reported_not_raised(tmp_path: Path, check: ListingCheck) -> None:
    _, saved, deps = _setup(tmp_path, check, ["/cv/a/p-1-11-cat.html"])
    assert isinstance(deps.fetcher, _Pages)
    del deps.fetcher.pages[BASE + "/cv/a/p-1-11-cat.html"]
    [outcome] = discover(_request(), deps)
    assert not outcome.kept
    assert "404" in outcome.reason
    assert saved == []


def test_unreadable_category_page_fails_the_check(tmp_path: Path, check: ListingCheck) -> None:
    _, _, deps = _setup(tmp_path, check, [])
    assert isinstance(deps.fetcher, _Pages)
    deps.fetcher.pages.clear()
    with pytest.raises(LinkFetchError):
        discover(_request(), deps)


def test_configured_look_alike_is_not_stored(tmp_path: Path, check: ListingCheck) -> None:
    _, saved, deps = _setup(tmp_path, check, ["/cv/a/p-1-11-cat.html"])
    request = replace(_request(), excludes=["flow control valve"])
    [outcome] = discover(request, deps)
    assert not outcome.kept
    assert outcome.reason == "names flow control valve, not control valve"
    assert saved == []


SEARCH = BASE + "/search?q=pneumatic%20control%20valve"
CV_PAGE = page_url(BASE, "valves-control-valve-pneumatic")


def _search_request(**changes: object) -> DiscoveryRequest:
    request = replace(
        _request(),
        page_urls=[page_url(BASE, "hand-listed")],
        search_urls=[SEARCH],
        excludes=["directional control valve"],
        base_url=BASE,
    )
    return replace(request, **changes)  # type: ignore[arg-type]


def test_search_finds_the_category_of_the_type(tmp_path: Path, check: ListingCheck) -> None:
    fetcher, _, deps = _setup(tmp_path, check, [])
    assert isinstance(fetcher, _Pages)
    fetcher.pages[SEARCH] = fixture_text("search_globe_control_valve.html")
    pages = find_category_pages(_search_request(), deps)
    assert pages.source == "search"
    assert pages.page_urls == [CV_PAGE]
    assert pages.names == ["Control Valve Pneumatic"]


def test_hand_listed_categories_when_search_fits_nothing(
    tmp_path: Path, check: ListingCheck
) -> None:
    fetcher, _, deps = _setup(tmp_path, check, [])
    assert isinstance(fetcher, _Pages)
    fetcher.pages[SEARCH] = "<html><ul><li class='bn-link'><a href='/x/search'>Gloves</a></li>"
    pages = find_category_pages(_search_request(), deps)
    assert pages.source == "configured"
    assert pages.page_urls == [page_url(BASE, "hand-listed")]


def test_unreadable_search_and_no_fallback_finds_none(tmp_path: Path, check: ListingCheck) -> None:
    _, _, deps = _setup(tmp_path, check, [])
    pages = find_category_pages(_search_request(page_urls=[]), deps)
    assert pages.source == "none"
    assert pages.page_urls == []


def test_best_ranked_listings_are_read_first(tmp_path: Path, check: ListingCheck) -> None:
    paths = [
        "/cv/festo-flow-fitting/p-1-11-cat.html",
        "/cv/pneumatic-globe-control/p-2-22-cat.html",
    ]
    _, _, deps = _setup(tmp_path, check, paths)
    request = replace(_request(max_listings=1), rank_words=frozenset({"globe"}))
    [candidate] = candidate_cards(request, deps)
    assert isinstance(candidate, ListingCard)
    assert candidate.url == BASE + paths[1]


def test_stored_listing_is_rechecked_against_todays_rule(check: ListingCheck) -> None:
    html = fixture_text("control_valve_listing.html")
    url = BASE + "/cv/a/p-1-11-cat.html"
    strict = replace(_request(), excludes=["flow control valve"])
    assert recheck_stored(html, url, strict, check) == "names flow control valve, not control valve"
    assert recheck_stored(html, url, _request(), check) is None


def test_the_buyers_own_category_is_searched_first(tmp_path: Path, check: ListingCheck) -> None:
    """8081591: the bid's "Relevant Categories selected for notification" are GeM categories."""
    fetcher, _, deps = _setup(tmp_path, check, [])
    assert isinstance(fetcher, _Pages)
    impeller = BASE + "/search?q=Stainless%20Steel%20Pump%20Impeller"
    fetcher.pages[impeller] = (
        "<ul><li class='bn-link'><a href='/stainless-steel-pump-impeller/search'>"
        "Stainless Steel Pump Impeller<i>(Q3)</i></a></li>"
        "<li class='bn-link'><a href='/pump-impeller-puller/search'>Impeller Puller</a></li></ul>"
    )
    keyed = check.for_type("impeller")
    request = replace(
        _search_request(product_type="impeller", search_urls=[]),
        buyer_categories=["Stainless Steel Pump Impeller", "Shaft and Shaft Assembly (RCF)"],
    )
    pages = find_category_pages(request, replace(deps, check=keyed))
    assert pages.source == "buyer"
    assert pages.names == ["Stainless Steel Pump Impeller"]
    assert pages.page_urls == [page_url(BASE, "stainless-steel-pump-impeller")]
    # the shaft category names no impeller: never searched for this item
    assert not any("Shaft" in url for url in fetcher.asked)
