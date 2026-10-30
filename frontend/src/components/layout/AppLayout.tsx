import { useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

const PAGE_NAMES: Readonly<Record<string, string>> = {
  "/upload": "Upload Tender",
  "/runs": "Runs",
  "/reports": "Reports",
};

export function AppLayout() {
  const [open, setOpen] = useState(true);
  const { pathname } = useLocation();
  return (
    <div className="flex min-h-screen">
      <Sidebar open={open} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          page={PAGE_NAMES[pathname] ?? "Tender Matcher"}
          onToggleSidebar={() => {
            setOpen((o) => !o);
          }}
        />
        <main className="flex-1 space-y-6 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
