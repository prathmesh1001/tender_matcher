import { Link } from "react-router-dom";

import { Alert } from "../../components/ui/Alert";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { useDiscovery, useStartGemCheck } from "../../hooks/queries";
import { sellerSummary } from "../../lib/seller";
import type { DiscoveryJob, UncoveredType } from "../../types/discovery";

function typeName(productType: string): string {
  return productType.replaceAll("_", " ");
}

function JobResult({ job }: { job: DiscoveryJob }) {
  if (job.status === "queued" || job.status === "running") {
    return (
      <p className="text-sm text-ink-600" aria-live="polite">
        Checking GeM Marketplace… ({job.status})
      </p>
    );
  }
  if (job.status === "failed") {
    return (
      <Alert tone="error" title="The GeM check failed">
        {job.error ?? "No reason was recorded."}
      </Alert>
    );
  }
  const kept = job.listings.filter((l) => l.kept).length;
  return (
    <div className="space-y-2 text-sm">
      <p>
        {kept} of {job.listings.length} listings kept.{" "}
        {job.result_run_id !== null ? (
          <Link
            className="font-semibold text-brand-700 underline underline-offset-2"
            to={`/reports/${job.result_run_id}`}
          >
            Open the re-matched report
          </Link>
        ) : (
          (job.error ?? "Nothing to re-match.")
        )}
      </p>
      {job.listings.length > 0 && (
        <ul className="space-y-1 text-ink-600">
          {job.listings.map((l) => (
            <li key={l.url}>
              <a className="underline" href={l.url} target="_blank" rel="noreferrer">
                {l.title || l.url}
              </a>{" "}
              — {l.kept ? "kept" : l.reason}
              {sellerSummary(l) && (
                <span className="block text-xs text-ink-600">{sellerSummary(l)}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function UncoveredRow({ runId, uncovered }: { runId: string; uncovered: UncoveredType }) {
  const start = useStartGemCheck(runId);
  const latest = uncovered.jobs.at(0);
  const busy = start.isPending || latest?.status === "queued" || latest?.status === "running";
  const name = typeName(uncovered.product_type);
  return (
    <li className="space-y-2 border-b border-line pb-4 last:border-0 last:pb-0">
      <p className="text-sm">
        No manufacturer document covers <strong>{name}</strong> (items{" "}
        {uncovered.item_nos.join(", ")}).
      </p>
      {uncovered.by_title === true && (
        <p className="text-xs text-ink-600">
          No product type in the ontology names these items: GeM is searched by the words of their
          title.
        </p>
      )}
      {uncovered.gem_configured ? (
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() => {
            start.mutate(uncovered.product_type);
          }}
        >
          Check GeM Marketplace for {name}?
        </Button>
      ) : (
        <p className="text-xs text-ink-400">
          No GeM Marketplace category is configured for {name}.
        </p>
      )}
      {start.error !== null && (
        <Alert tone="error" title="The check could not start">
          {start.error.message}
        </Alert>
      )}
      {latest !== undefined && <JobResult job={latest} />}
    </li>
  );
}

/**
 * Product types of a finished run that no manufacturer document covers, each with a button
 * that reads GeM Marketplace listings (seller-declared) and re-matches those items by rules.
 * Shows nothing when discovery is off.
 */
export function DiscoveryPanel({ runId }: { runId: string }) {
  const discovery = useDiscovery(runId, true);
  if (discovery.isPending || discovery.error !== null) {
    return null; // an optional extra: never block the report
  }
  const { enabled, uncovered } = discovery.data;
  // Older backends answer without this shape; the report must never break on it.
  if (!enabled || !Array.isArray(uncovered) || uncovered.length === 0) {
    return null;
  }
  return (
    <Card title="Products without a manufacturer document">
      <p className="mb-4 text-sm text-ink-600">
        GeM Marketplace specifications are typed in by sellers. Results from them are marked “GeM
        listing (seller-declared)” and always need review.
      </p>
      <ul className="space-y-4">
        {uncovered.map((u) => (
          <UncoveredRow key={u.product_type} runId={runId} uncovered={u} />
        ))}
      </ul>
    </Card>
  );
}
