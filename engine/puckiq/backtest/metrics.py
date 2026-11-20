"""Forecast evaluation metrics. All are computed from predictions made
without knowledge of the game being predicted."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..sim.simulator import SimParams, batch_matrices

EPS = 1e-9
CONF_BUCKETS = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.01)]


def game_predictions(lam_h, lam_a, sp: SimParams, playoff, home_score, away_score) -> pd.DataFrame:
    """Per-game forecast summary plus the scores needed for evaluation."""
    reg, fin = batch_matrices(lam_h, lam_a, sp, playoff)
    n = fin.shape[1]
    g = np.arange(n)
    I, J = np.meshgrid(g, g, indexing="ij")
    mass = fin.sum(axis=(1, 2))
    p_home = np.where(I > J, fin, 0).sum(axis=(1, 2)) / mass
    exp_h = (fin.sum(axis=2) * g).sum(axis=1) / mass
    exp_a = (fin.sum(axis=1) * g).sum(axis=1) / mass
    flat = fin.reshape(len(fin), -1)
    arg = flat.argmax(axis=1)
    ml_h, ml_a = arg // n, arg % n
    hs, as_ = np.asarray(home_score, int), np.asarray(away_score, int)
    inside = (hs < n) & (as_ < n)
    p_actual = np.where(inside, fin[np.arange(len(fin)), np.minimum(hs, n - 1), np.minimum(as_, n - 1)], 0.0)
    p_tie = reg[:, g, g].sum(axis=1)
    # ranked probability scores on total goals and goal difference
    tot = np.zeros((len(fin), 2 * n - 1))
    dif = np.zeros((len(fin), 2 * n - 1))
    for t in range(2 * n - 1):
        tot[:, t] = np.where(I + J == t, fin, 0).sum(axis=(1, 2))
        dif[:, t] = np.where(I - J == t - (n - 1), fin, 0).sum(axis=(1, 2))
    tot /= mass[:, None]
    dif /= mass[:, None]
    ks = np.arange(2 * n - 1)
    rps_tot = ((tot.cumsum(axis=1) - (ks[None, :] >= np.minimum(hs + as_, 2 * n - 2)[:, None])) ** 2).sum(axis=1)
    rps_dif = ((dif.cumsum(axis=1) - (ks[None, :] >= np.clip(hs - as_ + n - 1, 0, 2 * n - 2)[:, None])) ** 2).sum(axis=1)
    return pd.DataFrame({
        "lam_home": lam_h, "lam_away": lam_a, "p_home_win": p_home, "exp_home_goals": exp_h, "exp_away_goals": exp_a,
        "ml_home_goals": ml_h, "ml_away_goals": ml_a, "ml_score_prob": flat.max(axis=1), "p_actual_score": p_actual,
        "p_tie_reg": p_tie, "rps_total": rps_tot, "rps_diff": rps_dif,
    })


def add_scores(pred: pd.DataFrame, home_score, away_score, went_extra) -> pd.DataFrame:
    p = pred.copy()
    hs, as_ = np.asarray(home_score, float), np.asarray(away_score, float)
    y = (hs > as_).astype(float)
    ph = np.clip(p["p_home_win"].to_numpy(), EPS, 1 - EPS)
    p["home_won"] = y
    p["ll"] = -(y * np.log(ph) + (1 - y) * np.log(1 - ph))
    p["brier"] = (ph - y) ** 2
    p["correct"] = ((ph >= 0.5) == (y == 1)).astype(float)
    p["conf"] = np.maximum(ph, 1 - ph)
    p["exact"] = ((p["ml_home_goals"] == hs) & (p["ml_away_goals"] == as_)).astype(float)
    p["score_ll"] = -np.log(np.clip(p["p_actual_score"], 1e-6, 1))
    p["ae_home"] = np.abs(p["exp_home_goals"] - hs)
    p["ae_away"] = np.abs(p["exp_away_goals"] - as_)
    p["ae_total"] = np.abs(p["exp_home_goals"] + p["exp_away_goals"] - hs - as_)
    p["ae_diff"] = np.abs(p["exp_home_goals"] - p["exp_away_goals"] - (hs - as_))
    p["went_extra"] = np.asarray(went_extra, float)
    return p


def summarise(p: pd.DataFrame) -> dict:
    if len(p) == 0:
        return {"n": 0}
    out = {
        "n": int(len(p)),
        "accuracy": float(p["correct"].mean()),
        "log_loss": float(p["ll"].mean()),
        "brier": float(p["brier"].mean()),
        "home_win_rate": float(p["home_won"].mean()),
        "mean_p_home": float(p["p_home_win"].mean()),
    }
    if "score_ll" in p:
        out.update({
            "exact_score_rate": float(p["exact"].mean()),
            "score_log_loss": float(p["score_ll"].mean()),
            "mae_home_goals": float(p["ae_home"].mean()),
            "mae_away_goals": float(p["ae_away"].mean()),
            "mae_team_goals": float((p["ae_home"].mean() + p["ae_away"].mean()) / 2),
            "mae_total_goals": float(p["ae_total"].mean()),
            "mae_goal_diff": float(p["ae_diff"].mean()),
            "rps_total_goals": float(p["rps_total"].mean()),
            "rps_goal_diff": float(p["rps_diff"].mean()),
            "pred_extra_time_rate": float(p["p_tie_reg"].mean()),
            "actual_extra_time_rate": float(p["went_extra"].mean()),
        })
    return out


def calibration(p: pd.DataFrame, bins: int = 10) -> list[dict]:
    """Reliability of the home-win probability in equal-width bins."""
    edges = np.linspace(0, 1, bins + 1)
    out = []
    ph = p["p_home_win"].to_numpy()
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (ph >= lo) & (ph < hi if hi < 1 else ph <= hi)
        if m.sum() == 0:
            continue
        out.append({"lo": float(lo), "hi": float(hi), "n": int(m.sum()), "mean_pred": float(ph[m].mean()),
                    "observed": float(p["home_won"].to_numpy()[m].mean())})
    return out


def expected_calibration_error(p: pd.DataFrame, bins: int = 10) -> float:
    cal = calibration(p, bins)
    n = sum(b["n"] for b in cal)
    return float(sum(b["n"] * abs(b["mean_pred"] - b["observed"]) for b in cal) / n) if n else float("nan")


def by_confidence(p: pd.DataFrame) -> list[dict]:
    out = []
    for lo, hi in CONF_BUCKETS:
        m = (p["conf"] >= lo) & (p["conf"] < hi)
        if m.sum() == 0:
            continue
        s = p[m]
        out.append({"lo": lo, "hi": min(hi, 1.0), "n": int(len(s)), "mean_confidence": float(s["conf"].mean()),
                    "accuracy": float(s["correct"].mean())})
    return out


def paired_bootstrap(a: np.ndarray, b: np.ndarray, n_boot: int = 4000, seed: int = 11) -> dict:
    """Mean of (a - b) with a 95% percentile bootstrap interval. Negative
    means `a` has the lower (better) loss."""
    d = np.asarray(a, float) - np.asarray(b, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(n_boot, len(d)))
    means = d[idx].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"mean_diff": float(d.mean()), "ci95": [float(lo), float(hi)], "significant": bool(hi < 0 or lo > 0), "n": int(len(d))}
