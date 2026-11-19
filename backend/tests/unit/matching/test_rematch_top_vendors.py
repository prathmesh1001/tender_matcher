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
