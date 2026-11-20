import type { Comparison, MatchStatus, TenderReport } from "../types/report";

/** One row of the requirements table: a requirement against one manufacturer's catalogue. */
export interface RequirementRow {
  key: string;
  itemNo: string;
  itemTitle: string;
  requirement: string;
  manufacturer: string;
  catalogue: string | null;
  tied: boolean;
  score: number | null;
  result: Comparison;
}

export interface RowFilters {
  manufacturer: string;
  status: MatchStatus | "";
  needsReview: boolean;
  search: string;
  /** Also rows nothing was decided on (unsupported, not applicable). */
  showUnchecked: boolean;
}

export const NO_FILTERS: RowFilters = {
  manufacturer: "",
  status: "",
  needsReview: false,
  search: "",
  showUnchecked: false,
};

const UNCHECKED: MatchStatus[] = ["unsupported", "not_applicable"];

/** The report's results as flat rows; each tied catalogue keeps its own, and none looks picked. */
export function requirementRows(report: TenderReport): RequirementRow[] {
  const rows: RequirementRow[] = [];
  report.items.forEach((item, itemIndex) => {
    const text = new Map(item.requirements.map((r) => [r.id, r.raw_text]));
    for (const m of item.manufacturers) {
      const sets =
        m.tied_rows.length > 0
          ? m.tied_rows.map((t) => ({ catalogue: t.document, results: t.results, tied: true }))
          : [{ catalogue: m.chosen?.document ?? null, results: m.results, tied: false }];
      for (const set of sets) {
        for (const result of set.results) {
          rows.push({
            // Printed item numbers can repeat within a tender, so the item's place is in the key.
            key: `${String(itemIndex)}|${m.manufacturer}|${set.catalogue ?? ""}|${result.requirement_id}`,
            itemNo: item.item.item_no,
            itemTitle: item.item.title,
            requirement: text.get(result.requirement_id) ?? result.requirement_id,
            manufacturer: m.manufacturer,
            catalogue: set.catalogue,
            tied: set.tied,
            score: report.status_scores[result.status] ?? null,
            result,
          });
        }
      }
    }
  });
  return rows;
}

/** Rows passing every filter. */
export function filterRows(rows: RequirementRow[], filters: RowFilters): RequirementRow[] {
  const search = filters.search.trim().toLowerCase();
  return rows.filter(
    (row) =>
      (filters.manufacturer === "" || row.manufacturer === filters.manufacturer) &&
      (filters.status === "" || row.result.status === filters.status) &&
      (filters.showUnchecked || filters.status !== "" || !UNCHECKED.includes(row.result.status)) &&
      (!filters.needsReview || row.result.needs_review) &&
      (search === "" ||
        row.requirement.toLowerCase().includes(search) ||
        row.itemTitle.toLowerCase().includes(search)),
  );
}

/** A percentage as shown to a reader; none when nothing was decided. */
export function formatPct(value: number | null): string {
  return value === null ? "Not decided" : `${value.toFixed(1)}%`;
}
