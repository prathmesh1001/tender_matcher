import { Link, useParams } from "react-router-dom";

import { reportExcelUrl, reportJsonUrl } from "../api/reports";
import { PageBanner } from "../components/layout/PageBanner";
import { Alert } from "../components/ui/Alert";
import { Skeleton } from "../components/ui/Skeleton";
import { DiscoveryPanel } from "../features/discovery/DiscoveryPanel";
import { BestVendors } from "../features/report/BestVendors";
import { GeneralTable } from "../features/report/GeneralTable";
import { ReportSummary } from "../features/report/ReportSummary";
import { ReportWarnings } from "../features/report/ReportWarnings";
import { RequirementTable } from "../features/report/RequirementTable";
import { TopVendors } from "../features/report/TopVendors";
import { useReport, useRun } from "../hooks/queries";

const EXPORT_LINK =
  "inline-flex items-center rounded-lg border border-line bg-white px-4 py-2.5 text-sm font-semibold hover:bg-canvas";

function Loading({ label }: { label: string }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-label={label}>
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-16 w-full" />
      ))}
    </div>
  );
}

/** One run's report; until there is one, its status, and the reason if it failed. */
export function ReportPage() {
  const { runId = "" } = useParams();
  const run = useRun(runId);
  const report = useReport(runId, run.data?.status === "done");

  if (run.isPending) {
    return <Loading label="Loading run" />;
  }
  if (run.error !== null) {
    return (
      <Alert tone="error" title="This run cannot be shown">
        {run.error.message} <Link to="/runs">Back to runs</Link>
      </Alert>
    );
  }
  if (run.data.status === "failed") {
    return (
      <Alert tone="error" title="This run failed, so it has no report">
        {run.data.error ?? "No reason was recorded."}
      </Alert>
    );
  }
  if (run.data.status !== "done") {
    return (
      <Alert title={`This run is ${run.data.status}`}>
        The report appears here when it finishes.
      </Alert>
    );
  }
  if (report.isPending) {
    return <Loading label="Loading report" />;
  }
  if (report.error !== null) {
    return (
      <Alert tone="error" title="The report cannot be shown">
        {report.error.message}
      </Alert>
    );
  }

  const data = report.data;
  const revision =
    data.tender_revision === null
      ? "revision not recorded"
      : `revision ${String(data.tender_revision)}`;
  return (
    <>
      <PageBanner
        title={`Tender ${data.bid_number}`}
        subtitle="Vendors from the database and from GeM"
      />
      <div className="space-y-6">
        <ReportWarnings report={data} />
        <div className="flex flex-wrap gap-3">
          <a className={EXPORT_LINK} href={reportExcelUrl(runId)} download>
            Export Excel
          </a>
          <a className={EXPORT_LINK} href={reportJsonUrl(runId)} download={`${data.tender}.json`}>
            Export JSON
          </a>
        </div>
        <BestVendors report={data} />
        <TopVendors report={data} />
        <details className="rounded-xl border border-line bg-white p-4">
          <summary className="cursor-pointer font-semibold">
            Details: scores, every requirement with its evidence, GeM checks
          </summary>
          <div className="mt-4 space-y-6">
            <p className="text-sm text-ink-600">
              bid.pdf {revision}. Read from {data.tender_files.join(", ")}. Decided by{" "}
              {data.decided_by}.
            </p>
            <DiscoveryPanel runId={runId} />
            <ReportSummary report={data} />
            <RequirementTable report={data} />
            <GeneralTable report={data} />
          </div>
        </details>
      </div>
    </>
  );
}
