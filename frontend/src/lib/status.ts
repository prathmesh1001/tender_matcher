import type { MatchStatus } from "../types/report";

export const STATUS_LABELS: Record<MatchStatus, string> = {
  full_match: "Full match",
  partial_match: "Partial match",
  missing_capability: "Missing capability",
  unsupported: "Unsupported",
  not_applicable: "Not applicable",
};

export const STATUS_ORDER: MatchStatus[] = [
  "full_match",
  "partial_match",
  "missing_capability",
  "unsupported",
  "not_applicable",
];
