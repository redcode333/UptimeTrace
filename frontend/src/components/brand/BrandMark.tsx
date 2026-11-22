/**
 * The current UptimeTrace mark and wordmark, as on the landing page: the trace ring, the trace line and the amber dot.
 * Colours come from the console tokens (currentColor and --forecast), so it follows the theme.
 */
export function BrandMark({ className, wordmarkClassName }: { className?: string; wordmarkClassName?: string }) {
  return (
    <div className={`brand${className ? ` ${className}` : ""}`}>
      <svg width="48" height="26" viewBox="0 0 60 32" aria-hidden="true">
        <path d="M22.4 12.6A10 10 0 1 0 22.4 19.4" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
        <polyline points="6.5,16 9.8,16 12,10.6 14.8,21.4 16.8,16 46,16" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="50.5" cy="16" r="3.1" fill="var(--forecast)" />
      </svg>
      <span className={`brand-wordmark${wordmarkClassName ? ` ${wordmarkClassName}` : ""}`}>uptime<b>trace</b></span>
    </div>
  );
}
