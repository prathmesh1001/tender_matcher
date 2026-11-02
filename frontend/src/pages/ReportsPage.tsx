import { PageBanner } from "../components/layout/PageBanner";
import { Card } from "../components/ui/Card";
import { ReportIcon } from "../components/ui/icons";
import { RunsTable } from "../features/runs/RunsTable";

export function ReportsPage() {
  return (
    <>
      <PageBanner
        title="Reports"
        subtitle="Compatibility %, ranking and cited evidence per manufacturer."
      />
      <Card title="Open a run's report" icon={<ReportIcon />}>
        <RunsTable />
      </Card>
    </>
  );
}
