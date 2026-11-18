import { API_BASE_URL } from "../lib/config";
import type { TenderReport } from "../types/report";
import { apiRequest } from "./client";

/** The finished report of a run (GET /reports/{id}). */
export function getReport(runId: string): Promise<TenderReport> {
  return apiRequest<TenderReport>(`/reports/${runId}`);
}

/** Download address of the report as JSON. */
export function reportJsonUrl(runId: string): string {
  return `${API_BASE_URL}/reports/${runId}`;
}

/** Download address of the report as an Excel workbook. */
export function reportExcelUrl(runId: string): string {
  return `${API_BASE_URL}/reports/${runId}/excel`;
}

/** Address that opens a stored document at a page. */
/** A stored document as served (a GeM listing: its saved, sandboxed snapshot). */
export function documentFileUrl(sha256: string): string {
  return `${API_BASE_URL}/documents/${sha256}/file`;
}

export function documentPageUrl(sha256: string, page: number): string {
  return `${API_BASE_URL}/documents/${sha256}/file#page=${String(page)}`;
}
