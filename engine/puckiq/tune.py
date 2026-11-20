"""Hyper-parameter tuning for the rating systems.

Uses ONLY seasons before `config.FIRST_TEST_SEASON`. The first ingested
season is a burn-in: it updates state but is not scored. Each objective is a
one-step-ahead (pre-game) predictive likelihood, so tuning itself is free of
look-ahead."""
from __future__ import annotations

import datetime as dt
import json
import logging
import math

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from . import config
from .features.build import LN2, METRICS, FeatureBuilder, Frames, League, load_frames, starter_probabilities
from .features.params import KF, TUNING_PATH, FeatureParams

log = logging.getLogger("puckiq.tune")


def _ordered_finals(fr: Frames, max_season: int) -> pd.DataFrame:
    g = fr.games[fr.games["home_score"].notna() & (fr.games["season"] <= max_season)].copy()
    g["_t"] = g["start_time_utc"].fillna(pd.Timestamp("1900-01-01"))
    return g.sort_values(["game_date", "_t", "id"])


def metric_arrays(fr: Frames, max_season: int) -> dict:
    g = _ordered_finals(fr, max_season)
    t = fr.teams.set_index("id")["franchise_key"]
    keys = {k: i for i, k in enumerate(sorted(t.unique()))}
    g["h"] = g["home_team_id"].map(t).map(keys)
    g["a"] = g["away_team_id"].map(t).map(keys)
    th = fr.tgs[fr.tgs["is_home"] == 1].set_index("game_id")
    ta = fr.tgs[fr.tgs["is_home"] == 0].set_index("game_id")
    out = {"n": len(keys)}
    base = g[["id", "h", "a", "season"]]
    out["g"] = dict(h=base["h"].tolist(), a=base["a"].tolist(), s=base["season"].tolist(),
                    yh=g["home_reg_goals"].astype(float).tolist(), ya=g["away_reg_goals"].astype(float).tolist(),
                    w=[1.0] * len(g))
    m = base.join(th[["toi_5v5", "toi_all", "sva_xgf_5v5", "fsva_xgf"]], on="id").join(
        ta[["sva_xgf_5v5", "fsva_xgf"]].add_suffix("_a"), on="id")
    m = m[m["toi_5v5"] > 600]
    out["xg5"] = dict(h=m["h"].tolist(), a=m["a"].tolist(), s=m["season"].tolist(),
                      yh=(m["sva_xgf_5v5"] * 3600 / m["toi_5v5"]).tolist(),
                      ya=(m["sva_xgf_5v5_a"] * 3600 / m["toi_5v5"]).tolist(), w=(m["toi_5v5"] / 2900.0).tolist())
    ta_ = m["toi_all"].where(m["toi_all"] > 0, 3600.0)
    out["xga"] = dict(h=m["h"].tolist(), a=m["a"].tolist(), s=m["season"].tolist(),
                      yh=(m["fsva_xgf"] * 3600 / ta_).tolist(), ya=(m["fsva_xgf_a"] * 3600 / ta_).tolist(),
                      w=[1.0] * len(m))
    out["elo"] = dict(h=base["h"].tolist(), a=base["a"].tolist(), s=base["season"].tolist(),
                      won=(g["home_score"] > g["away_score"]).tolist(),
                      margin=(g["home_score"] - g["away_score"]).abs().astype(int).tolist(),
                      so=(g["last_period_type"] == "SO").tolist())
    return out


def kf_loglik(arr: dict, k: KF, mu0: float, hfa0: float, alpha: float, n: int, score_from: int | None = None) -> tuple[float, int]:
    """Stand-alone replica of FeatureBuilder._kf_update (tested for equality)."""
    off, de = [0.0] * n, [0.0] * n
    po, pd_ = [k.p0] * n, [k.p0] * n
    ts = [None] * n
    mu, hfa, ll, cnt = mu0, hfa0, 0.0, 0
    c2 = k.carry * k.carry
    H, A, S, YH, YA, W = arr["h"], arr["a"], arr["s"], arr["yh"], arr["ya"], arr["w"]
    log2pi = math.log(2 * math.pi)
    for i in range(len(H)):
        h, a, s = H[i], A[i], S[i]
        for t in (h, a):
            if ts[t] != s:
                if ts[t] is not None:
                    off[t] *= k.carry
                    de[t] *= k.carry
                    po[t] = c2 * po[t] + k.q_season
                    pd_[t] = c2 * pd_[t] + k.q_season
                ts[t] = s
        r = k.r / max(W[i], 0.25)
        scored = score_from is None or s >= score_from
        for att, dfn, y, sign in ((h, a, YH[i], 1.0), (a, h, YA[i], -1.0)):
            sv = po[att] + pd_[dfn] + r
            err = y - (mu + off[att] - de[dfn] + sign * hfa)
            if scored:
                ll += -0.5 * (log2pi + math.log(sv) + err * err / sv)
                cnt += 1
            ka, kd = po[att] / sv, pd_[dfn] / sv
            off[att] += ka * err
            de[dfn] -= kd * err
            po[att] = po[att] * (1 - ka) + k.q
            pd_[dfn] = pd_[dfn] * (1 - kd) + k.q
        mu += alpha * ((YH[i] + YA[i]) / 2 - mu)
        hfa += alpha * ((YH[i] - YA[i]) / 2 - hfa)
    return ll, cnt


def tune_kf(arr: dict, m: str, start: KF, lg: League, n: int, score_from: int) -> tuple[KF, float]:
    def unpack(v):
        return KF(r=math.exp(v[0]), q=math.exp(v[1]), q_season=math.exp(v[2]),
                  carry=1 / (1 + math.exp(-v[3])), p0=min(max(math.exp(v[4]), 0.01), 0.15))

    def obj(v):
        ll, cnt = kf_loglik(arr, unpack(v), lg.mu[m], lg.hfa[m], lg.a, n, score_from)
        return -ll / cnt
    x0 = [math.log(start.r), math.log(start.q), math.log(start.q_season),
          math.log(start.carry / (1 - start.carry)), math.log(start.p0)]
    res = minimize(obj, x0, method="Nelder-Mead", options={"xatol": 1e-3, "fatol": 1e-6, "maxiter": 900})
    return unpack(res.x), float(-res.fun)


def elo_loglik(arr: dict, k: float, hfa: float, carry: float, n: int, score_from: int) -> float:
    """Stand-alone replica of FeatureBuilder._elo_update."""
    elo, ts = [1500.0] * n, [None] * n
    ll, cnt = 0.0, 0
    for h, a, s, won, margin, so in zip(arr["h"], arr["a"], arr["s"], arr["won"], arr["margin"], arr["so"]):
        for t in (h, a):
            if ts[t] != s:
                if ts[t] is not None:
                    elo[t] = 1500.0 + carry * (elo[t] - 1500.0)
                ts[t] = s
        diff = elo[h] + hfa - elo[a]
        e = 1.0 / (1.0 + 10 ** (-diff / 400.0))
        if s >= score_from:
            ll += math.log(e if won else 1 - e)
            cnt += 1
        mov = 1.0 if so else (0.6686 * math.log(max(margin, 1)) + 0.8048)
        mov *= 2.05 / ((diff if won else -diff) * 0.001 + 2.05)
        d = k * mov * ((1.0 if won else 0.0) - e)
        elo[h] += d
        elo[a] -= d
    return ll / cnt


def goalie_loglik(rows: list[tuple], hl: float, prior_xg: float, prior_mean: float, score_from: int) -> float:
    """Poisson likelihood of goals allowed given xG faced and the goalie's
    pre-game rating (replica of the goalie update in FeatureBuilder)."""
    state: dict[int, list[float]] = {}
    ll, cnt = 0.0, 0
    for pid, season, xga, ga in rows:
        st = state.get(pid)
        rating = prior_mean if st is None else (st[1] + prior_mean * prior_xg) / (st[0] + prior_xg)
        if season >= score_from and xga > 0.5:
            mu = xga * (1.0 - rating)
            ll += ga * math.log(mu) - mu - math.lgamma(ga + 1)
            cnt += 1
        if st is None:
            st = state[pid] = [0.0, 0.0]
        dec = math.exp(-LN2 * xga / hl)
        st[0] = st[0] * dec + xga
        st[1] = st[1] * dec + (xga - ga)
    return ll / cnt


def run(max_season: int | None = None) -> dict:
    max_season = max_season or config.FIRST_TEST_SEASON - 1
    score_from = config.FIRST_SEASON + 1
    fr = load_frames()
    arr = metric_arrays(fr, max_season)
    p = FeatureParams()
    lg = League(p.league_hl_games)
    report: dict = {"tuned_on_seasons": [score_from, max_season], "burn_in_season": config.FIRST_SEASON, "objectives": {}}

    for m in METRICS:
        before = kf_loglik(arr[m], p.kf[m], lg.mu[m], lg.hfa[m], lg.a, arr["n"], score_from)
        best, ll = tune_kf(arr[m], m, p.kf[m], lg, arr["n"], score_from)
        p.kf[m] = best
        report["objectives"][f"kf_{m}"] = {"mean_loglik_default": before[0] / before[1], "mean_loglik_tuned": ll, "n": before[1]}
        log.info("KF %s: %s  ll %.4f -> %.4f", m, best, before[0] / before[1], ll)

    best = (-9e9, None)
    for k in (3, 4, 5, 6, 7, 8, 10):
        for hfa in (20, 30, 40, 50, 60):
            for carry in (0.5, 0.6, 0.7, 0.8, 0.9):
                ll = elo_loglik(arr["elo"], k, hfa, carry, arr["n"], score_from)
                if ll > best[0]:
                    best = (ll, (k, hfa, carry))
    p.elo_k, p.elo_hfa, p.elo_carry = map(float, best[1])
    report["objectives"]["elo"] = {"mean_loglik_tuned": best[0]}
    log.info("Elo: k=%s hfa=%s carry=%s ll %.4f", *best[1], best[0])

    order = _ordered_finals(fr, max_season)[["id", "season"]].reset_index(drop=True)
    order["ord"] = order.index
    gg = fr.ggs[fr.ggs["xga"].notna() & (fr.ggs["xga"] > 0)].merge(order, left_on="game_id", right_on="id")
    gg = gg.sort_values(["ord", "player_id"])
    grow = list(zip(gg["player_id"].astype(int), gg["season"].astype(int), gg["xga"].astype(float), gg["ga_on_xg"].astype(float)))
    best = (-9e9, None)
    for hl in (120, 180, 250, 350, 500, 800, 1200, 2000):
        for prior in (40, 60, 90, 120, 160, 220, 300, 400, 600):
            for mean in (-0.05, -0.04, -0.03, -0.02, -0.01, 0.0, 0.01):
                ll = goalie_loglik(grow, hl, prior, mean, score_from)
                if ll > best[0]:
                    best = (ll, (hl, prior, mean))
    flat = goalie_loglik(grow, 250, 1e9, 0.0, score_from)
    p.gk_hl_xg, p.gk_prior_xg, p.gk_prior_mean = map(float, best[1])
    report["objectives"]["goalie"] = {"mean_loglik_tuned": best[0], "mean_loglik_no_goalie_effect": flat}
    log.info("Goalie: hl=%s prior=%s mean=%s ll %.5f (no-effect %.5f)", *best[1], best[0], flat)

    # Starting-goalie projection: conditional logit on usage features.
    sub = Frames(fr.games[fr.games["season"] <= max_season], fr.tgs, fr.ggs, fr.teams, fr.players)
    fb = FeatureBuilder(sub, p)
    fb.run()
    c = fb.cands[fb.cands["started"].notna() & (fb.cands["season"] >= score_from)].copy()
    has = c.groupby(["game_id", "side"])["started"].transform("sum") > 0
    c = c[has].reset_index(drop=True)
    names = ["share10", "prev_rested", "prev_b2b", "share30"]

    def nll(v):
        pr = starter_probabilities(c, dict(zip(names, v)))
        return -float(np.log(pr[c["started"] == 1.0]).mean())
    res = minimize(nll, [3.0, 0.5, -2.0, 1.0], method="Nelder-Mead", options={"xatol": 1e-3, "fatol": 1e-6})
    p.starter_w = {k: float(v) for k, v in zip(names, res.x)}
    pr = starter_probabilities(c, p.starter_w)
    top = c.loc[pr.groupby([c["game_id"], c["side"]]).idxmax()]
    report["objectives"]["starter"] = {"mean_log_prob_of_starter": -float(res.fun), "top_pick_accuracy": float(top["started"].mean()),
                                       "n_team_games": int(len(top))}
    log.info("Starter weights %s  logp %.4f  top-pick acc %.3f", p.starter_w, -res.fun, top["started"].mean())

    out = {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "params": p.to_json(), "report": report,
           "not_tuned": ["st_hl_games", "st_prior_sec", "fin_hl_games", "fin_prior_xg", "league_hl_games"]}
    config.ensure_dirs()
    TUNING_PATH.write_text(json.dumps(out, indent=2))
    return out
