import type { ReactNode } from "react";

import { CheckIcon } from "./icons";

interface StepCardProps {
  number: number;
  label: string;
  value: string;
  done: boolean;
  icon: ReactNode;
}

/** One step of the upload flow, styled like the reference's stat cards. */
export function StepCard({ number, label, value, done, icon }: StepCardProps) {
  return (
    <div className="flex items-start justify-between rounded-card border-l-4 border-brand-600 bg-white p-5 shadow-card">
      <div className="min-w-0">
        <p className="text-xs font-semibold tracking-wide text-ink-600 uppercase">
          Step {number} · {label}
        </p>
        <p className="mt-2 truncate text-sm font-medium text-ink-900">{value}</p>
      </div>
      <span
        className={`flex size-11 shrink-0 items-center justify-center rounded-lg text-xl ${
          done ? "bg-green-50 text-status-full" : "bg-brand-50 text-brand-600"
        }`}
      >
        {done ? <CheckIcon /> : icon}
      </span>
    </div>
  );
}
