/* eslint-disable @next/next/no-img-element */
import type { ReactNode } from "react";

import type { TeamBrief } from "@/lib/types";

/** Team logos are served by the NHL's own asset host. */
export function TeamLogo({ team, size = 36 }: { team: TeamBrief; size?: number }) {
  return <img src={team.logo} alt="" width={size} height={size} loading="lazy" decoding="async" className="shrink-0 select-none" style={{ width: size, height: size }} />;
}

export function Section({ id, eyebrow, title, note, children, aside }: { id?: string; eyebrow?: string; title: string; note?: ReactNode; children: ReactNode; aside?: ReactNode }) {
  return (
    <section id={id} className="mt-10 scroll-mt-20" aria-labelledby={id ? `${id}-h` : undefined}>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        <div>
          {eyebrow && <div className="eyebrow mb-1">{eyebrow}</div>}
          <h2 id={id ? `${id}-h` : undefined} className="display text-2xl font-semibold tracking-wide text-ink">
            {title}
          </h2>
          {note && <p className="mt-1 max-w-3xl text-sm text-ink-2">{note}</p>}
        </div>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function Chip({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "ice" | "good" | "bad" | "warn" | "live" }) {
  const tones: Record<string, string> = {
    neutral: "border-line text-ink-2",
    ice: "border-ice/40 text-ice",
    good: "border-good/40 text-good",
    bad: "border-bad/40 text-bad",
    warn: "border-warn/40 text-warn",
    live: "border-bad/50 text-ink",
  };
  return <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-[0.7rem] font-medium ${tones[tone]}`}>{children}</span>;
}

export function Stat({ label, value, sub, wide }: { label: string; value: ReactNode; sub?: ReactNode; wide?: boolean }) {
  return (
    <div className={`panel px-4 py-3.5 ${wide ? "sm:col-span-2" : ""}`}>
      <div className="eyebrow">{label}</div>
      <div className="num mt-1.5 text-2xl font-semibold text-ink">{value}</div>
      {sub && <div className="mt-1 text-xs text-ink-2">{sub}</div>}
    </div>
  );
}

export function Unavailable({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="panel hatch px-5 py-6 text-sm">
      <div className="font-semibold text-ink">{title}</div>
      {children && <div className="mt-1 text-ink-2">{children}</div>}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden />;
}

export function PageSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="mt-3 h-4 w-96 max-w-full" />
      <div className="mt-8 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-56" />
        ))}
      </div>
    </div>
  );
}
