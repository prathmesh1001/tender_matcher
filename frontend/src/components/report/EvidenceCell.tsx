import { documentPageUrl } from "../../api/reports";
import type { Evidence } from "../../types/report";

interface EvidenceCellProps {
  evidence: Evidence[];
  /** The row is undecided: these fields were cited for a reviewer and prove nothing. */
  undecided?: boolean;
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
          <a
            className="font-medium text-brand-700 underline underline-offset-2 hover:text-brand-600"
            href={documentPageUrl(e.sha256, e.page_number)}
            target="_blank"
            rel="noreferrer"
          >
            {e.document}, p.{e.page_number}
          </a>
          <blockquote className="mt-0.5 text-ink-600">“{e.quote}”</blockquote>
          {e.verified !== true && (
            <p className="text-xs text-status-partial">Quote not verified on this page</p>
          )}
        </li>
      ))}
    </ul>
  );
}
