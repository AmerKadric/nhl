import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { PageSkeleton, TeamLogo, Unavailable } from "@/components/ui";
import { apiCached } from "@/lib/api";
import { pct, recordStr, seasonLabel, signed } from "@/lib/format";
import type { TeamsResponse } from "@/lib/types";

export const metadata: Metadata = { title: "Teams" };

const SORTS: Record<string, { label: string; get: (t: TeamsResponse["teams"][number]) => number }> = {
  elo: { label: "Elo", get: (t) => t.summary?.ratings?.elo ?? -1e9 },
  points: { label: "Points %", get: (t) => t.summary?.point_pct ?? -1 },
  net: { label: "Net rating", get: (t) => (t.summary?.ratings ? t.summary.ratings.off_g + t.summary.ratings.def_g : -1e9) },
  xg: { label: "xG share", get: (t) => t.summary?.xg_share_5v5 ?? -1 },
};

export default function Page({ searchParams }: PageProps<"/teams">) {
  return (
    <>
      <h1 className="display text-[2rem] font-bold leading-none tracking-wide sm:text-[2.4rem]">Team intelligence</h1>
      <p className="mt-2 max-w-3xl text-sm text-ink-2">
        All 32 teams as the model currently rates them. Ratings are in real units, measured against a league-average team, and update after every game.
      </p>
      <Suspense fallback={<div className="mt-8"><PageSkeleton /></div>}>
        <Table searchParams={searchParams} />
      </Suspense>
    </>
  );
}

async function Table({ searchParams }: { searchParams: PageProps<"/teams">["searchParams"] }) {
  const sp = await searchParams;
  const data = await apiCached<TeamsResponse>("/v1/teams");
  if (!data) return <Unavailable title="Team data is unavailable." />;
  const key = typeof sp.sort === "string" && SORTS[sp.sort] ? sp.sort : "elo";
  const teams = [...data.teams].sort((a, b) => SORTS[key].get(b) - SORTS[key].get(a));
  const r = data.rating_ranges;
  const bar = (v: number, k: "off_g" | "def_g") => {
    const span = Math.max(Math.abs(r[k]?.min ?? 1), Math.abs(r[k]?.max ?? 1));
    return Math.min(50, (Math.abs(v) / span) * 50);
  };
  return (
    <div className="mt-6">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-ink-2">{seasonLabel(data.season)} · sort by</span>
        {Object.entries(SORTS).map(([k, s]) => (
          <Link key={k} href={`/teams?sort=${k}`} aria-current={k === key ? "true" : undefined} className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${k === key ? "border-ice/50 text-ice" : "border-line text-ink-2 hover:text-ink"}`}>
            {s.label}
          </Link>
        ))}
      </div>
      <div className="panel overflow-x-auto">
        <table className="w-full min-w-[860px] text-sm">
          <thead className="text-left text-xs uppercase tracking-wider text-mute">
            <tr className="border-b border-line">
              <th className="px-4 py-2.5 font-semibold">#</th>
              <th className="px-4 py-2.5 font-semibold">Team</th>
              <th className="px-4 py-2.5 font-semibold">Record</th>
              <th className="px-4 py-2.5 font-semibold">Points %</th>
              <th className="px-4 py-2.5 font-semibold">Elo</th>
              <th className="px-4 py-2.5 font-semibold">Offence <span className="normal-case text-mute">goals/gm vs avg</span></th>
              <th className="px-4 py-2.5 font-semibold">Defence <span className="normal-case text-mute">prevented/gm</span></th>
              <th className="px-4 py-2.5 font-semibold">5-on-5 xG share</th>
              <th className="px-4 py-2.5 font-semibold">Last 10</th>
            </tr>
          </thead>
          <tbody>
            {teams.map((t, i) => {
              const s = t.summary;
              const rt = s?.ratings;
              return (
                <tr key={t.id} className="border-b border-line-soft last:border-0 hover:bg-raised/60">
                  <td className="num px-4 py-2 text-mute">{i + 1}</td>
                  <td className="px-4 py-2">
                    <Link href={`/teams/${t.abbrev}`} className="flex items-center gap-2.5 font-medium text-ink hover:text-ice">
                      <TeamLogo team={t} size={24} /> {t.name}
                    </Link>
                  </td>
                  <td className="num px-4 py-2 text-ink-2">{recordStr(s?.record)}</td>
                  <td className="num px-4 py-2 text-ink">{s ? pct(s.point_pct, 1) : "—"}</td>
                  <td className="num px-4 py-2 text-ink">{rt ? rt.elo.toFixed(0) : "—"}</td>
                  {(["off_g", "def_g"] as const).map((k) => (
                    <td key={k} className="px-4 py-2">
                      {rt ? (
                        <div className="flex items-center gap-2">
                          <span className="num w-12 text-ink">{signed(rt[k])}</span>
                          <span className="relative h-2 w-24 rounded-sm bg-raised" aria-hidden>
                            <span className="absolute inset-y-0 left-1/2 w-px bg-mute/60" />
                            <span className={`absolute inset-y-0 rounded-sm ${rt[k] >= 0 ? "left-1/2 bg-home" : "right-1/2 bg-away"}`} style={{ width: `${bar(rt[k], k)}%` }} />
                          </span>
                        </div>
                      ) : "—"}
                    </td>
                  ))}
                  <td className="num px-4 py-2 text-ink-2">{s?.xg_share_5v5 != null ? pct(s.xg_share_5v5, 1) : "—"}</td>
                  <td className="num px-4 py-2 text-ink-2">{recordStr(s?.last10)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-mute">
        Offence and defence ratings are regulation goals per game relative to league average, adjusted for opponents and home ice; positive is better for both. Blue bars are above
        average, amber below. Elo is a results-only rating where 1500 is average.
      </p>
    </div>
  );
}
