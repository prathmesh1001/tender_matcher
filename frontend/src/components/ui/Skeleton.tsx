/** Grey placeholder bar shown while data loads (the reference's loading state). */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden="true" className={`animate-pulse rounded-full bg-line ${className}`} />;
}
