import { ACCEPTED_TENDER_TYPES, MAX_UPLOAD_MB } from "./config";

const BYTES_PER_MB = 1024 * 1024;

/** "2.4 MB" / "180 KB". */
export function formatBytes(bytes: number): string {
  if (bytes >= BYTES_PER_MB) {
    return `${(bytes / BYTES_PER_MB).toFixed(1)} MB`;
  }
  return `${String(Math.max(1, Math.round(bytes / 1024)))} KB`;
}

/**
 * Why a file cannot be uploaded as a tender, or null if it can.
 *
 * Checks the extension as well as the MIME type: Windows often reports "" for PDFs and ZIPs.
 */
export function validateTenderFile(file: File): string | null {
  const extensions = [...new Set(Object.values(ACCEPTED_TENDER_TYPES))];
  const name = file.name.toLowerCase();
  const typeOk = file.type === "" || file.type in ACCEPTED_TENDER_TYPES;
  if (!typeOk || !extensions.some((ext) => name.endsWith(ext))) {
    return `Only ${extensions.join(", ")} files can be uploaded.`;
  }
  if (file.size === 0) {
    return "The file is empty.";
  }
  if (file.size > MAX_UPLOAD_MB * BYTES_PER_MB) {
    return `The file is larger than ${String(MAX_UPLOAD_MB)} MB.`;
  }
  return null;
}
