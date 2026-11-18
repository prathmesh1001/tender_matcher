import type { DiscoveryJob, DiscoveryOverview } from "../types/discovery";
import { apiRequest } from "./client";

/** Whether discovery is on, and the run's uncovered product types (GET /discovery/runs/{id}). */
export function getDiscovery(runId: string): Promise<DiscoveryOverview> {
  return apiRequest<DiscoveryOverview>(`/discovery/runs/${runId}`);
}

/** Queue a GeM Marketplace check for one product type (POST /discovery/runs/{id}/gem). */
export function startGemCheck(runId: string, productType: string): Promise<DiscoveryJob> {
  return apiRequest<DiscoveryJob>(`/discovery/runs/${runId}/gem`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ product_type: productType }),
  });
}
