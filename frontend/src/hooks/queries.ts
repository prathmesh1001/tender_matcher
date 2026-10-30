import { useMutation, useQuery } from "@tanstack/react-query";

import { listManufacturers } from "../api/manufacturers";
import { listRuns, startRun } from "../api/runs";
import { uploadTender } from "../api/tenders";
import type { ApiError } from "../api/client";
import type { Manufacturer, Run } from "../types/api";

/** How often the runs list refreshes while a run is queued or running. */
const RUNS_POLL_MS = 5000;

export const queryKeys = {
  manufacturers: ["manufacturers"] as const,
  runs: ["runs"] as const,
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
