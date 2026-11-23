"""Walk-forward (expanding window) backtest.

For each test season S every model is fitted on seasons before S only, then
scores every game of S. Features for a game depend only on earlier games
(see features/build.py), so no information from the predicted game, or from
later games, reaches the forecast."""
from __future__ import annotations

import datetime as dt
import json
import logging

import numpy as np
import pandas as pd

from .. import config, db
from ..models.design import GROUPS, team_rows
from ..models.rates import GBMRateModel, GLMRateModel
from ..sim.simulator import SimParams, fit_sim_params
from . import metrics as M

log = logging.getLogger("puckiq.backtest")

# Lean candidate: drops the groups whose removal did not hurt in ablation.
LEAN = ["home", "env", "xga", "goals", "goalie", "rest", "elo"]
CORE = ["home", "env", "xg5", "xga", "goals", "special", "finishing", "goalie", "rest"]
# Groups removed one at a time from the lean model, and groups added one at a
# time to it, to measure what each contributes out of sample.
ABLATE = ["xga", "goals", "elo", "goalie", "rest"]
ADD = ["xg5", "special", "finishing"]
SPREAD_GRID = (1.0, 1.05, 1.1, 1.15, 1.2, 1.25, 1.3, 1.4)
# Candidates in order of increasing complexity.
CANDIDATES = ["xg_lean", "xg_lean_cal", "xg_glm", "xg_glm_elo", "xg_glm_elo_cal", "blend", "gbm"]


def apply_spread(lam_h, lam_a, c: float):
    """Stretch the log ratio of the two scoring rates by `c`, leaving their
    geometric mean unchanged. c > 1 makes forecasts more decisive."""
    d = 0.5 * (c - 1.0) * (np.log(lam_h) - np.log(lam_a))
    return lam_h * np.exp(d), lam_a * np.exp(-d)


def fit_spread(lam_h, lam_a, sp, playoff, hs, as_) -> float:
    """Spread factor minimising score log loss on the supplied (already
    out-of-sample) predictions."""
    best = (9e9, 1.0)
    for c in SPREAD_GRID:
        lh, la = apply_spread(lam_h, lam_a, c)
        sc = M.game_predictions(lh, la, sp, playoff, hs, as_)
        v = float(-np.log(np.clip(sc["p_actual_score"], 1e-6, 1)).mean())
        if v < best[0] - 1e-6:
            best = (v, c)
    return best[1]

# Seasons used to choose between candidate architectures. Later seasons are a
# confirmation set that plays no part in any modelling decision.
SELECTION_SEASONS = (config.FIRST_TEST_SEASON, 2023)

GLM_SPECS: dict[str, list[str]] = {
    "baseline_home": ["home", "env"],
    "elo_goals": ["home", "env", "elo"],
    "scoring_avg": ["home", "env", "goals"],
    "xg_glm": CORE,
    "xg_glm_elo": CORE + ["elo"],
    "xg_lean": LEAN,
    **{f"ablate_{g}": [x for x in LEAN if x != g] for g in ABLATE},
    **{f"add_{g}": LEAN + [g] for g in ADD},
}

DESCRIPTIONS = {
    "baseline_home": "Home-ice baseline: league-average scoring plus home advantage only.",
    "elo_raw": "Elo win probability used directly (winner metrics only).",
    "elo_goals": "Elo rating difference mapped to scoring rates.",
    "scoring_avg": "Historical scoring baseline: opponent-adjusted goals for and against.",
    "xg_glm": "Expected-goals Poisson model, confirmed starting goalies.",
    "xg_lean_proj": "Lean Poisson model, projected starting goalies (pre-confirmation).",
    "xg_glm_elo": "Expected-goals Poisson model plus Elo rating.",
    "gbm": "Gradient-boosted trees (LightGBM, Poisson objective).",
    "blend": "Log-linear blend of the Poisson model and gradient boosting.",
    "xg_lean": "Lean Poisson model: all-situations xG, goals, Elo, goaltending, rest.",
    "xg_lean_cal": "Lean Poisson model with spread calibration.",
    "xg_glm_elo_cal": "Expected-goals Poisson model plus Elo, with spread calibration.",
}


def _frame(rows: pd.DataFrame, lam: np.ndarray) -> pd.DataFrame:
    d = pd.DataFrame({"game_id": rows["game_id"].values, "is_home": rows["is_home"].values, "lam": lam})
    h = d[d["is_home"] == 1].set_index("game_id")["lam"]
    a = d[d["is_home"] == 0].set_index("game_id")["lam"]
    return pd.DataFrame({"lam_home": h, "lam_away": a.reindex(h.index)})


def run(feat: pd.DataFrame, with_gbm: bool = True, persist: bool = True) -> dict:
    feat = feat[feat["final"] & (feat["season"] >= config.FIRST_MODEL_SEASON)].copy()
    rc, rp = team_rows(feat, "confirmed"), team_rows(feat, "projected")
    G = feat.set_index("game_id")
    G["went_extra"] = (G["last_period_type"] != "REG").astype(float)
    seasons = sorted(int(s) for s in feat["season"].unique() if s >= config.FIRST_TEST_SEASON)
    preds: dict[str, list[pd.DataFrame]] = {}
    sim_by_season: dict[int, dict] = {}
    blend_w: dict[int, float] = {}
    spread_c: dict[str, dict[int, float]] = {"xg_lean": {}, "xg_glm_elo": {}}
    coef_log: dict[int, dict] = {}

    for S in seasons:
        tr_c = rc[rc["season"] < S]
        te_c, te_p = rc[rc["season"] == S], rp[rp["season"] == S]
        gids = G.index[G["season"] == S]
        g = G.loc[gids]
        hs, as_, ex, po = g["home_score"].astype(int).values, g["away_score"].astype(int).values, g["went_extra"].values, g["playoff"].values == 1

        models = {name: GLMRateModel(groups).fit(tr_c) for name, groups in GLM_SPECS.items()}
        core = models["xg_lean"]
        coef_log[S] = dict(zip(["const"] + core.cols, core.beta.round(5).tolist()))

        # Simulator parameters from the training window (in-sample rates of a
        # ~20-parameter GLM on 10k+ games: negligible optimism).
        tr_games = G[G["season"] < S]
        lam_tr = _frame(tr_c, core.predict(tr_c)).reindex(tr_games.index)
        sp = fit_sim_params(lam_tr["lam_home"].values, lam_tr["lam_away"].values,
                            tr_games["home_reg_goals"].astype(int).values, tr_games["away_reg_goals"].astype(int).values,
                            tr_games["went_extra"].values == 1, (tr_games["home_score"] > tr_games["away_score"]).values,
                            (tr_games["last_period_type"] == "SO").values, tr_games["playoff"].values == 1)
        # shootout share and OT model only from the 3-on-3 era (2015-16 onward)
        era = tr_games[tr_games["season"] >= 2015]
        if (era["went_extra"] == 1).sum() > 300:
            lam_era = lam_tr.reindex(era.index)
            sp_era = fit_sim_params(lam_era["lam_home"].values, lam_era["lam_away"].values,
                                    era["home_reg_goals"].astype(int).values, era["away_reg_goals"].astype(int).values,
                                    era["went_extra"].values == 1, (era["home_score"] > era["away_score"]).values,
                                    (era["last_period_type"] == "SO").values, era["playoff"].values == 1)
            sp.p_shootout, sp.ot_home_intercept, sp.ot_strength_slope, sp.so_strength_slope = \
                sp_era.p_shootout, sp_era.ot_home_intercept, sp_era.ot_strength_slope, sp_era.so_strength_slope
        sim_by_season[S] = sp.to_json()

        lam: dict[str, pd.DataFrame] = {name: _frame(te_c, m.predict(te_c)).reindex(gids) for name, m in models.items()}
        lam["xg_lean_proj"] = _frame(te_p, core.predict(te_p)).reindex(gids)
        if with_gbm:
            gbm = GBMRateModel(CORE).fit(tr_c)
            lam["gbm"] = _frame(te_c, gbm.predict(te_c)).reindex(gids)
            # blend weight from earlier out-of-sample seasons only
            w = 0.0
            if "gbm" in preds and len(preds["gbm"]) >= 2:
                prev_g, prev_l = pd.concat(preds["gbm"]), pd.concat(preds["xg_glm"])
                pg = G.loc[prev_g.index]
                best = (9e9, 0.0)
                for cand in (0.0, 0.25, 0.5, 0.75, 1.0):
                    lh = np.exp((1 - cand) * np.log(prev_l["lam_home"]) + cand * np.log(prev_g["lam_home"]))
                    la = np.exp((1 - cand) * np.log(prev_l["lam_away"]) + cand * np.log(prev_g["lam_away"]))
                    sc = M.game_predictions(lh.values, la.values, sp, pg["playoff"].values == 1,
                                            pg["home_score"].astype(int).values, pg["away_score"].astype(int).values)
                    v = float(-np.log(np.clip(sc["p_actual_score"], 1e-6, 1)).mean())
                    if v < best[0] - 1e-6:
                        best = (v, cand)
                w = best[1]
            blend_w[S] = w
            lam["blend"] = pd.DataFrame({
                "lam_home": np.exp((1 - w) * np.log(lam["xg_glm"]["lam_home"]) + w * np.log(lam["gbm"]["lam_home"])),
                "lam_away": np.exp((1 - w) * np.log(lam["xg_glm"]["lam_away"]) + w * np.log(lam["gbm"]["lam_away"])),
            }, index=gids)

        # spread calibration fitted on earlier out-of-sample seasons only
        for base in ("xg_lean", "xg_glm_elo"):
            c = 1.0
            if base in preds and len(preds[base]) >= 2:
                prev = pd.concat(preds[base])
                pg = G.loc[prev.index]
                c = fit_spread(prev["lam_home"].values, prev["lam_away"].values, sp, pg["playoff"].values == 1,
                               pg["home_score"].astype(int).values, pg["away_score"].astype(int).values)
            spread_c[base][S] = c
            lh, la = apply_spread(lam[base]["lam_home"].values, lam[base]["lam_away"].values, c)
            lam[f"{base}_cal"] = pd.DataFrame({"lam_home": lh, "lam_away": la}, index=gids)

        for name, l in lam.items():
            p = M.game_predictions(l["lam_home"].values, l["lam_away"].values, sp, po, hs, as_)
            p = M.add_scores(p, hs, as_, ex)
            p.index = gids
            p["season"], p["game_date"], p["playoff"] = S, g["game_date"].values, po
            preds.setdefault(name, []).append(p)

        # raw Elo probability (no goals model)
        hfa = _elo_hfa()
        pe = 1.0 / (1.0 + 10 ** (-(g["h_elo"].values + hfa - g["a_elo"].values) / 400.0))
        e = pd.DataFrame({"p_home_win": pe}, index=gids)
        y = (hs > as_).astype(float)
        e["home_won"] = y
        e["ll"] = -(y * np.log(pe) + (1 - y) * np.log(1 - pe))
        e["brier"] = (pe - y) ** 2
        e["correct"] = ((pe >= 0.5) == (y == 1)).astype(float)
        e["conf"] = np.maximum(pe, 1 - pe)
        e["season"], e["game_date"], e["playoff"] = S, g["game_date"].values, po
        preds.setdefault("elo_raw", []).append(e)
        log.info("season %s done: n=%d  xg_glm ll=%.4f  baseline ll=%.4f", S, len(gids),
                 preds["xg_glm"][-1]["ll"].mean(), preds["baseline_home"][-1]["ll"].mean())

    all_preds = {k: pd.concat(v) for k, v in preds.items()}
    summary = build_summary(all_preds, seasons, sim_by_season, blend_w, coef_log, spread_c)
    if persist:
        save(all_preds, summary)
    return {"summary": summary, "preds": all_preds}


def _elo_hfa() -> float:
    from ..features.params import load_params
    return load_params().elo_hfa


def _describe(name: str) -> str:
    if name in DESCRIPTIONS:
        return DESCRIPTIONS[name]
    kind, _, group = name.partition("_")
    return f"Lean model {'without' if kind == 'ablate' else 'plus'} the '{group}' feature group."


def _window(p: pd.DataFrame, lo: int, hi: int) -> pd.DataFrame:
    return p[(p["season"] >= lo) & (p["season"] <= hi)]


def build_summary(preds: dict[str, pd.DataFrame], seasons: list[int], sims: dict, blend_w: dict, coefs: dict, spread_c: dict) -> dict:
    sel_lo, sel_hi = SELECTION_SEASONS
    conf_lo = sel_hi + 1
    last = max(seasons)
    models = {}
    for name, p in preds.items():
        models[name] = {
            "description": _describe(name),
            "overall": M.summarise(p),
            "selection_window": M.summarise(_window(p, sel_lo, sel_hi)),
            "confirmation_window": M.summarise(_window(p, conf_lo, last)),
            "regular_season": M.summarise(p[~p["playoff"]]),
            "playoffs": M.summarise(p[p["playoff"]]),
            "by_season": {str(s): M.summarise(p[p["season"] == s]) for s in seasons},
            "calibration": M.calibration(p),
            "ece": M.expected_calibration_error(p),
            "by_confidence": M.by_confidence(p),
        }
        if name in ("xg_lean", "xg_lean_proj"):
            d = pd.to_datetime(p["game_date"])
            models[name]["by_month"] = {f"{m:02d}": M.summarise(p[d.dt.month == m]) for m in sorted(d.dt.month.unique())}

    # --- architecture selection on the selection window only ---------------
    # Rule: find the candidate with the lowest score log loss, then choose
    # the SIMPLEST candidate that is not significantly worse than it on
    # either score log loss or winner log loss (paired bootstrap, 95%).
    cands = [c for c in CANDIDATES if c in preds]
    sel = {c: _window(preds[c], sel_lo, sel_hi) for c in cands}
    best = min(cands, key=lambda c: sel[c]["score_ll"].mean())
    tests, chosen = {}, None
    for c in cands:
        t_score = M.paired_bootstrap(sel[c]["score_ll"].values, sel[best]["score_ll"].values)
        t_win = M.paired_bootstrap(sel[c]["ll"].values, sel[best]["ll"].values)
        worse = (t_score["significant"] and t_score["mean_diff"] > 0) or (t_win["significant"] and t_win["mean_diff"] > 0)
        tests[c] = {"score_log_loss_vs_best": t_score, "log_loss_vs_best": t_win, "significantly_worse": bool(worse),
                    "score_log_loss": float(sel[c]["score_ll"].mean()), "log_loss": float(sel[c]["ll"].mean())}
        if chosen is None and not worse:
            chosen = c
    reason = (f"Lowest score log loss on the selection seasons: {best}. Chosen: {chosen}, the simplest candidate "
              f"not significantly worse than it on score log loss or winner log loss.")

    def vs(a: str, b: str, col: str, p_a=None) -> dict:
        pa, pb = preds[a], preds[b]
        idx = pa.index.intersection(pb.index)
        return M.paired_bootstrap(pa.loc[idx, col].values, pb.loc[idx, col].values)

    significance = {
        "chosen_vs_baseline_home_log_loss": vs(chosen, "baseline_home", "ll"),
        "chosen_vs_elo_raw_log_loss": vs(chosen, "elo_raw", "ll"),
        "chosen_vs_elo_goals_score_log_loss": vs(chosen, "elo_goals", "score_ll"),
        "chosen_vs_scoring_avg_score_log_loss": vs(chosen, "scoring_avg", "score_ll"),
        "chosen_vs_baseline_home_score_log_loss": vs(chosen, "baseline_home", "score_ll"),
        "projected_vs_confirmed_goalie_log_loss": vs("xg_lean_proj", "xg_lean", "ll"),
        "projected_vs_confirmed_goalie_score_log_loss": vs("xg_lean_proj", "xg_lean", "score_ll"),
    }
    ablation = {}
    for g in ABLATE:
        name = f"ablate_{g}"
        ablation[g] = {
            "features": GROUPS[g],
            # positive = removing the group makes forecasts worse
            "delta_log_loss": vs(name, "xg_lean", "ll"),
            "delta_score_log_loss": vs(name, "xg_lean", "score_ll"),
        }
    additions = {}
    for g in ADD:
        additions[g] = {
            "features": GROUPS[g],
            # negative = adding the group improves forecasts
            "delta_log_loss": vs(f"add_{g}", "xg_lean", "ll"),
            "delta_score_log_loss": vs(f"add_{g}", "xg_lean", "score_ll"),
        }
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "method": "Expanding-window walk-forward by season. Models are refitted before each test season on all earlier seasons.",
        "first_training_season": config.FIRST_MODEL_SEASON,
        "test_seasons": seasons,
        "selection_seasons": [sel_lo, sel_hi],
        "confirmation_seasons": [conf_lo, last],
        "chosen_model": chosen,
        "selection_reason": reason,
        "selection_tests": tests,
        "significance": significance,
        "ablation": ablation,
        "additions": additions,
        "best_on_selection": best,
        "candidates_by_complexity": cands,
        "blend_weight_by_season": {str(k): v for k, v in blend_w.items()},
        "spread_by_season": {b: {str(k): v for k, v in d.items()} for b, d in spread_c.items()},
        "simulator_params_by_season": {str(k): v for k, v in sims.items()},
        "coefficients_by_season": {str(k): v for k, v in coefs.items()},
        "models": models,
    }


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    raise TypeError(type(o).__name__)


def save(preds: dict[str, pd.DataFrame], summary: dict) -> None:
    out = config.ARTIFACT_DIR / "backtest"
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(summary, indent=1, default=_json_default)
    (out / "summary.json").write_text(text)
    db.put_document("backtest_summary", json.loads(text))
    keep = ["baseline_home", "elo_goals", "scoring_avg", "xg_lean", "xg_lean_proj"]
    if summary["chosen_model"] not in keep:
        keep.append(summary["chosen_model"])
    rows = []
    for name in keep:
        p = preds[name]
        for gid, r in p.iterrows():
            rows.append(dict(model_key=name, game_id=int(gid), season=int(r["season"]), game_date=r["game_date"],
                             p_home_win=float(r["p_home_win"]), lam_home=float(r["lam_home"]), lam_away=float(r["lam_away"]),
                             exp_home_goals=float(r["exp_home_goals"]), exp_away_goals=float(r["exp_away_goals"]),
                             ml_home_goals=int(r["ml_home_goals"]), ml_away_goals=int(r["ml_away_goals"]),
                             p_actual_score=float(r["p_actual_score"]), p_overtime_or_so=float(r["p_tie_reg"])))
    with db.begin() as c:
        c.execute(db.backtest_predictions.delete())
        db.upsert(c, db.backtest_predictions, rows)
    log.info("saved %d backtest rows", len(rows))
