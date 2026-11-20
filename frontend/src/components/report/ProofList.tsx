import { documentPageUrl } from "../../api/reports";
import type { ProofLine } from "../../lib/proof";
import { StatusBadge } from "./StatusBadge";

/** Citations shown before "Show more". */
const SHOWN = 3;

const LINK = "text-brand-700 underline underline-offset-2 hover:text-brand-600";

function Source({ line }: { line: ProofLine }) {
  const e = line.evidence;
  if (e.seller_declared === true && e.source_url) {
    return (
      <a className={LINK} href={e.source_url} target="_blank" rel="noreferrer">
        GeM listing
      </a>
    );
  }
  return (
    <a
      className={LINK}
      href={documentPageUrl(e.sha256, e.page_number)}
      target="_blank"
      rel="noreferrer"
    >
      {e.document}, p.{e.page_number}
    </a>
  );
}

function Proof({ line }: { line: ProofLine }) {
  return (
    <li className="space-y-0.5">
      <p className="flex flex-wrap items-center gap-2">
        <StatusBadge status={line.status} />
        <span className="text-ink-800">{line.requirement}</span>
      </p>
      <p className="text-xs text-ink-600">
        “{line.evidence.quote}” · <Source line={line} />
      </p>
    </li>
  );
}

/** A vendor's proof on one item: the cited quote and source of each decided requirement. */
export function ProofList({ lines }: { lines: ProofLine[] }) {
  if (lines.length === 0) {
    return <p className="text-xs text-ink-600">No citation: nothing on this item was decided.</p>;
  }
  const first = lines.slice(0, SHOWN);
  const rest = lines.slice(SHOWN);
  return (
    <div className="text-sm" aria-label="Proof">
      <ul className="space-y-2">
        {first.map((line) => (
          <Proof key={line.requirementId} line={line} />
        ))}
      </ul>
      {rest.length > 0 && (
        <details className="mt-1">
          <summary className="cursor-pointer text-xs font-semibold text-brand-700">
            Show {rest.length} more
          </summary>
          <ul className="mt-2 space-y-2">
            {rest.map((line) => (
              <Proof key={line.requirementId} line={line} />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
