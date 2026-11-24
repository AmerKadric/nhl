import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { CompareBars, type CompareRow, DriverBars, OutcomeBar } from "@/components/charts";
import { DistBars, ScoreMatrix } from "@/components/interactive";
import { Lab } from "@/components/Lab";
import { GameStakes } from "@/components/Stakes";
import { LocalTime } from "@/components/LocalTime";
import { Chip, PageSkeleton, Section, Stat, TeamLogo, Unavailable } from "@/components/ui";
import { apiCached } from "@/lib/api";
import { dayLabel, finalLabel, fx, onDark, pct, periodLabel, recordStr, signed, stampET } from "@/lib/format";
import type { GameDetail, GoalieMatchup, LineupWatch, TeamBrief } from "@/lib/types";

export const metadata: Metadata = { title: "Matchup intelligence" };

export default function Page({ params }: PageProps<"/game/[id]">) {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <Matchup params={params} />
    </Suspense>
  );
}

async function Matchup({ params }: { params: PageProps<"/game/[id]">["params"] }) {
  const { id } = await params;
  if (!/^\d{6,12}$/.test(id)) notFound();
  const d = await apiCached<GameDetail>(`/v1/games/${id}`, "live");
  if (!d) notFound();
  const { game: g, forecast: f } = d;
  const { home, away } = g;
  const live = (g.state === "LIVE" || g.state === "CRIT") && g.live;
  const observed = live ? { h: g.live!.home_score ?? 0, a: g.live!.away_score ?? 0 } : g.result ? { h: g.result.home_score, a: g.result.away_score } : null;

  return (
    <article>
      <nav aria-label="Breadcrumb" className="mb-4 text-xs text-mute">
        <Link href="/" className="hover:text-ice">Games</Link> <span aria-hidden>/</span> <span className="text-ink-2">{away.abbrev} at {home.abbrev}</span>
      </nav>

      {/* A — predicted score */}
      <header className="panel rink relative overflow-hidden px-4 py-7 sm:px-8">
        <div aria-hidden className="pointer-events-none absolute inset-0" style={{ background: `radial-gradient(520px 260px at 0% 50%, ${onDark(away.color)}22, transparent 70%), radial-gradient(520px 260px at 100% 50%, ${onDark(home.color)}22, transparent 70%)` }} />
        <div className="relative">
          <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1.5 text-xs text-ink-2">
            <span className="num">
              {dayLabel(g.date)} · <LocalTime iso={g.start_time_utc} />
            </span>
            {g.venue && <span>· {g.venue}</span>}
            {g.game_type === 3 && <Chip tone="ice">Playoffs</Chip>}
            {g.schedule_state !== "OK" && <Chip tone="warn">{g.schedule_state === "PPD" ? "Postponed" : g.schedule_state}</Chip>}
            {live && (
              <Chip tone="live">
                <span className="live-dot h-1.5 w-1.5 rounded-full bg-bad" aria-hidden />
                LIVE · {periodLabel(g.live!.period, g.live!.period_type)} {g.live!.in_intermission ? "intermission" : g.live!.clock}
              </Chip>
            )}
            {g.result && <Chip>{finalLabel(g.result.last_period_type)}</Chip>}
          </div>

          <div className="mt-6 grid grid-cols-[1fr_auto_1fr] items-center gap-3 sm:gap-8">
            <TeamBlock team={away} side="Away" record={recordStr(d.comparison.away?.record)} align="right" />
            <div className="text-center">
              <div className="eyebrow">{observed ? (live ? "Live score" : "Final score") : f ? "Projected final" : "No forecast"}</div>
              <div className="display mt-1 flex items-center justify-center gap-3 text-[4.2rem] font-bold leading-none sm:gap-5 sm:text-[6.5rem]">
                <span className={scoreTone(observed ? observed.a > observed.h : !!f && f.p_away_win > 0.5, !!observed)}>{observed ? observed.a : f ? f.ml_away_goals : "–"}</span>
                <span className="text-mute/60">:</span>
                <span className={scoreTone(observed ? observed.h > observed.a : !!f && f.p_home_win >= 0.5, !!observed)}>{observed ? observed.h : f ? f.ml_home_goals : "–"}</span>
              </div>
              {f && !observed && <div className="num mt-2 text-xs text-ink-2">most likely exact score · {pct(f.ml_score_prob, 1)} chance</div>}
              {f && observed && (
                <div className="num mt-2 text-xs text-ink-2">
                  pre-game projection {away.abbrev} {f.ml_away_goals}–{f.ml_home_goals} {home.abbrev}
                </div>
              )}
            </div>
            <TeamBlock team={home} side="Home" record={recordStr(d.comparison.home?.record)} align="left" />
          </div>

          {f && (
            <div className="mx-auto mt-7 max-w-3xl">
              <div className="flex items-end justify-between">
                <div>
                  <div className="num text-3xl font-semibold">{pct(f.p_away_win, 1)}</div>
                  <div className="text-xs text-ink-2">{away.abbrev} win</div>
                </div>
                <div className="pb-1 text-center text-xs text-mute">
                  {observed ? "pre-game " : ""}win probability
                  <div className="text-ink-2">{f.confidence.label}</div>
                </div>
                <div className="text-right">
                  <div className="num text-3xl font-semibold">{pct(f.p_home_win, 1)}</div>
                  <div className="text-xs text-ink-2">{home.abbrev} win</div>
                </div>
              </div>
              <div className="mt-2 flex h-2.5 gap-[2px] overflow-hidden rounded-full" role="img" aria-label={`${away.name} ${pct(f.p_away_win, 1)}, ${home.name} ${pct(f.p_home_win, 1)}`}>
                <span className="grow-x rounded-l-full bg-away" style={{ width: `${f.p_away_win * 100}%` }} />
                <span className="rounded-r-full bg-home" style={{ width: `${f.p_home_win * 100}%` }} />
              </div>
              {f.headline && <p className="mt-4 text-center text-sm text-ink">{f.headline}</p>}
              <div className="num mt-4 flex flex-wrap justify-center gap-x-6 gap-y-1 text-xs text-ink-2">
                <span>expected goals <span className="text-ink">{fx(f.exp_away_goals)} – {fx(f.exp_home_goals)}</span></span>
                <span>expected total <span className="text-ink">{fx(f.expected_total, 1)}</span></span>
                <span>forecast v{f.version_no} · {stampET(f.published_at)}</span>
                <span>{f.model_version}</span>
              </div>
            </div>
          )}
        </div>
      </header>

      {g.evaluation && f && (
        <div className="mt-4 flex flex-wrap items-center gap-2 text-sm text-ink-2">
          <span>How the published forecast did:</span>
          <Chip tone={g.evaluation.winner_correct ? "good" : "bad"}>{g.evaluation.winner_correct ? "✓ Picked the winner" : "✕ Missed the winner"}</Chip>
          <Chip tone={g.evaluation.exact_score_hit ? "good" : "neutral"}>{g.evaluation.exact_score_hit ? "✓ Exact score" : "Exact score missed"}</Chip>
          {g.evaluation.p_actual_score != null && (
            <span className="num text-xs">the actual score was given a {pct(g.evaluation.p_actual_score, 1)} chance</span>
          )}
          {g.evaluation.provisional && <span className="text-xs text-mute">Provisional: not yet in the track record.</span>}
        </div>
      )}

      {!f ? (
        <div className="mt-8">
          <Unavailable title="No forecast was published for this game.">
            PUCKIQ only shows forecasts that were generated by the model and stored before puck drop. Team statistics are shown below.
          </Unavailable>
        </div>
      ) : (
        <>
          {!observed && (
            <div role="note" className="mt-4 rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm">
              <strong className="font-semibold">Starting goalies are projected, not confirmed.</strong>{" "}
              <span className="text-ink-2">
                This forecast averages over the likely starters:{" "}
                {(["away", "home"] as const).map((s, i) => (
                  <span key={s}>
                    {i > 0 && "; "}
                    {(s === "home" ? home : away).abbrev}{" "}
                    {f.goalies[s].map((x) => `${x.name} ${pct(x.prob)}`).join(", ")}
                  </span>
                ))}
                . The NHL&apos;s public feed does not name starters before puck drop, so the forecast stays an average.
              </span>
            </div>
          )}

          {/* B — outcome probability */}
          <Section id="outcomes" eyebrow="Outcome probability" title="How the game ends" note="Regulation, overtime and shootout are modelled separately, the way the game is actually decided.">
            <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
              <div className="panel p-5">
                <OutcomeBar home={home} away={away} pHomeReg={f.p_home_reg} pAwayReg={f.p_away_reg} pHomeExtra={Math.max(0, f.p_home_win - f.p_home_reg)} pAwayExtra={Math.max(0, f.p_away_win - f.p_away_reg)} />
                <dl className="mt-5 grid grid-cols-3 gap-3 border-t border-line-soft pt-4 text-sm">
                  <div><dt className="text-xs text-mute">Level after regulation</dt><dd className="num text-lg text-ink">{pct(f.p_tie_reg, 1)}</dd></div>
                  <div><dt className="text-xs text-mute">Decided in overtime</dt><dd className="num text-lg text-ink">{pct(f.p_overtime, 1)}</dd></div>
                  <div><dt className="text-xs text-mute">Decided in a shootout</dt><dd className="num text-lg text-ink">{g.game_type === 3 ? "n/a" : pct(f.p_shootout, 1)}</dd></div>
                </dl>
              </div>
              <div className="panel p-5">
                <div className="eyebrow mb-2">Final margin</div>
                <DistBars
                  unit="margin"
                  data={[-5, -4, -3, -2, -1, 1, 2, 3, 4, 5].map((k) => ({
                    label: k < 0 ? `${away.abbrev} +${-k}` : `${home.abbrev} +${k}`,
                    p: f.diffs[String(k)] ?? 0,
                    tone: k < 0 ? ("away" as const) : ("home" as const),
                  }))}
                />
                <p className="mt-2 text-xs text-mute">Hover or tab through the bars. Margins beyond five goals are omitted.</p>
              </div>
            </div>
          </Section>

          {d.stakes && (
            <Section
              id="stakes"
              eyebrow="Playoff race"
              title="What this game means"
              note="Each team's chance of making the playoffs in the simulated seasons where it wins this game, against those where it loses. From the daily season simulation; swings within its noise are not reported."
              aside={<Link href="/season#stakes" className="text-sm text-ice hover:underline">All of the day&apos;s games</Link>}
            >
              <GameStakes g={d.stakes} />
            </Section>
          )}

          {/* C — why */}
          <Section
            id="why"
            eyebrow="Explanation"
            title="Why the model thinks this"
            note="Each bar is the change in win probability when that factor alone is reset to league average. These are properties of the model's inputs, not proof of cause, and they are measured one at a time, so they do not add up exactly."
          >
            <div className="panel p-5">
              <DriverBars drivers={f.drivers} home={home} away={away} />
              <p className="mt-5 border-t border-line-soft pt-3 text-xs text-mute">
                Not in the model: injury reports and tonight&apos;s confirmed lineup (no licensed source; it sees only who dressed last game), and separate special-teams or five-on-five
                ratings, which were tested and did not improve out-of-sample forecasts. See <Link href="/methodology" className="text-ice hover:underline">the method</Link>.
              </p>
            </div>
          </Section>

          {/* D — team comparison */}
          <Section id="teams" eyebrow="Team comparison" title="Tale of the tape" note="Descriptive season statistics. The model itself uses opponent-adjusted ratings built from these, listed at the bottom.">
            <TeamCompare d={d} />
          </Section>

          {/* E — goalies */}
          <Section id="goalies" eyebrow="Goalie matchup" title="In the crease" note="Goals saved above expected (GSAx) compares goals allowed with the quality of shots faced.">
            <div className="grid gap-4 md:grid-cols-2">
              {(["away", "home"] as const).map((s) => (
                <GoaliePanel key={s} team={s === "home" ? home : away} goalies={d.goalie_matchup?.[s] ?? []} effect={goalieEffect(f, s)} opp={s === "home" ? away : home} />
              ))}
            </div>
          </Section>

          {/* lineup watch */}
          {f.lineup_watch && (
            <Section
              id="lineups"
              eyebrow="Lineup watch"
              title="Regulars who sat out last game"
              note="Skaters who usually dress but were not in their team's most recent lineup, from NHL box scores. This is not an injury report: PUCKIQ does not know why a player sat out or whether he returns tonight."
            >
              <div className="grid gap-4 md:grid-cols-2">
                {(["away", "home"] as const).map((s) => (
                  <LineupPanel key={s} team={s === "home" ? home : away} watch={f.lineup_watch![s]} />
                ))}
              </div>
            </Section>
          )}

          {/* F — matrix */}
          <Section id="matrix" eyebrow="Score probability matrix" title="Every final score" note="Hover, tap or tab to a cell to read the chance of that exact final score.">
            <div className="panel p-5">
              <ScoreMatrix matrix={f.matrix.final} overflow={f.matrix.overflow} homeAbbrev={home.abbrev} awayAbbrev={away.abbrev} />
            </div>
          </Section>

          {/* G — simulated outcomes */}
          <Section id="sims" eyebrow="Simulated outcomes" title={`${f.n_sims.toLocaleString("en-US")} simulated games`} note={`Each simulation draws the starting goalies, the scoring rates and the score, then resolves overtime or a shootout. Monte Carlo standard error of the win probability: ±${(f.confidence.mc_standard_error * 100).toFixed(1)} points.`}>
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="panel p-5">
                <div className="eyebrow mb-3">Five most frequent final scores</div>
                <ol className="space-y-2.5">
                  {f.top_scores.map((t, i) => (
                    <li key={i} className="grid grid-cols-[7.5rem_1fr_3.2rem] items-center gap-3">
                      <span className="num text-sm text-ink">{away.abbrev} {t.away}–{t.home} {home.abbrev}</span>
                      <span className="h-2.5 rounded-sm bg-raised">
                        <span className={`grow-x block h-full rounded-sm ${t.home > t.away ? "bg-home" : "bg-away"}`} style={{ width: `${(t.p / f.top_scores[0].p) * 100}%` }} />
                      </span>
                      <span className="num text-right text-sm text-ink">{pct(t.p, 1)}</span>
                    </li>
                  ))}
                </ol>
                <p className="mt-4 text-xs text-mute">
                  Even the most likely exact score is rare. These five together cover {pct(f.top_scores.reduce((s, t) => s + t.p, 0))} of outcomes.
                </p>
              </div>
              <div className="panel p-5">
                <div className="eyebrow mb-2">Total goals</div>
                <DistBars unit="total goals" data={f.totals.slice(0, 13).map((p, k) => ({ label: String(k), p, tone: "neutral" as const }))} highlight={String(f.totals.indexOf(Math.max(...f.totals)))} />
              </div>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-5">
              <Stat label="Expected total" value={fx(f.expected_total, 2)} sub={`80% range ${fx(f.extras.total_p10, 0)}–${fx(f.extras.total_p90, 0)} goals`} />
              <Stat label="High scoring" value={pct(f.extras.p_total_ge_7, 1)} sub="7 or more total goals" />
              <Stat label="Low scoring" value={pct(f.extras.p_total_le_4, 1)} sub="4 or fewer total goals" />
              <Stat label="One-goal game" value={pct(f.extras.p_one_goal_game, 1)} sub="includes every OT and SO result" />
              <Stat label="Shutout" value={pct(f.extras.p_shutout, 1)} sub="either team held to zero" />
            </div>
          </Section>

          {/* Lab */}
          <Section id="lab" eyebrow="Prediction Lab" title="Change the assumptions" note="Re-run this matchup with a different starting goalie or schedule situation. Only inputs the model was validated on can be changed.">
            {d.lab ? <Lab gameId={g.id} lab={d.lab} home={home} away={away} /> : <Unavailable title="The lab is closed for this game.">Scenarios can be run until the game is final.</Unavailable>}
          </Section>

          {/* versions */}
          <Section id="versions" eyebrow="Forecast history" title="Every version, as published" note="Forecasts are never edited. When inputs change, a new version is added and the earlier ones stay on the record.">
            <div className="panel overflow-x-auto">
              <table className="w-full min-w-[640px] text-sm">
                <thead className="text-left text-xs uppercase tracking-wider text-mute">
                  <tr className="border-b border-line">
                    {["Version", "Published", "Why", `${home.abbrev} win`, "Projected", "Expected goals", "Result"].map((h) => (
                      <th key={h} className="px-4 py-2.5 font-semibold">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...d.versions].reverse().map((v) => (
                    <tr key={v.version_no} className="border-b border-line-soft last:border-0">
                      <td className="num px-4 py-2.5 text-ink">v{v.version_no}{v.is_latest && <span className="ml-2 text-xs text-ice">latest</span>}</td>
                      <td className="num px-4 py-2.5 text-ink-2">{stampET(v.published_at)}</td>
                      <td className="px-4 py-2.5 text-ink-2">{v.reason === "initial" ? "First forecast" : v.reason === "model_updated" ? "Model updated" : "New data"}</td>
                      <td className="num px-4 py-2.5 text-ink">{pct(v.p_home_win, 1)}</td>
                      <td className="num px-4 py-2.5 text-ink">{away.abbrev} {v.ml_away_goals}–{v.ml_home_goals} {home.abbrev}</td>
                      <td className="num px-4 py-2.5 text-ink-2">{fx(v.exp_away_goals)} – {fx(v.exp_home_goals)}</td>
                      <td className="px-4 py-2.5">{v.evaluation ? <Chip tone={v.evaluation.winner_correct ? "good" : "bad"}>{v.evaluation.winner_correct ? "✓ winner" : "✕ winner"}</Chip> : <span className="text-mute">pending</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        </>
      )}
      {!f && (
        <Section id="teams" eyebrow="Team comparison" title="Tale of the tape">
          <TeamCompare d={d} />
        </Section>
      )}
    </article>
  );
}

const scoreTone = (lead: boolean, observed: boolean) => (lead ? (observed ? "text-ink" : "text-ice") : "text-ink-2/60");

function TeamBlock({ team, side, record, align }: { team: TeamBrief; side: string; record: string; align: "left" | "right" }) {
  return (
    <Link href={`/teams/${team.abbrev}`} className={`group flex flex-col items-center gap-2 sm:flex-row sm:gap-4 ${align === "right" ? "sm:flex-row-reverse sm:text-right" : ""}`}>
      <TeamLogo team={team} size={76} />
      <div className="text-center sm:text-inherit">
        <div className="eyebrow">{side}</div>
        <div className="display text-xl font-bold leading-tight tracking-wide group-hover:text-ice sm:text-3xl">
          <span className="sm:hidden">{team.abbrev}</span>
          <span className="hidden sm:inline">{team.name}</span>
        </div>
        <div className="num text-xs text-ink-2">{record}</div>
      </div>
    </Link>
  );
}

function goalieEffect(f: NonNullable<GameDetail["forecast"]>, side: "home" | "away") {
  const d = f.drivers.find((x) => x.key === "goalie");
  if (!d) return null;
  // the home goalie changes the away team's scoring rate, and vice versa
  return side === "home" ? d.away_goal_effect : d.home_goal_effect;
}

function GoaliePanel({ team, goalies, effect, opp }: { team: TeamBrief; goalies: GoalieMatchup[]; effect: number | null; opp: TeamBrief }) {
  return (
    <div className="panel p-5">
      <div className="flex items-center gap-3">
        <TeamLogo team={team} size={30} />
        <div className="display text-lg font-semibold tracking-wide">{team.name}</div>
      </div>
      {goalies.length === 0 || goalies[0].pid === 0 ? (
        <p className="mt-4 text-sm text-ink-2">No goalie usage history is available for this team, so the model assumes a league-average starter.</p>
      ) : (
        <ul className="mt-4 space-y-4">
          {goalies.map((x) => (
            <li key={x.pid}>
              <div className="flex items-center justify-between gap-3">
                <span className="font-semibold text-ink">{x.name}</span>
                <Chip>{pct(x.prob)} to start · projected</Chip>
              </div>
              <div className="mt-1.5 h-1.5 rounded-full bg-raised">
                <div className="h-full rounded-full bg-ice/80" style={{ width: `${x.prob * 100}%` }} />
              </div>
              <dl className="num mt-2.5 grid grid-cols-4 gap-2 text-xs">
                <div><dt className="font-sans text-mute">Starts</dt><dd className="text-ink">{x.season?.gs ?? "—"}</dd></div>
                <div><dt className="font-sans text-mute">Save %</dt><dd className="text-ink">{x.season?.sv_pct != null ? x.season.sv_pct.toFixed(3).replace(/^0/, "") : "—"}</dd></div>
                <div><dt className="font-sans text-mute">GSAx</dt><dd className="text-ink">{signed(x.season?.gsax, 1)}</dd></div>
                <div><dt className="font-sans text-mute">Last 5 GSAx</dt><dd className="text-ink">{signed(x.last5?.gsax, 1)}</dd></div>
              </dl>
              <div className="mt-1.5 text-xs text-mute">
                Model rating {signed(x.rating * 100, 1)}% (goals saved per expected goal, shrunk toward average; based on {fx(x.xga_n, 0)} expected goals faced, recent games weighted more).
              </div>
            </li>
          ))}
        </ul>
      )}
      {effect != null && (
        <p className="mt-4 border-t border-line-soft pt-3 text-xs text-ink-2">
          Estimated effect on this game: {team.abbrev}&apos;s expected goaltending changes {opp.abbrev}&apos;s scoring rate by{" "}
          <span className="num text-ink">{signed(effect * 100, 1)}%</span> relative to a typical NHL starter.
        </p>
      )}
    </div>
  );
}

function LineupPanel({ team, watch }: { team: TeamBrief; watch: LineupWatch }) {
  return (
    <div className="panel p-5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <TeamLogo team={team} size={30} />
          <div className="display text-lg font-semibold tracking-wide">{team.name}</div>
        </div>
        {watch.as_of && <span className="num text-xs text-mute">last game {watch.as_of}</span>}
      </div>
      {!watch.available ? (
        <p className="mt-4 text-sm text-ink-2">Lineup data is not available for this team.</p>
      ) : watch.absent.length === 0 ? (
        <p className="mt-4 text-sm text-ink-2">Every regular skater dressed in the last game.</p>
      ) : (
        <ul className="mt-4 divide-y divide-line-soft">
          {watch.absent.map((a) => (
            <li key={a.pid} className="flex items-center justify-between gap-3 py-2 text-sm">
              <span>
                <span className="font-medium text-ink">{a.name}</span> <span className="text-xs text-mute">{a.defence ? "defence" : "forward"}</span>
              </span>
              <span className="num text-xs text-ink-2">
                {a.toi.toFixed(1)} min/game · dressed {Math.round(a.dress_rate * 100)}% recently
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TeamCompare({ d }: { d: GameDetail }) {
  const { home: h, away: a } = d.comparison;
  const { home, away } = d.game;
  if (!h || !a) return <Unavailable title="Season statistics are not available yet for one of these teams." />;
  const f2 = (v: number) => v.toFixed(2);
  const p1 = (v: number) => `${(v * 100).toFixed(1)}%`;
  const rest = d.lab?.current;
  const rows: CompareRow[] = [
    { label: "Record", away: null, home: null, fmt: f2, text: { away: `${recordStr(a.record)} · ${a.points} pts`, home: `${recordStr(h.record)} · ${h.points} pts` } },
    { label: "Goals for per game", away: a.gf_pg, home: h.gf_pg, fmt: f2 },
    { label: "Goals against per game", away: a.ga_pg, home: h.ga_pg, fmt: f2, lowerBetter: true },
    { label: "Expected goals for / 60, 5-on-5", away: a.xgf60_5v5, home: h.xgf60_5v5, fmt: f2 },
    { label: "Expected goals against / 60, 5-on-5", away: a.xga60_5v5, home: h.xga60_5v5, fmt: f2, lowerBetter: true },
    { label: "Share of expected goals, 5-on-5", away: a.xg_share_5v5, home: h.xg_share_5v5, fmt: p1 },
    { label: "Power play: expected goals / 60", away: a.pp_xg60, home: h.pp_xg60, fmt: f2 },
    { label: "Penalty kill: expected goals against / 60", away: a.pk_xga60, home: h.pk_xga60, fmt: f2, lowerBetter: true },
    { label: "Shooting %", away: a.sh_pct, home: h.sh_pct, fmt: p1 },
    { label: "Team save %", away: a.sv_pct, home: h.sv_pct, fmt: (v) => v.toFixed(3).replace(/^0/, "") },
    { label: "Last 10", away: null, home: null, fmt: f2, text: { away: `${recordStr(a.last10)} · ${a.last10.gf_pg.toFixed(1)} GF, ${a.last10.ga_pg.toFixed(1)} GA`, home: `${recordStr(h.last10)} · ${h.last10.gf_pg.toFixed(1)} GF, ${h.last10.ga_pg.toFixed(1)} GA` } },
    { label: "Venue split", away: null, home: null, fmt: f2, text: { away: `${recordStr(a.away)} on the road`, home: `${recordStr(h.home)} at home` } },
  ];
  if (rest) rows.push({ label: "Days since last game", away: null, home: null, fmt: f2, text: { away: `${fx(rest.away_rest_days, 0)}${rest.away_b2b ? " · back-to-back" : ""}`, home: `${fx(rest.home_rest_days, 0)}${rest.home_b2b ? " · back-to-back" : ""}` } });
  const ra = a.ratings, rh = h.ratings;
  const model: CompareRow[] = ra && rh ? [
    { label: "Elo rating (1500 = average)", away: ra.elo, home: rh.elo, fmt: (v) => v.toFixed(0) },
    { label: "Offence rating, goals / game vs average", away: null, home: null, fmt: f2, text: { away: signed(ra.off_g), home: signed(rh.off_g) } },
    { label: "Defence rating, goals prevented / game", away: null, home: null, fmt: f2, text: { away: signed(ra.def_g), home: signed(rh.def_g) } },
  ] : [];
  return (
    <div className="space-y-4">
      <CompareBars rows={rows} home={home} away={away} />
      {model.length > 0 && (
        <div>
          <div className="eyebrow mb-2">Model ratings (opponent-adjusted, updated after every game)</div>
          <CompareBars rows={model} home={home} away={away} />
        </div>
      )}
    </div>
  );
}
