import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { apiCached } from "@/lib/api";
import { stampET } from "@/lib/format";
import type { Meta } from "@/lib/types";

import { Mark, Wordmark } from "./Brand";
import { NavLinks } from "./NavLinks";

export function Header() {
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-bg/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2.5" aria-label="PUCKIQ home">
          <Mark />
          <Wordmark />
        </Link>
        <Suspense fallback={<span className="ml-6 h-5 w-56" />}>
          <NavLinks />
        </Suspense>
        <div className="ml-auto hidden md:block">
          <Suspense fallback={<span className="skeleton block h-5 w-44" />}>
            <DataStatus />
          </Suspense>
        </div>
      </div>
    </header>
  );
}

async function DataStatus() {
  let meta: Meta | null = null;
  await connection();
  try {
    meta = await apiCached<Meta>("/v1/meta");
  } catch {
    return <StatusPill tone="bad" text="Data service unreachable" />;
  }
  if (!meta) return null;
  const stale = meta.freshness.stale;
  if (!meta.model) return <StatusPill tone="warn" text="No model published" />;
  if (stale.length) return <StatusPill tone="warn" text={`Stale data: ${stale.map((s) => s.replace(/_/g, " ")).join(", ")}`} />;
  return <StatusPill tone="good" text={`Data current · checked ${stampET(meta.freshness.checked_at + "Z")}`} />;
}

function StatusPill({ tone, text }: { tone: "good" | "warn" | "bad"; text: string }) {
  const c = tone === "good" ? "bg-good" : tone === "warn" ? "bg-warn" : "bg-bad";
  return (
    <span className="flex items-center gap-2 text-xs text-ink-2">
      <span className={`h-1.5 w-1.5 rounded-full ${c}`} aria-hidden />
      {text}
    </span>
  );
}

/** Shown wherever forecasts appear if any upstream feed is stale. */
export async function StaleBanner() {
  let meta: Meta | null = null;
  await connection();
  try {
    meta = await apiCached<Meta>("/v1/meta");
  } catch {
    return null;
  }
  if (!meta || meta.freshness.stale.length === 0) return null;
  return (
    <div role="status" className="mb-5 rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-ink">
      <strong className="font-semibold">Some data is out of date.</strong>{" "}
      {meta.freshness.stale.map((s) => s.replace(/_/g, " ")).join(", ")} did not refresh on schedule. Results are current through{" "}
      {meta.freshness.results_through}; advanced stats through {meta.freshness.xg_through}. Forecasts below were made with the data
      available at their timestamp.
    </div>
  );
}

export function Footer() {
  return (
    <footer className="mt-20 border-t border-line">
      <div className="mx-auto grid max-w-7xl gap-8 px-4 py-10 text-sm text-ink-2 sm:px-6 md:grid-cols-[1.2fr_1fr_1fr]">
        <div>
          <div className="flex items-center gap-2.5">
            <Mark size={24} />
            <Wordmark />
          </div>
          <p className="mt-3 max-w-sm text-mute">
            Independent NHL score forecasts from a published statistical model. Probabilities, not promises: no model can know a
            final score in advance.
          </p>
        </div>
        <div>
          <div className="eyebrow mb-2">Data</div>
          <p>
            Expected goals and shot data:{" "}
            <a className="text-ice hover:underline" href="https://moneypuck.com" target="_blank" rel="noreferrer">
              MoneyPuck.com
            </a>
            . Schedule, results and live game state: NHL.
          </p>
          <p className="mt-2 text-mute">Injury reports and confirmed game-day lineups are not available to the model.</p>
        </div>
        <div>
          <div className="eyebrow mb-2">About</div>
          <p>
            <Link className="text-ice hover:underline" href="/methodology">
              How the model works
            </Link>{" "}
            ·{" "}
            <Link className="text-ice hover:underline" href="/accuracy">
              Track record
            </Link>
          </p>
          <p className="mt-2 text-mute">
            Non-commercial. Not affiliated with the NHL or its teams; names and logos belong to their owners. Not betting advice.
          </p>
        </div>
      </div>
    </footer>
  );
}
