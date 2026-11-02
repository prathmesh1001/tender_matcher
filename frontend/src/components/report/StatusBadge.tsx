import { STATUS_LABELS } from "../../lib/status";
import type { MatchStatus } from "../../types/report";

const STATUS_STYLE: Record<MatchStatus, string> = {
  full_match: "bg-green-50 text-status-full",
  partial_match: "bg-amber-50 text-status-partial",
  missing_capability: "bg-red-50 text-status-missing",
  unsupported: "bg-canvas text-status-unsupported",
  not_applicable: "bg-canvas text-status-na",
};

export function StatusBadge({ status }: { status: MatchStatus }) {
  return (
    <span
      className={`inline-block rounded-full px-2.5 py-1 text-xs font-semibold whitespace-nowrap ${STATUS_STYLE[status]}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}
