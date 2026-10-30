import { API_BASE_URL } from "../lib/config";
import type { ErrorResponse } from "../types/api";

/** A failed API call, with the backend's error code when it sent one. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  /** True when the endpoint does not exist yet or the backend is not running. */
  get isUnavailable(): boolean {
    return this.status === 0 || this.status === 404 || this.status === 502 || this.status === 503;
  }
}

function isErrorResponse(value: unknown): value is ErrorResponse {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }
  const error: unknown = value.error;
  return typeof error === "object" && error !== null && "message" in error && "code" in error;
}

async function toApiError(response: Response): Promise<ApiError> {
  const body: unknown = await response.json().catch(() => null);
  if (isErrorResponse(body)) {
    return new ApiError(response.status, body.error.code, body.error.message);
  }
  return new ApiError(response.status, "http_error", `Request failed (${String(response.status)})`);
}

/**
 * Call the backend and parse JSON. Network failures become `ApiError` with status 0.
 *
 * @param path - Path below the API prefix, e.g. "/tenders".
 * @param init - Fetch options (method, body, ...).
 */
export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, init);
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the backend.");
  }
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as T;
}
