"""Page layout from positions, not text order: table grids rebuilt from drawn rules.

Some tender tables are drawn with thin filled rectangles instead of stroked lines, and
PyMuPDF's table finder returns nothing for them (failure case T1: 8017367 `boq_spec.pdf`,
28 line items read as 2 prose chunks, item 11's text running into items 12 and 13). The rules
are still on the page, so the grid is rebuilt here from their positions:

1. every thin rectangle or straight line is a `Rule` (horizontal or vertical);
2. rules that touch form one component, so a page border never joins a table;
3. a component's distinct rule positions give the column edges (x) and row edges (y);
4. each cell (row band x column) grows left, right, up and down until a rule stops it, so a
   merged cell (one specification shared by items 10-12) covers every row it spans and each
   of those items gets its whole text.

Everything here is pure geometry on plain numbers; reading drawings and words from the PDF
is done by `table_extractor`.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.core.config import Settings
from app.schemas.parsing import BBox


@dataclass(frozen=True, slots=True)
class LayoutOptions:
    """Rule-grid thresholds (built from settings so this module never reads config)."""

    rule_max_thickness: float
    rule_tolerance: float
    min_columns: int
    min_rows: int
    max_rules: int = 0  # more thin shapes than this = drawing or outlined text; 0 = no cap
    header_max_words: int = 8  # a first band of longer cells is data, not the header

    @classmethod
    def from_settings(cls, settings: Settings) -> LayoutOptions:
        """Build options from application settings."""
        return cls(
            rule_max_thickness=settings.LAYOUT_RULE_MAX_THICKNESS,
            rule_tolerance=settings.LAYOUT_RULE_TOLERANCE,
            min_columns=settings.LAYOUT_GRID_MIN_COLUMNS,
            min_rows=settings.LAYOUT_GRID_MIN_ROWS,
            max_rules=settings.LAYOUT_MAX_RULES,
            header_max_words=settings.TABLE_HEADER_MAX_WORDS,
        )


@dataclass(frozen=True, slots=True)
class Rule:
    """A ruling line: horizontal at y=`at` from x=`start` to `end`, or vertical at x=`at`."""

    horizontal: bool
    at: float
    start: float
    end: float


def rule_from_box(box: BBox, max_thickness: float) -> Rule | None:
    """A thin box (a filled rectangle or a stroked line's extent) as a rule, else None.

    Args:
        box: The drawn shape's bounding box.
        max_thickness: Thicker shapes are areas (cell shading, frames), not rules.

    Returns:
        The rule along the box's long side, or None.
    """
    width, height = box.x1 - box.x0, box.y1 - box.y0
    if height <= max_thickness < width:
        return Rule(horizontal=True, at=(box.y0 + box.y1) / 2, start=box.x0, end=box.x1)
    if width <= max_thickness < height:
        return Rule(horizontal=False, at=(box.x0 + box.x1) / 2, start=box.y0, end=box.y1)
    return None


def _touch(a: Rule, b: Rule, tol: float) -> bool:
    """True if two rules cross, meet, or continue each other (within `tol`)."""
    if a.horizontal == b.horizontal:
        return abs(a.at - b.at) <= tol and a.start <= b.end + tol and b.start <= a.end + tol
    h, v = (a, b) if a.horizontal else (b, a)
    return h.start - tol <= v.at <= h.end + tol and v.start - tol <= h.at <= v.end + tol


class _UnionFind:
    """Disjoint sets over rule indexes."""

    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def root(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        self.parent[self.root(j)] = self.root(i)


def _join_collinear(
    rules: Sequence[Rule], indexes: list[int], tol: float, sets: _UnionFind
) -> None:
    """Join rules of one orientation that continue each other (same position, overlapping)."""
    indexes = sorted(indexes, key=lambda i: rules[i].at)
    start = 0
    for stop in range(1, len(indexes) + 1):
        if stop < len(indexes) and rules[indexes[stop]].at - rules[indexes[stop - 1]].at <= tol:
            continue
        line = sorted(indexes[start:stop], key=lambda i: rules[i].start)
        reach = line[0] if line else -1
        for i in line[1:]:
            if rules[i].start <= rules[reach].end + tol:
                sets.union(reach, i)
            if rules[i].end > rules[reach].end:
                reach = i
        start = stop


def _join_crossings(
    rules: Sequence[Rule], horizontal: list[int], vertical: list[int], tol: float, sets: _UnionFind
) -> None:
    """Join each vertical rule with the horizontal rules it crosses or meets."""
    by_height = sorted(horizontal, key=lambda i: rules[i].at)
    heights = [rules[i].at for i in by_height]
    for v in vertical:
        lo = bisect_left(heights, rules[v].start - tol)
        hi = bisect_right(heights, rules[v].end + tol)
        for h in by_height[lo:hi]:
            if _touch(rules[h], rules[v], tol):
                sets.union(h, v)


def connected_rules(rules: Sequence[Rule], tol: float) -> list[list[Rule]]:
    """Group rules into components of touching rules (union-find, sort-based).

    Pages with many thin shapes (outlined text, drawings) make an all-pairs check too slow,
    so collinear rules are joined in one sorted sweep and crossings are looked up by height.

    Args:
        rules: All rules of a page.
        tol: Gap still counted as touching, in points.

    Returns:
        Components, each in the original rule order.
    """
    sets = _UnionFind(len(rules))
    horizontal = [i for i, r in enumerate(rules) if r.horizontal]
    vertical = [i for i, r in enumerate(rules) if not r.horizontal]
    _join_collinear(rules, horizontal, tol, sets)
    _join_collinear(rules, vertical, tol, sets)
    _join_crossings(rules, horizontal, vertical, tol, sets)
    groups: dict[int, list[Rule]] = {}
    for i, rule in enumerate(rules):
        groups.setdefault(sets.root(i), []).append(rule)
    return list(groups.values())


def cluster_positions(values: Iterable[float], tol: float) -> list[float]:
    """Distinct positions, values within `tol` of the previous one merged (first kept)."""
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


@dataclass(frozen=True, slots=True)
class RuleGrid:
    """Column edges (x), row edges (y) and the rules that bound cells, for one table."""

    xs: tuple[float, ...]
    ys: tuple[float, ...]
    rules: tuple[Rule, ...]
    tol: float

    @property
    def bbox(self) -> BBox:
        """The table's outer box."""
        return BBox(x0=self.xs[0], y0=self.ys[0], x1=self.xs[-1], y1=self.ys[-1])

    def vertical_rule_at(self, x: float, y: float) -> bool:
        """True if a vertical rule at `x` passes through height `y`."""
        return any(
            not r.horizontal and abs(r.at - x) <= self.tol and r.start <= y <= r.end
            for r in self.rules
        )

    def horizontal_rule_at(self, y: float, x: float) -> bool:
        """True if a horizontal rule at `y` passes through `x`."""
        return any(
            r.horizontal and abs(r.at - y) <= self.tol and r.start <= x <= r.end for r in self.rules
        )


def build_grid(rules: Sequence[Rule], options: LayoutOptions) -> RuleGrid | None:
    """The grid of one component of rules, or None if it is too small to be a table.

    Args:
        rules: One component (see `connected_rules`).
        options: Grid thresholds.

    Returns:
        The grid, needing at least `min_columns` columns and `min_rows` rows.
    """
    tol = options.rule_tolerance
    xs = cluster_positions((r.at for r in rules if not r.horizontal), tol)
    ys = cluster_positions((r.at for r in rules if r.horizontal), tol)
    if len(xs) - 1 < options.min_columns or len(ys) - 1 < options.min_rows:
        return None
    return RuleGrid(xs=tuple(xs), ys=tuple(ys), rules=tuple(rules), tol=tol)


def page_grids(rules: Sequence[Rule], options: LayoutOptions) -> list[RuleGrid]:
    """Every table-sized grid among a page's rules, top to bottom.

    A page with more than `max_rules` thin shapes is a drawing or outlined text (one tender
    ATC page has about 15,000), not a ruled table: it gives no grids.
    """
    if options.max_rules and len(rules) > options.max_rules:
        return []
    grids = [build_grid(c, options) for c in connected_rules(rules, options.rule_tolerance)]
    return sorted((g for g in grids if g is not None), key=lambda g: (g.ys[0], g.xs[0]))


def cell_box(grid: RuleGrid, row: int, column: int) -> BBox:
    """The cell that contains band `row` x column `column`, grown until rules stop it.

    Args:
        grid: The table grid.
        row: Row band index (between `ys[row]` and `ys[row + 1]`).
        column: Column index (between `xs[column]` and `xs[column + 1]`).

    Returns:
        The merged cell's box; several bands/columns share it when no rule divides them.
    """
    xs, ys = grid.xs, grid.ys
    y_mid = (ys[row] + ys[row + 1]) / 2
    x_mid = (xs[column] + xs[column + 1]) / 2
    left, right, top, bottom = column, column + 1, row, row + 1
    while left > 0 and not grid.vertical_rule_at(xs[left], y_mid):
        left -= 1
    while right < len(xs) - 1 and not grid.vertical_rule_at(xs[right], y_mid):
        right += 1
    while top > 0 and not grid.horizontal_rule_at(ys[top], x_mid):
        top -= 1
    while bottom < len(ys) - 1 and not grid.horizontal_rule_at(ys[bottom], x_mid):
        bottom += 1
    return BBox(x0=xs[left], y0=ys[top], x1=xs[right], y1=ys[bottom])


def grid_cells(grid: RuleGrid) -> list[list[BBox]]:
    """Cell box of every row band and column (merged cells repeat their box)."""
    return [
        [cell_box(grid, row, column) for column in range(len(grid.xs) - 1)]
        for row in range(len(grid.ys) - 1)
    ]


def band_box(grid: RuleGrid, row: int) -> BBox:
    """The full-width box of one row band."""
    return BBox(x0=grid.xs[0], y0=grid.ys[row], x1=grid.xs[-1], y1=grid.ys[row + 1])
