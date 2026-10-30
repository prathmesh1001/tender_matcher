import type { Manufacturer } from "../types/api";
import { apiRequest } from "./client";

/** Manufacturers with ingested catalogues (GET /manufacturers). */
export function listManufacturers(): Promise<Manufacturer[]> {
  return apiRequest<Manufacturer[]>("/manufacturers");
}
