import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getDiscovery, startGemCheck } from "../api/discovery";
import { listManufacturers } from "../api/manufacturers";
import { getReport } from "../api/reports";
import { getRun, listRuns, startRun } from "../api/runs";
import { uploadTender } from "../api/tenders";
import type { ApiError } from "../api/client";
import type { Manufacturer, Run } from "../types/api";
import type { DiscoveryJob, DiscoveryOverview } from "../types/discovery";
import type { TenderReport } from "../types/report";

/** How often the runs list refreshes while a run is queued or running. */
const RUNS_POLL_MS = 5000;

export const queryKeys = {
  manufacturers: ["manufacturers"] as const,
  runs: ["runs"] as const,
  run: (runId: string) => ["runs", runId] as const,
  report: (runId: string) => ["reports", runId] as const,
  discovery: (runId: string) => ["discovery", runId] as const,
};

export function useManufacturers() {
  return useQuery<Manufacturer[], ApiError>({
    queryKey: queryKeys.manufacturers,
    queryFn: listManufacturers,
    retry: false,
  });
}

export function useRuns() {
  return useQuery<Run[], ApiError>({
    queryKey: queryKeys.runs,
    queryFn: listRuns,
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.some((r) => r.status === "queued" || r.status === "running")
        ? RUNS_POLL_MS
        : false,
  });
}

export function useRun(runId: string) {
  return useQuery<Run, ApiError>({
    queryKey: queryKeys.run(runId),
    queryFn: () => getRun(runId),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.status === "queued" || query.state.data?.status === "running"
        ? RUNS_POLL_MS
        : false,
  });
}

/** A run's report, fetched once the run has finished. */
export function useReport(runId: string, enabled: boolean) {
  return useQuery<TenderReport, ApiError>({
    queryKey: queryKeys.report(runId),
    queryFn: () => getReport(runId),
    retry: false,
    enabled,
  });
}

export interface StartMatchingInput {
  file: File;
  category: string;
  manufacturers: string[];
}

/** Upload the tender, then start a run on it. */
export function useStartMatching() {
  return useMutation<Run, ApiError, StartMatchingInput>({
    mutationFn: async ({ file, category, manufacturers }) => {
      const tender = await uploadTender(file);
      return startRun({ tender_id: tender.tender_id, category, manufacturers });
    },
  });
}

/** How often the GeM panel refreshes while a check is queued or running. */
const DISCOVERY_POLL_MS = 3000;

function checking(overview: DiscoveryOverview | undefined): boolean {
  // Older backends answer without this shape; polling must never throw on it.
  const uncovered = overview?.uncovered;
  return (
    Array.isArray(uncovered) &&
    uncovered.some((u) => u.jobs.some((j) => j.status === "queued" || j.status === "running"))
  );
}

/** A finished run's uncovered product types and their GeM checks (polls while one runs). */
export function useDiscovery(runId: string, enabled: boolean) {
  return useQuery<DiscoveryOverview, ApiError>({
    queryKey: queryKeys.discovery(runId),
    queryFn: () => getDiscovery(runId),
    retry: false,
    enabled,
    refetchInterval: (query) => (checking(query.state.data) ? DISCOVERY_POLL_MS : false),
  });
}

/** Queue a GeM Marketplace check, then refresh the panel so it starts polling. */
export function useStartGemCheck(runId: string) {
  const client = useQueryClient();
  return useMutation<DiscoveryJob, ApiError, string>({
    mutationFn: (productType) => startGemCheck(runId, productType),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.discovery(runId) }),
  });
}
