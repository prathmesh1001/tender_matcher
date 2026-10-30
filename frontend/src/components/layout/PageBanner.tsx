interface PageBannerProps {
  title: string;
  subtitle: string;
}

/** Gradient welcome strip at the top of a page (the reference's greeting card). */
export function PageBanner({ title, subtitle }: PageBannerProps) {
  return (
    <div className="relative overflow-hidden rounded-card bg-gradient-to-r from-brand-600 via-brand-500 to-accent-500 px-6 py-5 text-white shadow-card">
      <div
        aria-hidden="true"
        className="absolute -top-10 right-40 size-36 rounded-full bg-white/10"
      />
      <div
        aria-hidden="true"
        className="absolute -bottom-12 right-10 size-28 rounded-full bg-white/10"
      />
      <h1 className="relative text-2xl font-bold">{title}</h1>
      <p className="relative mt-1 text-sm text-white/90">{subtitle}</p>
    </div>
  );
}
