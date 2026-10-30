import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "../../test/render";
import { Sidebar } from "./Sidebar";

describe("Sidebar", () => {
  it("links every screen and marks the current one", () => {
    renderWithProviders(<Sidebar open />, "/runs");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Upload Tender/ })).toHaveAttribute("href", "/upload");
    expect(screen.getByRole("link", { name: /Runs/ })).toHaveAttribute("aria-current", "page");
  });

  it("keeps accessible names when collapsed", () => {
    renderWithProviders(<Sidebar open={false} />);
    expect(screen.getByRole("link", { name: "Reports" })).toBeInTheDocument();
  });
});
