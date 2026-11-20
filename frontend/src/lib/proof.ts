import type { Comparison, Evidence, ItemReport, MatchStatus } from "../types/report";

/** One citation that proves a vendor's result on one requirement. */
export interface ProofLine {
  requirementId: string;
  requirement: string;
  status: MatchStatus;
  evidence: Evidence;
}

/** Decided statuses, strongest first; only these are shown as proof. */
const DECIDED: MatchStatus[] = ["full_match", "partial_match", "missing_capability"];

const REQUIREMENT_CHARS = 70;
const QUOTE_CHARS = 140;

/** Text cut at a word boundary with an ellipsis, so lines stay short. */
export function shorten(text: string, limit: number): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= limit) return clean;
  const cut = clean.slice(0, limit);
  const space = cut.lastIndexOf(" ");
  return `${(space > limit / 2 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

function proofOf(result: Comparison, text: Map<string, string>): ProofLine | null {
  const evidence = result.evidence[0];
  if (!DECIDED.includes(result.status) || evidence === undefined) return null;
  return {
    requirementId: result.requirement_id,
    requirement: shorten(
      text.get(result.requirement_id) ?? result.requirement_id,
      REQUIREMENT_CHARS,
    ),
    status: result.status,
    evidence: { ...evidence, quote: shorten(evidence.quote, QUOTE_CHARS) },
  };
}

/**
 * The citations behind a vendor's score on one item: decided results with evidence only,
 * matches first. Undecided (unsupported, not applicable) results prove nothing and are left out.
 */
export function vendorProof(item: ItemReport, manufacturer: string): ProofLine[] {
  const report = item.manufacturers.find((m) => m.manufacturer === manufacturer);
  if (report === undefined) return [];
  const text = new Map(item.requirements.map((r) => [r.id, r.raw_text]));
  const lines = report.results
    .map((r) => proofOf(r, text))
    .filter((line): line is ProofLine => line !== null);
  return lines.sort((a, b) => DECIDED.indexOf(a.status) - DECIDED.indexOf(b.status));
}
