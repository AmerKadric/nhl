import Link from "next/link";

import { odds, pct } from "@/lib/format";
import type { PlayoffSeries, PlayoffSim, PlayoffValidation } from "@/lib/types";

import { Chip, Section, Stat, TeamLogo, Unavailable } from "./ui";

const ROUNDS: Record<number, { name: string; series: number }> = {
  1: { name: "First round", series: 8 },
  2: { name: "Second round", series: 4 },
  3: { name: "Conference finals", series: 2 },
  4: { name: "Stanley Cup Final", series: 1 },
};

function SeriesCard({ s }: { s: PlayoffSeries }) {
  const rows = [
    { team: s.top_team, id: s.top, wins: s.wins_top, p: s.p_top, tone: "bg-home" },
    { team: s.bottom_team, id: s.bottom, wins: s.wins_bottom, p: s.p_top == null ? null : 1 - s.p_top, tone: "bg-away" },
  ];
  return (
    <div className="panel p-3">
      {rows.map((r) => {
        const won = s.done && s.winner === r.id;
        const lost = s.done && s.winner !== r.id;
        return (
          <div key={r.id} className={`flex items-center gap-2 py-1 ${lost ? "opacity-50" : ""}`}>
            {r.team ? <TeamLogo team={r.team} size={22} /> : <span className="h-[22px] w-[22px]" />}
            {r.team ? (
              <Link href={`/teams/${r.team.abbrev}`} className={`min-w-0 flex-1 truncate text-sm hover:text-ice ${won ? "font-semibold text-ink" : "text-ink-2"}`}>
                {r.team.name}
              </Link>
            ) : (
              <span className="flex-1 text-sm text-ink-2">{r.id}</span>
            )}
            {r.p != null && <span className="num text-xs text-ink-2">{odds(r.p)}</span>}
            <span className={`display w-5 text-right text-xl font-bold leading-none ${won ? "text-ice" : "text-ink"}`}>{r.wins}</span>
          </div>
        );
      })}
      {s.p_top != null ? (
        <div className="mt-2 flex h-1.5 gap-[2px] overflow-hidden rounded-full" role="img" aria-label={`${s.top_team?.name ?? "Higher seed"} ${pct(s.p_top)} to win the series`}>
          <span className="rounded-l-full bg-home" style={{ width: `${s.p_top * 100}%` }} />
          <span className="rounded-r-full bg-away" style={{ width: `${(1 - s.p_top) * 100}%` }} />
        </div>
      ) : (
        <div className="mt-1.5 text-[0.7rem] text-mute">
          {s.winner === s.top ? s.top_team?.abbrev : s.bottom_team?.abbrev} won {Math.max(s.wins_top, s.wins_bottom)}–{Math.min(s.wins_top, s.wins_bottom)}
        </div>
      )}
    </div>
  );
}

/** The bracket as it stands, with the model's odds on every live series. */
export function Bracket({ sim, validation }: { sim: PlayoffSim; validation: PlayoffValidation | null }) {
  const byRound = (r: number) => sim.series.filter((s) => s.round === r).sort((a, b) => a.matchup - b.matchup);
  const live = sim.teams.filter((t) => t.alive);
  const rounds = [1, 2, 3, 4].filter((r) => r >= sim.round);
  const champion = sim.champion ? sim.teams.find((t) => t.team_id === sim.champion)?.team : null;
  return (
    <>
      {champion && (
        <div className="panel mt-6 flex items-center gap-4 border-ice/40 p-5">
          <TeamLogo team={champion} size={56} />
          <div>
            <div className="eyebrow text-ice">Stanley Cup champions</div>
            <div className="display text-3xl font-bold tracking-wide">{champion.name}</div>
          </div>
        </div>
      )}

      <Section
        id="bracket"
        eyebrow="Playoffs"
        title={champion ? "How the bracket played out" : `${sim.round_name}: the bracket as it stands`}
        note={
          champion
            ? "Every series of the playoffs. The team listed first had home ice."
            : "The percentage beside each team is its chance of winning the series from the current score. The team listed first has home ice; home games follow the 2-2-1-1-1 pattern."
        }
      >
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {[1, 2, 3, 4].map((r) => {
            const list = byRound(r);
            return (
              <div key={r}>
                <div className="eyebrow mb-2">{ROUNDS[r].name}</div>
                <div className="space-y-3">
                  {list.map((s) => (
                    <SeriesCard key={s.matchup} s={s} />
                  ))}
                  {Array.from({ length: ROUNDS[r].series - list.length }).map((_, i) => (
                    <div key={i} className="hatch rounded-[14px] border border-dashed border-line px-3 py-4 text-xs text-mute">
                      To be decided
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </Section>

      {!champion && (
        <Section id="odds" eyebrow="Remaining teams" title="Odds from here" note="Later rounds are played out between simulated winners; home ice goes to the team with the better regular-season record.">
          <div className="panel overflow-x-auto">
            <table className="w-full min-w-[640px] whitespace-nowrap text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-mute">
                <tr className="border-b border-line">
                  <th className="px-4 py-2 font-semibold">Team</th>
                  {rounds.map((r) => (
                    <th key={r} className="px-3 py-2 text-right font-semibold">
                      {r === 4 ? "Win Cup" : `Win ${ROUNDS[r].name.toLowerCase()}`}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {live.map((t) => (
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
                    {rounds.map((r) => (
                      <td key={r} className={`num px-3 py-2 text-right ${r === 4 ? "text-ink" : "text-ink-2"}`}>
                        {odds(t.p_round[String(r)] ?? 0)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      <Section
        id="validation"
        eyebrow="Does it hold up?"
        title="How series odds did in past playoffs"
        note={validation ? `Every series in ${validation.seasons.length} past playoffs was priced on the first day of its round, with everything after that date hidden.` : undefined}
        aside={<Chip tone="warn">Historical simulation</Chip>}
      >
        {!validation ? (
          <Unavailable title="No validation report is available.">These series odds have not been checked against past playoffs; treat them accordingly.</Unavailable>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <Stat label="Series checked" value={validation.series.n} />
              <Stat
                label="Series odds error"
                value={validation.series.brier.toFixed(3)}
                sub={`Brier score, lower is better. 0.250 for a coin flip; ${validation.series.brier_base_rate.toFixed(3)} for quoting every series at the rate teams with home ice actually won.`}
              />
              <Stat label="Series winner picked" value={pct(validation.series.accuracy, 1)} sub={`the team with home ice won ${pct(validation.series.home_ice_win_rate, 1)}`} />
              <Stat
                label="Champion's Cup odds"
                value={pct(validation.cup.champion_mean_prob, 1)}
                sub={`average given to the eventual champion before round one, over ${validation.cup.n_champions} playoffs; ${pct(validation.cup.champion_uniform_prob, 1)} if all sixteen were equal`}
              />
            </div>
            <div className="panel mt-4 overflow-x-auto p-5">
              <div className="eyebrow mb-3">When the model gave the team with home ice…</div>
              <table className="w-full max-w-xl text-sm">
                <thead className="text-left text-xs uppercase tracking-wider text-mute">
                  <tr className="border-b border-line">
                    {["Stated chance", "Series", "Average stated", "Actually won"].map((h) => (
                      <th key={h} className="py-2 pr-3 font-semibold">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {validation.calibration.map((b) => (
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
              <p className="mt-3 text-xs text-mute">
                {validation.series.n} series is a small sample: a few upsets move every figure here. Cup odds given before round one scored{" "}
                {validation.cup.brier.toFixed(4)} against {validation.cup.brier_uniform.toFixed(4)} for giving all sixteen teams the same chance (lower is better), which is{" "}
                {validation.cup.brier < validation.cup.brier_uniform ? "barely better" : "no better"}; with {validation.cup.n_champions} champions to go on, treat Cup odds as a
                rough guide.
              </p>
            </div>
          </>
        )}
      </Section>
    </>
  );
}
