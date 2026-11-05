/**
 * Types mirroring the backend schemas (backend/app/schemas). Keep field names identical.
 */

/** `ErrorBody` in backend/app/schemas/errors.py. */
export interface ErrorBody {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

/** `ErrorResponse`: the envelope of every non-2xx response. */
export interface ErrorResponse {
  error: ErrorBody;
}

/** One manufacturer and what its registered catalogues cover (GET /manufacturers, Step 12). */
export interface Manufacturer {
  name: string;
  category: string;
  product_types: string[];
  documents: number;
}

/** Response of POST /tenders (Step 12). */
export interface TenderUploadResponse {
  tender_id: string;
  filename: string;
  pages: number;
  bid_number: string;
  revision: number;
  created: boolean;
}

/** Request body of POST /runs (Step 12). */
export interface StartRunRequest {
  tender_id: string;
  category: string;
  manufacturers: string[];
}

export type RunStatus = "queued" | "running" | "done" | "failed";

/** Response of POST /runs and GET /runs/{id} (Step 12). */
export interface Run {
  run_id: string;
  tender_id: string;
  category: string;
  manufacturers: string[];
  status: RunStatus;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}
