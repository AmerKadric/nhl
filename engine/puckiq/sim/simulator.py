"""Final-score distribution for an NHL game.

Structure (mirrors the rules of the game):

1. Regulation. Home and away regulation goals follow a joint distribution
   built from two count distributions (Poisson, or negative binomial when a
   dispersion parameter is supplied) whose diagonal is re-weighted by a
   draw-inflation factor `theta`. Level games are more common than
   independent scoring implies because teams protect a tie late.
2. If regulation ends level, the game is decided in overtime or (regular
   season only) a shootout. Either way the winner is credited with exactly
   one extra goal, so a final score is never a tie and an OT/SO final always
   has a one-goal margin.

`score_matrices` is the exact (analytic) calculation. `simulate` is the
Monte Carlo engine used for published forecasts: it additionally integrates
over uncertain starting goalies and uncertainty in the scoring rates, which
has no closed form."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import stats

from .. import config


@dataclass
class SimParams:
    theta: float = 1.0          # draw inflation (>= 1)
    dispersion: float = 0.0     # NB2 alpha; 0 => Poisson
    ot_home_intercept: float = 0.0   # logit P(home wins | level after regulation)
    ot_strength_slope: float = 0.5   # effect of log(lam_home / lam_away)
    p_shootout: float = 0.33    # P(shootout | level), regular season
    so_strength_slope: float = 0.0   # shootout is close to a coin flip

    def to_json(self) -> dict:
        return dict(self.__dict__)


def _pmf(lam: float, n: int, dispersion: float) -> np.ndarray:
    k = np.arange(n + 1)
    if dispersion > 1e-9:
        r = 1.0 / dispersion
        return stats.nbinom.pmf(k, r, r / (r + lam))
    return stats.poisson.pmf(k, lam)


def regulation_matrix(lam_home: float, lam_away: float, sp: SimParams, max_goals: int = config.MAX_GOALS) -> np.ndarray:
    """P(home regulation goals = i, away = j). Sums to 1 minus the mass beyond
    the grid, which is returned implicitly as 1 - matrix.sum()."""
    ph, pa = _pmf(lam_home, max_goals, sp.dispersion), _pmf(lam_away, max_goals, sp.dispersion)
    m = np.outer(ph, pa)
    # Tail-inclusive normalising constant for the draw re-weighting.
    big = 40
    fh, fa = _pmf(lam_home, big, sp.dispersion), _pmf(lam_away, big, sp.dispersion)
    p_tie = float(np.sum(fh * fa))
    z = 1.0 + (sp.theta - 1.0) * p_tie
    m = m / z
    idx = np.arange(max_goals + 1)
    m[idx, idx] *= sp.theta
    return m


def p_home_wins_extra(lam_home: float, lam_away: float, sp: SimParams, playoff: bool) -> tuple[float, float, float]:
    """(P home wins in OT, P home wins in SO, P shootout) given a level game."""
    x = np.log(lam_home / lam_away)
    p_ot = 1.0 / (1.0 + np.exp(-(sp.ot_home_intercept + sp.ot_strength_slope * x)))
    p_so_h = 1.0 / (1.0 + np.exp(-(sp.so_strength_slope * x)))
    q = 0.0 if playoff else sp.p_shootout
    return float(p_ot), float(p_so_h), float(q)


def final_matrix(reg: np.ndarray, lam_home: float, lam_away: float, sp: SimParams, playoff: bool) -> np.ndarray:
    """Final-score matrix: regulation ties are resolved by adding one goal to
    the OT/SO winner. Ties at the grid edge spill outside the grid."""
    n = reg.shape[0]
    p_ot, p_so_h, q = p_home_wins_extra(lam_home, lam_away, sp, playoff)
    p_home = (1 - q) * p_ot + q * p_so_h
    fin = reg.copy()
    idx = np.arange(n)
    ties = reg[idx, idx].copy()
    fin[idx, idx] = 0.0
    fin[idx[1:], idx[:-1]] += ties[:-1] * p_home          # (t+1, t)
    fin[idx[:-1], idx[1:]] += ties[:-1] * (1 - p_home)    # (t, t+1)
    return fin


@dataclass
class ScoreForecast:
    """Everything derived from one game's score distribution."""
    lam_home: float
    lam_away: float
    final: np.ndarray            # [home, away] final score probabilities
    regulation: np.ndarray
    p_home_win: float
    p_home_reg: float
    p_tie_reg: float
    p_away_reg: float
    p_overtime: float            # decided in overtime
    p_shootout: float            # decided in a shootout
    exp_home_goals: float
    exp_away_goals: float
    overflow: float              # probability mass outside the grid
    n_sims: int = 0
    seed: int = 0
    mc_se_win: float = 0.0       # Monte Carlo standard error of p_home_win
    extras: dict = field(default_factory=dict)

    @property
    def most_likely(self) -> tuple[int, int, float]:
        i, j = np.unravel_index(int(np.argmax(self.final)), self.final.shape)
        return int(i), int(j), float(self.final[i, j])

    def top_scores(self, n: int = 5) -> list[dict]:
        flat = np.argsort(self.final, axis=None)[::-1][:n]
        out = []
        for k in flat:
            i, j = np.unravel_index(int(k), self.final.shape)
            out.append({"home": int(i), "away": int(j), "p": float(self.final[i, j])})
        return out

    def totals(self) -> list[float]:
        n = self.final.shape[0]
        t = np.zeros(2 * n - 1)
        for i in range(n):
            t[i:i + n] += self.final[i]
        return t.tolist()

    def diffs(self) -> dict[int, float]:
        n = self.final.shape[0]
        return {d: float(np.trace(self.final, offset=-d)) for d in range(-(n - 1), n)}  # home - away


def exact_forecast(lam_home: float, lam_away: float, sp: SimParams, playoff: bool = False,
                   max_goals: int = config.MAX_GOALS) -> ScoreForecast:
    reg = regulation_matrix(lam_home, lam_away, sp, max_goals)
    fin = final_matrix(reg, lam_home, lam_away, sp, playoff)
    return _summarise(lam_home, lam_away, reg, fin, sp, playoff)


def _summarise(lam_home, lam_away, reg, fin, sp, playoff, p_ot_dec=None, p_so_dec=None) -> ScoreForecast:
    n = reg.shape[0]
    idx = np.arange(n)
    tie = float(reg[idx, idx].sum())
    home_reg = float(np.tril(reg, -1).sum())
    away_reg = float(np.triu(reg, 1).sum())
    overflow = max(0.0, 1.0 - float(fin.sum()))
    p_home = float(np.tril(fin, -1).sum())
    p_away = float(np.triu(fin, 1).sum())
    tot = p_home + p_away
    if p_ot_dec is None:
        _, _, q = p_home_wins_extra(lam_home, lam_away, sp, playoff)
        p_ot_dec, p_so_dec = tie * (1 - q), tie * q
    goals = np.arange(n)
    return ScoreForecast(
        lam_home=float(lam_home), lam_away=float(lam_away), final=fin, regulation=reg,
        p_home_win=p_home / tot if tot > 0 else 0.5,
        p_home_reg=home_reg, p_tie_reg=tie, p_away_reg=away_reg,
        p_overtime=float(p_ot_dec), p_shootout=float(p_so_dec),
        exp_home_goals=float((fin.sum(axis=1) * goals).sum() / max(fin.sum(), 1e-12)),
        exp_away_goals=float((fin.sum(axis=0) * goals).sum() / max(fin.sum(), 1e-12)),
        overflow=overflow,
    )


def batch_matrices(lam_h: np.ndarray, lam_a: np.ndarray, sp: SimParams, playoff: np.ndarray,
                   max_goals: int = config.MAX_GOALS) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised (regulation, final) matrices for many games, shape
    [N, max_goals+1, max_goals+1]. Same maths as the single-game functions."""
    lam_h, lam_a = np.asarray(lam_h, float), np.asarray(lam_a, float)
    playoff = np.asarray(playoff, bool)
    n = max_goals + 1
    k, kb = np.arange(n), np.arange(41)
    if sp.dispersion > 1e-9:
        r = 1.0 / sp.dispersion
        ph = stats.nbinom.pmf(k[None, :], r, (r / (r + lam_h))[:, None])
        pa = stats.nbinom.pmf(k[None, :], r, (r / (r + lam_a))[:, None])
        fh = stats.nbinom.pmf(kb[None, :], r, (r / (r + lam_h))[:, None])
        fa = stats.nbinom.pmf(kb[None, :], r, (r / (r + lam_a))[:, None])
    else:
        ph, pa = stats.poisson.pmf(k[None, :], lam_h[:, None]), stats.poisson.pmf(k[None, :], lam_a[:, None])
        fh, fa = stats.poisson.pmf(kb[None, :], lam_h[:, None]), stats.poisson.pmf(kb[None, :], lam_a[:, None])
    z = 1.0 + (sp.theta - 1.0) * (fh * fa).sum(axis=1)
    reg = ph[:, :, None] * pa[:, None, :] / z[:, None, None]
    idx = np.arange(n)
    reg[:, idx, idx] *= sp.theta
    x = np.log(lam_h / lam_a)
    p_ot = 1.0 / (1.0 + np.exp(-(sp.ot_home_intercept + sp.ot_strength_slope * x)))
    p_so = 1.0 / (1.0 + np.exp(-(sp.so_strength_slope * x)))
    q = np.where(playoff, 0.0, sp.p_shootout)
    p_home = (1 - q) * p_ot + q * p_so
    fin = reg.copy()
    ties = reg[:, idx, idx].copy()
    fin[:, idx, idx] = 0.0
    fin[:, idx[1:], idx[:-1]] += ties[:, :-1] * p_home[:, None]
    fin[:, idx[:-1], idx[1:]] += ties[:, :-1] * (1 - p_home)[:, None]
    return reg, fin


def _sample_counts(rng: np.random.Generator, lam: np.ndarray, dispersion: float) -> np.ndarray:
    if dispersion > 1e-9:
        r = 1.0 / dispersion
        return rng.poisson(rng.gamma(shape=r, scale=lam / r))
    return rng.poisson(lam)


def simulate(scenarios: list[dict], sp: SimParams, playoff: bool = False, n_sims: int = config.N_SIMS,
             seed: int = 0, max_goals: int = config.MAX_GOALS) -> ScoreForecast:
    """Monte Carlo over game scenarios.

    `scenarios` is a list of {"weight", "lam_home", "lam_away", "sd_home",
    "sd_away"}: one entry per combination of possible starting goalies, with
    `sd_*` the standard deviation of log scoring rate (model uncertainty).
    Each simulated game draws a scenario, then its scoring rates, then the
    regulation score (with draw inflation by rejection sampling), then the
    OT / shootout result."""
    rng = np.random.default_rng(seed)
    w = np.array([s["weight"] for s in scenarios], dtype=float)
    w = w / w.sum()
    lam_h = np.array([s["lam_home"] for s in scenarios])
    lam_a = np.array([s["lam_away"] for s in scenarios])
    sd_h = np.array([s.get("sd_home", 0.0) for s in scenarios])
    sd_a = np.array([s.get("sd_away", 0.0) for s in scenarios])

    hs = np.empty(0, dtype=np.int64)
    as_ = np.empty(0, dtype=np.int64)
    lh_keep = np.empty(0)
    la_keep = np.empty(0)
    while hs.size < n_sims:
        m = int((n_sims - hs.size) * 1.35) + 64
        sc = rng.choice(len(scenarios), size=m, p=w)
        # mean-preserving log-normal rate uncertainty
        lh = lam_h[sc] * np.exp(rng.normal(0.0, 1.0, m) * sd_h[sc] - 0.5 * sd_h[sc] ** 2)
        la = lam_a[sc] * np.exp(rng.normal(0.0, 1.0, m) * sd_a[sc] - 0.5 * sd_a[sc] ** 2)
        h = _sample_counts(rng, lh, sp.dispersion)
        a = _sample_counts(rng, la, sp.dispersion)
        # accept ties always, non-ties with probability 1/theta
        keep = (h == a) | (rng.random(m) < 1.0 / sp.theta)
        hs = np.concatenate([hs, h[keep]])
        as_ = np.concatenate([as_, a[keep]])
        lh_keep = np.concatenate([lh_keep, lh[keep]])
        la_keep = np.concatenate([la_keep, la[keep]])
    hs, as_, lh_keep, la_keep = hs[:n_sims], as_[:n_sims], lh_keep[:n_sims], la_keep[:n_sims]

    tie = hs == as_
    x = np.log(lh_keep / la_keep)
    p_ot = 1.0 / (1.0 + np.exp(-(sp.ot_home_intercept + sp.ot_strength_slope * x)))
    p_so = 1.0 / (1.0 + np.exp(-(sp.so_strength_slope * x)))
    shootout = tie & (rng.random(n_sims) < (0.0 if playoff else sp.p_shootout))
    home_extra = rng.random(n_sims) < np.where(shootout, p_so, p_ot)
    fh = hs + (tie & home_extra)
    fa = as_ + (tie & ~home_extra)

    n = max_goals + 1
    reg = np.zeros((n, n))
    fin = np.zeros((n, n))
    ok_r = (hs <= max_goals) & (as_ <= max_goals)
    np.add.at(reg, (hs[ok_r], as_[ok_r]), 1.0)
    ok_f = (fh <= max_goals) & (fa <= max_goals)
    np.add.at(fin, (fh[ok_f], fa[ok_f]), 1.0)
    reg /= n_sims
    fin /= n_sims

    lam_home_mean, lam_away_mean = float(lh_keep.mean()), float(la_keep.mean())
    out = _summarise(lam_home_mean, lam_away_mean, reg, fin, sp, playoff,
                     p_ot_dec=float((tie & ~shootout).mean()), p_so_dec=float(shootout.mean()))
    p = float((fh > fa).mean())
    out.p_home_win = p
    out.exp_home_goals, out.exp_away_goals = float(fh.mean()), float(fa.mean())
    out.p_home_reg, out.p_tie_reg, out.p_away_reg = float((hs > as_).mean()), float(tie.mean()), float((hs < as_).mean())
    out.n_sims, out.seed = n_sims, seed
    out.mc_se_win = float(np.sqrt(p * (1 - p) / n_sims))
    tot = fh + fa
    out.extras = {
        "total_p10": float(np.percentile(tot, 10)), "total_p90": float(np.percentile(tot, 90)),
        "home_goals_p10": float(np.percentile(fh, 10)), "home_goals_p90": float(np.percentile(fh, 90)),
        "away_goals_p10": float(np.percentile(fa, 10)), "away_goals_p90": float(np.percentile(fa, 90)),
        "p_total_ge_7": float((tot >= 7).mean()), "p_total_le_4": float((tot <= 4).mean()),
        "p_one_goal_game": float((np.abs(fh - fa) == 1).mean()),
        "p_shutout": float(((fh == 0) | (fa == 0)).mean()),
    }
    return out


def fit_sim_params(lam_h: np.ndarray, lam_a: np.ndarray, reg_h: np.ndarray, reg_a: np.ndarray,
                   went_extra: np.ndarray, home_won: np.ndarray, shootout: np.ndarray,
                   playoff: np.ndarray) -> SimParams:
    """Estimate draw inflation, dispersion and the OT / shootout model by
    maximum likelihood, given out-of-sample scoring rates for past games."""
    from scipy.optimize import minimize, minimize_scalar

    lam_h, lam_a = np.asarray(lam_h, float), np.asarray(lam_a, float)
    reg_h, reg_a = np.asarray(reg_h, int), np.asarray(reg_a, int)

    def nll(theta: float, alpha: float) -> float:
        if alpha > 1e-9:
            r = 1.0 / alpha
            lh = stats.nbinom.logpmf(reg_h, r, r / (r + lam_h))
            la = stats.nbinom.logpmf(reg_a, r, r / (r + lam_a))
            k = np.arange(40)[:, None]
            p_tie = (stats.nbinom.pmf(k, r, r / (r + lam_h)) * stats.nbinom.pmf(k, r, r / (r + lam_a))).sum(axis=0)
        else:
            lh, la = stats.poisson.logpmf(reg_h, lam_h), stats.poisson.logpmf(reg_a, lam_a)
            k = np.arange(40)[:, None]
            p_tie = (stats.poisson.pmf(k, lam_h) * stats.poisson.pmf(k, lam_a)).sum(axis=0)
        ll = lh + la + np.where(reg_h == reg_a, np.log(theta), 0.0) - np.log(1.0 + (theta - 1.0) * p_tie)
        return -float(ll.sum())

    th = minimize_scalar(lambda t: nll(t, 0.0), bounds=(0.8, 2.5), method="bounded").x
    pois = nll(th, 0.0)
    res = minimize(lambda v: nll(v[0], v[1]), x0=[th, 0.02], bounds=[(0.8, 2.5), (1e-6, 0.5)], method="L-BFGS-B")
    theta, alpha = float(th), 0.0
    # Keep the negative binomial only if it earns its extra parameter (AIC).
    if res.success and 2 * (pois - res.fun) > 2.0 and res.x[1] > 1e-4:
        theta, alpha = float(res.x[0]), float(res.x[1])

    sp = SimParams(theta=theta, dispersion=alpha)
    ex = np.asarray(went_extra, bool)
    reg_season = ex & ~np.asarray(playoff, bool)
    if reg_season.sum() > 50:
        sp.p_shootout = float(np.asarray(shootout, bool)[reg_season].mean())
    ot = ex & ~np.asarray(shootout, bool)
    if ot.sum() > 200:
        x, y = np.log(lam_h[ot] / lam_a[ot]), np.asarray(home_won, float)[ot]

        def ot_nll(v):
            z = v[0] + v[1] * x
            return float(np.sum(np.log1p(np.exp(-z)) + (1 - y) * z))
        r2 = minimize(ot_nll, x0=[0.0, 0.5], method="L-BFGS-B", bounds=[(-0.5, 0.5), (0.0, 3.0)])
        sp.ot_home_intercept, sp.ot_strength_slope = float(r2.x[0]), float(r2.x[1])
    so = ex & np.asarray(shootout, bool)
    if so.sum() > 200:
        x, y = np.log(lam_h[so] / lam_a[so]), np.asarray(home_won, float)[so]

        def so_nll(v):
            z = v[0] * x
            return float(np.sum(np.log1p(np.exp(-z)) + (1 - y) * z))
        sp.so_strength_slope = float(minimize(so_nll, x0=[0.2], method="L-BFGS-B", bounds=[(0.0, 3.0)]).x[0])
    return sp
