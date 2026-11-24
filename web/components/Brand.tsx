/* eslint-disable @next/next/no-img-element */

/** The PUCKIQ icon on its own: used where the full logo does not fit. */
export function Mark({ size = 34, className = "" }: { size?: number; className?: string }) {
  return (
    <img
      src="/brand/mark.webp"
      alt=""
      width={size}
      height={size}
      decoding="async"
      className={`shrink-0 select-none ${className}`}
      style={{ width: size, height: size }}
    />
  );
}

// The logo file is 589 x 160.
const LOGO_RATIO = 589 / 160;

/** The full PUCKIQ logo, icon and name together. Drawn for a dark background. */
export function Logo({ height = 34, className = "" }: { height?: number; className?: string }) {
  const width = Math.round(height * LOGO_RATIO);
  return (
    <img
      src="/brand/logo.webp"
      alt="PUCKIQ"
      width={width}
      height={height}
      decoding="async"
      className={`shrink-0 select-none ${className}`}
      style={{ width, height }}
    />
  );
}
