/** Mirrors backend `app/schemas/discovery.py` (GeM Marketplace discovery). */

export type DiscoveryStatus = "queued" | "running" | "done" | "failed";

export interface ListingResult {
  url: string;
  kept: boolean;
  /** "kept", or why the listing was not used. */
  reason: string;
  title: string;
  brand: string | null;
  model: string | null;
  snapshot_ref: string | null;
}

export interface DiscoveryJob {
  job_id: string;
  run_id: string;
  product_type: string;
  category: string;
  status: DiscoveryStatus;
  error: string | null;
  listings: ListingResult[];
  /** The re-matched run with the kept listings, once done. */
  result_run_id: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface UncoveredType {
  product_type: string;
  category: string;
  item_nos: string[];
  /** config/gem_marketplace.yaml lists GeM categories for this type. */
  gem_configured: boolean;
  /** Checks already asked for this type, newest first. */
  jobs: DiscoveryJob[];
}

export interface DiscoveryOverview {
  enabled: boolean;
  uncovered: UncoveredType[];
}
