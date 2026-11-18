import { documentFileUrl, documentPageUrl } from "../../api/reports";
import type { Evidence } from "../../types/report";

interface EvidenceCellProps {
  evidence: Evidence[];
  /** The row is undecided: these fields were cited for a reviewer and prove nothing. */
  undecided?: boolean;
}

const LINK = "font-medium text-brand-700 underline underline-offset-2 hover:text-brand-600";

function fetchedDate(iso: string | null | undefined): string {
  if (iso === null || iso === undefined) {
    return "date not recorded";
  }
  return new Date(iso).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** Badge for evidence a GeM Marketplace seller typed in (not a manufacturer document). */
export function SellerDeclaredBadge() {
  return (
    <span className="inline-block rounded-full bg-violet-50 px-2 py-0.5 text-xs font-semibold text-source-seller">
      GeM listing (seller-declared)
    </span>
  );
}

/** A GeM listing citation: badge, listing link with fetch date, and the saved snapshot. */
function ListingSource({ e }: { e: Evidence }) {
  return (
    <div className="space-y-0.5">
      <SellerDeclaredBadge />
      <div>
        <a className={LINK} href={e.source_url ?? undefined} target="_blank" rel="noreferrer">
          {e.title}
        </a>
      </div>
      <p className="text-xs text-ink-600">
        Read {fetchedDate(e.fetched_at)} ·{" "}
        <a className="underline" href={documentFileUrl(e.sha256)} target="_blank" rel="noreferrer">
          saved snapshot
        </a>
      </p>
    </div>
  );
}

/** Each cited quote with its document and a link that opens the PDF at the cited page. */
export function EvidenceCell({ evidence, undecided = false }: EvidenceCellProps) {
  if (evidence.length === 0) {
    return <span className="text-ink-400">No evidence</span>;
  }
  return (
    <ul className="space-y-2">
      {undecided && (
        <li className="text-xs font-semibold text-ink-600">
          No evidence. Cited for review only, this decides nothing:
        </li>
      )}
      {evidence.map((e) => (
        <li key={`${e.sha256}|${String(e.page_number)}|${e.quote}`}>
          {e.seller_declared === true ? (
            <ListingSource e={e} />
          ) : (
            <a
              className={LINK}
              href={documentPageUrl(e.sha256, e.page_number)}
              target="_blank"
              rel="noreferrer"
            >
              {e.document}, p.{e.page_number}
            </a>
          )}
          <blockquote className="mt-0.5 text-ink-600">“{e.quote}”</blockquote>
          {e.verified !== true && (
            <p className="text-xs text-status-partial">Quote not verified on this page</p>
          )}
        </li>
      ))}
    </ul>
  );
}
