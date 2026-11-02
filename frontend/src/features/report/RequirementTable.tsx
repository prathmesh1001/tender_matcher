import { useMemo, useState } from "react";

import { EvidenceCell } from "../../components/report/EvidenceCell";
import { StatusBadge } from "../../components/report/StatusBadge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { STATUS_LABELS, STATUS_ORDER } from "../../lib/status";
import { filterRows, NO_FILTERS, requirementRows, type RowFilters } from "../../lib/reportRows";
import type { MatchStatus, TenderReport } from "../../types/report";

/** Rows added each time; a large tender has thousands, too many to draw at once. */
const PAGE_ROWS = 100;
const CONTROL = "rounded-lg border border-line bg-white px-3 py-2 text-sm";

/** Every requirement against every manufacturer, with filters and evidence. */
export function RequirementTable({ report }: { report: TenderReport }) {
  const [filters, setFilters] = useState<RowFilters>(NO_FILTERS);
  const [visible, setVisible] = useState(PAGE_ROWS);
  const rows = useMemo(() => requirementRows(report), [report]);
  const filtered = useMemo(() => filterRows(rows, filters), [rows, filters]);
  const manufacturers = report.ranking.map((r) => r.manufacturer);

  function change(next: Partial<RowFilters>) {
    setFilters({ ...filters, ...next });
    setVisible(PAGE_ROWS);
  }

  return (
    <Card title="Requirements">
      <form className="mb-4 flex flex-wrap items-center gap-3" aria-label="Filter requirements">
        <label className="flex items-center gap-2 text-sm">
          Manufacturer
          <select
            className={CONTROL}
            value={filters.manufacturer}
            onChange={(e) => {
              change({ manufacturer: e.target.value });
            }}
          >
            <option value="">All</option>
            {manufacturers.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          Status
          <select
            className={CONTROL}
            value={filters.status}
            onChange={(e) => {
              change({ status: e.target.value as MatchStatus | "" });
            }}
          >
            <option value="">All</option>
            {STATUS_ORDER.map((status) => (
              <option key={status} value={status}>
                {STATUS_LABELS[status]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={filters.needsReview}
            onChange={(e) => {
              change({ needsReview: e.target.checked });
            }}
          />
          Needs review only
        </label>
        <label className="flex items-center gap-2 text-sm">
          Search
          <input
            type="search"
            className={CONTROL}
            value={filters.search}
            onChange={(e) => {
              change({ search: e.target.value });
            }}
          />
        </label>
        <span className="text-sm text-ink-600" role="status">
          {filtered.length} of {rows.length} rows
        </span>
      </form>
      {filtered.length === 0 ? (
        <p className="py-8 text-center text-ink-600">No requirements match these filters.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left align-top text-sm">
            <thead className="border-b border-line text-xs tracking-wide text-ink-600 uppercase">
              <tr>
                <th scope="col" className="py-2 pr-3">
                  Item
                </th>
                <th scope="col" className="pr-3">
                  Requirement
                </th>
                <th scope="col" className="pr-3">
                  Manufacturer
                </th>
                <th scope="col" className="pr-3">
                  Capability
                </th>
                <th scope="col" className="pr-3">
                  Status
                </th>
                <th scope="col" className="pr-3">
                  Score
                </th>
                <th scope="col">Evidence</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {filtered.slice(0, visible).map((row) => (
                <tr key={row.key} className="align-top">
                  <td className="py-3 pr-3" title={row.itemTitle}>
                    {row.itemNo}
                  </td>
                  <td className="pr-3 py-3">{row.requirement}</td>
                  <td className="pr-3 py-3">
                    {row.manufacturer}
                    {row.tied && (
                      <p className="text-xs text-status-partial">
                        Tied, none picked: {row.catalogue}
                      </p>
                    )}
                  </td>
                  <td className="pr-3 py-3">{row.result.capability ?? "—"}</td>
                  <td className="pr-3 py-3">
                    <StatusBadge status={row.result.status} />
                    {row.result.needs_review && (
                      <p className="mt-1 text-xs font-semibold text-status-partial">Needs review</p>
                    )}
                    {row.result.confidence !== null && (
                      <p className="mt-1 text-xs text-ink-600">
                        Confidence {Math.round(row.result.confidence * 100)}%
                        {row.result.confidence_factors.length > 0 &&
                          `: ${row.result.confidence_factors.join(", ")}`}
                      </p>
                    )}
                  </td>
                  <td className="pr-3 py-3">{row.score === null ? "—" : row.score}</td>
                  <td className="py-3">
                    <EvidenceCell
                      evidence={row.result.evidence}
                      undecided={row.result.status === "unsupported"}
                    />
                    {(row.result.evidence.length === 0 || row.result.status === "unsupported") && (
                      <p className="text-xs text-ink-600">{row.result.reason}</p>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {filtered.length > visible && (
        <div className="mt-4 text-center">
          <Button
            variant="secondary"
            onClick={() => {
              setVisible(visible + PAGE_ROWS);
            }}
          >
            Show more rows
          </Button>
        </div>
      )}
    </Card>
  );
}
