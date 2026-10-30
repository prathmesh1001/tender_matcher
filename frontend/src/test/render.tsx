import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

/** Render inside a fresh query client and a router at `path`. */
export function renderWithProviders(ui: ReactElement, path = "/upload") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

type Route = (url: string, init?: RequestInit) => Response | Promise<Response>;

/** Stub `fetch` with a router function; returns the spy for call assertions. */
export function stubFetch(route: Route) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation((input: RequestInfo | URL, init?: RequestInit) =>
      Promise.resolve(route(input instanceof Request ? input.url : String(input), init)),
    );
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function pdf(name = "bid.pdf", size = 2048): File {
  return new File([new Uint8Array(size)], name, { type: "application/pdf" });
}
