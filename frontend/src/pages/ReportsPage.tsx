import { PageBanner } from "../components/layout/PageBanner";
import { Alert } from "../components/ui/Alert";
import { Card } from "../components/ui/Card";
import { ReportIcon } from "../components/ui/icons";

export function ReportsPage() {
  return (
    <>
      <PageBanner
        title="Reports"
        subtitle="Compatibility %, ranking and cited evidence per manufacturer."
      />
      <Card title="Reports" icon={<ReportIcon />}>
        <Alert title="Reports arrive with scoring">
          The report view (summary cards, requirement table, evidence with page links) is built in
          plan step 14, once scoring and reporting (step 11) produce reports.
        </Alert>
      </Card>
    </>
  );
}
