/** A vendor's match level on one item, set by the backend rules (`scorer.vendor_level`). */
export type MatchLevel =
  "full_match" | "partial_match" | "needs_review" | "does_not_meet" | "not_checked";

/** Display order of the groups, best first. */
export const LEVEL_ORDER: readonly MatchLevel[] = [
  "full_match",
  "partial_match",
  "needs_review",
  "does_not_meet",
  "not_checked",
];

export const LEVEL_LABEL: Record<MatchLevel, string> = {
  full_match: "Full match",
  partial_match: "Partial match",
  needs_review: "Needs review",
  does_not_meet: "Does not meet spec",
  not_checked: "Not checked",
};

export const LEVEL_TONE: Record<MatchLevel, string> = {
  full_match: "text-status-full",
  partial_match: "text-status-partial",
  needs_review: "text-status-review",
  does_not_meet: "text-status-missing",
  not_checked: "text-status-unsupported",
};

/** The level of a report saved before levels existed, from its compatibility % alone. */
export function levelFromPct(compatibility: number | null): MatchLevel {
  if (compatibility === null) return "not_checked";
  if (compatibility >= 100) return "full_match";
  if (compatibility <= 0) return "does_not_meet";
  return "partial_match";
}
