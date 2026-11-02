import { StatusBadge } from "../../components/report/StatusBadge";
import { ScoreBar } from "../../components/report/ScoreBar";
import { Card } from "../../components/ui/Card";
import { STATUS_LABELS, STATUS_ORDER } from "../../lib/status";
import type { TenderReport } from "../../types/report";

/** Ranking, compatibility and coverage per manufacturer, status counts and how % is worked out. */
export function ReportSummary({ report }: { report: TenderReport }) {
  return (
    <>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {report.ranking.map((entry) => (
          <Card key={entry.manufacturer}>
            <div className="flex items-baseline justify-between gap-2">
              <h3 className="font-semibold">
                #{entry.rank} {entry.manufacturer}
              </h3>
              {entry.low_coverage && (
                <span className="text-xs font-semibold text-status-partial">Low coverage</span>
              )}
            </div>
            <div className="mt-3 space-y-3">
              <ScoreBar label="Compatibility" value={entry.score.compatibility_pct} />
              <ScoreBar label="Coverage" value={entry.score.coverage_pct} />
            </div>
            <p className="mt-3 text-xs text-ink-600">
              {entry.score.decided} of {entry.score.total} decided, {entry.score.needs_review} for
              review
            </p>
          </Card>
        ))}
      </div>
      <Card title="Status counts">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-line text-xs tracking-wide text-ink-600 uppercase">
              <tr>
                <th scope="col" className="py-2">
                  Manufacturer
                </th>
                {STATUS_ORDER.map((status) => (
                  <th key={status} scope="col" className="px-2">
                    {STATUS_LABELS[status]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {report.ranking.map((entry) => (
                <tr key={entry.manufacturer}>
                  <th scope="row" className="py-2 font-medium">
                    {entry.manufacturer}
                  </th>
                  {STATUS_ORDER.map((status) => (
                    <td key={status} className="px-2">
                      {entry.score.counts[status] ?? 0}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <details className="mt-4 text-sm">
          <summary className="cursor-pointer font-medium">Lists for each status</summary>
          {report.status_lists.map((block) => (
            <div key={block.manufacturer} className="mt-3">
              <h4 className="font-semibold">{block.manufacturer}</h4>
              {STATUS_ORDER.map((status) => {
                const entries = block.lists[status] ?? [];
                return entries.length === 0 ? null : (
                  <details key={status} className="mt-1">
                    <summary className="cursor-pointer">
                      <StatusBadge status={status} /> {entries.length}
                    </summary>
                    <ul className="mt-1 list-disc pl-5 text-ink-600">
                      {entries.map((entry, index) => (
                        <li key={`${entry.requirement_id}|${String(index)}`}>
                          Item {entry.item_no}: {entry.requirement}
                        </li>
                      ))}
                    </ul>
                  </details>
                );
              })}
            </div>
          ))}
        </details>
      </Card>
      <Card title="How the percentages are calculated">
        <p className="text-sm text-ink-600">{report.score_explanation}</p>
      </Card>
    </>
  );
}
