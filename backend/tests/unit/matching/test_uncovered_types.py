"""Which product types a run may check on GeM: those no document of the run's makers covers."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from app.ontology.loader import load_ontologies
from app.services.match_service import MatchService
from app.tender.bid_facts import BidFacts
from app.tender.title_key import TitleKeyOptions

BACKEND = Path(__file__).resolve().parents[3]
TITLE = 'Pneumatic (ATO) Globe Type Two Way Control Valve Of Size 1" For Steam Line'


class _Source:
    """Catalogues of two valve makers plus one stored GeM listing (brand "Unbranded")."""

    def __init__(self) -> None:
        self.docs = {
            ("KSB", "valves_pumps", "gate_valve"): ["ksb gate"],
            ("Unbranded", "valves_pumps", "control_valve"): ["gem listing"],
        }

    def refresh(self) -> None:
        return None

    def product_types(self) -> dict[str, frozenset[str]]:
        return {t: frozenset({c}) for (_, c, t) in self.docs}

    def documents(self, manufacturer: str, category: str, product_type: str) -> list[str]:
        return self.docs.get((manufacturer, category, product_type), [])


KEYS = TitleKeyOptions(stop_words=frozenset({"supply", "of", "with"}), max_words=3)


def _service(title_keys: TitleKeyOptions | None = None) -> MatchService:
    unused = cast(Any, None)
    return MatchService(
        source=cast(Any, _Source()),
        tender_options=unused,
        requirement_options=unused,
        selection=unused,
        confidence=unused,
        ontologies=load_ontologies(BACKEND / "ontology"),
        tender_root=BACKEND,
        title_keys=title_keys,
        untyped_category="general",
    )


def _report(*titles: str) -> Any:
    items = [
        SimpleNamespace(item=SimpleNamespace(title=t, item_no=str(n)))
        for n, t in enumerate(titles, 1)
    ]
    return SimpleNamespace(items=items, buyer_gem_categories=[])


def test_a_listing_stored_by_another_check_does_not_hide_the_button() -> None:
    # Run 01a1ee4b (8063368): 15 valve makers chosen, a GeM listing stored earlier.
    uncovered = _service().uncovered_types(_report(TITLE, TITLE), ["KSB", "KITZ"])
    assert [(u.product_type, u.item_nos) for u in uncovered] == [("control_valve", ["1", "2"])]


def test_a_run_that_matched_the_listing_brand_is_covered() -> None:
    # The run a GeM check produces includes the listing brands.
    assert _service().uncovered_types(_report(TITLE), ["KSB", "Unbranded"]) == []


def test_without_manufacturers_any_stored_document_covers() -> None:
    assert _service().uncovered_types(_report(TITLE)) == []


def test_a_type_with_a_catalogue_of_the_run_is_not_offered() -> None:
    assert _service().uncovered_types(_report("Gate Valve DN100 PN16"), ["KSB"]) == []


def test_an_item_naming_no_ontology_type_is_offered_by_its_title_key() -> None:
    report = _report("PVC foot valve Ball type 50 mm", "Supply of Hydraulic Hose Pipe 1/2 inch")
    uncovered = _service(KEYS).uncovered_types(report, ["KSB"])
    assert [(u.product_type, u.category, u.item_nos, u.by_title) for u in uncovered] == [
        ("foot_valve", "valves_pumps", ["1"], True),  # an ontology product without a type
        ("hydraulic_hose_pipe", "general", ["2"], True),
    ]


def test_without_title_keys_untyped_items_are_not_offered() -> None:
    assert _service().uncovered_types(_report("PVC foot valve Ball type"), ["KSB"]) == []


def test_a_title_naming_several_types_gets_no_key() -> None:
    # Never guessed: "Gate valve and globe valve" names two ontology types.
    assert _service(KEYS).uncovered_types(_report("Gate valve and globe valve set"), []) == []


def test_item_types_lists_every_type_covered_or_not(monkeypatch: Any) -> None:
    service = _service(KEYS)
    items = [
        SimpleNamespace(item=SimpleNamespace(title=t, item_no=str(n)))
        for n, t in enumerate(["Gate Valve DN100 PN16", TITLE, "Gate Valve DN150"], 1)
    ]
    monkeypatch.setattr(service, "parse_tender", lambda tender: SimpleNamespace(items=items, facts=BidFacts()))
    found = service.item_types("GEM_X")
    assert [(t.product_type, t.item_nos, t.titles) for t in found] == [
        ("gate_valve", ["1", "3"], ["Gate Valve DN100 PN16", "Gate Valve DN150"]),
        ("control_valve", ["2"], [TITLE]),
    ]


def _parsed(service: MatchService, monkeypatch: Any, *titles: str) -> None:
    items = [
        SimpleNamespace(item=SimpleNamespace(title=t, item_no=str(n)))
        for n, t in enumerate(titles, 1)
    ]
    monkeypatch.setattr(service, "parse_tender", lambda tender: SimpleNamespace(items=items, facts=BidFacts()))


def test_the_gem_branch_searches_types_without_database_documents_first(monkeypatch: Any) -> None:
    service = _service(KEYS)
    _parsed(service, monkeypatch, "Gate Valve DN100", TITLE, "Supply of Hydraulic Hose Pipe")
    found = service.branch_types("GEM_X", ["KSB"])
    assert [(t.product_type, t.by_title) for t in found] == [
        ("control_valve", False),  # no document of KSB: GeM is its only source
        ("hydraulic_hose_pipe", True),  # not a valve or pump: searched by its title words
        ("gate_valve", False),  # KSB has a document: GeM vendors only beside it
    ]


def test_the_gem_branch_can_leave_title_keys_out(monkeypatch: Any) -> None:
    service = _service(KEYS)
    _parsed(service, monkeypatch, "Gate Valve DN100", "Supply of Hydraulic Hose Pipe")
    found = service.branch_types("GEM_X", ["KSB"], title_keys=False)
    assert [t.product_type for t in found] == ["gate_valve"]
