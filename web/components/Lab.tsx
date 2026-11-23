"use client";

import { useEffect, useRef, useState } from "react";

import { fx, pct } from "@/lib/format";
import type { LabInfo, LabResponse, TeamBrief } from "@/lib/types";

import { ScoreMatrix } from "./interactive";

type Side = "home" | "away";
type State = { home_goalie_id: number | null; away_goalie_id: number | null; home_b2b: boolean; away_b2b: boolean; home_g7: number; away_g7: number; home_out: number[]; away_out: number[] };

const delta = (a: number, b: number, digits = 1, scale = 100, unit = " pts") => {
  const d = (a - b) * scale;
  if (Math.abs(d) < 0.05 / (scale === 100 ? 1 : 10)) return "no change";
  return `${d > 0 ? "+" : "−"}${Math.abs(d).toFixed(digits)}${unit}`;
};

/** Prediction Lab: re-runs the production model and simulator with the
 * user's hypothetical inputs. Controls exist only for inputs the model
 * actually has; everything else is shown as unsupported, with the reason. */
export function Lab({ gameId, lab, home, away }: { gameId: number; lab: LabInfo; home: TeamBrief; away: TeamBrief }) {
  const base: State = {
    home_goalie_id: null, away_goalie_id: null,
    home_b2b: lab.current.home_b2b, away_b2b: lab.current.away_b2b,
    home_g7: lab.current.home_g7, away_g7: lab.current.away_g7,
    home_out: [], away_out: [],
  };
  const [s, setS] = useState<State>(base);
  const [res, setRes] = useState<LabResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const seq = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const changed = JSON.stringify(s) !== JSON.stringify(base);

  const run = async (st: State) => {
    const id = ++seq.current;
    const body: Record<string, number | boolean | number[]> = {};
    if (st.home_goalie_id != null) body.home_goalie_id = st.home_goalie_id;
    if (st.away_goalie_id != null) body.away_goalie_id = st.away_goalie_id;
    for (const side of ["home", "away"] as Side[]) {
      if (st[`${side}_b2b`] !== lab.current[`${side}_b2b`]) body[`${side}_b2b`] = st[`${side}_b2b`];
      if (st[`${side}_g7`] !== lab.current[`${side}_g7`]) body[`${side}_g7`] = st[`${side}_g7`];
      if (st[`${side}_out`].length) body[`${side}_out`] = st[`${side}_out`];
    }
    try {
      const r = await fetch(`/api/puckiq/v1/games/${gameId}/lab`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      const j = await r.json();
      if (id !== seq.current) return;
      if (!r.ok) {
        setErr(typeof j.detail === "string" ? j.detail : "That combination is outside the range the model was validated on.");
        setRes(null);
      } else setRes(j as LabResponse);
    } catch {
      if (id === seq.current) setErr("The prediction service could not be reached. No scenario result is shown.");
    } finally {
      if (id === seq.current) setBusy(false);
    }
  };

  // Re-run (debounced) whenever the user changes an input.
  const apply = (next: State) => {
    // a back-to-back implies at least one game in the last 7 days
    for (const side of ["home", "away"] as Side[]) if (next[`${side}_b2b`] && next[`${side}_g7`] < 1) next[`${side}_g7`] = 1;
    setS(next);
    if (timer.current) clearTimeout(timer.current);
    seq.current++;
    setErr(null);
    if (JSON.stringify(next) === JSON.stringify(base)) {
      setRes(null);
      setBusy(false);
      return;
    }
    setBusy(true);
    timer.current = setTimeout(() => run(next), 350);
  };

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const set = <K extends keyof State>(k: K, v: State[K]) => apply({ ...s, [k]: v });

  const teams: { side: Side; t: TeamBrief }[] = [{ side: "away", t: away }, { side: "home", t: home }];
  const b = res?.baseline, sc = res?.scenario;

  return (
    <div className="panel overflow-hidden">
      <div className="grid gap-px bg-line lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="bg-surface p-5">
          <div className="flex items-center justify-between">
            <div className="eyebrow">Scenario inputs</div>
            <button type="button" onClick={() => apply(base)} disabled={!changed} className="rounded-md border border-line px-2.5 py-1 text-xs font-medium text-ink-2 transition-colors enabled:hover:border-ice/40 enabled:hover:text-ink enabled:active:scale-95 disabled:opacity-40">
              Reset
            </button>
          </div>
          <div className="mt-4 grid gap-5 sm:grid-cols-2">
            {teams.map(({ side, t }) => (
              <fieldset key={side} className="space-y-4">
                <legend className="display text-lg font-semibold tracking-wide">{t.abbrev}</legend>
                <label className="block">
                  <span className="text-xs text-ink-2">Starting goalie</span>
                  <select
                    value={s[`${side}_goalie_id`] ?? ""}
                    onChange={(e) => set(`${side}_goalie_id`, e.target.value === "" ? null : Number(e.target.value))}
                    className="mt-1 h-10 w-full rounded-lg border border-line bg-raised px-2.5 text-sm text-ink"
                  >
                    <option value="">Projected ({lab.goalies[side].map((g) => `${g.name.split(" ").slice(-1)[0]} ${Math.round(g.prob * 100)}%`).join(", ")})</option>
                    {lab.goalies[side].filter((g) => g.pid > 0).map((g) => (
                      <option key={g.pid} value={g.pid}>
                        {g.name} starts
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex cursor-pointer items-center justify-between gap-3">
                  <span className="text-xs text-ink-2">Second night of a back-to-back</span>
                  <input type="checkbox" role="switch" checked={s[`${side}_b2b`]} onChange={(e) => set(`${side}_b2b`, e.target.checked)} className="peer sr-only" />
                  <span aria-hidden className="relative h-6 w-11 shrink-0 rounded-full bg-raised ring-1 ring-line transition-colors peer-checked:bg-ice/80 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-ice after:absolute after:left-0.5 after:top-0.5 after:h-5 after:w-5 after:rounded-full after:bg-ink after:transition-transform peer-checked:after:translate-x-5" />
                </label>
                <div>
                  <div className="flex items-center justify-between text-xs text-ink-2">
                    <span id={`${side}-g7`}>Games in the last 7 days</span>
                    <span className="num text-ink">{s[`${side}_g7`]}</span>
                  </div>
                  <input
                    type="range"
                    aria-labelledby={`${side}-g7`}
                    min={Math.max(lab.domain.g7[0], s[`${side}_b2b`] ? 1 : 0)}
                    max={lab.domain.g7[1]}
                    step={1}
                    value={s[`${side}_g7`]}
                    onChange={(e) => set(`${side}_g7`, Number(e.target.value))}
                    className="mt-2 w-full accent-[var(--color-ice)]"
                  />
                </div>
                {lab.skaters && lab.skaters[side].length > 0 && (
                  <div>
                    <div className="flex items-center justify-between text-xs text-ink-2">
                      <span id={`${side}-out`}>Skaters out</span>
                      <span className="num text-mute">{s[`${side}_out`].length} of 3</span>
                    </div>
                    <div role="group" aria-labelledby={`${side}-out`} className="mt-2 flex flex-wrap gap-1.5">
                      {lab.skaters[side].map((k) => {
                        const on = s[`${side}_out`].includes(k.pid);
                        const full = !on && s[`${side}_out`].length >= 3;
                        return (
                          <button
                            key={k.pid}
                            type="button"
                            aria-pressed={on}
                            disabled={full}
                            title={`${k.name}: ${k.toi.toFixed(1)} minutes per game`}
                            onClick={() => set(`${side}_out`, on ? s[`${side}_out`].filter((x) => x !== k.pid) : [...s[`${side}_out`], k.pid])}
                            className={`rounded-md border px-2 py-1 text-[0.7rem] transition-colors active:scale-95 disabled:opacity-40 ${on ? "border-bad/60 bg-bad/15 text-ink line-through" : "border-line text-ink-2 hover:border-ice/40 hover:text-ink"}`}
                          >
                            {k.name.split(" ").slice(-1)[0]} <span className="num text-mute">{k.toi.toFixed(0)}</span>
                          </button>
                        );
                      })}
                    </div>
                    <p className="mt-1.5 text-[0.65rem] leading-snug text-mute">
                      From the last lineup dressed; the number is minutes per game. A skater marked out is replaced by a league-average unknown.
                    </p>
                  </div>
                )}
              </fieldset>
            ))}
          </div>
          <div className="mt-6 border-t border-line-soft pt-4">
            <div className="eyebrow mb-2">Not available</div>
            <ul className="space-y-2">
              {lab.unsupported.map((u) => (
                <li key={u.control} className="hatch rounded-lg border border-dashed border-line px-3 py-2 text-xs text-ink-2">
                  <span className="font-semibold text-ink">{u.control === "player_unavailable" ? "Key skater unavailable" : u.control === "goalie_injury_or_form" ? "Goalie form or injury" : "Extra days of rest"}</span>
                  <span className="mt-0.5 block text-mute">{u.reason}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="bg-surface p-5" aria-live="polite" aria-busy={busy}>
          <div className="flex items-center justify-between">
            <div className="eyebrow">Scenario result</div>
            {busy && <span className="num text-xs text-ice">simulating…</span>}
          </div>
          {err && <p role="alert" className="mt-4 rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-ink">{err}</p>}
          {!changed && !err && <p className="mt-4 text-sm text-ink-2">Change an input to re-run the model. Results are recalculated by the same model and simulator as the published forecast; nothing here is saved or published.</p>}
          {changed && b && sc && (
            <div className={`mt-4 transition-opacity ${busy ? "opacity-50" : "opacity-100"}`}>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {[
                  { l: `${home.abbrev} win`, v: pct(sc.p_home_win, 1), d: delta(sc.p_home_win, b.p_home_win) },
                  { l: `${away.abbrev} win`, v: pct(sc.p_away_win, 1), d: delta(sc.p_away_win, b.p_away_win) },
                  { l: `${home.abbrev} expected goals`, v: fx(sc.exp_home_goals), d: delta(sc.exp_home_goals, b.exp_home_goals, 2, 1, "") },
                  { l: `${away.abbrev} expected goals`, v: fx(sc.exp_away_goals), d: delta(sc.exp_away_goals, b.exp_away_goals, 2, 1, "") },
                ].map((x) => (
                  <div key={x.l} className="rounded-lg bg-raised px-3 py-2.5">
                    <div className="text-[0.7rem] text-mute">{x.l}</div>
                    <div className="num mt-0.5 text-xl font-semibold text-ink">{x.v}</div>
                    <div className="num text-xs text-ice">{x.d}</div>
                  </div>
                ))}
              </div>
              <div className="mt-4 flex flex-wrap items-baseline gap-x-6 gap-y-1 text-sm">
                <span className="text-ink-2">
                  Most likely score{" "}
                  <span className="num font-semibold text-ink">
                    {away.abbrev} {sc.ml_away_goals}–{sc.ml_home_goals} {home.abbrev}
                  </span>{" "}
                  <span className="num text-mute">({pct(sc.ml_score_prob, 1)})</span>
                </span>
                <span className="text-ink-2">
                  Unchanged inputs <span className="num text-ink">{away.abbrev} {b.ml_away_goals}–{b.ml_home_goals} {home.abbrev}</span>
                </span>
                <span className="text-ink-2">
                  OT / SO <span className="num text-ink">{pct(sc.p_overtime + sc.p_shootout, 1)}</span>
                </span>
              </div>
              {res.warnings.map((w) => (
                <p key={w} className="mt-3 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-ink">{w}</p>
              ))}
              <div className="mt-5">
                <ScoreMatrix matrix={sc.matrix} compare={b.matrix} homeAbbrev={home.abbrev} awayAbbrev={away.abbrev} overflow={0} />
              </div>
              <p className="mt-3 text-xs text-mute">
                {sc.n_sims.toLocaleString("en-US")} simulations with the same random seed for both runs, so differences come from the inputs, not chance (standard error of the win
                probability ±{(sc.mc_standard_error * 100).toFixed(1)} pts).
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
