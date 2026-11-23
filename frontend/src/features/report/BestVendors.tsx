import { SellerDeclaredBadge } from "../../components/report/EvidenceCell";
import { Card } from "../../components/ui/Card";
import { LEVEL_LABEL, LEVEL_TONE } from "../../lib/matchLevel";
import type { TenderReport, TenderVendor } from "../../types/report";

const SOURCE_LABEL = {
  catalogue: "Database (catalogue)",
  gem_listing: "GeM listing",
} as const;

/** A vendor checked on at least one item (older reports also hold unchecked GeM sellers). */
type CheckedVendor = TenderVendor & {
  source: NonNullable<TenderVendor["source"]>;
  match_level: NonNullable<TenderVendor["match_level"]>;
};

function isChecked(vendor: TenderVendor): vendor is CheckedVendor {
  return vendor.source !== null && vendor.match_level !== null;
}

function itemsLine(vendor: TenderVendor): string {
  const parts: string[] = [];
  if (vendor.items_full > 0) parts.push(`full on ${String(vendor.items_full)}`);
  if (vendor.items_partial > 0) parts.push(`partial on ${String(vendor.items_partial)}`);
  if (vendor.items_listed > 0) {
    const of = `${String(vendor.items_listed)} item${vendor.items_listed === 1 ? "" : "s"}`;
    parts.push(parts.length > 0 ? `of ${of}` : `listed on ${of}`);
  }
  if (vendor.best_compatibility_pct !== null) {
    parts.push(`best ${vendor.best_compatibility_pct.toFixed(0)}%`);
  }
  return parts.join(" · ");
}

function Row({ vendor, rank }: { vendor: CheckedVendor; rank: number }) {
  const level = vendor.match_level;
  return (
    <li className="space-y-0.5">
      <p>
        <span className="font-semibold">
          {rank}. {vendor.name}
        </span>{" "}
        <span className={`text-xs font-semibold ${LEVEL_TONE[level]}`}>{LEVEL_LABEL[level]}</span>
      </p>
      <p className="text-xs text-ink-600">
        {vendor.source === "gem_listing" && <SellerDeclaredBadge />} {SOURCE_LABEL[vendor.source]}
        {itemsLine(vendor) !== "" && <> · {itemsLine(vendor)}</>}
        {vendor.source_url !== null && (
          <>
            {" · "}
            <a className="underline" href={vendor.source_url} target="_blank" rel="noreferrer">
              GeM page
            </a>
          </>
        )}
      </p>
      {vendor.level_reason !== "" && <p className="text-xs text-ink-600">{vendor.level_reason}</p>}
    </li>
  );
}

/** The tender's best vendors over all items: full and partial first, at least a few. */
export function BestVendors({ report }: { report: TenderReport }) {
  const vendors = (report.best_vendors ?? []).filter(isChecked);
  if (vendors.length === 0) return null;
  const matched = vendors.filter(
    (v) => v.match_level === "full_match" || v.match_level === "partial_match",
  ).length;
  return (
    <Card title="Best vendors for this tender">
      <p className="mb-3 text-sm text-ink-600">
        {matched > 0
          ? `${String(matched)} vendor${matched === 1 ? "" : "s"} fully or partly match an item.`
          : "No vendor fully or partly matches an item yet."}{" "}
        Each shows its own level; the next best fill the list and are never shown as a match.
      </p>
      <ol className="space-y-2 text-sm">
        {vendors.map((v, index) => (
          <Row key={`${v.source}|${v.name}`} vendor={v} rank={index + 1} />
        ))}
      </ol>
    </Card>
  );
}
