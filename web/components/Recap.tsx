import Link from "next/link";

import { dayLabel, finalLabel } from "@/lib/format";
import type { Game } from "@/lib/types";

import { TeamLogo } from "./ui";

/** How the most recent slate's published forecasts turned out. Every game
 * with a forecast is listed, hits and misses alike. */
export function Recap({ date, games }: { date: string; games: Game[] }) {
  const scored = games.filter((g) => g.result && g.forecast && g.evaluation);
  if (scored.length === 0) return null;
  const right = scored.filter((g) => g.evaluation!.winner_correct).length;
  const exact = scored.filter((g) => g.evaluation!.exact_score_hit).length;
  const provisional = scored.some((g) => g.evaluation!.provisional);
  return (
    <section className="mt-10" aria-labelledby="recap-h">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-x-6 gap-y-1">
        <div>
          <div className="eyebrow mb-1">Last slate · {dayLabel(date)}</div>
          <h2 id="recap-h" className="display text-2xl font-semibold tracking-wide">
            {right} of {scored.length} winners called{exact > 0 ? `, ${exact} exact ${exact === 1 ? "score" : "scores"}` : ""}
          </h2>
        </div>
        <Link href="/accuracy" className="text-sm text-ice hover:underline">
          Full track record
        </Link>
      </div>
      <div className="panel overflow-x-auto">
        <table className="w-full min-w-[620px] whitespace-nowrap text-sm">
          <thead className="text-left text-xs uppercase tracking-wider text-mute">
            <tr className="border-b border-line">
              <th className="px-4 py-2 font-semibold">Game</th>
              <th className="px-3 py-2 font-semibold">Final</th>
              <th className="px-3 py-2 font-semibold">Projected</th>
              <th className="px-3 py-2 font-semibold">Pre-game favourite</th>
              <th className="px-4 py-2 font-semibold">Result</th>
            </tr>
          </thead>
          <tbody>
            {scored.map((g) => {
              const f = g.forecast!, r = g.result!, e = g.evaluation!;
              const fav = f.p_home_win >= 0.5 ? g.home : g.away;
              return (
                <tr key={g.id} className="border-b border-line-soft last:border-0 hover:bg-raised/60">
                  <td className="px-4 py-2">
                    <Link href={`/game/${g.id}`} className="flex items-center gap-2 text-ink hover:text-ice">
                      <TeamLogo team={g.away} size={18} /> {g.away.abbrev} <span className="text-mute">@</span> <TeamLogo team={g.home} size={18} /> {g.home.abbrev}
                    </Link>
                  </td>
                  <td className="num px-3 py-2 font-semibold text-ink">
                    {r.away_score}–{r.home_score}
                    {r.last_period_type && r.last_period_type !== "REG" && <span className="ml-1.5 font-normal text-mute">{finalLabel(r.last_period_type).replace("Final / ", "")}</span>}
                  </td>
                  <td className="num px-3 py-2 text-ink-2">
                    {f.ml_away_goals}–{f.ml_home_goals}
                  </td>
                  <td className="num px-3 py-2 text-ink-2">
                    {fav.abbrev} {Math.round(Math.max(f.p_home_win, f.p_away_win) * 100)}%
                  </td>
                  <td className="px-4 py-2">
                    <span className={e.winner_correct ? "text-good" : "text-bad"}>{e.winner_correct ? "✓ winner" : "✕ winner"}</span>
                    {e.exact_score_hit && <span className="ml-3 text-good">✓ exact score</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-mute">
        Scores are away–home. Forecasts shown are the last version published before each game.
        {provisional && " Marked from the NHL's final scores; these games join the track record at the next data update."}
      </p>
    </section>
  );
}
