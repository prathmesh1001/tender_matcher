"""The tender's best vendors over all its items, database and GeM (user decisions A1/B1/C1).

Each item already lists its best vendors with a match level (`scorer.top_vendors`). Here they
are gathered per vendor (source + name): its best level on any item, how many items it fully or
partly matches, its best compatibility %. The list then holds

1. every vendor whose best level is full or partial match, best first, at most `max_count`;
2. when fewer than `min_count` are found: the next best vendors under their own level (needs
   review, not checked), then vendors that do not meet the specification.

Only vendors listed on an item count: sellers of the buyer's GeM categories that no item was
checked against are left out (user decision 2026-11-23). A level is never raised to fill the
list: a vendor shows the level its own results gave it.
Within a level, a match on decided requirements goes before one by product name only, then
more full items, more partial items, higher compatibility, branded before unbranded, name.

Pure functions.
"""

from dataclasses import dataclass, field
from typing import Final

from app.discovery.pipeline import UNBRANDED
from app.matching.scorer import NAME_ONLY_REASON
from app.schemas.enums import VendorMatchLevel, VendorSource
from app.schemas.report import ItemVendor, TenderReport, TenderVendor

# Fill order (None only before a tally's first item).
_LEVEL_RANK: Final[dict[VendorMatchLevel | None, int]] = {
    VendorMatchLevel.FULL_MATCH: 0,
    VendorMatchLevel.PARTIAL_MATCH: 1,
    VendorMatchLevel.NEEDS_REVIEW: 2,
    VendorMatchLevel.NOT_CHECKED: 3,
    None: 4,
    VendorMatchLevel.DOES_NOT_MEET: 5,
}
_MATCHED: Final = frozenset({VendorMatchLevel.FULL_MATCH, VendorMatchLevel.PARTIAL_MATCH})


@dataclass(slots=True)
class _Tally:
    """One vendor's results gathered over the items."""

    name: str
    source: VendorSource | None
    level: VendorMatchLevel | None = None
    reason: str = ""
    full: int = 0
    partial: int = 0
    listed: int = 0
    best_pct: float | None = None
    url: str | None = None
    category: str | None = None
    levels: list[VendorMatchLevel] = field(default_factory=list)

    def add(self, vendor: ItemVendor) -> None:
        """Count one item's result."""
        level = vendor.match_level or VendorMatchLevel.NOT_CHECKED
        self.listed += 1
        self.full += level is VendorMatchLevel.FULL_MATCH
        self.partial += level is VendorMatchLevel.PARTIAL_MATCH
        pct = vendor.score.compatibility_pct
        if pct is not None and (self.best_pct is None or pct > self.best_pct):
            self.best_pct = pct
        self.url = self.url or vendor.source_url
        if self.level is None or _LEVEL_RANK[level] < _LEVEL_RANK[self.level]:
            self.level, self.reason = level, vendor.level_reason
        elif level is self.level and self.reason == NAME_ONLY_REASON:
            self.reason = vendor.level_reason  # a decided partial beats a name-only one

    def order(self) -> tuple[int, bool, int, int, float, bool, str]:
        """Sort key, best first."""
        return (
            _LEVEL_RANK[self.level],
            self.reason == NAME_ONLY_REASON,
            -self.full,
            -self.partial,
            -(self.best_pct or 0.0),
            self.name.casefold() == UNBRANDED.casefold(),
            self.name.casefold(),
        )

    def as_vendor(self, rank: int) -> TenderVendor:
        """The tally as a report row."""
        return TenderVendor(
            rank=rank,
            name=self.name,
            source=self.source,
            match_level=self.level,
            level_reason=self.reason,
            items_full=self.full,
            items_partial=self.partial,
            items_listed=self.listed,
            best_compatibility_pct=self.best_pct,
            source_url=self.url,
            category=self.category,
        )


def _tallies(report: TenderReport) -> dict[tuple[str, str], _Tally]:
    """Every item vendor gathered per (source, name)."""
    out: dict[tuple[str, str], _Tally] = {}
    for item in report.items:
        for vendor in item.top_vendors:
            key = (vendor.source.value, vendor.manufacturer.casefold())
            tally = out.setdefault(key, _Tally(vendor.manufacturer, vendor.source))
            tally.add(vendor)
    return out


def best_vendors(report: TenderReport, min_count: int, max_count: int) -> list[TenderVendor]:
    """The tender's best vendors: full and partial first, filled up to `min_count`.

    Args:
        report: A finished report (items' top vendors set).
        min_count: The list is filled up to this many when vendors exist.
        max_count: Most vendors listed.

    Returns:
        Ranked vendors, ranks from 1.
    """
    ranked = sorted(_tallies(report).values(), key=_Tally.order)
    matched = [t for t in ranked if t.level in _MATCHED][:max_count]
    rest = [t for t in ranked if t.level not in _MATCHED]
    chosen = matched + rest[: max(0, min(min_count, max_count) - len(matched))]
    return [t.as_vendor(rank) for rank, t in enumerate(chosen, start=1)]


def with_best_vendors(report: TenderReport, min_count: int, max_count: int) -> TenderReport:
    """The report with its best vendors list (`best_vendors`) set.

    Args:
        report: A finished report.
        min_count: Fill the list up to this many.
        max_count: Most vendors listed.

    Returns:
        A new report.
    """
    return report.model_copy(update={"best_vendors": best_vendors(report, min_count, max_count)})
