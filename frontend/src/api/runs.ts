import type { Run, StartRunRequest } from "../types/api";
import { apiRequest } from "./client";

/** Start matching a stored tender against manufacturers (POST /runs). */
export function startRun(request: StartRunRequest): Promise<Run> {
  return apiRequest<Run>("/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

/** List past runs, newest first (GET /runs). */
export function listRuns(): Promise<Run[]> {
  return apiRequest<Run[]>("/runs");
}
