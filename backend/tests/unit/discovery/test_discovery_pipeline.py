from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.exceptions import LinkFetchError
from app.discovery.gem_parser import GemListing
from app.discovery.listing_check import ListingCheck
from app.discovery.pipeline import (
    DiscoveryDeps,
    DiscoveryRequest,
    discover,
    page_url,
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
    assert "does not name thermocouple" in outcome.reason
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
