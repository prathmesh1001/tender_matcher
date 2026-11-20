import { SellerDeclaredBadge } from "../../components/report/EvidenceCell";
import { Card } from "../../components/ui/Card";
import { matchLevel } from "../../lib/matchLevel";
import type { GemRecommendation, ItemReport, ItemVendor, TenderReport } from "../../types/report";

function pct(value: number | null): string {
  return value === null ? "not decided" : `${value.toFixed(0)}%`;
}

function Vendor({ vendor }: { vendor: ItemVendor }) {
  const gem = vendor.source === "gem_listing";
  return (
    <li className="space-y-0.5">
      <p>
        <span className="font-semibold">
          {vendor.rank}. {vendor.manufacturer}
        </span>{" "}
        <span className="text-ink-600">
          {pct(vendor.score.compatibility_pct)} match, {vendor.score.decided} of{" "}
          {vendor.score.total} checked
        </span>
        <span className="ml-2 text-xs font-semibold">
          {matchLevel(vendor.score.compatibility_pct)}
        </span>
        {vendor.low_coverage && (
          <span className="ml-2 text-xs font-semibold text-status-partial">Few checked</span>
        )}
      </p>
      <p className="text-xs text-ink-600">
        {gem && <SellerDeclaredBadge />}{" "}
        {gem && vendor.source_url !== null ? (
          <a className="underline" href={vendor.source_url} target="_blank" rel="noreferrer">
            {vendor.document}
          </a>
        ) : (
          vendor.document
        )}
      </p>
    </li>
  );
}

function VendorList({
  title,
  vendors,
  empty,
}: {
  title: string;
  vendors: ItemVendor[];
  empty: string;
}) {
  return (
    <section aria-label={title}>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-600">{title}</h4>
      {vendors.length === 0 ? (
        <p className="mt-1 text-sm text-ink-600">{empty}</p>
      ) : (
        <ol className="mt-1 space-y-2 text-sm">
          {vendors.map((v) => (
            <Vendor key={v.manufacturer} vendor={v} />
          ))}
        </ol>
      )}
    </section>
  );
}

function Recommendation({ rec }: { rec: GemRecommendation }) {
  return (
    <li className="space-y-0.5">
      <p>
        <span className="font-semibold">{rec.brand}</span>{" "}
        <span className="text-xs text-ink-600">{rec.sold_as ?? ""}</span>
      </p>
      <p className="text-xs text-ink-600">
        <a className="underline" href={rec.url} target="_blank" rel="noreferrer">
          {rec.product}
        </a>
        {rec.reason !== "" && <> · {rec.reason}</>}
      </p>
    </li>
  );
}

function RelatedOnGem({ recs }: { recs: GemRecommendation[] }) {
  if (recs.length === 0) return null;
  return (
    <section aria-label="Related on GeM" className="mt-2">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-600">
        Related on GeM (not checked against the spec)
      </h4>
      <ul className="mt-1 space-y-2 text-sm">
        {recs.map((r) => (
          <Recommendation key={r.url} rec={r} />
        ))}
      </ul>
    </section>
  );
}

/** Listings of the GeM categories the buyer selected: the tender's recommended GeM vendors. */
export function RecommendedGemVendors({ report }: { report: TenderReport }) {
  const recs = report.recommended_gem_vendors ?? [];
  if (recs.length === 0) return null;
  const categories = [...new Set(recs.map((r) => r.category))];
  return (
    <Card title="Recommended GeM vendors for this tender">
      <p className="mb-3 text-sm text-ink-600">
        Sellers in the GeM categories the buyer selected for this bid. Offered on GeM; not checked
        against the specification.
      </p>
      {categories.map((category) => (
        <section key={category} aria-label={category} className="mb-3 last:mb-0">
          <h3 className="text-sm font-semibold">{category}</h3>
          <ul className="mt-1 space-y-2 text-sm">
            {recs
              .filter((r) => r.category === category)
              .map((r) => (
                <Recommendation key={r.url} rec={r} />
              ))}
          </ul>
        </section>
      ))}
    </Card>
  );
}

function ItemRow({ item }: { item: ItemReport }) {
  const vendors = item.top_vendors ?? [];
  return (
    <li className="border-b border-line pb-4 last:border-0 last:pb-0">
      <h3 className="font-semibold">
        Item {item.item.item_no}: {item.item.title}
      </h3>
      <div className="mt-2 grid gap-4 md:grid-cols-2">
        <VendorList
          title="From database (catalogues)"
          vendors={vendors.filter((v) => v.source === "catalogue")}
          empty="No catalogue vendor."
        />
        <VendorList
          title="From GeM"
          vendors={vendors.filter((v) => v.source === "gem_listing")}
          empty="No GeM vendor."
        />
      </div>
      <RelatedOnGem recs={item.related_gem_vendors ?? []} />
    </li>
  );
}

/** Per item: the best vendors from the catalogues (database) and from GeM, side by side. */
export function TopVendors({ report }: { report: TenderReport }) {
  if (report.items.length === 0) return null;
  return (
    <Card title="Vendors per item">
      <ul className="space-y-4">
        {report.items.map((item, index) => (
          <ItemRow key={`${item.item.item_no}|${String(index)}`} item={item} />
        ))}
      </ul>
    </Card>
  );
}
