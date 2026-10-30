import { Link } from "react-router-dom";

import { ChevronRightIcon, HomeIcon, MenuIcon } from "../ui/icons";

interface TopBarProps {
  page: string;
  onToggleSidebar: () => void;
}

export function TopBar({ page, onToggleSidebar }: TopBarProps) {
  return (
    <header className="flex h-16 items-center gap-4 bg-gradient-to-r from-brand-700 to-brand-500 px-6 text-white">
      <button
        type="button"
        onClick={onToggleSidebar}
        aria-label="Toggle navigation"
        className="rounded-md p-1.5 text-xl hover:bg-white/15 focus-visible:outline-2 focus-visible:outline-white"
      >
        <MenuIcon />
      </button>
      <nav aria-label="Breadcrumb">
        <ol className="flex items-center gap-2 text-[15px]">
          <li>
            <Link to="/upload" className="flex items-center gap-1.5 opacity-90 hover:opacity-100">
              <HomeIcon /> Home
            </Link>
          </li>
          <li aria-hidden="true" className="opacity-70">
            <ChevronRightIcon />
          </li>
          <li aria-current="page" className="font-semibold">
            {page}
          </li>
        </ol>
      </nav>
    </header>
  );
}
