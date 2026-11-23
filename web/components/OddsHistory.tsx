"use client";

import { useState } from "react";

import { dayLabel, odds } from "@/lib/format";
import type { OddsHistory } from "@/lib/types";

const MEASURES = {
  p_playoffs: { label: "Make playoffs", fmt: (v: number) => odds(v), axis: (v: number) => `${Math.round(v * 100)}%`, fixed: [0, 1] as [number, number] | null },
  p_cup: { label: "Win Stanley Cup", fmt: (v: number) => odds(v), axis: (v: number) => `${(v * 100).toFixed(1)}%`, fixed: null },
  proj_points: { label: "Projected points", fmt: (v: number) => v.toFixed(0), axis: (v: number) => v.toFixed(0), fixed: null },
} as const;
type Key = keyof typeof MEASURES;

/** One team's simulated odds on each day of the season. One measure at a
 * time; days reconstructed after the fact are drawn dashed. */
export function OddsHistoryChart({ history, color }: { history: OddsHistory; color: string }) {
  const keys = (Object.keys(MEASURES) as Key[]).filter((k) => history[k].filter((v) => v != null).length >= 2);
  const [key, setKey] = useState<Key>(keys[0] ?? "p_playoffs");
  const [hover, setHover] = useState<number | null>(null);
  const m = MEASURES[key];
  const cut = history.reconstructed_through ?? null;
  const pts = history.dates
    .map((d, i) => ({ date: d, t: Date.parse(d), v: history[key][i], rec: cut != null && d <= cut }))
    .filter((p): p is { date: string; t: number; v: number; rec: boolean } => p.v != null);
  if (pts.length < 2) return <p className="text-sm text-ink-2">Not enough days yet to chart.</p>;

  const W = 720, H = 240, mg = { l: 52, r: 16, t: 14, b: 28 };
  const vals = pts.map((p) => p.v);
  let lo = m.fixed ? m.fixed[0] : Math.min(...vals), hi = m.fixed ? m.fixed[1] : Math.max(...vals);
  if (!m.fixed) {
    const pad = (hi - lo) * 0.15 || (key === "proj_points" ? 2 : 0.01);
    lo = key === "p_cup" ? 0 : lo - pad;
    hi = hi + pad;
  }
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  const x = (t: number) => mg.l + ((t - t0) / (t1 - t0 || 1)) * (W - mg.l - mg.r);
  const y = (v: number) => H - mg.b - ((v - lo) / (hi - lo || 1)) * (H - mg.t - mg.b);
  const ticks = m.fixed ? [0, 0.5, 1] : [lo, (lo + hi) / 2, hi];
  const firstPub = pts.findIndex((p) => !p.rec);
  const recPart = firstPub < 0 ? pts : pts.slice(0, firstPub + 1);       // shares the boundary point so the line is unbroken
  const pubPart = firstPub < 0 ? [] : pts.slice(firstPub);
  const line = (a: typeof pts) => a.map((p) => `${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  const cur = hover ?? pts.length - 1;
  const c = pts[cur];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="Measure" className="flex gap-1 rounded-lg border border-line bg-surface p-1">
          {keys.map((k) => (
            <button key={k} role="tab" aria-selected={key === k} onClick={() => { setKey(k); setHover(null); }} className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${key === k ? "bg-raised text-ink" : "text-ink-2 hover:text-ink"}`}>
              {MEASURES[k].label}
            </button>
          ))}
        </div>
        <span className="num ml-auto text-xs text-ink-2" aria-live="polite">
          {dayLabel(c.date, "short")}: <span className="text-ink">{m.fmt(c.v)}</span>
          {c.rec && <span className="text-mute"> (reconstructed)</span>}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full touch-none"
        role="img"
        aria-label={`${m.label} on each day this season, from ${m.fmt(pts[0].v)} on ${pts[0].date} to ${m.fmt(pts[pts.length - 1].v)} on ${pts[pts.length - 1].date}`}
        onPointerMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const t = t0 + ((((e.clientX - r.left) / r.width) * W - mg.l) / (W - mg.l - mg.r)) * (t1 - t0);
          let best = 0;
          for (let i = 1; i < pts.length; i++) if (Math.abs(pts[i].t - t) < Math.abs(pts[best].t - t)) best = i;
          setHover(best);
        }}
        onPointerLeave={() => setHover(null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={mg.l} x2={W - mg.r} y1={y(t)} y2={y(t)} stroke="var(--color-line)" />
            <text x={mg.l - 8} y={y(t) + 3.5} textAnchor="end" className="num" fontSize="10" fill="var(--color-mute)">{m.axis(t)}</text>
          </g>
        ))}
        {firstPub > 0 && (
          <g>
            <line x1={x(pts[firstPub].t)} x2={x(pts[firstPub].t)} y1={mg.t} y2={H - mg.b} stroke="var(--color-mute)" strokeDasharray="2 4" />
            <text x={x(pts[firstPub].t) - 6} y={mg.t + 9} textAnchor="end" fontSize="10" fill="var(--color-mute)">published daily from here</text>
          </g>
        )}
        {recPart.length > 1 && <polyline points={line(recPart)} fill="none" stroke={color} strokeOpacity="0.7" strokeWidth="2" strokeDasharray="5 4" strokeLinejoin="round" />}
        {pubPart.length > 1 && <polyline points={line(pubPart)} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />}
        <line x1={x(c.t)} x2={x(c.t)} y1={mg.t} y2={H - mg.b} stroke="var(--color-mute)" strokeWidth="1" />
        <circle cx={x(c.t)} cy={y(c.v)} r="5" fill={color} stroke="var(--color-surface)" strokeWidth="2" />
        <text x={mg.l} y={H - 8} fontSize="10" className="num" fill="var(--color-mute)">{pts[0].date}</text>
        <text x={W - mg.r} y={H - 8} textAnchor="end" fontSize="10" className="num" fill="var(--color-mute)">{pts[pts.length - 1].date}</text>
      </svg>
      <p className="mt-1 text-xs text-mute">
        One point per day, from the daily season simulation.
        {cut != null && " The dashed part was reconstructed afterwards by re-running the simulation with only what was known each morning; it was not on this site at the time."}
      </p>
    </div>
  );
}
