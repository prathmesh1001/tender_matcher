"""The GeM branch of a run: check every item type on GeM while the catalogues are matched.

The run starts the branch before matching; a background thread checks the types one by one
(one fetcher, so GeM's pause between requests holds across types). When the catalogue side is
done, the run takes the brands of every type checked so far, waiting at most until the budget
ends. Types checked later are handed to `late` once the run's report is saved, which stores
them as a child run, the way a GeM check asked for by hand does.
"""

import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from app.core.logging import log_context
from app.schemas.report import TenderReport
from app.services.match_service import UncoveredType

logger = logging.getLogger(__name__)

# One check: the job and the type -> brands of the type's stored listings (empty: none kept).
type CheckType = Callable[[uuid.UUID, UncoveredType], list[str]]
# Types finished after the run took its brands -> stored as a child run of the run.
type LateTypes = Callable[[Mapping[str, list[str]]], None]


@dataclass
class GemBranch:
    """GeM checks of one run's item types, in a background thread."""

    run_id: str
    jobs: Sequence[tuple[uuid.UUID, UncoveredType]]
    deadline: float  # time.monotonic() after which the run stops waiting
    check: CheckType
    late: LateTypes
    saved_timeout_s: float = 3600.0  # how long the thread waits for the run's report
    # Adds the buyer categories' listings to the run's report (`recommend.with_recommendations`).
    recommend: Callable[[TenderReport], TenderReport] | None = None
    _brands: dict[str, list[str]] = field(default_factory=dict, init=False)
    _taken: set[str] = field(default_factory=set, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _finished: threading.Event = field(default_factory=threading.Event, init=False)
    _saved: threading.Event = field(default_factory=threading.Event, init=False)
    _report_saved: bool = field(default=False, init=False)

    def start(self) -> None:
        """Start checking in a daemon thread."""
        threading.Thread(target=self._run, name="gem-branch", daemon=True).start()

    def collect(self) -> dict[str, list[str]]:
        """Brands per type checked so far, waiting for the rest until the deadline.

        Returns:
            Types with at least one stored listing brand; these are the run's from now on.
        """
        self._finished.wait(max(0.0, self.deadline - time.monotonic()))
        with self._lock:
            self._taken = set(self._brands)
            return {t: list(b) for t, b in self._brands.items() if b}

    def report_saved(self, saved: bool = True) -> None:
        """The run's report is saved (late types may now follow), or the run failed (drop them).

        Args:
            saved: False when the run failed: there is no report to add late types to.
        """
        self._report_saved = saved
        self._saved.set()

    def _run(self) -> None:
        with log_context(run_id=self.run_id):
            started = time.monotonic()
            for job_id, item_type in self.jobs:
                brands = self._check_one(job_id, item_type)
                with self._lock:
                    self._brands[item_type.product_type] = brands
            self._finished.set()
            logger.info(
                "gem branch done",
                extra={"types": len(self.jobs), "seconds": round(time.monotonic() - started, 2)},
            )
            self._hand_over_late()

    def _check_one(self, job_id: uuid.UUID, item_type: UncoveredType) -> list[str]:
        """One type's check; a failure is logged (the check records it on its job)."""
        try:
            return self.check(job_id, item_type)
        except Exception:  # one type's failure never stops the others or the run
            logger.exception("gem check failed", extra={"type": item_type.product_type})
            return []

    def _hand_over_late(self) -> None:
        """After the run saved its report: types it did not take go to `late`."""
        if not self._saved.wait(self.saved_timeout_s):
            logger.warning("gem branch: run report never saved, late types dropped")
            return
        with self._lock:
            late = {t: b for t, b in self._brands.items() if t not in self._taken and b}
        if not late or not self._report_saved:
            return
        try:
            self.late(late)
        except Exception:  # the run is saved already; a failed child run is only logged
            logger.exception("gem late types failed", extra={"types": sorted(late)})
