import type { ReactNode } from "react";

interface CardProps {
  title?: string;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** White panel with an optional heading row (the reference's section cards). */
export function Card({ title, icon, actions, children, className = "" }: CardProps) {
  return (
    <section className={`rounded-card bg-white p-6 shadow-card ${className}`}>
      {title !== undefined && (
        <header className="mb-4 flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 border-l-4 border-brand-600 pl-3 text-lg font-semibold">
            {icon !== undefined && <span className="text-brand-600">{icon}</span>}
            {title}
          </h2>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}
