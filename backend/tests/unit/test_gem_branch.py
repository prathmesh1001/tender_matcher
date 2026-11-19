"""The GeM branch of a run: brands within the budget, late types after the report is saved."""

import threading
import time
import uuid
from collections.abc import Mapping

from app.services.gem_branch import GemBranch
from app.services.match_service import UncoveredType


def _type(name: str) -> UncoveredType:
    return UncoveredType(name, "valves_pumps", ["1"], titles=[f"{name} title"])


class _Checks:
    """Answers per type; a type in `slow` waits for `release` first."""

    def __init__(
        self, answers: Mapping[str, list[str] | Exception], slow: frozenset[str] = frozenset()
    ) -> None:
        self.answers = answers
        self.slow = slow
        self.release = threading.Event()
        self.late: list[dict[str, list[str]]] = []
        self.late_done = threading.Event()

    def check(self, job_id: uuid.UUID, item_type: UncoveredType) -> list[str]:
        if item_type.product_type in self.slow:
            self.release.wait(5)
        answer = self.answers[item_type.product_type]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def take_late(self, late: Mapping[str, list[str]]) -> None:
        self.late.append(dict(late))
        self.late_done.set()


def _branch(checks: _Checks, types: list[str], budget_s: float) -> GemBranch:
    return GemBranch(
        run_id="run",
        jobs=[(uuid.uuid4(), _type(t)) for t in types],
        deadline=time.monotonic() + budget_s,
        check=checks.check,
        late=checks.take_late,
        saved_timeout_s=5,
    )


def test_collect_returns_the_brands_of_every_type_checked_in_time() -> None:
    checks = _Checks({"gate_valve": ["Acme", "Beta"], "foot_valve": []})
    branch = _branch(checks, ["gate_valve", "foot_valve"], budget_s=5)
    branch.start()
    assert branch.collect() == {"gate_valve": ["Acme", "Beta"]}  # no listing kept: left out
    branch.report_saved()
    assert not checks.late_done.wait(0.2)


def test_a_type_checked_after_the_budget_arrives_late_once_the_report_is_saved() -> None:
    checks = _Checks(
        {"gate_valve": ["Acme"], "foot_valve": ["Gamma"]}, slow=frozenset({"foot_valve"})
    )
    branch = _branch(checks, ["gate_valve", "foot_valve"], budget_s=0.3)
    branch.start()
    assert branch.collect() == {"gate_valve": ["Acme"]}
    checks.release.set()
    assert not checks.late_done.wait(0.3)  # not before the run saved its report
    branch.report_saved()
    assert checks.late_done.wait(5)
    assert checks.late == [{"foot_valve": ["Gamma"]}]


def test_late_types_are_dropped_when_the_run_failed() -> None:
    checks = _Checks({"foot_valve": ["Gamma"]}, slow=frozenset({"foot_valve"}))
    branch = _branch(checks, ["foot_valve"], budget_s=0.1)
    branch.start()
    assert branch.collect() == {}
    checks.release.set()
    branch.report_saved(saved=False)
    assert not checks.late_done.wait(0.5)


def test_a_failing_type_does_not_stop_the_others() -> None:
    checks = _Checks({"gate_valve": RuntimeError("GeM down"), "foot_valve": ["Gamma"]})
    branch = _branch(checks, ["gate_valve", "foot_valve"], budget_s=5)
    branch.start()
    assert branch.collect() == {"foot_valve": ["Gamma"]}
