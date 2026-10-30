import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

import { ListIcon, ReportIcon, UploadIcon } from "../ui/icons";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
}

const NAV: readonly NavItem[] = [
  { to: "/upload", label: "Upload Tender", icon: <UploadIcon /> },
  { to: "/runs", label: "Runs", icon: <ListIcon /> },
  { to: "/reports", label: "Reports", icon: <ReportIcon /> },
];

export function Sidebar({ open }: { open: boolean }) {
  return (
    <aside
      className={`${open ? "w-60" : "w-16"} flex shrink-0 flex-col border-r border-line bg-white transition-[width]`}
    >
      <div className="flex h-16 items-center gap-2 px-4">
        <span className="flex size-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
          TM
        </span>
        {open && (
          <span className="text-lg font-bold tracking-tight">
            Tender<span className="text-brand-600">Matcher</span>
          </span>
        )}
      </div>
      <nav aria-label="Main" className="flex-1 py-2">
        <ul>
          {NAV.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                title={open ? undefined : item.label}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-5 py-3 text-[15px] font-medium transition-colors focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-600 ${
                    isActive
                      ? "bg-brand-600 text-white"
                      : "text-ink-600 hover:bg-canvas hover:text-ink-900"
                  }`
                }
              >
                <span className="text-xl">{item.icon}</span>
                {open ? item.label : <span className="sr-only">{item.label}</span>}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
}
