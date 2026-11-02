import { Link } from "react-router-dom";

import { Alert } from "../../components/ui/Alert";
import { Button } from "../../components/ui/Button";
import { Skeleton } from "../../components/ui/Skeleton";
import { UploadIcon } from "../../components/ui/icons";
import { useRuns } from "../../hooks/queries";
import type { RunStatus } from "../../types/api";

const STATUS_STYLE: Record<RunStatus, string> = {
  queued: "bg-canvas text-ink-600",
  running: "bg-brand-50 text-brand-700",
  done: "bg-green-50 text-status-full",
  failed: "bg-red-50 text-status-missing",
};

/** Past runs with status; refreshes itself while a run is queued or running. */
export function RunsTable() {
  const { data, isPending, error } = useRuns();

  if (isPending) {
    return (
      <div className="space-y-3" aria-busy="true" aria-label="Loading runs">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-5 w-full" />
        ))}
      </div>
    );
  }
  if (error !== null) {
    return (
      <Alert tone="error" title="Runs are not available">
        {error.isUnavailable ? "Cannot reach the backend." : error.message}
      </Alert>
    );
  }
  if (data.length === 0) {
    return (
      <div className="flex flex-col items-center gap-3 py-10 text-center">
        <p className="text-ink-600">No runs yet.</p>
        <Link to="/upload">
          <Button icon={<UploadIcon />}>Upload a tender</Button>
        </Link>
      </div>
    );
  }
  return (
    <table className="w-full text-left text-sm">
      <thead className="border-b border-line text-xs tracking-wide text-ink-600 uppercase">
        <tr>
          <th scope="col" className="py-3">
            Run
          </th>
          <th scope="col">Tender</th>
          <th scope="col">Started</th>
          <th scope="col">Status</th>
          <th scope="col">Report</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-line">
        {data.map((run) => (
          <tr key={run.run_id}>
            <td className="py-3 font-mono text-xs">{run.run_id.slice(0, 8)}</td>
            <td className="font-mono text-xs">{run.tender_id.slice(0, 8)}</td>
            <td>{new Date(run.created_at).toLocaleString()}</td>
            <td>
              <span
                className={`rounded-full px-2.5 py-1 text-xs font-semibold ${STATUS_STYLE[run.status]}`}
              >
                {run.status}
              </span>
            </td>
            <td>
              {run.status === "done" && (
                <Link
                  className="font-medium text-brand-700 underline underline-offset-2"
                  to={`/reports/${run.run_id}`}
                >
                  Open report
                </Link>
              )}
              {run.status === "failed" && (
                <span className="text-xs text-status-missing">{run.error}</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
