/** PUCKIQ mark: a puck seen from above as a face-off circle, with a single
 * probability arc sweeping around it. Reads at 16px and at 64px. */
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="#10131a" />
      <rect x="0.5" y="0.5" width="31" height="31" rx="7.5" fill="none" stroke="#222835" />
      <circle cx="16" cy="16" r="9.5" fill="none" stroke="#2c3443" strokeWidth="2.5" />
      <path d="M16 6.5 A9.5 9.5 0 1 1 7.77 20.75" fill="none" stroke="#5cd0ff" strokeWidth="2.5" strokeLinecap="round" />
      <circle cx="16" cy="16" r="3.4" fill="#e9eef4" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <span className="display text-[1.45rem] font-bold leading-none tracking-[0.06em]">
      PUCK<span className="text-ice">IQ</span>
    </span>
  );
}
