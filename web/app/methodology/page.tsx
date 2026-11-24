import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { Chip, PageSkeleton, Section, Unavailable } from "@/components/ui";
import { apiCached } from "@/lib/api";
import { pct, seasonLabel } from "@/lib/format";
import type { Accuracy, Meta } from "@/lib/types";

export const metadata: Metadata = { title: "How the model works" };

export default function Page() {
  return (
    <div className="max-w-4xl">
      <h1 className="display text-[2rem] font-bold leading-none tracking-wide sm:text-[2.4rem]">How PUCKIQ works</h1>
      <p className="mt-2 text-sm text-ink-2">
        A plain-language account of the model, followed by its actual fitted numbers. Nothing on this site is written or estimated by a language model: every figure comes from the
        statistical pipeline described here.
      </p>

      <Section id="steps" eyebrow="The pipeline" title="From box scores to a final-score forecast">
        <ol className="space-y-3">
          {[
            ["Rate every team.", "After each game, each team's offence and defence ratings are updated with a Kalman filter: a statistical method that moves a rating toward new evidence in proportion to how uncertain the rating is. Three pairs are tracked: expected goals (shot quality) in all situations, regulation goals, and an Elo rating from results. Ratings are adjusted for the opponent and for home ice, and partly reset toward average each off-season."],
            ["Rate the goalies.", "Each goalie's goals saved above expected is tracked per shot quality faced, weighted toward recent games and shrunk toward league average, so a hot month from a backup is not mistaken for elite talent."],
            ["Project who starts.", "Until the NHL lists a starter, the model estimates each goalie's chance of starting from recent usage and whether the team played yesterday. The forecast then averages over those possibilities instead of assuming one."],
            ["Check who is dressing.", "Team ratings are averages over many games, so they cannot see that a top defenceman has been out for a week. The model compares the lineup each team last dressed with the lineup it usually dresses, weighting each skater by the ice time his coach gives him. A regular who sat out counts against his team from his first missed game."],
            ["Turn ratings into scoring rates.", "A Poisson regression converts the two teams' ratings, lineup availability, the opposing goalie, home ice, back-to-backs and recent workload into an expected regulation scoring rate for each team."],
            ["Simulate the game by the rules.", "Each of 20,000 simulations draws the goalies, the scoring rates and a regulation score. Level games are more common than independent scoring implies, so a fitted draw-inflation factor corrects for that. A tie goes to overtime or a shootout, and the winner gets exactly one more goal, so a final score can never be level."],
            ["Publish, then keep score.", "Forecasts are stored with a timestamp and never edited. New information creates a new version. When the game ends, every version is scored automatically and the result is shown on the Accuracy page, good or bad."],
          ].map(([h, body], i) => (
            <li key={h} className="panel flex gap-4 p-4">
              <span className="display flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-raised text-xl font-bold text-ice">{i + 1}</span>
              <p className="text-sm leading-relaxed text-ink-2">
                <strong className="text-ink">{h}</strong> {body}
              </p>
            </li>
          ))}
        </ol>
      </Section>

      <Suspense fallback={<div className="mt-10"><PageSkeleton /></div>}>
        <Numbers />
      </Suspense>

      <Section id="limits" eyebrow="Honest limits" title="What this model cannot do">
        <ul className="space-y-2 text-sm leading-relaxed text-ink-2">
          {[
            ["Exact scores are mostly luck.", "Hockey is low scoring and random. The single most likely final score is right about one game in thirteen; the value is in the full distribution, not the headline number."],
            ["No injury reports or game-day lineups.", "There is no free, licensed injury feed. The model only knows who dressed in each team's last game, so a new absence is invisible until the first game missed, and it never knows why a player sat or whether he returns tonight. In testing, confirmed game-day lineups would have helped a little more."],
            ["Starting goalies are projected, never confirmed.", "The NHL's public feed does not name a game's starters before puck drop, and there is no licensed source for the announcements teams make beforehand. Every forecast averages over the likely starters, weighted by recent usage. In the backtest, knowing the actual starter did not measurably improve accuracy over the projection, but individual games can still move."],
            ["No in-game probabilities.", "Live scores are shown as observed facts. There is no validated in-game model, so no live win probability is displayed."],
            ["Markets are not inputs.", "The model never sees betting odds. They are recorded before each game only to score the model against them, and that comparison is on the Accuracy page. Nothing here is betting advice."],
            ["The past is not a promise.", "Backtest results are a simulation. The published record on the Accuracy page is the real test, and it is still short."],
          ].map(([h, body]) => (
            <li key={h} className="panel px-4 py-3">
              <strong className="text-ink">{h}</strong> {body}
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

const LABELS: Record<string, [string, string]> = {
  const: ["Baseline", "log scoring rate for an average team"],
  is_home: ["Home ice", "playing at home"],
  log_lg: ["League scoring", "log of recent league-average goals"],
  playoff: ["Playoff game", "postseason indicator"],
  t_off_xga: ["Team chance creation", "xG per 60 above average"],
  o_def_xga: ["Opponent chance suppression", "xG per 60 prevented"],
  t_off_g: ["Team scoring", "goals per game above average"],
  o_def_g: ["Opponent goal prevention", "goals per game prevented"],
  o_gk: ["Opposing goalie", "goals saved per expected goal; effect shown per +0.05 (a very good starter)"],
  t_b2b: ["Team on a back-to-back", "second game in two nights"],
  o_b2b: ["Opponent on a back-to-back", "second game in two nights"],
  t_g7: ["Team workload", "games in the last 7 days"],
  o_g7: ["Opponent workload", "games in the last 7 days"],
  t_travel: ["Team travel", "thousands of km since last game"],
  t_tz: ["Time zones crossed", "hours, eastward positive"],
  elo_diff: ["Elo difference", "per 100 Elo points"],
  t_lu_toi: ["Team lineup availability", "minutes of usual ice time gained or missing"],
  o_lu_toi: ["Opponent lineup availability", "minutes of usual ice time gained or missing"],
  t_lu_gs: ["Team lineup availability", "game-score value gained or missing"],
  o_lu_gs: ["Opponent lineup availability", "game-score value gained or missing"],
};

async function Numbers() {
  await connection();
  const [acc, meta] = await Promise.all([apiCached<Accuracy>("/v1/accuracy"), apiCached<Meta>("/v1/meta")]);
  const mv = acc?.model_versions.find((v) => v.is_active);
  const bt = acc?.backtest;
  return (
    <>
      <Section id="model" eyebrow="The fitted model" title="Actual coefficients" note="Each coefficient is the change in the log of a team's regulation scoring rate per unit of the input. The percentage column is the same thing in plain terms.">
        {!mv ? (
          <Unavailable title="No model version is active." />
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-2 text-sm text-ink-2">
              <Chip tone="ice">{mv.version}</Chip> trained on every game through {mv.trained_through}
            </div>
            <div className="panel overflow-x-auto">
              <table className="w-full min-w-[560px] text-sm">
                <thead className="text-left text-xs uppercase tracking-wider text-mute">
                  <tr className="border-b border-line">
                    {["Input", "Unit", "Coefficient", "Effect on scoring rate per unit"].map((h) => (
                      <th key={h} className="px-4 py-2.5 font-semibold">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(mv.coefficients).map(([k, v]) => (
                    <tr key={k} className="border-b border-line-soft last:border-0">
                      <td className="px-4 py-2 text-ink">{LABELS[k]?.[0] ?? k}</td>
                      <td className="px-4 py-2 text-xs text-ink-2">{LABELS[k]?.[1] ?? ""}</td>
                      <td className="num px-4 py-2 text-ink">{v >= 0 ? "+" : "−"}{Math.abs(v).toFixed(4)}</td>
                      <td className="num px-4 py-2 text-ink-2">{k === "const" || k === "log_lg" ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs((Math.exp(v * (k === "o_gk" ? 0.05 : 1)) - 1) * 100).toFixed(1)}%`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-3">
              {[
                ["Draw inflation", mv.simulator.theta?.toFixed(3), "how much more often regulation ends level than independent scoring implies"],
                ["Shootout share", pct(mv.simulator.p_shootout, 1), "of regular-season games level after regulation that reach a shootout"],
                ["Scoring distribution", mv.simulator.dispersion > 1e-6 ? "Negative binomial" : "Poisson", "a wider distribution was tested and was not supported by the data"],
              ].map(([l, v, s]) => (
                <div key={l} className="panel px-4 py-3">
                  <div className="eyebrow">{l}</div>
                  <div className="num mt-1 text-xl font-semibold text-ink">{v}</div>
                  <div className="mt-1 text-xs text-ink-2">{s}</div>
                </div>
              ))}
            </div>
          </>
        )}
      </Section>

      <Section id="validation" eyebrow="Validation" title="How the model was chosen and tested">
        {bt ? (
          <div className="space-y-3 text-sm leading-relaxed text-ink-2">
            <p>
              <strong className="text-ink">Walk-forward testing.</strong> For each season from {seasonLabel(bt.test_seasons[0])} onward, every candidate model was fitted only on earlier seasons
              (starting {seasonLabel(bt.first_training_season)}) and then forecast that season. Rating settings were tuned on seasons before {seasonLabel(bt.test_seasons[0])} only.
            </p>
            <p>
              <strong className="text-ink">No look-ahead.</strong> A game&apos;s inputs are built only from games played on earlier days. An automated test rebuilds the inputs with all later games
              deleted and requires an identical result.
            </p>
            <p>
              <strong className="text-ink">Simplest model that holds up.</strong> Candidates were compared on seasons {seasonLabel(bt.selection_seasons[0])} to {seasonLabel(bt.selection_seasons[1])};
              the simplest one not significantly worse than the best was kept. Seasons {seasonLabel(bt.confirmation_seasons[0])} onward were held back and used only to confirm the result.
              Lineup availability was added this way, after it improved forecasts on the comparison seasons. Gradient boosting, a blended model, separate five-on-five and
              special-teams ratings, and a spread calibration were all tested and left out because they did not help.
            </p>
            <p>
              Full results, including benchmarks, calibration and every individual forecast, are on the <Link href="/accuracy" className="text-ice hover:underline">Accuracy page</Link>.
            </p>
          </div>
        ) : (
          <Unavailable title="No backtest report is available." />
        )}
      </Section>

      <Section id="data" eyebrow="Data sources" title="Where the data comes from">
        <div className="space-y-3">
          {(meta?.sources ?? []).map((s) => (
            <div key={s.key} className={`panel px-4 py-3 ${s.status === "unavailable" ? "hatch" : ""}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-semibold text-ink">{s.name}</span>
                {s.status === "unavailable" ? <Chip tone="warn">Not available</Chip> : meta?.freshness.sources[s.key] ? <Chip tone={meta.freshness.sources[s.key].stale ? "warn" : "good"}>{meta.freshness.sources[s.key].stale ? "Stale" : "Current"}</Chip> : <Chip>In use</Chip>}
              </div>
              <p className="mt-1 text-xs text-ink-2">{s.attribution}</p>
              <p className="mt-0.5 text-xs text-mute">{s.license}</p>
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs text-mute">
          PUCKIQ is a non-commercial project. MoneyPuck data is used under its non-commercial terms with credit. PUCKIQ is not affiliated with or endorsed by the NHL, its teams or
          MoneyPuck.
        </p>
      </Section>
    </>
  );
}
