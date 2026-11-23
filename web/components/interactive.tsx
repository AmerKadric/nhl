"use client";

import { useState } from "react";

import { oneIn, pct } from "@/lib/format";

const VIS = 7; // matrix shows 0..7 goals per team

/** Exact-score probability matrix. One sequential hue: darker = less likely.
 * Diagonal cells are hatched because an NHL game cannot end level. */
export function ScoreMatrix({ matrix, homeAbbrev, awayAbbrev, overflow, compare }: { matrix: number[][]; homeAbbrev: string; awayAbbrev: string; overflow: number; compare?: number[][] }) {
  const [hover, setHover] = useState<{ h: number; a: number } | null>(null);
  const idx = Array.from({ length: VIS + 1 }, (_, i) => i);
  let max = 0, inside = 0, best = { h: 0, a: 0 };
  for (const h of idx) for (const a of idx) {
    inside += matrix[h][a];
    if (matrix[h][a] > max) { max = matrix[h][a]; best = { h, a }; }
  }
  const total = matrix.flat().reduce((s, v) => s + v, 0);
  const outside = Math.max(0, total - inside + overflow);
  const cur = hover ?? best;
  const p = matrix[cur.h][cur.a];
  const delta = compare ? p - compare[cur.h][cur.a] : null;

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_260px]">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[420px] border-separate border-spacing-[2px]" aria-label={`Probability of each final score, ${awayAbbrev} goals by row and ${homeAbbrev} goals by column`}>
          <thead>
            <tr>
              <th className="w-16" />
              <th colSpan={VIS + 1} className="pb-1 text-center text-[0.7rem] font-semibold uppercase tracking-wider text-ink-2">
                {homeAbbrev} goals
              </th>
            </tr>
            <tr>
              <th className="text-[0.7rem] font-semibold uppercase tracking-wider text-ink-2">{awayAbbrev}</th>
              {idx.map((h) => (
                <th key={h} scope="col" className="num pb-1 text-xs font-medium text-mute">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {idx.map((a) => (
              <tr key={a}>
                <th scope="row" className="num pr-2 text-right text-xs font-medium text-mute">
                  {a}
                </th>
                {idx.map((h) => {
                  const v = matrix[h][a];
                  const tie = h === a;
                  const on = cur.h === h && cur.a === a;
                  const t = max > 0 ? v / max : 0;
                  return (
                    <td key={h} className="p-0">
                      {tie ? (
                        <div className="hatch flex aspect-[1.25] items-center justify-center rounded-[5px] bg-raised/40" title="Not possible: NHL games cannot end tied" aria-label={`${a} to ${h}: not possible, games cannot end tied`} />
                      ) : (
                        <button
                          type="button"
                          onMouseEnter={() => setHover({ h, a })}
                          onFocus={() => setHover({ h, a })}
                          onMouseLeave={() => setHover(null)}
                          onBlur={() => setHover(null)}
                          aria-label={`${awayAbbrev} ${a}, ${homeAbbrev} ${h}: ${pct(v, 1)}`}
                          className={`num flex aspect-[1.25] w-full items-center justify-center rounded-[5px] text-[0.72rem] transition-[outline-color] duration-100 ${on ? "outline outline-2 outline-ink" : "outline outline-2 outline-transparent"}`}
                          style={{ background: `color-mix(in oklab, var(--color-home) ${Math.round(6 + t * 94)}%, var(--color-surface))`, color: t > 0.55 ? "var(--color-bg)" : "var(--color-ink-2)" }}
                        >
                          {v >= 0.005 ? (v * 100).toFixed(1) : ""}
                        </button>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="panel self-start p-4" aria-live="polite">
        <div className="eyebrow">{hover ? "Selected score" : "Most likely score"}</div>
        <div className="display mt-1 text-4xl font-bold leading-none">
          {awayAbbrev} {cur.a} <span className="text-mute">–</span> {cur.h} {homeAbbrev}
        </div>
        <div className="num mt-3 text-2xl font-semibold text-ice">{pct(p, 1)}</div>
        <div className="text-xs text-ink-2">{oneIn(p)} games</div>
        {delta != null && (
          <div className="num mt-2 text-xs text-ink-2">
            {delta >= 0 ? "+" : "−"}
            {Math.abs(delta * 100).toFixed(1)} pts vs the unchanged inputs
          </div>
        )}
        <p className="mt-4 border-t border-line-soft pt-3 text-xs leading-relaxed text-mute">
          Values are percentages. {pct(outside, 1)} of the probability lies outside this grid (a team scoring 8 or more). Hatched cells cannot happen: a tie after
          regulation is settled in overtime or a shootout, which adds one goal to the winner.
        </p>
      </div>
    </div>
  );
}

/** Probability distribution as thin bars with a hover readout. */
export function DistBars({ data, unit, highlight }: { data: { label: string; p: number; tone?: "home" | "away" | "neutral" }[]; unit: string; highlight?: string }) {
  const [hi, setHi] = useState<number | null>(null);
  const max = Math.max(...data.map((d) => d.p), 1e-9);
  const cur = hi != null ? data[hi] : (data.find((d) => d.label === highlight) ?? data.reduce((b, d) => (d.p > b.p ? d : b), data[0]));
  return (
    <div>
      <div className="num mb-2 h-5 text-xs text-ink-2" aria-live="polite">
        <span className="text-ink">{cur.label}</span> {unit}: <span className="text-ink">{pct(cur.p, 1)}</span>
      </div>
      <div className="flex h-32 items-end gap-[2px]" role="list">
        {data.map((d, i) => (
          <button
            key={d.label}
            type="button"
            role="listitem"
            onMouseEnter={() => setHi(i)}
            onFocus={() => setHi(i)}
            onMouseLeave={() => setHi(null)}
            onBlur={() => setHi(null)}
            aria-label={`${d.label} ${unit}: ${pct(d.p, 1)}`}
            className="group flex h-full min-w-0 flex-1 flex-col justify-end"
          >
            <span
              className={`block w-full rounded-t-[4px] transition-opacity ${d.tone === "away" ? "bg-away" : d.tone === "neutral" ? "bg-mute" : "bg-home"} ${hi === null || hi === i ? "opacity-100" : "opacity-45"}`}
              style={{ height: `${Math.max(1.5, (d.p / max) * 100)}%` }}
            />
          </button>
        ))}
      </div>
      <div className="mt-1 flex gap-[2px]">
        {data.map((d) => (
          <span key={d.label} className="num min-w-0 flex-1 text-center text-[0.62rem] text-mute">
            {d.label}
          </span>
        ))}
      </div>
    </div>
  );
}

type Pt = { date: string; elo: number; off_g: number; def_g: number; off_xg: number; def_xg: number };
const SERIES = {
  elo: { label: "Elo rating", unit: "points (1500 = average)", fmt: (v: number) => v.toFixed(0), base: 1500 },
  net_g: { label: "Net goals rating", unit: "regulation goals per game vs average", fmt: (v: number) => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2), base: 0 },
  net_xg: { label: "Net chance rating", unit: "5-on-5 expected goals per 60 vs average", fmt: (v: number) => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2), base: 0 },
} as const;

/** How the model's rating of a team has moved this season. One measure at a
 * time (never two y-scales on one chart). */
export function RatingChart({ history, color }: { history: Pt[]; color: string }) {
  const [key, setKey] = useState<keyof typeof SERIES>("elo");
  const [hi, setHi] = useState<number | null>(null);
  const s = SERIES[key];
  const vals = history.map((h) => (key === "elo" ? h.elo : key === "net_g" ? h.off_g + h.def_g : h.off_xg + h.def_xg));
  if (vals.length < 2) return <p className="text-sm text-ink-2">Not enough games yet to chart.</p>;
  const W = 720, H = 240, m = { l: 52, r: 16, t: 14, b: 28 };
  const lo = Math.min(...vals, s.base), hi_ = Math.max(...vals, s.base);
  const pad = (hi_ - lo) * 0.12 || 1;
  const x = (i: number) => m.l + (i / (vals.length - 1)) * (W - m.l - m.r);
  const y = (v: number) => H - m.b - ((v - (lo - pad)) / (hi_ - lo + 2 * pad)) * (H - m.t - m.b);
  const cur = hi ?? vals.length - 1;
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="Rating" className="flex gap-1 rounded-lg border border-line bg-surface p-1">
          {(Object.keys(SERIES) as (keyof typeof SERIES)[]).map((k) => (
            <button key={k} role="tab" aria-selected={key === k} onClick={() => setKey(k)} className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${key === k ? "bg-raised text-ink" : "text-ink-2 hover:text-ink"}`}>
              {SERIES[k].label}
            </button>
          ))}
        </div>
        <span className="num ml-auto text-xs text-ink-2" aria-live="polite">
          {history[cur].date}: <span className="text-ink">{s.fmt(vals[cur])}</span>
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full touch-none"
        role="img"
        aria-label={`${s.label} after each game this season, currently ${s.fmt(vals[vals.length - 1])}`}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * W;
          setHi(Math.max(0, Math.min(vals.length - 1, Math.round(((px - m.l) / (W - m.l - m.r)) * (vals.length - 1)))));
        }}
        onMouseLeave={() => setHi(null)}
      >
        {[lo, s.base, hi_].filter((v, i, a) => a.indexOf(v) === i).map((t) => (
          <g key={t}>
            <line x1={m.l} x2={W - m.r} y1={y(t)} y2={y(t)} stroke={t === s.base ? "var(--color-mute)" : "var(--color-line)"} strokeDasharray={t === s.base ? "4 4" : undefined} />
            <text x={m.l - 8} y={y(t) + 3.5} textAnchor="end" className="num" fontSize="10" fill="var(--color-mute)">{s.fmt(t)}</text>
          </g>
        ))}
        <polyline points={vals.map((v, i) => `${x(i)},${y(v)}`).join(" ")} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
        <line x1={x(cur)} x2={x(cur)} y1={m.t} y2={H - m.b} stroke="var(--color-mute)" strokeWidth="1" />
        <circle cx={x(cur)} cy={y(vals[cur])} r="5" fill={color} stroke="var(--color-surface)" strokeWidth="2" />
        <text x={m.l} y={H - 8} fontSize="10" className="num" fill="var(--color-mute)">{history[0].date}</text>
        <text x={W - m.r} y={H - 8} textAnchor="end" fontSize="10" className="num" fill="var(--color-mute)">{history[history.length - 1].date}</text>
      </svg>
      <p className="mt-1 text-xs text-mute">Unit: {s.unit}. The dashed line is league average. One point per game played.</p>
    </div>
  );
}
