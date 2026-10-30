import type { ReactNode } from "react";

import { AlertIcon, CheckIcon } from "./icons";

type Tone = "error" | "info" | "success";

const TONES: Record<Tone, string> = {
  error: "border-status-missing/30 bg-red-50 text-status-missing",
  info: "border-line bg-canvas text-ink-600",
  success: "border-status-full/30 bg-green-50 text-status-full",
};

interface AlertProps {
  tone?: Tone;
  title: string;
  children?: ReactNode;
}

export function Alert({ tone = "info", title, children }: AlertProps) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={`flex gap-3 rounded-lg border px-4 py-3 text-sm ${TONES[tone]}`}
    >
      <span className="mt-0.5 text-base">{tone === "success" ? <CheckIcon /> : <AlertIcon />}</span>
      <div>
        <p className="font-semibold">{title}</p>
        {children !== undefined && <div className="mt-1 text-ink-600">{children}</div>}
      </div>
    </div>
  );
}
