import { Alert } from "../../components/ui/Alert";
import type { TenderReport } from "../../types/report";

/** What a reader must know before trusting the numbers; shown above everything else. */
export function ReportWarnings({ report }: { report: TenderReport }) {
  const { extraction_quality: quality, single_firm: firm } = report;
  const spares = report.spare_parts ?? null;
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
      {spares !== null && (
        <Alert tone="info" title="Spare parts tender">
          <p>
            “{spares.boq_title}”. Catalogues and GeM rarely list spare parts as products, so few or
            no vendors below is expected.
            {spares.oem !== null
              ? ` Spares for this equipment usually come from its maker: ${spares.oem} (original spares).`
              : " Spares usually come from the maker of the equipment named in the tender."}
          </p>
        </Alert>
      )}
    </>
  );
}
