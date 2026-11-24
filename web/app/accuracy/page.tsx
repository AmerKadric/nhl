import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { CalibrationChart, ConfidenceChart, SeasonLines } from "@/components/charts";
import { Chip, PageSkeleton, Section, Stat, TeamLogo, Unavailable } from "@/components/ui";
import { apiCached } from "@/lib/api";
import { fx, GROUP_NAMES, MODEL_NAMES, pct, seasonLabel, stampET } from "@/lib/format";
import type { Accuracy, Boot, History, MarketBlock, Metrics, TeamsResponse } from "@/lib/types";

export const metadata: Metadata = { title: "Model accuracy" };

export default function Page({ searchParams }: PageProps<"/accuracy">) {
  return (
    <>
      <h1 className="display text-[2rem] font-bold leading-none tracking-wide sm:text-[2.4rem]">Accuracy Center</h1>
      <p className="mt-2 max-w-3xl text-sm text-ink-2">
        Two separate records. <strong className="text-ink">Published forecasts</strong> were stored with a timestamp before the game and scored automatically afterwards.{" "}
        <strong className="text-ink">The backtest</strong> is a historical simulation of what the model would have forecast. They are never combined.
      </p>
      <Suspense fallback={<div className="mt-8"><PageSkeleton /></div>}>
        <Body searchParams={searchParams} />
      </Suspense>
    </>
  );
}

const m4 = (v?: number) => (v == null ? "—" : v.toFixed(4));
const ci = (b: Boot) => `${b.mean_diff >= 0 ? "+" : "−"}${Math.abs(b.mean_diff).toFixed(4)} (${b.ci95[0].toFixed(4)} to ${b.ci95[1].toFixed(4)})`;

function Tiles({ m, base }: { m: Metrics; base?: Metrics }) {
  const vs = (a?: number, b?: number, digits = 1, scale = 100, unit = " pts") =>
    a != null && b != null ? `${a - b >= 0 ? "+" : "−"}${Math.abs((a - b) * scale).toFixed(digits)}${unit} vs home-ice baseline` : undefined;
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
      <Stat label="Games evaluated" value={m.n.toLocaleString("en-US")} />
      <Stat label="Winner accuracy" value={pct(m.accuracy, 1)} sub={vs(m.accuracy, base?.accuracy)} />
      <Stat label="Exact score rate" value={pct(m.exact_score_rate, 1)} sub={vs(m.exact_score_rate, base?.exact_score_rate)} />
      <Stat label="Avg goal error" value={fx(m.mae_team_goals, 2)} sub="per team, vs expected goals" />
      <Stat label="Log loss" value={m4(m.log_loss)} sub={base ? `${m4(base.log_loss)} for the baseline · lower is better` : "lower is better; a coin flip scores 0.6931"} />
      <Stat label="Brier score" value={m4(m.brier)} sub={base ? `${m4(base.brier)} for the baseline · lower is better` : "lower is better; a coin flip scores 0.25"} />
    </div>
  );
}

async function Body({ searchParams }: { searchParams: PageProps<"/accuracy">["searchParams"] }) {
  const sp = await searchParams;
  const acc = await apiCached<Accuracy>("/v1/accuracy");
  if (!acc) return <Unavailable title="Accuracy data is unavailable." />;
  const { forward: fw, backtest: bt } = acc;
  const src = sp.source === "backtest" || (sp.source !== "forward" && fw.final.n === 0) ? "backtest" : "forward";
  const season = typeof sp.season === "string" && /^\d{4}$/.test(sp.season) ? sp.season : "";
  const team = typeof sp.team === "string" && /^[A-Za-z]{2,4}$/.test(sp.team) ? sp.team.toUpperCase() : "";
  const page = Math.max(0, Number(typeof sp.page === "string" ? sp.page : 0) || 0);
  const qs = new URLSearchParams({ source: src, limit: "25", offset: String(page * 25) });
  if (season) qs.set("season", season);
  if (team) qs.set("team", team);
  const [hist, teams] = await Promise.all([apiCached<History>(`/v1/accuracy/history?${qs}`), apiCached<TeamsResponse>("/v1/teams")]);
  const link = (o: Record<string, string | number>) => {
    const q = new URLSearchParams({ source: src, ...(season && { season }), ...(team && { team }) });
    for (const [k, v] of Object.entries(o)) {
      if (v === "") q.delete(k);
      else q.set(k, String(v));
    }
    return `/accuracy?${q}#history`;
  };

  const primary = bt ? bt.detail[bt.primary] : null;
  const base = bt?.models.baseline_home;

  return (
    <>
      {/* ---- forward record ---- */}
      <Section
        id="published"
        eyebrow="Real forecasts, published before the game"
        title="Published forecast record"
        note={fw.first_forecast_at ? `Every forecast since the first one on ${stampET(fw.first_forecast_at)}. Scored on the latest version published before puck drop. Nothing is excluded.` : undefined}
        aside={<Chip tone="ice">Live record</Chip>}
      >
        {fw.final.n === 0 ? (
          <Unavailable title="No published forecast has been settled yet.">
            {fw.games_pending > 0 ? `${fw.games_pending} games have a published forecast and are waiting to be played. ` : ""}
            Results appear here automatically when games finish. No figures are shown until then.
          </Unavailable>
        ) : (
          <>
            <Tiles m={fw.final} />
            <p className="mt-3 text-sm text-ink-2">
              {fw.final.n < 200 && (
                <>
                  <strong className="text-warn">Small sample.</strong> With {fw.final.n} games, accuracy can swing by ±{Math.round(196 * Math.sqrt(0.25 / fw.final.n))} points by chance alone; these
                  numbers say little yet.{" "}
                </>
              )}
              {fw.games_pending} more games have a published forecast awaiting a result.
            </p>
            <div className="mt-5 grid gap-4 lg:grid-cols-2">
              {fw.by_goalie_state && (
                <div className="panel p-5">
                  <div className="eyebrow mb-3">By goalie information at publication</div>
                  <MetricTable rows={Object.entries(fw.by_goalie_state).map(([k, v]) => ({ name: k === "projected" ? "Projected goalies" : k === "confirmed" ? "After both goalies confirmed" : "One goalie confirmed", m: v }))} />
                  {fw.first_version && <p className="mt-3 text-xs text-mute">First version of each forecast: {pct(fw.first_version.accuracy, 1)} winners, log loss {m4(fw.first_version.log_loss)}.</p>}
                </div>
              )}
              {fw.by_month && (
                <div className="panel p-5">
                  <div className="eyebrow mb-3">By month</div>
                  <MetricTable rows={Object.entries(fw.by_month).map(([k, v]) => ({ name: k, m: v }))} />
                </div>
              )}
            </div>
          </>
        )}
      </Section>

      {/* ---- market benchmark ---- */}
      <Section
        id="market"
        eyebrow="The hardest benchmark"
        title="Against the betting market"
        note="Betting odds are the strongest public forecast of a hockey game. PUCKIQ does not use them: they are recorded before each game only so the two can be scored on exactly the same games."
        aside={<Chip tone="ice">Live record</Chip>}
      >
        {!acc.market || (!acc.market.latest && !acc.market.first) ? (
          <Unavailable title="No finished game has both a published forecast and recorded odds yet.">
            {acc.market && acc.market.games_with_odds > 0
              ? `Odds have been recorded for ${acc.market.games_with_odds} ${acc.market.games_with_odds === 1 ? "game" : "games"} so far. `
              : "Recording starts with the next slate. "}
            The comparison appears here once those games finish. Nothing is shown in its place.
          </Unavailable>
        ) : (
          <>
            <div className="grid gap-4 lg:grid-cols-2">
              {acc.market.latest && <MarketPanel title="Last odds before puck drop" b={acc.market.latest} />}
              {acc.market.first && <MarketPanel title="First odds of the day" b={acc.market.first} />}
            </div>
            <ul className="mt-3 space-y-1 text-xs leading-relaxed text-mute">
              <li>
                Odds come from two of the NHL&apos;s sportsbook partners through the NHL&apos;s own feed, converted to probabilities with the bookmaker&apos;s margin removed.
                Individual prices are not shown.
              </li>
              <li>Late odds reflect confirmed goalies, lineups and injury news that PUCKIQ does not have, so matching them would be a strong result, not the expected one.</li>
              <li>This is a measure of forecast quality, not betting advice.</li>
            </ul>
          </>
        )}
      </Section>

      {/* ---- backtest ---- */}
      {!bt || !primary ? (
        <Section id="backtest" eyebrow="Historical simulation" title="Backtest">
          <Unavailable title="No backtest report is available.">Run the backtest to generate it. No figures are shown in its place.</Unavailable>
        </Section>
      ) : (
        <>
          <Section
            id="backtest"
            eyebrow="Historical simulation, not published forecasts"
            title={`Backtest, ${seasonLabel(bt.test_seasons[0])} to ${seasonLabel(bt.test_seasons[bt.test_seasons.length - 1])}`}
            note={`${bt.method} Features for each game use only games played on earlier days. Headline figures use projected starting goalies, the information available well before puck drop.`}
            aside={<Chip tone="warn">Simulation</Chip>}
          >
            <Tiles m={primary.overall} base={base?.overall} />
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div className="panel p-5">
                <div className="eyebrow">Selection seasons {seasonLabel(bt.selection_seasons[0])} to {seasonLabel(bt.selection_seasons[1])}</div>
                <p className="mt-1 text-xs text-mute">Used to choose between candidate models.</p>
                <div className="num mt-3 text-sm text-ink">{primary.selection_window.n.toLocaleString("en-US")} games · {pct(primary.selection_window.accuracy, 1)} winners · log loss {m4(primary.selection_window.log_loss)} · exact {pct(primary.selection_window.exact_score_rate, 1)}</div>
              </div>
              <div className="panel border-ice/30 p-5">
                <div className="eyebrow text-ice">Confirmation seasons {seasonLabel(bt.confirmation_seasons[0])} to {seasonLabel(bt.confirmation_seasons[1])}</div>
                <p className="mt-1 text-xs text-mute">Played no part in any modelling decision. The fairest guide to future performance.</p>
                <div className="num mt-3 text-sm text-ink">{primary.confirmation_window.n.toLocaleString("en-US")} games · {pct(primary.confirmation_window.accuracy, 1)} winners · log loss {m4(primary.confirmation_window.log_loss)} · exact {pct(primary.confirmation_window.exact_score_rate, 1)}</div>
              </div>
            </div>
          </Section>

          <Section id="benchmarks" eyebrow="Benchmarks" title="Against simpler and more complex models" note={bt.selection_reason}>
            <div className="panel overflow-x-auto">
              <table className="w-full min-w-[760px] text-sm">
                <thead className="text-left text-xs uppercase tracking-wider text-mute">
                  <tr className="border-b border-line">
                    {["Model", "Winner accuracy", "Log loss", "Brier", "Exact score", "Score log loss", "Goal error"].map((h) => (
                      <th key={h} className="px-4 py-2.5 font-semibold">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(bt.models).map(([k, v]) => {
                    const on = k === bt.primary || k === bt.chosen_model;
                    const o = v.overall;
                    return (
                      <tr key={k} className={`border-b border-line-soft last:border-0 ${on ? "bg-ice/[0.06]" : ""}`}>
                        <td className="px-4 py-2.5">
                          <div className={`font-medium ${on ? "text-ice" : "text-ink"}`}>{MODEL_NAMES[k] ?? k}</div>
                          <div className="text-xs text-mute">{v.description}</div>
                        </td>
                        <td className="num px-4 py-2.5 text-ink">{pct(o.accuracy, 1)}</td>
                        <td className="num px-4 py-2.5 text-ink">{m4(o.log_loss)}</td>
                        <td className="num px-4 py-2.5 text-ink-2">{m4(o.brier)}</td>
                        <td className="num px-4 py-2.5 text-ink">{o.exact_score_rate != null ? pct(o.exact_score_rate, 1) : "—"}</td>
                        <td className="num px-4 py-2.5 text-ink-2">{o.score_log_loss != null ? o.score_log_loss.toFixed(3) : "—"}</td>
                        <td className="num px-4 py-2.5 text-ink-2">{fx(o.mae_team_goals, 3)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-mute">
              All {primary.overall.n.toLocaleString("en-US")} backtest games. Score log loss is the average surprise at the actual final score under the full score distribution; lower is better.
              Gradient boosting and the blend were evaluated and not adopted because they did not beat the simpler model.
            </p>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <div className="panel p-5">
                <div className="eyebrow mb-3">Is the improvement real? (paired bootstrap, 95% interval)</div>
                <ul className="space-y-2 text-sm">
                  {[
                    ["chosen_vs_baseline_home_log_loss", "Winner log loss vs home-ice baseline"],
                    ["chosen_vs_elo_raw_log_loss", "Winner log loss vs Elo"],
                    ["chosen_vs_scoring_avg_score_log_loss", "Score log loss vs scoring-average baseline"],
                    ["chosen_vs_elo_goals_score_log_loss", "Score log loss vs Elo → goals"],
                    ["projected_vs_confirmed_goalie_log_loss", "Projected vs confirmed goalies, winner log loss"],
                    ["chosen_vs_lean_score_log_loss", "Score log loss vs the same model without lineup inputs"],
                  ].filter(([k]) => bt.significance[k]).map(([k, label]) => (
                    <li key={k} className="flex flex-wrap items-baseline justify-between gap-x-4">
                      <span className="text-ink-2">{label}</span>
                      <span className="num text-ink">
                        {ci(bt.significance[k]!)} <span className={bt.significance[k]!.significant ? "text-good" : "text-mute"}>{bt.significance[k]!.significant ? "significant" : "not significant"}</span>
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="mt-3 text-xs text-mute">Negative means PUCKIQ is better. Knowing the confirmed starter did not measurably change accuracy.</p>
              </div>
              <div className="panel p-5">
                <div className="eyebrow mb-3">What each input group contributes</div>
                <ul className="space-y-2 text-sm">
                  {Object.entries(bt.ablation).map(([k, v]) => (
                    <li key={k} className="flex flex-wrap items-baseline justify-between gap-x-4">
                      <span className="text-ink-2">Remove: {GROUP_NAMES[k] ?? k}</span>
                      <span className="num text-ink">{ci(v.delta_score_log_loss)} <span className={v.delta_score_log_loss.significant ? "text-good" : "text-mute"}>{v.delta_score_log_loss.significant ? "matters" : "no clear effect"}</span></span>
                    </li>
                  ))}
                  {Object.entries(bt.additions).map(([k, v]) => (
                    <li key={k} className="flex flex-wrap items-baseline justify-between gap-x-4">
                      <span className="text-ink-2">Add: {GROUP_NAMES[k] ?? k}</span>
                      <span className="num text-ink">
                        {ci(v.delta_score_log_loss)}{" "}
                        {bt.chosen_groups?.includes(k) ? (
                          <span className="text-good">adopted</span>
                        ) : k.endsWith("_conf") ? (
                          <span className="text-mute">game-day only</span>
                        ) : (
                          <span className="text-mute">{v.delta_score_log_loss.significant && v.delta_score_log_loss.mean_diff < 0 ? "helps, not chosen" : "not adopted"}</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="mt-3 text-xs text-mute">
                  Change in score log loss against the model without lineup inputs. For removals, positive means forecasts got worse without it. For additions, negative means an
                  improvement. Game-day lineups are only known shortly before puck drop, so they show what confirmed lineups would be worth, not what the model uses.
                </p>
              </div>
            </div>
          </Section>

          <Section id="calibration" eyebrow="Calibration" title="Do the probabilities mean what they say?" note={`Expected calibration error ${(primary.ece * 100).toFixed(2)} points across ${primary.overall.n.toLocaleString("en-US")} backtest games. Predicted overtime or shootout rate ${pct(primary.overall.pred_extra_time_rate, 1)}; actual ${pct(primary.overall.actual_extra_time_rate, 1)}.`}>
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="panel p-5">
                <CalibrationChart bins={primary.calibration} minN={20} />
              </div>
              <div className="panel p-5">
                <div className="eyebrow mb-4">Performance by confidence</div>
                <ConfidenceChart bins={primary.by_confidence} />
              </div>
            </div>
          </Section>

          <Section id="seasons" eyebrow="Stability" title="Season by season" note="Each season is forecast by a model trained only on earlier seasons.">
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="panel p-5">
                <div className="eyebrow mb-2">Winner log loss (lower is better)</div>
                <SeasonLines
                  lowerBetter
                  seasons={bt.test_seasons.map(String)}
                  fmt={(v) => v.toFixed(3)}
                  series={[
                    { name: "PUCKIQ", color: "var(--color-home)", values: bt.test_seasons.map((s) => primary.by_season[s]?.log_loss) },
                    { name: "Home-ice baseline", color: "var(--color-mute)", dashed: true, values: bt.test_seasons.map(() => base?.overall.log_loss) },
                  ]}
                />
                <p className="mt-1 text-xs text-mute">The baseline is drawn at its all-season average.</p>
              </div>
              <div className="panel overflow-x-auto p-5">
                <div className="eyebrow mb-3">By season</div>
                <MetricTable rows={bt.test_seasons.map((s) => ({ name: seasonLabel(s) + (s === bt.test_seasons[bt.test_seasons.length - 1] ? " (in progress)" : ""), m: primary.by_season[s] }))} />
              </div>
            </div>
            {primary.by_month && (
              <div className="panel mt-4 overflow-x-auto p-5">
                <div className="eyebrow mb-3">By calendar month, all seasons</div>
                <MetricTable rows={["10", "11", "12", "01", "02", "03", "04", "05", "06", "07", "08", "09"].filter((k) => primary.by_month![k]?.n).map((k) => ({ name: new Date(2000, Number(k) - 1, 1).toLocaleString("en-US", { month: "long" }), m: primary.by_month![k] }))} />
              </div>
            )}
          </Section>
        </>
      )}

      {/* ---- history ---- */}
      <Section id="history" eyebrow="Prediction history" title="Forecasts next to what happened" note="Every game, including the misses.">
        <form action="/accuracy#history" className="mb-4 flex flex-wrap items-end gap-3">
          <label className="text-xs text-ink-2">
            Record
            <select name="source" defaultValue={src} className="mt-1 block h-10 rounded-lg border border-line bg-surface px-2.5 text-sm text-ink">
              <option value="forward">Published forecasts</option>
              <option value="backtest">Backtest (simulation)</option>
            </select>
          </label>
          <label className="text-xs text-ink-2">
            Season
            <select name="season" defaultValue={season} className="mt-1 block h-10 rounded-lg border border-line bg-surface px-2.5 text-sm text-ink">
              <option value="">All</option>
              {(bt?.test_seasons ?? []).slice().reverse().map((s) => (
                <option key={s} value={s}>{seasonLabel(s)}</option>
              ))}
            </select>
          </label>
          <label className="text-xs text-ink-2">
            Team
            <select name="team" defaultValue={team} className="mt-1 block h-10 rounded-lg border border-line bg-surface px-2.5 text-sm text-ink">
              <option value="">All</option>
              {(teams?.teams ?? []).map((t) => (
                <option key={t.abbrev} value={t.abbrev}>{t.name}</option>
              ))}
            </select>
          </label>
          <button type="submit" className="h-10 rounded-lg border border-line bg-surface px-4 text-sm font-medium text-ink transition-colors hover:border-ice/40 active:scale-95">Apply</button>
          <span className="ml-auto">{src === "backtest" ? <Chip tone="warn">Simulation · projected goalies</Chip> : <Chip tone="ice">Published before the game</Chip>}</span>
        </form>
        {!hist || hist.rows.length === 0 ? (
          <Unavailable title="No games match these filters.">{src === "forward" ? "Published forecasts appear here once their games are final." : undefined}</Unavailable>
        ) : (
          <>
            <div className="panel overflow-x-auto">
              <table className="w-full min-w-[820px] text-sm">
                <thead className="text-left text-xs uppercase tracking-wider text-mute">
                  <tr className="border-b border-line">
                    {["Date", "Game", "Projected", "Actual", "Favourite", "Winner", "Exact", "Chance given to actual score", ...(src === "forward" ? ["Goalies"] : [])].map((h) => (
                      <th key={h} className="px-4 py-2.5 font-semibold">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {hist.rows.map((r) => {
                    const fav = r.p_home_win >= 0.5 ? r.home : r.away;
                    return (
                      <tr key={r.game_id} className="border-b border-line-soft last:border-0 hover:bg-raised/60">
                        <td className="num whitespace-nowrap px-4 py-2 text-ink-2">{r.date}</td>
                        <td className="px-4 py-2">
                          <Link href={`/game/${r.game_id}`} className="flex items-center gap-2 text-ink hover:text-ice">
                            <TeamLogo team={r.away} size={18} /> {r.away.abbrev} <span className="text-mute">@</span> <TeamLogo team={r.home} size={18} /> {r.home.abbrev}
                          </Link>
                        </td>
                        <td className="num px-4 py-2 text-ink-2">{r.ml_away_goals}–{r.ml_home_goals}</td>
                        <td className="num px-4 py-2 font-semibold text-ink">{r.away_score}–{r.home_score}{r.last_period_type && r.last_period_type !== "REG" ? <span className="ml-1 font-normal text-mute">{r.last_period_type}</span> : null}</td>
                        <td className="num px-4 py-2 text-ink-2">{fav.abbrev} {pct(Math.max(r.p_home_win, 1 - r.p_home_win))}</td>
                        <td className="px-4 py-2"><span className={r.winner_correct ? "text-good" : "text-bad"}>{r.winner_correct ? "✓ correct" : "✕ wrong"}</span></td>
                        <td className="px-4 py-2">{r.exact_score_hit ? <span className="text-good">✓ hit</span> : <span className="text-mute">miss</span>}</td>
                        <td className="num px-4 py-2 text-ink-2">{pct(r.p_actual_score, 1)}</td>
                        {src === "forward" && <td className="px-4 py-2 text-xs text-ink-2">{r.goalie_state} · v{r.version_no}</td>}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="mt-3 flex items-center justify-between text-xs text-ink-2">
              <span className="num">
                {hist.offset + 1}–{hist.offset + hist.rows.length} of {hist.total.toLocaleString("en-US")}
              </span>
              <span className="flex gap-2">
                {page > 0 && <Link href={link({ page: page - 1 })} className="rounded-md border border-line px-3 py-1.5 hover:border-ice/40 hover:text-ink">Newer</Link>}
                {hist.offset + hist.rows.length < hist.total && <Link href={link({ page: page + 1 })} className="rounded-md border border-line px-3 py-1.5 hover:border-ice/40 hover:text-ink">Older</Link>}
              </span>
            </div>
          </>
        )}
      </Section>

      <Section id="versions" eyebrow="Model version history" title="Which model made which forecasts">
        <div className="panel overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="text-left text-xs uppercase tracking-wider text-mute">
              <tr className="border-b border-line">
                {["Version", "Created", "Trained through", "Inputs", "Forecast versions", "Status"].map((h) => (
                  <th key={h} className="px-4 py-2.5 font-semibold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {acc.model_versions.map((v) => (
                <tr key={v.id} className="border-b border-line-soft last:border-0">
                  <td className="num px-4 py-2.5 text-ink">{v.version}</td>
                  <td className="num px-4 py-2.5 text-ink-2">{stampET(v.created_at)}</td>
                  <td className="num px-4 py-2.5 text-ink-2">{v.trained_through}</td>
                  <td className="px-4 py-2.5 text-xs text-ink-2">{v.feature_groups?.join(", ")}</td>
                  <td className="num px-4 py-2.5 text-ink-2">{v.n_forecasts}</td>
                  <td className="px-4 py-2.5">{v.is_active ? <Chip tone="ice">Active</Chip> : <Chip>Retired</Chip>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </>
  );
}

function MarketPanel({ title, b }: { title: string; b: MarketBlock }) {
  const hrs = b.median_minutes_before / 60;
  const when = hrs >= 1.5 ? `${hrs.toFixed(0)} hours` : `${b.median_minutes_before.toFixed(0)} minutes`;
  const se = b.log_loss_diff_se;
  const clear = se != null && Math.abs(b.log_loss_diff) > 1.96 * se;
  const verdict = !clear ? "No clear difference yet" : b.log_loss_diff < 0 ? "PUCKIQ ahead" : "Market ahead";
  return (
    <div className="panel overflow-x-auto p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="eyebrow">{title}</div>
        <span className="num text-xs text-mute">
          {b.n.toLocaleString("en-US")} games · odds recorded a median of {when} before the start
        </span>
      </div>
      <table className="mt-3 w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wider text-mute">
          <tr className="border-b border-line">
            {["", "Winners", "Log loss", "Brier"].map((h) => (
              <th key={h} className="py-2 pr-3 font-semibold">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {([["PUCKIQ", b.puckiq], ["Betting market", b.market]] as const).map(([name, m]) => (
            <tr key={name} className="border-b border-line-soft last:border-0">
              <td className="py-2 pr-3 text-ink-2">{name}</td>
              <td className="num py-2 pr-3 text-ink">{pct(m.accuracy, 1)}</td>
              <td className="num py-2 pr-3 text-ink">{m.log_loss.toFixed(4)}</td>
              <td className="num py-2 pr-3 text-ink-2">{m.brier.toFixed(4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-sm text-ink-2">
        <strong className="text-ink">{verdict}.</strong> PUCKIQ&apos;s log loss is {Math.abs(b.log_loss_diff).toFixed(4)} {b.log_loss_diff < 0 ? "lower (better)" : "higher (worse)"}
        {se != null ? `, give or take ${(1.96 * se).toFixed(4)}` : ""}. The two differ by {(b.mean_abs_gap * 100).toFixed(1)} points of win probability on a typical game.
        {b.n < 200 && <span className="text-warn"> With {b.n} games this says very little.</span>}
      </p>
      {b.totals && (
        <p className="mt-2 text-xs text-mute">
          Over/under on the posted total ({b.totals.n} games): Brier {b.totals.puckiq_brier.toFixed(4)} for PUCKIQ, {b.totals.market_brier.toFixed(4)} for the market; lower is
          better and 0.25 is a coin flip.
        </p>
      )}
    </div>
  );
}

function MetricTable({ rows }: { rows: { name: string; m?: Metrics }[] }) {
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs uppercase tracking-wider text-mute">
        <tr className="border-b border-line">
          {["", "Games", "Winners", "Log loss", "Exact", "Goal error"].map((h) => (
            <th key={h} className="py-2 pr-3 font-semibold">{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.filter((r) => r.m && r.m.n > 0).map((r) => (
          <tr key={r.name} className="border-b border-line-soft last:border-0">
            <td className="py-2 pr-3 text-ink-2">{r.name}</td>
            <td className="num py-2 pr-3 text-ink-2">{r.m!.n.toLocaleString("en-US")}</td>
            <td className="num py-2 pr-3 text-ink">{pct(r.m!.accuracy, 1)}</td>
            <td className="num py-2 pr-3 text-ink">{m4(r.m!.log_loss)}</td>
            <td className="num py-2 pr-3 text-ink-2">{pct(r.m!.exact_score_rate, 1)}</td>
            <td className="num py-2 pr-3 text-ink-2">{fx(r.m!.mae_team_goals, 2)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
