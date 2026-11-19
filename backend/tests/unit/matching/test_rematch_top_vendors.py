"""Items matched again after a GeM check keep their top vendors (run 01a1f2c7, 8157758 item 13).

The item report, matcher and scorer are fakes: only the wiring of `_rematched` is tested.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.services import match_service
from app.services.match_service import MatchService


class _Item:
    """Stands in for ItemReport: records the fields a copy updates."""

    def __init__(self, **fields: Any) -> None:
        self.fields = fields
        self.manufacturers = ["listing"]

    def model_copy(self, update: dict[str, Any]) -> _Item:
        return _Item(**{**self.fields, **update})


def _service() -> MatchService:
    unused = cast(Any, None)
    selection = SimpleNamespace(min_coverage_pct=50.0, top_vendors_per_item=3, status_scores={})
    return MatchService(
        source=unused,
        tender_options=unused,
        requirement_options=unused,
        selection=cast(Any, selection),
        confidence=unused,
        ontologies=[],
        tender_root=Path(__file__).parent,
    )


@pytest.fixture(autouse=True)
def _fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(match_service, "match_item", lambda *a, **k: _Item())
    monkeypatch.setattr(match_service, "items_with_confidence", lambda reports, *a: reports)
    review = lambda r, note, o: r.model_copy({"note": note})  # noqa: E731
    monkeypatch.setattr(match_service, "item_for_review", review)
    monkeypatch.setattr(match_service, "top_vendors", lambda m, cov, limit: [f"best of {m}"])


def test_a_rematched_item_lists_its_top_vendors() -> None:
    item = cast(Any, _service()._rematched(cast(Any, None), None, None))
    assert item.fields["top_vendors"] == ["best of ['listing']"]


def test_review_notes_come_before_the_top_vendors() -> None:
    item = cast(Any, _service()._rematched(cast(Any, None), None, "extraction unreliable"))
    assert item.fields == {"note": "extraction unreliable", "top_vendors": ["best of ['listing']"]}


class _Old:
    """Stands in for the run's item report: catalogue vendors already matched."""

    def __init__(self, *names: str) -> None:
        self.manufacturers = [SimpleNamespace(manufacturer=n) for n in names]
        self.fields: dict[str, Any] = {}

    def model_copy(self, update: dict[str, Any]) -> _Old:
        copy = _Old()
        copy.manufacturers = update.get("manufacturers", self.manufacturers)
        copy.fields = {**self.fields, **update}
        return copy


def _entry() -> Any:
    return SimpleNamespace(
        item=SimpleNamespace(title="Gate valve"), ctx=SimpleNamespace(ontology=None)
    )


def test_listing_brands_are_appended_after_the_catalogue_vendors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    monkeypatch.setattr(service, "item_key", lambda title, ontology: "gate_valve")
    asked: list[Any] = []

    def rematched(entry: Any, brands: Any, note: Any) -> Any:
        asked.append(list(brands))
        return SimpleNamespace(manufacturers=[SimpleNamespace(manufacturer=b) for b in brands])

    monkeypatch.setattr(service, "_rematched", rematched)
    old = _Old("KSB", "Kirloskar Brothers")
    brands = {"gate_valve": ["ksb", "Zoloto"], "ball_valve": ["Other"]}
    item = cast(Any, service._with_brands(_entry(), cast(Any, old), brands, None))
    assert asked == [["Zoloto"]]  # KSB is listed already: its catalogue evidence is kept
    assert [m.manufacturer for m in item.manufacturers] == ["KSB", "Kirloskar Brothers", "Zoloto"]
    assert "top_vendors" in item.fields


def test_an_item_with_no_new_brand_is_returned_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    monkeypatch.setattr(service, "item_key", lambda title, ontology: "gate_valve")
    old = _Old("KSB")
    result: Any = service._with_brands(_entry(), cast(Any, old), {"ball_valve": ["X"]}, None)
    assert result is old
