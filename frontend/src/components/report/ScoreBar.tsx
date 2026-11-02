const FULL = 100;

/** A labelled percentage bar; empty with "Not decided" when there is no value. */
export function ScoreBar({ label, value }: { label: string; value: number | null }) {
  return (
    <div>
      <div className="flex justify-between text-xs text-ink-600">
        <span>{label}</span>
        <span className="font-semibold text-ink-900">
          {value === null ? "Not decided" : `${value.toFixed(1)}%`}
        </span>
      </div>
      <div
        className="mt-1 h-2 overflow-hidden rounded-full bg-canvas"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={FULL}
        aria-valuenow={value ?? undefined}
      >
        <div
          className="h-full rounded-full bg-brand-500"
          style={{ width: `${String(value ?? 0)}%` }}
        />
      </div>
    </div>
  );
}
