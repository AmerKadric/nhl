import Link from "next/link";

import { finalLabel, fx, onDark, pct, periodLabel, stampET, timeET } from "@/lib/format";
import type { Game } from "@/lib/types";

import { Chip, TeamLogo } from "./ui";

const LIVE = new Set(["LIVE", "CRIT"]);

function goalieChip(g: Game) {
  const s = g.forecast?.goalie_state;
  if (!s) return null;
  if (s === "confirmed") return <Chip tone="good">Goalies confirmed</Chip>;
  if (s === "partial") return <Chip tone="warn">One goalie confirmed</Chip>;
  return <Chip>Goalies projected</Chip>;
}

function statusChip(g: Game) {
  if (g.schedule_state === "PPD") return <Chip tone="warn">Postponed</Chip>;
  if (g.schedule_state === "CNCL") return <Chip tone="warn">Cancelled</Chip>;
  if (g.schedule_state === "SUSP") return <Chip tone="warn">Suspended</Chip>;
  if (LIVE.has(g.state) && g.live) {
    return (
      <Chip tone="live">
        <span className="live-dot h-1.5 w-1.5 rounded-full bg-bad" aria-hidden />
        LIVE · {g.live.in_intermission ? `End ${periodLabel(g.live.period, g.live.period_type)}` : `${periodLabel(g.live.period, g.live.period_type)} ${g.live.clock ?? ""}`}
      </Chip>
    );
  }
  if (g.result) return <Chip>{finalLabel(g.result.last_period_type)}</Chip>;
  if (g.state === "PRE") return <Chip tone="ice">Warm-up</Chip>;
  return null;
}

/** One matchup. The large numerals are the projected final score before the
 * game, and the observed score once it is live or final; the two are never
 * mixed without a label. */
export function GameCard({ g }: { g: Game }) {
  const f = g.forecast;
  const live = LIVE.has(g.state) && g.live;
  const observed = live ? { h: g.live!.home_score, a: g.live!.away_score } : g.result ? { h: g.result.home_score, a: g.result.away_score } : null;
  const off = g.schedule_state !== "OK";
  const big = observed ?? (f && !off ? { h: f.ml_home_goals, a: f.ml_away_goals } : null);
  const homeFav = f ? f.p_home_win >= 0.5 : null;
  const bigLabel = live ? "Live score" : g.result ? "Final score" : f && !off ? "Projected final" : null;
  const rows = [
    { t: g.away, score: big?.a, p: f?.p_away_win, lead: observed ? observed.a! > observed.h! : homeFav === false, side: "away" as const },
    { t: g.home, score: big?.h, p: f?.p_home_win, lead: observed ? observed.h! > observed.a! : homeFav === true, side: "home" as const },
  ];

  return (
    <Link
      href={`/game/${g.id}`}
      prefetch
      className="panel group relative block overflow-hidden p-4 transition-[transform,border-color,background-color] duration-150 hover:border-ice/40 hover:bg-raised active:scale-[0.985]"
      aria-label={`${g.away.name} at ${g.home.name}${f ? `, projected ${g.away.abbrev} ${f.ml_away_goals}, ${g.home.abbrev} ${f.ml_home_goals}` : ""}`}
    >
      <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: `linear-gradient(${onDark(g.away.color)} 0 50%, ${onDark(g.home.color)} 50% 100%)` }} />
      <div className="flex items-center justify-between gap-2 pl-1.5">
        <span className="num text-xs text-ink-2">{g.result || live ? stampDate(g) : timeET(g.start_time_utc)}</span>
        <span className="flex items-center gap-1.5">
          {statusChip(g)}
          {!g.result && !live && !off && goalieChip(g)}
        </span>
      </div>

      <div className="mt-3 space-y-2 pl-1.5">
        {bigLabel && <div className="eyebrow flex justify-end text-[0.62rem]">{bigLabel}</div>}
        {rows.map((r) => (
          <div key={r.side} className="flex items-center gap-3">
            <TeamLogo team={r.t} size={34} />
            <div className="min-w-0 flex-1">
              <div className={`truncate text-[0.95rem] font-semibold ${r.lead ? "text-ink" : "text-ink-2"}`}>{r.t.name}</div>
              <div className="text-[0.7rem] uppercase tracking-wider text-mute">{r.side}</div>
            </div>
            {r.score != null ? (
              <span className={`display w-10 text-right text-[2.6rem] font-bold leading-none ${r.lead ? (observed ? "text-ink" : "text-ice") : "text-ink-2/70"}`}>{r.score}</span>
            ) : (
              <span className="display w-10 text-right text-3xl leading-none text-mute">–</span>
            )}
          </div>
        ))}
      </div>

      {f && !off ? (
        <div className="mt-4 pl-1.5">
          <div className="flex items-center justify-between text-xs">
            <span className="num font-medium text-ink">
              {g.away.abbrev} {pct(f.p_away_win)}
            </span>
            <span className="text-mute">{observed ? "pre-game win probability" : "win probability"}</span>
            <span className="num font-medium text-ink">
              {pct(f.p_home_win)} {g.home.abbrev}
            </span>
          </div>
          <div className="mt-1.5 flex h-1.5 gap-[2px] overflow-hidden rounded-full" role="img" aria-label={`${g.away.abbrev} ${pct(f.p_away_win)}, ${g.home.abbrev} ${pct(f.p_home_win)}`}>
            <span className="rounded-l-full bg-away transition-[width] duration-500" style={{ width: `${f.p_away_win * 100}%` }} />
            <span className="rounded-r-full bg-home transition-[width] duration-500" style={{ width: `${f.p_home_win * 100}%` }} />
          </div>

          {observed ? (
            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-2">
              <span>
                Pre-game projection{" "}
                <span className="num text-ink">
                  {g.away.abbrev} {f.ml_away_goals}–{f.ml_home_goals} {g.home.abbrev}
                </span>
              </span>
              {g.evaluation && (
                <>
                  <Chip tone={g.evaluation.winner_correct ? "good" : "bad"}>{g.evaluation.winner_correct ? "✓ Winner" : "✕ Winner"}</Chip>
                  {g.evaluation.exact_score_hit && <Chip tone="good">✓ Exact score</Chip>}
                </>
              )}
            </div>
          ) : (
            <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
              <div>
                <dt className="text-mute">Expected goals</dt>
                <dd className="num text-ink">
                  {fx(f.exp_away_goals)} – {fx(f.exp_home_goals)}
                </dd>
              </div>
              <div>
                <dt className="text-mute">Total</dt>
                <dd className="num text-ink">{fx(f.expected_total, 1)}</dd>
              </div>
              <div>
                <dt className="text-mute">OT / SO</dt>
                <dd className="num text-ink">{pct(f.p_extra_time)}</dd>
              </div>
            </dl>
          )}
          <div className="mt-3 flex items-center justify-between border-t border-line-soft pt-2.5 text-[0.7rem] text-mute">
            <span className="text-ink-2">{f.confidence_label}</span>
            <span>
              Forecast v{f.version_no} · {stampET(f.published_at)}
            </span>
          </div>
        </div>
      ) : (
        <div className="mt-4 rounded-lg border border-dashed border-line px-3 py-2.5 pl-3 text-xs text-ink-2">
          {off ? "No active forecast: this game is not currently scheduled to be played." : "No forecast was published for this game."}
        </div>
      )}
    </Link>
  );
}

function stampDate(g: Game) {
  return timeET(g.start_time_utc);
}
