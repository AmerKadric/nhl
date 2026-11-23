import { fx, pct } from "@/lib/format";
import type { CalBin, ConfBin, Driver, TeamBrief } from "@/lib/types";

/* Static (server-rendered) charts. Colour roles are fixed everywhere:
   home = blue, away = amber; status green/red is never used for a series. */

export function OutcomeBar({ home, away, pHomeReg, pHomeExtra, pAwayExtra, pAwayReg }: { home: TeamBrief; away: TeamBrief; pHomeReg: number; pHomeExtra: number; pAwayExtra: number; pAwayReg: number }) {
  const segs = [
    { k: "ar", label: `${away.abbrev} in regulation`, p: pAwayReg, cls: "bg-away" },
    { k: "ae", label: `${away.abbrev} in OT / SO`, p: pAwayExtra, cls: "bg-away/55" },
    { k: "he", label: `${home.abbrev} in OT / SO`, p: pHomeExtra, cls: "bg-home/55" },
    { k: "hr", label: `${home.abbrev} in regulation`, p: pHomeReg, cls: "bg-home" },
  ];
  return (
    <div>
      <div className="flex h-9 gap-[2px] overflow-hidden rounded-lg" role="img" aria-label={segs.map((s) => `${s.label} ${pct(s.p)}`).join(", ")}>
        {segs.map((s) => (
          <div key={s.k} className={`${s.cls} grow-x flex items-center justify-center`} style={{ width: `${s.p * 100}%` }} title={`${s.label}: ${pct(s.p, 1)}`}>
            {s.p > 0.09 && <span className="num text-xs font-semibold text-bg">{pct(s.p)}</span>}
          </div>
        ))}
      </div>
      <ul className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-4">
        {segs.map((s) => (
          <li key={s.k} className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 shrink-0 rounded-sm ${s.cls}`} aria-hidden />
            <span className="text-ink-2">{s.label}</span>
            <span className="num ml-auto text-ink">{pct(s.p, 1)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const pts = (v: number) => `${Math.abs(v * 100).toFixed(1)} pts`;

function driverDetail(d: Driver, home: TeamBrief, away: TeamBrief): string {
  const i = d.inputs;
  const s = (v: number | null | undefined, digits = 2) => (v == null ? "n/a" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}`);
  switch (d.key) {
    case "home":
      return `${home.abbrev} is the home team.`;
    case "xga":
      return `Expected goals per 60 vs league average. ${home.abbrev}: creates ${s(i["t_off_xga"])}, prevents ${s(i["away:o_def_xga"])}. ${away.abbrev}: creates ${s(i["away:t_off_xga"])}, prevents ${s(i["o_def_xga"])}.`;
    case "goals":
      return `Regulation goals per game vs league average. ${home.abbrev}: scores ${s(i["t_off_g"])}, prevents ${s(i["away:o_def_g"])}. ${away.abbrev}: scores ${s(i["away:t_off_g"])}, prevents ${s(i["o_def_g"])}.`;
    case "elo":
      return `Elo gap from results: ${home.abbrev} ${s((i["elo_diff"] ?? 0) * 100, 0)} points.`;
    case "goalie":
      return `Goals saved above expected per expected goal (shrunk toward average). ${home.abbrev} goalie ${s((i["away:o_gk"] ?? 0) * 100, 1)}%, ${away.abbrev} goalie ${s((i["o_gk"] ?? 0) * 100, 1)}%.`;
    case "rest":
      return `${home.abbrev}: ${i["t_b2b"] ? "second night of a back-to-back" : "not on a back-to-back"}, ${fx(i["t_g7"], 0)} games in the last 7 days. ${away.abbrev}: ${i["o_b2b"] ? "second night of a back-to-back" : "not on a back-to-back"}, ${fx(i["o_g7"], 0)} games in the last 7 days, ${fx((i["away:t_travel"] ?? 0) * 1000, 0)} km travelled.`;
    case "lineup_toi":
      return `Ice time of the lineup each team last dressed, against its usual lineup (minutes per game; negative means regulars were missing). ${home.abbrev} ${s(i["t_lu_toi"], 1)}, ${away.abbrev} ${s(i["o_lu_toi"], 1)}.`;
    case "lineup_gs":
      return `Game-score value of the lineup each team last dressed, against its usual lineup (negative means regulars were missing). ${home.abbrev} ${s(i["t_lu_gs"])}, ${away.abbrev} ${s(i["o_lu_gs"])}.`;
    default:
      return "";
  }
}

/** Diverging bars: what moves this forecast away from a league-average
 * matchup. Bars point toward the team the factor favours. */
export function DriverBars({ drivers, home, away }: { drivers: Driver[]; home: TeamBrief; away: TeamBrief }) {
  const rows = drivers.filter((d) => d.key !== "env").sort((a, b) => Math.abs(b.home_win_prob_effect) - Math.abs(a.home_win_prob_effect));
  const max = Math.max(0.02, ...rows.map((d) => Math.abs(d.home_win_prob_effect)));
  return (
    <div>
      <div className="mb-2 grid grid-cols-[1fr_1fr] text-[0.7rem] uppercase tracking-wider text-mute">
        <span>← favours {away.abbrev}</span>
        <span className="text-right">favours {home.abbrev} →</span>
      </div>
      <ul className="space-y-4">
        {rows.map((d) => {
          const v = d.home_win_prob_effect;
          const w = (Math.abs(v) / max) * 50;
          const fav = v >= 0 ? home : away;
          const tiny = Math.abs(v) < 0.0005;
          return (
            <li key={d.key}>
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-sm font-semibold text-ink">{d.label}</span>
                <span className="num text-sm text-ink">{tiny ? "no effect" : `${pts(v)} toward ${fav.abbrev}`}</span>
              </div>
              <div className="relative mt-1.5 h-3 rounded-sm bg-raised" role="img" aria-label={`${d.label}: ${tiny ? "no effect" : `${pts(v)} of win probability toward ${fav.name}`}`}>
                <span className="absolute inset-y-0 left-1/2 w-px bg-mute/60" aria-hidden />
                {!tiny && <span className={`grow-x absolute inset-y-0 ${v >= 0 ? "left-1/2 rounded-r-sm bg-home" : "right-1/2 origin-right rounded-l-sm bg-away"}`} style={{ width: `${w}%` }} />}
              </div>
              <p className="mt-1.5 text-xs leading-relaxed text-ink-2">
                {driverDetail(d, home, away)}{" "}
                <span className="text-mute">
                  Scoring-rate effect: {home.abbrev} {d.home_goal_effect >= 0 ? "+" : "−"}
                  {Math.abs(d.home_goal_effect * 100).toFixed(1)}%, {away.abbrev} {d.away_goal_effect >= 0 ? "+" : "−"}
                  {Math.abs(d.away_goal_effect * 100).toFixed(1)}%.
                </span>
              </p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export type CompareRow = { label: string; away: number | null; home: number | null; fmt: (v: number) => string; lowerBetter?: boolean; note?: string; text?: { away: string; home: string } };

/** Side-by-side comparison. Each metric is scaled to the larger of the two
 * values, so bars compare the teams, not different metrics. */
export function CompareBars({ rows, home, away }: { rows: CompareRow[]; home: TeamBrief; away: TeamBrief }) {
  return (
    <div className="panel overflow-hidden">
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 border-b border-line px-4 py-2.5 text-xs font-semibold uppercase tracking-wider text-ink-2">
        <span>{away.abbrev}</span>
        <span className="text-mute">Season to date</span>
        <span className="text-right">{home.abbrev}</span>
      </div>
      <ul>
        {rows.map((r) => {
          if (r.text) {
            return (
              <li key={r.label} className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 border-b border-line-soft px-4 py-2.5 last:border-0">
                <span className="num text-sm text-ink">{r.text.away}</span>
                <span className="w-40 text-center text-xs text-ink-2 sm:w-52">{r.label}</span>
                <span className="num text-right text-sm text-ink">{r.text.home}</span>
              </li>
            );
          }
          const a = r.away, h = r.home;
          const ok = a != null && h != null;
          const max = ok ? Math.max(Math.abs(a), Math.abs(h), 1e-9) : 1;
          const awayBetter = ok && (r.lowerBetter ? a < h : a > h);
          const homeBetter = ok && (r.lowerBetter ? h < a : h > a);
          return (
            <li key={r.label} className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 border-b border-line-soft px-4 py-2.5 last:border-0">
              <div className="flex items-center gap-2">
                <span className={`num w-14 shrink-0 text-sm ${awayBetter ? "font-semibold text-ink" : "text-ink-2"}`}>{a == null ? "—" : r.fmt(a)}</span>
                <span className="flex h-2 flex-1 justify-end rounded-sm bg-raised">
                  {ok && <span className={`rounded-sm ${awayBetter ? "bg-away" : "bg-away/40"}`} style={{ width: `${(Math.abs(a) / max) * 100}%` }} />}
                </span>
              </div>
              <span className="w-40 text-center text-xs text-ink-2 sm:w-52">
                {r.label}
                {r.lowerBetter && <span className="block text-[0.65rem] text-mute">lower is better</span>}
              </span>
              <div className="flex items-center gap-2">
                <span className="flex h-2 flex-1 rounded-sm bg-raised">
                  {ok && <span className={`rounded-sm ${homeBetter ? "bg-home" : "bg-home/40"}`} style={{ width: `${(Math.abs(h) / max) * 100}%` }} />}
                </span>
                <span className={`num w-14 shrink-0 text-right text-sm ${homeBetter ? "font-semibold text-ink" : "text-ink-2"}`}>{h == null ? "—" : r.fmt(h)}</span>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Reliability diagram: predicted home-win probability vs how often the home
 * team actually won. Points on the diagonal are perfectly calibrated. */
export function CalibrationChart({ bins, minN = 1 }: { bins: CalBin[]; minN?: number }) {
  const W = 360, H = 300, m = { l: 44, r: 12, t: 12, b: 38 };
  const x = (v: number) => m.l + v * (W - m.l - m.r);
  const y = (v: number) => H - m.b - v * (H - m.t - m.b);
  const pts = bins.filter((b) => b.n >= minN);
  const maxN = Math.max(...pts.map((b) => b.n), 1);
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-md" role="img" aria-label="Calibration: predicted probability against observed frequency">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={x(0)} x2={x(1)} y1={y(t)} y2={y(t)} stroke="var(--color-line)" strokeWidth="1" />
            <line x1={x(t)} x2={x(t)} y1={y(0)} y2={y(1)} stroke="var(--color-line-soft)" strokeWidth="1" />
            <text x={x(0) - 8} y={y(t) + 4} textAnchor="end" className="num" fontSize="10" fill="var(--color-mute)">{t * 100}%</text>
            <text x={x(t)} y={H - m.b + 16} textAnchor="middle" className="num" fontSize="10" fill="var(--color-mute)">{t * 100}%</text>
          </g>
        ))}
        <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="var(--color-mute)" strokeWidth="1" strokeDasharray="4 4" />
        <polyline points={pts.map((b) => `${x(b.mean_pred)},${y(b.observed)}`).join(" ")} fill="none" stroke="var(--color-home)" strokeWidth="2" />
        {pts.map((b) => (
          <circle key={b.lo} cx={x(b.mean_pred)} cy={y(b.observed)} r={4 + 5 * Math.sqrt(b.n / maxN)} fill="var(--color-home)" stroke="var(--color-surface)" strokeWidth="2">
            <title>{`Predicted ${pct(b.mean_pred, 1)} · observed ${pct(b.observed, 1)} · ${b.n.toLocaleString("en-US")} games`}</title>
          </circle>
        ))}
        <text x={(x(0) + x(1)) / 2} y={H - 4} textAnchor="middle" fontSize="11" fill="var(--color-ink-2)">Predicted home win probability</text>
        <text transform={`translate(12 ${(y(0) + y(1)) / 2}) rotate(-90)`} textAnchor="middle" fontSize="11" fill="var(--color-ink-2)">Home team actually won</text>
      </svg>
      <figcaption className="mt-1 text-xs text-mute">Dot size shows the number of games. The dashed line is perfect calibration.</figcaption>
    </figure>
  );
}

/** Accuracy by stated confidence: bars are observed accuracy, the tick is
 * the confidence the model claimed. */
export function ConfidenceChart({ bins }: { bins: ConfBin[] }) {
  return (
    <ul className="space-y-3">
      {bins.map((b) => (
        <li key={b.lo}>
          <div className="flex items-baseline justify-between text-xs">
            <span className="text-ink-2">
              Favourite at {Math.round(b.lo * 100)}–{b.hi >= 1 ? "100" : Math.round(b.hi * 100)}%
            </span>
            <span className="num text-ink">
              won {pct(b.accuracy, 1)} <span className="text-mute">· claimed {pct(b.mean_confidence, 1)} · {b.n.toLocaleString("en-US")} games</span>
            </span>
          </div>
          <div className="relative mt-1.5 h-2.5 rounded-sm bg-raised" role="img" aria-label={`Claimed ${pct(b.mean_confidence, 1)}, observed ${pct(b.accuracy, 1)} over ${b.n} games`}>
            <span className="absolute inset-y-0 left-0 rounded-sm bg-home" style={{ width: `${b.accuracy * 100}%` }} />
            <span className="absolute -inset-y-1 w-0.5 bg-ink" style={{ left: `${b.mean_confidence * 100}%` }} aria-hidden />
          </div>
        </li>
      ))}
      <li className="text-xs text-mute">The white tick marks the confidence the model stated; the bar is how often the favourite actually won.</li>
    </ul>
  );
}

/** Small-multiple style line chart of one metric by season for two models. */
export function SeasonLines({ seasons, series, fmt, lowerBetter }: { seasons: string[]; series: { name: string; values: (number | undefined)[]; color: string; dashed?: boolean }[]; fmt: (v: number) => string; lowerBetter?: boolean }) {
  const W = 560, H = 220, m = { l: 48, r: 110, t: 14, b: 30 };
  const all = series.flatMap((s) => s.values.filter((v): v is number => v != null));
  const lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.15 || 0.01;
  const x = (i: number) => m.l + (i / Math.max(1, seasons.length - 1)) * (W - m.l - m.r);
  const y = (v: number) => H - m.b - ((v - (lo - pad)) / (hi - lo + 2 * pad)) * (H - m.t - m.b);
  const ticks = [lo, (lo + hi) / 2, hi];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`${series.map((s) => s.name).join(" versus ")} by season${lowerBetter ? "; lower is better" : ""}`}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={m.l} x2={W - m.r} y1={y(t)} y2={y(t)} stroke="var(--color-line)" />
          <text x={m.l - 8} y={y(t) + 3.5} textAnchor="end" className="num" fontSize="10" fill="var(--color-mute)">{fmt(t)}</text>
        </g>
      ))}
      {seasons.map((s, i) => (
        <text key={s} x={x(i)} y={H - 10} textAnchor="middle" className="num" fontSize="10" fill="var(--color-mute)">{`'${String(s).slice(2)}`}</text>
      ))}
      {series.map((s) => {
        const pts = s.values.map((v, i) => (v == null ? null : [x(i), y(v)] as const)).filter((p): p is readonly [number, number] => p != null);
        const last = pts[pts.length - 1];
        return (
          <g key={s.name}>
            <polyline points={pts.map((p) => p.join(",")).join(" ")} fill="none" stroke={s.color} strokeWidth="2" strokeDasharray={s.dashed ? "5 4" : undefined} />
            {pts.map((p, i) => (
              <circle key={i} cx={p[0]} cy={p[1]} r="4" fill={s.color} stroke="var(--color-surface)" strokeWidth="2">
                <title>{`${s.name} · ${seasons[i]}: ${fmt(s.values[i] as number)}`}</title>
              </circle>
            ))}
            {last && <text x={last[0] + 10} y={last[1] + 4} fontSize="11" fill="var(--color-ink-2)">{s.name}</text>}
          </g>
        );
      })}
    </svg>
  );
}
