import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { AppRoutes } from "../../App";
import { json, pdf, renderWithProviders, stubFetch } from "../../test/render";
import type { Manufacturer } from "../../types/api";

// Synthetic test data (not real catalogues).
const MANUFACTURERS: Manufacturer[] = [
  { name: "Acme_Valves", category: "valves_pumps", product_types: ["ball_valve"], documents: 2 },
  {
    name: "Beta_Pumps",
    category: "valves_pumps",
    product_types: ["centrifugal_pump"],
    documents: 1,
  },
];
const BOTH: Manufacturer = {
  name: "Gamma_Works",
  category: "valves_pumps",
  product_types: ["gate_valve", "submersible_pump"],
  documents: 2,
};

async function fillForm() {
  const user = userEvent.setup();
  await user.upload(screen.getByLabelText("Tender PDF"), pdf());
  await user.selectOptions(await screen.findByLabelText("Product category"), "valves_pumps");
  await user.click(screen.getByLabelText(/Acme Valves/));
  return user;
}

describe("UploadForm", () => {
  it("enables Start only once a file, category and manufacturer are chosen", async () => {
    stubFetch(() => json(MANUFACTURERS));
    renderWithProviders(<AppRoutes />);
    const start = screen.getByRole("button", { name: "Start matching" });
    expect(start).toBeDisabled();

    await fillForm();

    expect(start).toBeEnabled();
    expect(screen.getByRole("button", { name: "Remove bid.pdf" })).toBeInTheDocument();
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("uploads, starts a run and opens the runs page", async () => {
    const calls: string[] = [];
    let runBody: unknown = null;
    stubFetch((url, init) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.endsWith("/manufacturers")) return json(MANUFACTURERS);
      if (url.endsWith("/tenders"))
        return json({ tender_id: "t-1", filename: "bid.pdf", pages: 5 });
      if (url.endsWith("/runs") && init?.method === "POST") {
        runBody = typeof init.body === "string" ? JSON.parse(init.body) : null;
        return json({
          run_id: "r-1",
          tender_id: "t-1",
          status: "queued",
          created_at: "2026-10-29T10:00:00Z",
        });
      }
      return json([]);
    });
    renderWithProviders(<AppRoutes />);
    const user = await fillForm();

    await user.click(screen.getByRole("button", { name: "Start matching" }));

    expect(await screen.findByRole("heading", { name: "Runs", level: 1 })).toBeInTheDocument();
    expect(calls).toContain("POST /api/v1/tenders");
    expect(runBody).toEqual({
      tender_id: "t-1",
      category: "valves_pumps",
      manufacturers: ["Acme_Valves"],
    });
  });

  it("explains when the upload API cannot be reached and keeps the selection", async () => {
    stubFetch((url) =>
      url.endsWith("/manufacturers")
        ? json(MANUFACTURERS)
        : json({ error: { code: "not_found", message: "Not Found", details: {} } }, 404),
    );
    renderWithProviders(<AppRoutes />);
    const user = await fillForm();

    await user.click(screen.getByRole("button", { name: "Start matching" }));

    expect(await screen.findByText(/Cannot reach the upload API/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start matching" })).toBeEnabled();
  });

  it("shows why the manufacturer list is missing", async () => {
    stubFetch(() => json({ error: { code: "not_found", message: "Not Found", details: {} } }, 404));
    renderWithProviders(<AppRoutes />);
    expect(await screen.findByText("Manufacturer list is not available")).toBeInTheDocument();
  });

  it("rejects a non-PDF file", async () => {
    stubFetch(() => json(MANUFACTURERS));
    renderWithProviders(<AppRoutes />);
    const user = userEvent.setup({ applyAccept: false });
    await user.upload(
      screen.getByLabelText("Tender PDF"),
      new File(["x"], "boq.xlsx", { type: "application/vnd.ms-excel" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Only .pdf files can be uploaded.");
    await waitFor(() => {
      expect(screen.getByText("No file chosen")).toBeInTheDocument();
    });
  });

  it("lists valve and pump vendors in their own sections", async () => {
    stubFetch(() => json([...MANUFACTURERS, BOTH]));
    renderWithProviders(<AppRoutes />);
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Product category"), "valves_pumps");

    const valves = within(screen.getByRole("region", { name: /Valves/ }));
    const pumps = within(screen.getByRole("region", { name: /Pumps/ }));
    expect(valves.getByLabelText(/Acme Valves/)).toBeInTheDocument();
    expect(valves.queryByLabelText(/Beta Pumps/)).not.toBeInTheDocument();
    expect(pumps.getByLabelText(/Beta Pumps/)).toBeInTheDocument();
    // A vendor that makes both appears in both, with only that section's products.
    expect(valves.getByText(/gate valve/)).toBeInTheDocument();
    expect(pumps.getByText(/submersible pump/)).toBeInTheDocument();
    expect(valves.queryByText(/submersible pump/)).not.toBeInTheDocument();
  });

  it("selects and clears a whole section", async () => {
    stubFetch(() => json([...MANUFACTURERS, BOTH]));
    renderWithProviders(<AppRoutes />);
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Product category"), "valves_pumps");

    const allValves = screen.getByLabelText("Select all valves");
    await user.click(allValves);
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    // The shared vendor is now also ticked under Pumps, so Pumps is half selected.
    const allPumps = screen.getByLabelText<HTMLInputElement>("Select all pumps");
    expect(allPumps.checked).toBe(false);
    expect(allPumps.indeterminate).toBe(true);

    await user.click(allPumps);
    expect(screen.getByText("3 selected")).toBeInTheDocument();
    await user.click(allValves);
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("clears everything", async () => {
    stubFetch(() => json(MANUFACTURERS));
    renderWithProviders(<AppRoutes />);
    const user = await fillForm();
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByText("No file chosen")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start matching" })).toBeDisabled();
  });
});
