import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { AppRoutes } from "../../App";
import { filterRows, NO_FILTERS, requirementRows } from "../../lib/reportRows";
import { json, renderWithProviders, stubFetch } from "../../test/render";
import type { Run } from "../../types/api";
import type { Comparison, TenderReport } from "../../types/report";

// Synthetic test data (not real catalogues or evidence).
const RUN: Run = {
  run_id: "run-1",
  tender_id: "tender-1",
  category: "valves_pumps",
  manufacturers: ["Acme", "Beta"],
  status: "done",
  error: null,
  created_at: "2026-01-01T00:00:00Z",
  started_at: "2026-01-01T00:00:01Z",
  finished_at: "2026-01-01T00:01:00Z",
};

const SCORE = {
  compatibility_pct: 75,
  coverage_pct: 50,
  decided: 2,
  total: 4,
  needs_review: 1,
  counts: { full_match: 1, partial_match: 1, unsupported: 2 },
};

function result(id: string, status: Comparison["status"], cited: boolean): Comparison {
  return {
    requirement_id: id,
    status,
    needs_review: status !== "full_match",
    reason: cited ? "" : "no catalogue field names this",
    capability: cited ? "Body: WCB" : null,
    confidence: status === "full_match" ? 0.7 : null,
    confidence_factors: status === "full_match" ? ["LLM decision x0.7"] : [],
    evidence: cited
      ? [
          {
            document: "alpha.pdf",
            sha256: "abc",
            title: "Alpha",
            page_number: 7,
            label: "Body",
            value: "WCB",
            quote: "Body: WCB",
            verified: true,
          },
        ]
      : [],
  };
}

function requirement(id: string, text: string) {
  return {
    id,
    parent_id: null,
    item_no: "10",
    raw_text: text,
    category: "warranty",
    mandatory: true,
    document: "bid.pdf",
    page_number: 2,
  };
}

const REPORT: TenderReport = {
  tender: "GEM_2099_B_1",
  bid_number: "GEM/2099/B/1",
  tender_revision: 2,
  bid_sha256: "def",
  extraction_quality: { status: "ok", reasons: [] },
  single_firm: null,
  tender_files: ["bid.pdf"],
  generated_at: "2026-01-01T00:01:00Z",
  decided_by: "rules",
  ranking: [
    { rank: 1, manufacturer: "Acme", score: SCORE, low_coverage: false },
    {
      rank: 2,
      manufacturer: "Beta",
      score: { ...SCORE, compatibility_pct: null, decided: 0 },
      low_coverage: true,
    },
  ],
  score_explanation: "Compatibility is the total score over decided requirements.",
  status_scores: { full_match: 1, partial_match: 0.5, missing_capability: 0 },
  status_lists: [
    {
      manufacturer: "Acme",
      lists: {
        full_match: [
          { item_no: "10", requirement_id: "10.1", requirement: "BODY : WCB", needs_review: false },
        ],
      },
    },
  ],
  items: [
    {
      item: { item_no: "10", title: "BALL VALVE", quantity: "2", unit: "Nos" },
      product_type: "ball_valve",
      requirements: [requirement("10.1", "BODY : WCB"), requirement("10.2", "PN 40")],
      manufacturers: [
        {
          manufacturer: "Acme",
          chosen: { document: "alpha.pdf" },
          tied_documents: [],
          tied_rows: [],
          results: [result("10.1", "full_match", true), result("10.2", "unsupported", false)],
          score: SCORE,
        },
        {
          manufacturer: "Beta",
          chosen: null,
          tied_documents: ["b1.pdf", "b2.pdf"],
          tied_rows: [
            { document: "b1.pdf", results: [result("10.1", "partial_match", false)] },
            { document: "b2.pdf", results: [result("10.1", "unsupported", false)] },
          ],
          results: [result("10.1", "partial_match", false)],
          score: SCORE,
        },
      ],
    },
  ],
  general_requirements: [requirement("g.1", "Warranty 12 months")],
  general_results: [result("g.1", "unsupported", false)],
};

function serve(report: TenderReport = REPORT, run: Run = RUN) {
  stubFetch((url) => {
    if (url.includes("/discovery/")) {
      return json({ enabled: false, uncovered: [] });
    }
    return url.includes("/reports/") ? json(report) : json(run);
  });
}

async function openReport() {
  renderWithProviders(<AppRoutes />, "/reports/run-1");
  return screen.findByRole("heading", { name: "Requirements" });
}

describe("reportRows", () => {
  it("keeps every tied catalogue's rows and marks them tied", () => {
    const rows = requirementRows(REPORT).filter((r) => r.manufacturer === "Beta");
    expect(rows.map((r) => [r.catalogue, r.tied])).toEqual([
      ["b1.pdf", true],
      ["b2.pdf", true],
    ]);
  });

  it("gives undecided rows no score rather than zero", () => {
    const rows = requirementRows(REPORT);
    expect(rows.find((r) => r.result.status === "full_match")?.score).toBe(1);
    expect(rows.find((r) => r.result.status === "unsupported")?.score).toBeNull();
  });

  it("keeps row keys unique when a tender repeats its item numbers", () => {
    const report = { ...REPORT, items: [...REPORT.items, ...REPORT.items] };
    const keys = requirementRows(report).map((r) => r.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("combines filters", () => {
    const rows = requirementRows(REPORT);
    expect(filterRows(rows, NO_FILTERS)).toHaveLength(rows.length);
    expect(
      filterRows(rows, { ...NO_FILTERS, manufacturer: "Acme", needsReview: true }).map(
        (r) => r.result.requirement_id,
      ),
    ).toEqual(["10.2"]);
    expect(filterRows(rows, { ...NO_FILTERS, status: "full_match" })).toHaveLength(1);
    expect(filterRows(rows, { ...NO_FILTERS, search: "pn 40" })).toHaveLength(1);
  });
});

describe("ReportPage", () => {
  it("shows a decided row's confidence and what lowered it", async () => {
    serve();
    await openReport();
    expect(screen.getByText("Confidence 70%: LLM decision x0.7")).toBeInTheDocument();
    expect(screen.queryAllByText(/^Confidence/)).toHaveLength(1);
  });

  it("lists each item's best vendors and marks a GeM seller", async () => {
    const [item] = REPORT.items;
    if (item === undefined) throw new Error("fixture has an item");
    serve({
      ...REPORT,
      items: [
        {
          ...item,
          top_vendors: [
            {
              rank: 1,
              manufacturer: "Acme",
              score: SCORE,
              low_coverage: false,
              source: "catalogue",
              document: "alpha.pdf",
              source_url: null,
            },
            {
              rank: 2,
              manufacturer: "ZOLOTO",
              score: SCORE,
              low_coverage: true,
              source: "gem_listing",
              document: "ZOLOTO Butterfly Valves 100",
              source_url: "https://mkp.gem.gov.in/x/p-1-1-cat.html",
            },
          ],
        },
      ],
    });
    await openReport();
    const card = screen.getByRole("heading", { name: "Vendors per item" }).closest("section");
    if (card === null) throw new Error("card section");
    expect(within(card).getByText("Item 10: BALL VALVE")).toBeInTheDocument();
    const database = within(screen.getByRole("region", { name: "From database (catalogues)" }));
    expect(database.getByText("1. Acme")).toBeInTheDocument();
    expect(database.getByText("alpha.pdf")).toBeInTheDocument();
    const gem = within(screen.getByRole("region", { name: "From GeM" }));
    expect(gem.getByRole("link", { name: "ZOLOTO Butterfly Valves 100" })).toHaveAttribute(
      "href",
      "https://mkp.gem.gov.in/x/p-1-1-cat.html",
    );
    expect(gem.getByText("GeM listing (seller-declared)")).toBeInTheDocument();
    expect(gem.getByText("Few checked")).toBeInTheDocument();
  });

  it("says when an item has no vendor from a source", async () => {
    serve();
    await openReport();
    expect(screen.getByText("No catalogue vendor.")).toBeInTheDocument();
    expect(screen.getByText("No GeM vendor.")).toBeInTheDocument();
  });

  it("shows the revision, ranking, formula and export links", async () => {
    serve();
    await openReport();
    expect(screen.getByRole("heading", { name: "Tender GEM/2099/B/1" })).toBeInTheDocument();
    expect(screen.getByText(/bid\.pdf revision 2/)).toBeInTheDocument();
    expect(screen.getByText("#1 Acme")).toBeInTheDocument();
    expect(screen.getByText("Low coverage")).toBeInTheDocument();
    expect(screen.getByText(/total score over decided requirements/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Export Excel" })).toHaveAttribute(
      "href",
      "/api/v1/reports/run-1/excel",
    );
    expect(screen.getByRole("link", { name: "Export JSON" })).toHaveAttribute(
      "href",
      "/api/v1/reports/run-1",
    );
  });

  it("links evidence to the PDF at the cited page, in a new tab", async () => {
    serve();
    await openReport();
    const link = screen.getByRole("link", { name: "alpha.pdf, p.7" });
    expect(link).toHaveAttribute("href", "/api/v1/documents/abc/file#page=7");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("filters the table to rows that need review", async () => {
    serve();
    const user = userEvent.setup();
    await openReport();
    expect(screen.getByRole("status", { name: "" })).toHaveTextContent("4 of 4 rows");
    await user.click(screen.getByLabelText("Needs review only"));
    expect(screen.getByText("3 of 4 rows")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Manufacturer"), "Acme");
    expect(screen.getByText("1 of 4 rows")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Search"), "nothing like this");
    expect(screen.getByText("No requirements match these filters.")).toBeInTheDocument();
  });

  it("shows warnings before the results", async () => {
    serve({
      ...REPORT,
      extraction_quality: {
        status: "unreliable",
        reasons: [{ code: "oversized_item", reason: "item 1 has 566 requirements" }],
      },
      single_firm: {
        makes: ["Acme"],
        citations: [{ document: "bid.pdf", page_number: 3, quote: "only Acme" }],
      },
    });
    await openReport();
    const [extraction, firm] = screen.getAllByRole("alert");
    if (extraction === undefined || firm === undefined) {
      throw new Error("Both warnings must show");
    }
    expect(within(extraction).getByText(/every result below needs review/)).toBeInTheDocument();
    expect(within(extraction).getByText("item 1 has 566 requirements")).toBeInTheDocument();
    expect(within(firm).getByText(/only one firm is acceptable: Acme/)).toBeInTheDocument();
  });

  it("says when the tender buys spare parts and names their maker", async () => {
    serve({
      ...REPORT,
      spare_parts: {
        boq_title: "Spares parts for various pump model of Kirloskar brother limited",
        oem: "Kirloskar Brothers",
      },
    });
    await openReport();
    expect(screen.getByText("Spare parts tender")).toBeInTheDocument();
    expect(screen.getByText(/usually come from its maker: Kirloskar Brothers/)).toBeInTheDocument();
  });

  it("shows why a run failed instead of a report", async () => {
    serve(REPORT, { ...RUN, status: "failed", error: "2 cited catalogues are not stored" });
    renderWithProviders(<AppRoutes />, "/reports/run-1");
    expect(await screen.findByText("2 cited catalogues are not stored")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Requirements" })).not.toBeInTheDocument();
  });

  it("says when a run has not finished", async () => {
    serve(REPORT, { ...RUN, status: "running" });
    renderWithProviders(<AppRoutes />, "/reports/run-1");
    expect(await screen.findByText("This run is running")).toBeInTheDocument();
  });

  it("explains a run that does not exist", async () => {
    stubFetch(() =>
      json({ error: { code: "not_found", message: "Run run-1 not found", details: {} } }, 404),
    );
    renderWithProviders(<AppRoutes />, "/reports/run-1");
    expect(await screen.findByText(/Run run-1 not found/)).toBeInTheDocument();
  });
});

describe("EvidenceCell", () => {
  it("says a cited field on an unsupported row proves nothing", async () => {
    const cited = result("10.2", "unsupported", true);
    const [item] = REPORT.items;
    const [acme] = item?.manufacturers ?? [];
    if (item === undefined || acme === undefined) {
      throw new Error("Test data must have an item and a manufacturer");
    }
    serve({
      ...REPORT,
      items: [
        {
          ...item,
          manufacturers: [{ ...acme, results: [cited] }],
        },
      ],
    });
    await openReport();
    expect(screen.getByText(/Cited for review only, this decides nothing/)).toBeInTheDocument();
  });

  it("does not say so on a decided row", async () => {
    serve();
    await openReport();
    expect(screen.queryByText(/Cited for review only/)).not.toBeInTheDocument();
  });
});

describe("RunsTable", () => {
  it("links a finished run to its report and shows why another failed", async () => {
    stubFetch(() =>
      json([RUN, { ...RUN, run_id: "run-2", status: "failed", error: "broken page" }]),
    );
    renderWithProviders(<AppRoutes />, "/runs");
    expect(await screen.findByRole("link", { name: "Open report" })).toHaveAttribute(
      "href",
      "/reports/run-1",
    );
    expect(screen.getByText("broken page")).toBeInTheDocument();
  });
});
