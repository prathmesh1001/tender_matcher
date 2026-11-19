import type { ListingResult } from "../types/discovery";

/** "ZOLOTO · authorised reseller · seller rating 4.0+ · vendor assessed": what GeM says of the seller. */
export function sellerSummary(l: ListingResult): string {
  const parts: string[] = [];
  if (l.brand) parts.push(l.brand);
  const sold = (l.sold_as ?? "").split(" ");
  if (sold.includes("oem")) parts.push("sold by the maker (OEM)");
  else if (sold.includes("authorized")) parts.push("authorised reseller");
  else if (sold.includes("resellers")) parts.push("reseller");
  if (l.seller_rating != null) parts.push(`seller rating ${l.seller_rating.toFixed(1)}+`);
  if (l.assessed === true) parts.push("vendor assessed");
  return parts.join(" · ");
}
