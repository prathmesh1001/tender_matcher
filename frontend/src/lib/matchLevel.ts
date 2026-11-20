/** How far a vendor meets the item's checked requirements, in words. */
export function matchLevel(compatibility: number | null): string {
  if (compatibility === null) return "Not checked";
  if (compatibility >= 100) return "Full match";
  if (compatibility <= 0) return "Does not meet spec";
  return "Partial match";
}
