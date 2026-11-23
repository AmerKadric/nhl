import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { Chip, PageSkeleton, Section, Stat, TeamLogo, Unavailable } from "@/components/ui";
import { apiCached } from "@/lib/api";
import { odds, pct, recordStr, seasonLabel, stampET } from "@/lib/format";
import type { SeasonOutlook, SeasonTeam } from "@/lib/types";

export const metadata: Metadata = { title: "Season outlook" };

export default function Page() {
  return (
    <>
      <h1 className="display text-[2rem] font-bold leading-none tracking-wide sm:text-[2.4rem]">Season outlook</h1>
      <p className="mt-2 max-w-3xl text-sm text-ink-2">
        The rest of the regular season and the playoffs, played out thousands of times with the same model that forecasts each game. These are probabilities from a
        simulation, not predictions of what will happen.
      </p>
      <Suspense fallback={<div className="mt-8"><PageSkeleton /></div>}>
        <Outlook />
      </Suspense>
    </>
  );
}

const PTS_LO = 45, PTS_HI = 135;
const x = (v: number) => `${Math.max(0, Math.min(100, ((v - PTS_LO) / (PTS_HI - PTS_LO)) * 100))}%`;

function OddsBar({ p, strong }: { p: number; strong?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <span className="h-2 w-16 shrink-0 rounded-sm bg-raised sm:w-24" aria-hidden>
        <span className={`block h-full rounded-sm ${strong ? "bg-home" : "bg-home/60"}`} style={{ width: `${p * 100}%` }} />
      </span>
      <span className="num w-14 text-right text-sm text-ink">{odds(p)}</span>
    </div>
  );
}

function DivisionTable({ name, teams }: { name: string; teams: SeasonTeam[] }) {
  return (
    <div className="panel overflow-x-auto">
      <table className="w-full min-w-[820px] text-sm">
        <caption className="border-b border-line px-4 py-2.5 text-left">
          <span className="display text-lg font-semibold tracking-wide text-ink">{name}</span>
        </caption>
        <thead className="text-left text-xs uppercase tracking-wider text-mute">
          <tr className="border-b border-line">
            <th className="px-4 py-2 font-semibold">Team</th>
            <th className="px-3 py-2 font-semibold">Record</th>
            <th className="px-3 py-2 font-semibold">Pts</th>
            <th className="px-3 py-2 font-semibold">Projected points <span className="normal-case text-mute">· 80% range</span></th>
            <th className="px-3 py-2 font-semibold">Make playoffs</th>
            <th className="px-3 py-2 text-right font-semibold">Win division</th>
            <th className="px-3 py-2 text-right font-semibold">Conf. final</th>
            <th className="px-4 py-2 text-right font-semibold">Win Cup</th>
          </tr>
        </thead>
        <tbody>
          {teams.map((t) => (
            <tr key={t.team_id} className="border-b border-line-soft last:border-0 hover:bg-raised/60">
              <td className="px-4 py-2">
                {t.team ? (
                  <Link href={`/teams/${t.team.abbrev}`} className="flex items-center gap-2.5 font-medium text-ink hover:text-ice">
                    <TeamLogo team={t.team} size={22} /> {t.team.name}
                  </Link>
                ) : (
                  t.team_id
                )}
              </td>
              <td className="num px-3 py-2 text-ink-2">{recordStr(t)}</td>
              <td className="num px-3 py-2 text-ink">{t.points}</td>
              <td className="px-3 py-2">
                <div className="flex items-center gap-3">
                  <span className="num w-9 text-ink">{t.proj_points.toFixed(0)}</span>
                  <span className="relative h-2 w-28 rounded-sm bg-raised sm:w-40" role="img" aria-label={`Projected ${t.proj_points.toFixed(0)} points; 80% range ${t.points_p10.toFixed(0)} to ${t.points_p90.toFixed(0)}`}>
                    <span className="absolute inset-y-0 rounded-sm bg-home/45" style={{ left: x(t.points_p10), right: `calc(100% - ${x(t.points_p90)})` }} />
                    <span className="absolute -inset-y-0.5 w-0.5 bg-ink" style={{ left: x(t.proj_points) }} />
                  </span>
                  <span className="num text-xs text-mute">
                    {t.points_p10.toFixed(0)}–{t.points_p90.toFixed(0)}
                  </span>
                </div>
              </td>
              <td className="px-3 py-2">
                <OddsBar p={t.p_playoffs} strong />
              </td>
              <td className="num px-3 py-2 text-right text-ink-2">{odds(t.p_division)}</td>
              <td className="num px-3 py-2 text-right text-ink-2">{odds(t.p_conf_final)}</td>
              <td className="num px-4 py-2 text-right text-ink">{odds(t.p_cup)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

async function Outlook() {
  await connection();
  const d = await apiCached<SeasonOutlook>("/v1/season");
  const sim = d?.simulation;
  if (!d || !sim) {
    return (
      <div className="mt-8">
        <Unavailable title="No season simulation is available.">
          It runs once a day while regular-season games remain. Nothing is shown in its place.
        </Unavailable>
      </div>
    );
  }
  const teams = sim.teams;
  const confs = [...new Set(teams.map((t) => t.conference))].sort();
  const fav = [...teams].sort((a, b) => b.p_cup - a.p_cup).slice(0, 8);
  const maxCup = fav[0]?.p_cup || 1;
  const v = d.validation;
  const hist = d.history;
  const grid = v?.scale_study?.grid;
  const study = grid
    ? { raw: grid.find((g) => g.scale === 1), used: grid.find((g) => g.scale === v?.strength_sd_scale), fit: v!.scale_study!.fit_seasons.length }
    : null;
  const days = hist?.dates.length ?? 0;

  return (
    <>
      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-ink-2">
        <Chip tone="ice">{seasonLabel(sim.season)}</Chip>
        <span className="num">{sim.n_sims.toLocaleString("en-US")} simulated seasons</span>
        <span className="num">results through {sim.results_through}</span>
        <span className="num">{sim.games_remaining} games left</span>
        <span className="num">run {stampET(sim.generated_at + "Z")}</span>
        <span className="num">{sim.model_version}</span>
      </div>

      <Section id="cup" eyebrow="Stanley Cup" title="Most likely champions" note="Even the favourite is far more likely to lose than to win: sixteen teams play four rounds of best-of-seven.">
        <div className="panel p-5">
          <ol className="grid gap-x-10 gap-y-2.5 md:grid-cols-2">
            {fav.map((t) => (
              <li key={t.team_id} className="grid grid-cols-[11rem_1fr_3.4rem] items-center gap-3">
                <span className="flex items-center gap-2 truncate text-sm font-medium text-ink">
                  {t.team && <TeamLogo team={t.team} size={20} />} {t.team?.name ?? t.team_id}
                </span>
                <span className="h-2.5 rounded-sm bg-raised" aria-hidden>
                  <span className="grow-x block h-full rounded-sm bg-home" style={{ width: `${(t.p_cup / maxCup) * 100}%` }} />
                </span>
                <span className="num text-right text-sm text-ink">{odds(t.p_cup)}</span>
              </li>
            ))}
          </ol>
        </div>
      </Section>

      {confs.map((c) => {
        const divs = [...new Set(teams.filter((t) => t.conference === c).map((t) => t.division))].sort();
        return (
          <Section key={c} id={c.toLowerCase()} eyebrow="Projected standings" title={`${c} Conference`} note="Three teams from each division and the next two in the conference make the playoffs. Teams are ordered by projected points.">
            <div className="space-y-4">
              {divs.map((dv) => (
                <DivisionTable key={dv} name={dv} teams={teams.filter((t) => t.division === dv).sort((a, b) => b.proj_points - a.proj_points)} />
              ))}
            </div>
          </Section>
        );
      })}

      <Section
        id="validation"
        eyebrow="Does it hold up?"
        title="How these odds did in past seasons"
        note={v ? `The simulation was re-run as of December 1 and February 1 in ${v.seasons.length} past seasons, with everything after each date hidden, and compared with what happened.` : undefined}
        aside={<Chip tone="warn">Historical simulation</Chip>}
      >
        {!v ? (
          <Unavailable title="No validation report is available.">The odds above have not been checked against past seasons; treat them accordingly.</Unavailable>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <Stat label="Team-seasons checked" value={v.overall.team_seasons.toLocaleString("en-US")} />
              <Stat
                label="Playoff odds error"
                value={v.overall.playoff_brier.toFixed(3)}
                sub={`Brier score, lower is better. ${v.overall.playoff_brier_in_position_baseline.toFixed(3)} for "whoever holds a spot today"; 0.250 for a coin flip.`}
              />
              <Stat label="Points projection error" value={`${v.overall.points_mae.toFixed(1)} pts`} sub="average miss in final points" />
              <Stat
                label="Final points inside range"
                value={pct(v.overall.points_interval_coverage)}
                sub="of the time the 80% range held the actual total; 80% would be ideal"
              />
            </div>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <div className="panel overflow-x-auto p-5">
                <div className="eyebrow mb-3">When the simulation said…</div>
                <table className="w-full text-sm">
                  <thead className="text-left text-xs uppercase tracking-wider text-mute">
                    <tr className="border-b border-line">
                      {["Stated playoff odds", "Team-seasons", "Average stated", "Actually made it"].map((h) => (
                        <th key={h} className="py-2 pr-3 font-semibold">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {v.calibration.map((b) => (
                      <tr key={b.lo} className="border-b border-line-soft last:border-0">
                        <td className="num py-2 pr-3 text-ink-2">
                          {Math.round(b.lo * 100)}–{Math.round(b.hi * 100)}%
                        </td>
                        <td className="num py-2 pr-3 text-ink-2">{b.n}</td>
                        <td className="num py-2 pr-3 text-ink">{pct(b.mean_pred, 1)}</td>
                        <td className="num py-2 pr-3 text-ink">{pct(b.observed, 1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="panel p-5 text-sm leading-relaxed text-ink-2">
                <div className="eyebrow mb-3">Reading this</div>
                <ul className="space-y-2">
                  <li>
                    <strong className="text-ink">Playoff odds</strong> are checked two ways: by how far off they were on average, and by whether teams given, say, 70 to 90%
                    made it about that often.
                  </li>
                  <li>
                    <strong className="text-ink">Cup odds</strong> rest on only {v.overall.n_champions} champion-checkpoints, far too few to judge. The eventual champion was
                    given {pct(v.overall.champion_mean_prob, 1)} on average, against {pct(v.overall.champion_uniform_prob, 1)} if all sixteen playoff teams were equal.
                  </li>
                  <li>
                    <strong className="text-ink">Not modelled:</strong> trades, injuries, call-ups and anything else that changes a roster between now and April. Team strength
                    is allowed to drift, but only as much as it has drifted historically.
                  </li>
                  {study && (
                    <li>
                      <strong className="text-ink">One setting was tuned on this history.</strong> Left alone, the simulation was too sure of each team&apos;s strength: its
                      80% points ranges held only {pct(study.raw?.fit_coverage)} of the time. Its uncertainty was widened using {study.fit} of these seasons (
                      {pct(study.used?.fit_coverage)} there) and then checked on the rest ({pct(study.used?.later_coverage)}).
                    </li>
                  )}
                  <li>
                    Odds shown as {"<0.1%"} or {">99.9%"} mean the event almost never or almost always happened in the simulation, not that it is mathematically settled.
                  </li>
                </ul>
              </div>
            </div>
          </>
        )}
      </Section>

      {days > 0 && (
        <p className="mt-6 text-xs text-mute">
          A snapshot of these odds is stored each day ({days} so far this season), so how each team&apos;s chances moved will be chartable as the season goes on.
        </p>
      )}
    </>
  );
}
