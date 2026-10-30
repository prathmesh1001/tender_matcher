import type { TenderUploadResponse } from "../types/api";
import { apiRequest } from "./client";

/** Upload a tender PDF (POST /tenders, multipart field `file`). */
export function uploadTender(file: File): Promise<TenderUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  return apiRequest<TenderUploadResponse>("/tenders", { method: "POST", body: form });
}
