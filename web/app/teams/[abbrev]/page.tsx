import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { GameCard } from "@/components/GameCard";
import { RatingChart } from "@/components/interactive";
import { Chip, PageSkeleton, Section, Stat, TeamLogo, Unavailable } from "@/components/ui";
import { api } from "@/lib/api";
import { fx, onDark, pct, recordStr, seasonLabel, signed } from "@/lib/format";
import type { HistoryRow, RatingRanges, TeamPage } from "@/lib/types";

export const metadata: Metadata = { title: "Team intelligence" };

export default function Page({ params }: PageProps<"/teams/[abbrev]">) {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <Team params={params} />
    </Suspense>
  );
}

function RangeBar({ value, range, fmt, label, unit }: { value: number; range?: { min: number; max: number; median: number }; fmt: (v: number) => string; label: string; unit: string }) {
  const lo = range?.min ?? value - 1, hi = range?.max ?? value + 1;
  const pos = hi > lo ? ((value - lo) / (hi - lo)) * 100 : 50;
  const med = range && hi > lo ? ((range.median - lo) / (hi - lo)) * 100 : 50;
  return (
    <div className="panel px-4 py-3.5">
      <div className="eyebrow">{label}</div>
      <div className="num mt-1.5 text-2xl font-semibold text-ink">{fmt(value)}</div>
      <div className="text-xs text-ink-2">{unit}</div>
      <div className="relative mt-3 h-1.5 rounded-full bg-raised" role="img" aria-label={`${label} ${fmt(value)}; league range ${fmt(lo)} to ${fmt(hi)}`}>
        <span className="absolute -inset-y-1 w-px bg-mute" style={{ left: `${med}%` }} aria-hidden />
        <span className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-ice ring-2 ring-surface" style={{ left: `${Math.max(0, Math.min(100, pos))}%` }} aria-hidden />
      </div>
      <div className="num mt-1.5 flex justify-between text-[0.65rem] text-mute">
        <span>league low {fmt(lo)}</span>
        <span>high {fmt(hi)}</span>
      </div>
    </div>
  );
}

async function Team({ params }: { params: PageProps<"/teams/[abbrev]">["params"] }) {
  const { abbrev } = await params;
  if (!/^[A-Za-z]{2,4}$/.test(abbrev)) notFound();
  const d = await api<TeamPage>(`/v1/teams/${abbrev.toUpperCase()}`);
  if (!d) notFound();
  const { team: t, summary: s } = d;
  const r: RatingRanges = d.rating_ranges;
  const rt = s?.ratings;
  const color = onDark(t.color);
  const settled = d.forward_history.length > 0;

  return (
    <article>
      <nav aria-label="Breadcrumb" className="mb-4 text-xs text-mute">
        <Link href="/teams" className="hover:text-ice">Teams</Link> <span aria-hidden>/</span> <span className="text-ink-2">{t.name}</span>
      </nav>
      <header className="panel relative overflow-hidden px-5 py-6 sm:px-8">
        <div aria-hidden className="pointer-events-none absolute inset-0" style={{ background: `radial-gradient(600px 240px at 0% 50%, ${color}26, transparent 70%)` }} />
        <span aria-hidden className="absolute inset-y-0 left-0 w-1" style={{ background: color }} />
        <div className="relative flex flex-wrap items-center gap-5">
          <TeamLogo team={t} size={88} />
          <div>
            <div className="eyebrow">{[t.conference, t.division].filter(Boolean).join(" · ")} · {seasonLabel(d.season)}</div>
            <h1 className="display text-4xl font-bold leading-tight tracking-wide sm:text-5xl">{t.name}</h1>
            {s && (
              <div className="num mt-1 flex flex-wrap gap-x-5 gap-y-1 text-sm text-ink-2">
                <span><span className="text-ink">{recordStr(s.record)}</span> record</span>
                <span><span className="text-ink">{s.points}</span> points ({pct(s.point_pct, 1)})</span>
                <span>home <span className="text-ink">{recordStr(s.home)}</span></span>
                <span>road <span className="text-ink">{recordStr(s.away)}</span></span>
                <span><span className="text-ink">{d.games_remaining}</span> games remaining</span>
              </div>
            )}
          </div>
          {s && (
            <div className="ml-auto flex gap-1" aria-label={`Last ten results: ${s.last10.results.join(", ")}`}>
              {s.last10.results.map((x, i) => (
                <span key={i} className={`num flex h-7 w-7 items-center justify-center rounded text-[0.65rem] font-semibold ${x === "W" ? "bg-good/20 text-good" : x === "L" ? "bg-bad/15 text-bad" : "bg-raised text-ink-2"}`}>
                  {x === "OTL" ? "OT" : x}
                </span>
              ))}
            </div>
          )}
        </div>
      </header>

      {!s ? (
        <div className="mt-8"><Unavailable title="No games played yet this season." /></div>
      ) : (
        <>
          <Section id="ratings" eyebrow="Model ratings" title="How the model rates this team" note="Each rating is in real units against a league-average team, adjusted for opponent strength and home ice. The marker shows where the team sits in the current league range; the tick is the league median.">
            {rt ? (
              <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
                <RangeBar label="Team strength" value={rt.elo} range={r.elo} fmt={(v) => v.toFixed(0)} unit="Elo, 1500 = average" />
                <RangeBar label="Offence" value={rt.off_g} range={r.off_g} fmt={(v) => signed(v)} unit="goals scored / game vs average" />
                <RangeBar label="Defence" value={rt.def_g} range={r.def_g} fmt={(v) => signed(v)} unit="goals prevented / game vs average" />
                <RangeBar label="Chance creation" value={rt.off_xg} range={r.off_xg} fmt={(v) => signed(v)} unit="5-on-5 xG / 60 vs average" />
                <RangeBar label="Chance suppression" value={rt.def_xg} range={r.def_xg} fmt={(v) => signed(v)} unit="5-on-5 xG / 60 prevented" />
              </div>
            ) : <Unavailable title="Ratings are not available yet." />}
            <p className="mt-2 text-xs text-mute">The five-on-five chance ratings are shown for context; the forecast itself uses all-situations chance ratings, the goals ratings and Elo.</p>
          </Section>

          <Section id="trend" eyebrow="Trend" title="Rating through the season">
            <div className="panel p-5">
              <RatingChart history={d.rating_history} color={color} />
            </div>
          </Section>

          <Section id="xg" eyebrow="Expected goals" title="Underlying play" note={`From ${s.xg_games} of ${s.gp} games with shot data.`}>
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
              <Stat label="xG for / 60" value={fx(s.xgf60_5v5)} sub="5-on-5" />
              <Stat label="xG against / 60" value={fx(s.xga60_5v5)} sub="5-on-5 · lower is better" />
              <Stat label="xG share" value={pct(s.xg_share_5v5, 1)} sub="5-on-5" />
              <Stat label="Power play" value={fx(s.pp_xg60)} sub={`xG / 60 · ${fx(s.pp_g60)} goals / 60`} />
              <Stat label="Penalty kill" value={fx(s.pk_xga60)} sub={`xG against / 60 · ${fx(s.pk_ga60)} goals / 60`} />
              <Stat label="Finishing" value={signed(s.goals_above_expected, 1)} sub="goals scored above expected" />
            </div>
            <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-6">
              <Stat label="Goals / game" value={fx(s.gf_pg)} />
              <Stat label="Against / game" value={fx(s.ga_pg)} />
              <Stat label="Shooting %" value={pct(s.sh_pct, 1)} />
              <Stat label="Save %" value={s.sv_pct != null ? s.sv_pct.toFixed(3).replace(/^0/, "") : "—"} />
              <Stat label="Goaltending" value={signed(s.goals_saved_above_expected, 1)} sub="goals saved above expected" />
              <Stat label="Last 10" value={recordStr(s.last10)} sub={`${pct(s.last10.xg_share_5v5, 1)} xG share`} />
            </div>
          </Section>

          <Section id="goalies" eyebrow="Goaltending" title="Goalies this season">
            <div className="panel overflow-x-auto">
              <table className="w-full min-w-[620px] text-sm">
                <thead className="text-left text-xs uppercase tracking-wider text-mute">
                  <tr className="border-b border-line">
                    {["Goalie", "Starts", "Save %", "GSAx", "GSAx / 60", "Last 5 GSAx", "Last game"].map((h) => (
                      <th key={h} className="px-4 py-2.5 font-semibold">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {d.goalies.map((x) => (
                    <tr key={x.pid} className="border-b border-line-soft last:border-0">
                      <td className="px-4 py-2.5 font-medium text-ink">{x.name}</td>
                      <td className="num px-4 py-2.5 text-ink-2">{x.season?.gs ?? "—"}</td>
                      <td className="num px-4 py-2.5 text-ink">{x.season?.sv_pct != null ? x.season.sv_pct.toFixed(3).replace(/^0/, "") : "—"}</td>
                      <td className="num px-4 py-2.5 text-ink">{signed(x.season?.gsax, 1)}</td>
                      <td className="num px-4 py-2.5 text-ink-2">{signed(x.season?.gsax_per_60, 2)}</td>
                      <td className="num px-4 py-2.5 text-ink-2">{signed(x.last5?.gsax, 1)}</td>
                      <td className="num px-4 py-2.5 text-ink-2">{x.last_game}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        </>
      )}

      <Section id="upcoming" eyebrow="Schedule" title="Upcoming games and forecasts">
        {d.upcoming.length ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {d.upcoming.map((g) => (
              <div key={g.id}>
                <div className="num mb-1.5 text-xs text-mute">{g.date}</div>
                <GameCard g={g} />
              </div>
            ))}
          </div>
        ) : <Unavailable title="No games scheduled in the next three weeks." />}
      </Section>

      <Section
        id="history"
        eyebrow="Prediction history"
        title="Past forecasts for this team"
        note={settled ? "Forecasts published before each game, with the result." : "No published forecast for this team has been settled yet, so this table shows the backtest simulation instead."}
        aside={settled ? <Chip tone="ice">Published forecasts</Chip> : <Chip tone="warn">Backtest simulation</Chip>}
      >
        <HistoryTable rows={settled ? d.forward_history : d.backtest_history} abbrev={t.abbrev} />
      </Section>
    </article>
  );
}

function HistoryTable({ rows, abbrev }: { rows: HistoryRow[]; abbrev: string }) {
  if (!rows.length) return <Unavailable title="Nothing to show yet." />;
  return (
    <div className="panel overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead className="text-left text-xs uppercase tracking-wider text-mute">
          <tr className="border-b border-line">
            {["Date", "Opponent", `${abbrev} win chance`, "Projected", "Actual", "Winner", "Exact"].map((h) => (
              <th key={h} className="px-4 py-2.5 font-semibold">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const isHome = r.home.abbrev === abbrev;
            const opp = isHome ? r.away : r.home;
            const p = isHome ? r.p_home_win : 1 - r.p_home_win;
            const us = (h: number, a: number) => (isHome ? `${h}–${a}` : `${a}–${h}`);
            return (
              <tr key={r.game_id} className="border-b border-line-soft last:border-0 hover:bg-raised/60">
                <td className="num px-4 py-2 text-ink-2">{r.date}</td>
                <td className="px-4 py-2">
                  <Link href={`/game/${r.game_id}`} className="flex items-center gap-2 text-ink hover:text-ice">
                    <span className="text-mute">{isHome ? "vs" : "@"}</span> <TeamLogo team={opp} size={18} /> {opp.abbrev}
                  </Link>
                </td>
                <td className="num px-4 py-2 text-ink">{pct(p, 1)}</td>
                <td className="num px-4 py-2 text-ink-2">{us(r.ml_home_goals, r.ml_away_goals)}</td>
                <td className="num px-4 py-2 font-semibold text-ink">{us(r.home_score, r.away_score)}{r.last_period_type && r.last_period_type !== "REG" ? <span className="ml-1 font-normal text-mute">{r.last_period_type}</span> : null}</td>
                <td className="px-4 py-2"><span className={r.winner_correct ? "text-good" : "text-bad"}>{r.winner_correct ? "✓ correct" : "✕ wrong"}</span></td>
                <td className="px-4 py-2">{r.exact_score_hit ? <span className="text-good">✓ hit</span> : <span className="text-mute">miss</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
