import { Alert } from "../../components/ui/Alert";
import type { TenderReport } from "../../types/report";

/** What a reader must know before trusting the numbers; shown above everything else. */
export function ReportWarnings({ report }: { report: TenderReport }) {
  const { extraction_quality: quality, single_firm: firm } = report;
  return (
    <>
      {quality.status === "unreliable" && (
        <Alert tone="error" title="Extraction unreliable: every result below needs review">
          <ul className="list-disc space-y-1 pl-4">
            {quality.reasons.map((issue) => (
              <li key={issue.code}>{issue.reason}</li>
            ))}
          </ul>
        </Alert>
      )}
      {firm !== null && (
        <Alert
          tone="error"
          title={`The tender says only one firm is acceptable: ${firm.makes.join(", ") || "not named"}`}
        >
          <ul className="list-disc space-y-1 pl-4">
            {firm.citations.map((c) => (
              <li key={`${c.document}|${String(c.page_number)}|${c.quote}`}>
                {c.document}, p.{c.page_number}: “{c.quote}”
              </li>
            ))}
          </ul>
        </Alert>
      )}
    </>
  );
}
