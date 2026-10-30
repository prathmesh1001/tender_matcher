import { PageBanner } from "../components/layout/PageBanner";
import { Card } from "../components/ui/Card";
import { ListIcon } from "../components/ui/icons";
import { RunsTable } from "../features/runs/RunsTable";

export function RunsPage() {
  return (
    <>
      <PageBanner title="Runs" subtitle="Every matching run, with its live status." />
      <Card title="Recent runs" icon={<ListIcon />}>
        <RunsTable />
      </Card>
    </>
  );
}
