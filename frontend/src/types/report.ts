import type { MatchLevel } from "../lib/matchLevel";

/**
 * Types mirroring backend/app/schemas/report.py and matching.py. Keep field names identical.
 */

export type MatchStatus =
  "full_match" | "partial_match" | "missing_capability" | "unsupported" | "not_applicable";

export interface Evidence {
  document: string;
  sha256: string;
  title: string;
  page_number: number;
  label: string;
  value: string;
  quote: string;
  verified: boolean | null;
  /** Where a GeM listing was read; null for a registered catalogue. */
  source_url?: string | null;
  /** When the listing was read (ISO date-time). */
  fetched_at?: string | null;
  /** Specifications typed in by a GeM Marketplace seller, not a manufacturer document. */
  seller_declared?: boolean;
}

export interface Comparison {
  requirement_id: string;
  status: MatchStatus;
  needs_review: boolean;
  reason: string;
  evidence: Evidence[];
  capability: string | null;
  /** 0-1; null when nothing was decided. */
  confidence: number | null;
  /** What lowered the confidence, e.g. "LLM decision x0.7". */
  confidence_factors: string[];
}

export interface Requirement {
  id: string;
  parent_id: string | null;
  item_no: string;
  raw_text: string;
  category: string | null;
  mandatory: boolean | null;
  document: string;
  page_number: number;
}

export interface Score {
  compatibility_pct: number | null;
  coverage_pct: number;
  decided: number;
  total: number;
  needs_review: number;
  counts: Partial<Record<MatchStatus, number>>;
}

export interface CandidateSummary {
  document: string;
}

export interface TiedDocumentRows {
  document: string;
  results: Comparison[];
}

export interface ManufacturerItemReport {
  manufacturer: string;
  chosen: CandidateSummary | null;
  tied_documents: string[];
  tied_rows: TiedDocumentRows[];
  results: Comparison[];
  score: Score;
}

/** Where a vendor's evidence for an item comes from. */
export type VendorSource = "catalogue" | "gem_listing";

/** One of the best vendors for one item, scored on that item alone. */
export interface ItemVendor {
  rank: number;
  manufacturer: string;
  score: Score;
  low_coverage: boolean;
  source: VendorSource;
  /** Title of the document the score rests on. */
  document: string;
  /** The GeM listing page, for a GeM vendor. */
  source_url: string | null;
  /** Category from the backend rules; absent in reports saved before levels existed. */
  match_level?: MatchLevel | null;
  /** Why it needs review, in words. */
  level_reason?: string;
}

/** A GeM listing in a category the buyer selected: offered, not checked against the spec. */
export interface GemRecommendation {
  brand: string;
  product: string;
  url: string;
  category: string;
  sold_as: string | null;
  /** Why it is shown for an item (the shared word), empty for the tender list. */
  reason: string;
}

export interface ItemReport {
  item: { item_no: string; title: string; quantity: string | null; unit: string | null };
  product_type: string | null;
  requirements: Requirement[];
  manufacturers: ManufacturerItemReport[];
  /** The best vendors for this item (absent in reports made before it existed). */
  top_vendors?: ItemVendor[];
  /** Related GeM listings when no GeM vendor matched the item. */
  related_gem_vendors?: GemRecommendation[];
}

export interface RankingEntry {
  rank: number;
  manufacturer: string;
  score: Score;
  low_coverage: boolean;
}

export interface ExtractionIssue {
  code: string;
  reason: string;
}

export interface ExtractionQuality {
  status: "ok" | "unreliable";
  reasons: ExtractionIssue[];
}

export interface Citation {
  document: string;
  page_number: number;
  quote: string;
}

export interface SingleFirmWarning {
  makes: string[];
  citations: Citation[];
}

export interface StatusListEntry {
  item_no: string;
  requirement_id: string;
  requirement: string;
  needs_review: boolean;
}

export interface ManufacturerStatusList {
  manufacturer: string;
  lists: Partial<Record<MatchStatus, StatusListEntry[]>>;
}

/** The tender buys spare parts; the BOQ title may name their maker. */
export interface SparePartsNotice {
  boq_title: string;
  oem: string | null;
}

export interface TenderReport {
  tender: string;
  bid_number: string;
  tender_revision: number | null;
  bid_sha256: string | null;
  extraction_quality: ExtractionQuality;
  single_firm: SingleFirmWarning | null;
  tender_files: string[];
  generated_at: string;
  decided_by: string;
  items: ItemReport[];
  ranking: RankingEntry[];
  score_explanation: string;
  status_scores: Partial<Record<MatchStatus, number>>;
  status_lists: ManufacturerStatusList[];
  general_requirements: Requirement[];
  general_results: Comparison[];
  boq_title?: string | null;
  buyer_gem_categories?: string[];
  spare_parts?: SparePartsNotice | null;
  recommended_gem_vendors?: GemRecommendation[];
}
