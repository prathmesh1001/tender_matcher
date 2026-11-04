/** Frontend settings read from Vite env vars (documented in .env.example). */

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api/v1";

/** Largest tender file the upload form accepts, in megabytes (matches HTTP_MAX_DOWNLOAD_MB). */
export const MAX_UPLOAD_MB = 100;

/** MIME type -> extension of the files the upload accepts: the bid PDF, or a ZIP of it and its attachments. */
export const ACCEPTED_TENDER_TYPES: Readonly<Record<string, string>> = {
  "application/pdf": ".pdf",
  "application/zip": ".zip",
  "application/x-zip-compressed": ".zip",
  "application/x-zip": ".zip",
};
