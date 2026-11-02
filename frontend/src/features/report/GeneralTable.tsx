import { StatusBadge } from "../../components/report/StatusBadge";
import { Card } from "../../components/ui/Card";
import type { TenderReport } from "../../types/report";

/** Warranty, turnover, certificates and the rest: listed, never scored. Closed until asked for. */
export function GeneralTable({ report }: { report: TenderReport }) {
  const rows = report.general_requirements.flatMap((requirement, index) => {
    const result = report.general_results[index];
    return result === undefined
      ? []
      : [{ requirement, result, key: `${requirement.id}|${String(index)}` }];
  });
  return (
    <Card title="General requirements">
      <p className="mb-3 text-sm text-ink-600">
        Outside the item schedule; these never enter the scores.
      </p>
      {rows.length === 0 ? (
        <p className="py-4 text-center text-ink-600">The tender has no general requirements.</p>
      ) : (
        <details>
          <summary className="cursor-pointer text-sm font-medium">Show {rows.length} rows</summary>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-line text-xs tracking-wide text-ink-600 uppercase">
                <tr>
                  <th scope="col" className="py-2 pr-3">
                    Category
                  </th>
                  <th scope="col" className="pr-3">
                    Requirement
                  </th>
                  <th scope="col" className="pr-3">
                    Status
                  </th>
                  <th scope="col">Source</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map(({ requirement, result, key }) => (
                  <tr key={key} className="align-top">
                    <td className="py-2 pr-3">{requirement.category ?? "—"}</td>
                    <td className="py-2 pr-3">{requirement.raw_text}</td>
                    <td className="py-2 pr-3">
                      <StatusBadge status={result.status} />
                    </td>
                    <td className="py-2 whitespace-nowrap">
                      {requirement.document}, p.{requirement.page_number}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </Card>
  );
}
