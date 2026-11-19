import { SellerDeclaredBadge } from "../../components/report/EvidenceCell";
import { Card } from "../../components/ui/Card";
import type { ItemReport, ItemVendor, TenderReport } from "../../types/report";

function pct(value: number | null): string {
  return value === null ? "not decided" : `${value.toFixed(1)}%`;
}

function Vendor({ vendor }: { vendor: ItemVendor }) {
  return (
    <li className="space-y-1">
      <p>
        <span className="font-semibold">
          #{vendor.rank} {vendor.manufacturer}
        </span>{" "}
        — compatibility {pct(vendor.score.compatibility_pct)}, coverage{" "}
        {pct(vendor.score.coverage_pct)} ({vendor.score.decided} of {vendor.score.total} decided)
        {vendor.low_coverage && (
          <span className="ml-2 text-xs font-semibold text-status-partial">Low coverage</span>
        )}
      </p>
      <p className="text-xs text-ink-600">
        {vendor.source === "gem_listing" ? (
          <>
            <SellerDeclaredBadge />{" "}
            {vendor.source_url !== null ? (
              <a className="underline" href={vendor.source_url} target="_blank" rel="noreferrer">
                {vendor.document}
              </a>
            ) : (
              vendor.document
            )}
          </>
        ) : (
          <>Catalogue: {vendor.document}</>
        )}
      </p>
    </li>
  );
}

function ItemRow({ item }: { item: ItemReport }) {
  const vendors = item.top_vendors ?? [];
  return (
    <li className="border-b border-line pb-4 last:border-0 last:pb-0">
      <h3 className="font-semibold">
        Item {item.item.item_no}: {item.item.title}
      </h3>
      {vendors.length === 0 ? (
        <p className="mt-1 text-sm text-ink-600">No vendor has a decided requirement yet.</p>
      ) : (
        <ol className="mt-2 space-y-2 text-sm">
          {vendors.map((v) => (
            <Vendor key={v.manufacturer} vendor={v} />
          ))}
        </ol>
      )}
    </li>
  );
}

/** The best vendors for each item (catalogue makers and GeM sellers), scored per item. */
export function TopVendors({ report }: { report: TenderReport }) {
  if (report.items.length === 0) return null;
  return (
    <Card title="Best vendors per item">
      <ul className="space-y-4">
        {report.items.map((item, index) => (
          <ItemRow key={`${item.item.item_no}|${String(index)}`} item={item} />
        ))}
      </ul>
    </Card>
  );
}
