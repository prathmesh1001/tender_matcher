import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { EvidenceCell } from "../../components/report/EvidenceCell";
import { json, renderWithProviders, stubFetch } from "../../test/render";
import type { DiscoveryJob, DiscoveryOverview } from "../../types/discovery";
import type { Evidence } from "../../types/report";
import { DiscoveryPanel } from "./DiscoveryPanel";

// Synthetic test data (not real listings or evidence).
const LISTING: Evidence = {
  document: "abc.html",
  sha256: "a".repeat(64),
  title: "Example Control Valve Pneumatic Size 15",
  page_number: 1,
  label: "Body & Bonnet Material",
  value: "WCB",
  quote: "Body & Bonnet Material: WCB",
  verified: true,
  source_url: "https://mkp.gem.gov.in/example/p-1-2-cat.html",
  fetched_at: "2026-11-18T07:38:05Z",
  seller_declared: true,
};

const CATALOGUE: Evidence = {
  document: "alpha.pdf",
  sha256: "b".repeat(64),
  title: "Alpha",
  page_number: 7,
  label: "Body",
  value: "WCB",
  quote: "Body: WCB",
  verified: true,
};

const JOB: DiscoveryJob = {
  job_id: "job-1",
  run_id: "run-1",
  product_type: "control_valve",
  category: "valves_pumps",
  status: "done",
  error: null,
  listings: [
    {
      url: LISTING.source_url ?? "",
      kept: true,
      reason: "kept",
      title: LISTING.title,
      brand: "Example",
      model: "M1",
      snapshot_ref: "gem_snapshots/control_valve/x.html",
      sold_as: "oem",
      seller_rating: 4.5,
      assessed: true,
    },
  ],
  result_run_id: "run-2",
  created_at: "2026-11-18T07:38:00Z",
  finished_at: "2026-11-18T07:38:30Z",
};

function overview(jobs: DiscoveryJob[] = [], enabled = true): DiscoveryOverview {
  return {
    enabled,
    uncovered: [
      {
        product_type: "control_valve",
        category: "valves_pumps",
        item_nos: ["1", "2"],
        gem_configured: true,
        jobs,
      },
    ],
  };
}

describe("EvidenceCell", () => {
  it("marks GeM evidence as seller-declared with listing link, date and snapshot", () => {
    renderWithProviders(<EvidenceCell evidence={[LISTING]} />);
    expect(screen.getByText("GeM listing (seller-declared)")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: LISTING.title })).toHaveAttribute(
      "href",
      LISTING.source_url,
    );
    expect(screen.getByText(/Read 18 Nov 2026/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "saved snapshot" })).toHaveAttribute(
      "href",
      `/api/v1/documents/${LISTING.sha256}/file`,
    );
    expect(screen.getByText("“Body & Bonnet Material: WCB”")).toBeInTheDocument();
  });

  it("shows no badge for a manufacturer catalogue", () => {
    renderWithProviders(<EvidenceCell evidence={[CATALOGUE]} />);
    expect(screen.queryByText("GeM listing (seller-declared)")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "alpha.pdf, p.7" })).toBeInTheDocument();
  });
});

describe("DiscoveryPanel", () => {
  it("shows nothing when discovery is off", async () => {
    const fetchSpy = stubFetch(() => json(overview([], false)));
    const { container } = renderWithProviders(<DiscoveryPanel runId="run-1" />);
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalled();
    });
    expect(container).toBeEmptyDOMElement();
  });

  it("asks before checking GeM and starts the check for that type", async () => {
    const fetchSpy = stubFetch((_url, init) =>
      init?.method === "POST" ? json({ ...JOB, status: "queued" }, 202) : json(overview()),
    );
    renderWithProviders(<DiscoveryPanel runId="run-1" />);
    const button = await screen.findByRole("button", {
      name: "Check GeM Marketplace for control valve?",
    });
    await userEvent.click(button);
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/v1/discovery/runs/run-1/gem",
        expect.objectContaining({ method: "POST", body: '{"product_type":"control_valve"}' }),
      );
    });
  });

  it("links to the re-matched report once the check is done", async () => {
    stubFetch(() => json(overview([JOB])));
    renderWithProviders(<DiscoveryPanel runId="run-1" />);
    expect(await screen.findByRole("link", { name: "Open the re-matched report" })).toHaveAttribute(
      "href",
      "/reports/run-2",
    );
    expect(screen.getByText(/1 of 1 listings kept/)).toBeInTheDocument();
    expect(
      screen.getByText("Example · sold by the maker (OEM) · seller rating 4.5+ · vendor assessed"),
    ).toBeInTheDocument();
  });
});
