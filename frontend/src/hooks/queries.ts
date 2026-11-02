import { useMutation, useQuery } from "@tanstack/react-query";

import { listManufacturers } from "../api/manufacturers";
import { getReport } from "../api/reports";
import { getRun, listRuns, startRun } from "../api/runs";
import { uploadTender } from "../api/tenders";
import type { ApiError } from "../api/client";
import type { Manufacturer, Run } from "../types/api";
import type { TenderReport } from "../types/report";

/** How often the runs list refreshes while a run is queued or running. */
const RUNS_POLL_MS = 5000;

export const queryKeys = {
  manufacturers: ["manufacturers"] as const,
  runs: ["runs"] as const,
  run: (runId: string) => ["runs", runId] as const,
  report: (runId: string) => ["reports", runId] as const,
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
