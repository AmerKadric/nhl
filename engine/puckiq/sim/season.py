"""Season simulation: projected standings, playoff odds and Cup odds.

Every remaining regular-season game is simulated with the production
model's scoring rates and the same rules as a single-game forecast
(regulation, overtime, shootout, loser point). Each simulated season then
seeds the playoffs by the NHL format (top three per division plus two wild
cards per conference) and plays best-of-seven series.

Two things make the odds honest rather than overconfident:

- team strength is uncertain and will drift, so each simulated season draws
  every team's offence and defence from the rating's posterior spread plus
  the drift expected over the games remaining;
- nothing here knows about trades, injuries or lineups months ahead.

Tie-breaks follow the NHL order as far as the simulation tracks it: points,
regulation wins, regulation plus overtime wins, total wins, goal difference."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import config
from ..features.params import load_params
from ..models.design import TEAM_FEATS, team_rows
from .simulator import SimParams, batch_matrices

HOME_GAMES = (0, 1, 4, 6)   # best-of-seven, 2-2-1-1-1: games hosted by the higher seed

# Multiplier on the model's own team-strength uncertainty. The rating filter
# only knows how much strength has moved from game to game; it cannot see
# trades, injuries or call-ups, so on its own it is too sure of itself over a
# whole season: its 80% points range held the actual total only 73% of the
# time. The multiplier was chosen on the 2017, 2018, 2021 and 2022 seasons
# (coverage 78%) and then checked on 2023-2025 (85%). It leaves the accuracy
# of the playoff odds unchanged. `puckiq season-backtest` re-runs the study.
STRENGTH_SD_SCALE = 2.0


# --------------------------------------------------------------------------
# small numeric helpers
# --------------------------------------------------------------------------
def win_prob_grid(sp: SimParams, playoff: bool = True, n: int = 56) -> tuple[np.ndarray, np.ndarray]:
    """Exact single-game home win probability on a grid of log scoring rates,
    so millions of playoff games can be priced by interpolation."""
    xs = np.linspace(math.log(1.0), math.log(7.0), n)
    lh, la = np.meshgrid(xs, xs, indexing="ij")
    _, fin = batch_matrices(np.exp(lh.ravel()), np.exp(la.ravel()), sp, np.full(n * n, playoff))
    g = np.arange(fin.shape[1])
    i, j = np.meshgrid(g, g, indexing="ij")
    p = np.where(i > j, fin, 0.0).sum(axis=(1, 2)) / fin.sum(axis=(1, 2))
    return xs, p.reshape(n, n)


def interp_win(xs: np.ndarray, grid: np.ndarray, lh: np.ndarray, la: np.ndarray) -> np.ndarray:
    """Bilinear interpolation of the win-probability grid."""
    step = xs[1] - xs[0]
    u = np.clip((lh - xs[0]) / step, 0, len(xs) - 1.0001)
    v = np.clip((la - xs[0]) / step, 0, len(xs) - 1.0001)
    i, j = u.astype(int), v.astype(int)
    fu, fv = u - i, v - j
    return (grid[i, j] * (1 - fu) * (1 - fv) + grid[i + 1, j] * fu * (1 - fv)
            + grid[i, j + 1] * (1 - fu) * fv + grid[i + 1, j + 1] * fu * fv)


def series_prob(p_home: np.ndarray, p_road: np.ndarray, wins_needed: int = 4) -> np.ndarray:
    """Probability the higher seed wins a best-of-seven, given its per-game
    win probability at home and on the road."""
    p_home, p_road = np.asarray(p_home, float), np.asarray(p_road, float)
    zero = np.zeros_like(p_home)
    state = {(0, 0): np.ones_like(p_home)}
    won = zero.copy()
    for g in range(2 * wins_needed - 1):
        p = p_home if g in HOME_GAMES else p_road
        nxt: dict[tuple[int, int], np.ndarray] = {}
        for (a, b), pr in state.items():
            if a + 1 == wins_needed:
                won = won + pr * p
            else:
                nxt[(a + 1, b)] = nxt.get((a + 1, b), zero) + pr * p
            if b + 1 < wins_needed:
                nxt[(a, b + 1)] = nxt.get((a, b + 1), zero) + pr * (1 - p)
        state = nxt
    return won


def _counts(rng: np.random.Generator, lam: np.ndarray, dispersion: float) -> np.ndarray:
    if dispersion > 1e-9:
        r = 1.0 / dispersion
        return rng.poisson(rng.gamma(shape=r, scale=lam / r))
    return rng.poisson(lam)


def _sig(z):
    return 1.0 / (1.0 + np.exp(-z))


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------
@dataclass
class SeasonInputs:
    team_ids: list[int]
    conference: list[str]
    division: list[str]
    # current standings, one entry per team
    gp: np.ndarray
    pts: np.ndarray
    rw: np.ndarray
    row: np.ndarray
    w: np.ndarray
    l: np.ndarray
    otl: np.ndarray
    gf: np.ndarray
    ga: np.ndarray
    # remaining regular-season games
    home: np.ndarray            # team index
    away: np.ndarray
    lam_home: np.ndarray        # baseline regulation scoring rates
    lam_away: np.ndarray
    # team-strength uncertainty on the log scoring-rate scale
    sd_off: np.ndarray
    sd_def: np.ndarray
    # playoff game rates for every ordered pair: [home team, away team]
    log_lam_home: np.ndarray
    log_lam_away: np.ndarray
    sp: SimParams = field(default_factory=SimParams)


def current_standings(games: pd.DataFrame, team_ids: list[int]) -> dict[str, np.ndarray]:
    """Standings from finished regular-season games."""
    idx = {t: i for i, t in enumerate(team_ids)}
    n = len(team_ids)
    out = {k: np.zeros(n) for k in ("gp", "pts", "rw", "row", "w", "l", "otl", "gf", "ga")}
    for g in games.itertuples():
        hs, as_ = int(g.home_score), int(g.away_score)
        lpt = g.last_period_type
        for tid, gf, ga in ((g.home_team_id, hs, as_), (g.away_team_id, as_, hs)):
            i = idx.get(tid)
            if i is None:
                continue
            won = gf > ga
            out["gp"][i] += 1
            out["gf"][i] += gf
            out["ga"][i] += ga
            if won:
                out["w"][i] += 1
                out["pts"][i] += 2
                out["rw"][i] += lpt == "REG"
                out["row"][i] += lpt != "SO"
            elif lpt == "REG":
                out["l"][i] += 1
            else:
                out["otl"][i] += 1
                out["pts"][i] += 1
    return out


def _team_features(fut: pd.DataFrame, team_ids: list[int]) -> dict[int, dict]:
    """Each team's current pre-game features, from its next scheduled game."""
    feats: dict[int, dict] = {}
    for g in fut.itertuples():
        for side, tid in (("h", g.home_team_id), ("a", g.away_team_id)):
            if tid in feats:
                continue
            feats[tid] = {c: getattr(g, f"{side}_{c}") for c in TEAM_FEATS}
        if len(feats) == len(team_ids):
            break
    return feats


def prepare(feat: pd.DataFrame, games: pd.DataFrame, fc, season: int, alignment: dict[int, tuple[str, str]],
            as_of: pd.Timestamp | None = None) -> SeasonInputs | None:
    """Assemble the simulation inputs from the feature frame (which already
    holds pre-game features for every unplayed game) and the production model."""
    team_ids = sorted(alignment)
    idx = {t: i for i, t in enumerate(team_ids)}
    fut = feat[(~feat["final"]) & (feat["season"] == season) & (feat["game_type"] == config.REGULAR)
               & (feat["schedule_state"].isin(["OK", "TBD"]))]
    fut = fut[fut["home_team_id"].isin(idx) & fut["away_team_id"].isin(idx)].sort_values(["game_date", "game_id"])
    if fut.empty:
        return None
    played = games[(games["season"] == season) & (games["game_type"] == config.REGULAR) & games["home_score"].notna()]
    st = current_standings(played, team_ids)

    rows = team_rows(fut, "projected")
    lam = pd.DataFrame({"g": rows["game_id"].values, "h": rows["is_home"].values, "lam": fc.glm.predict(rows)})
    lam_h = lam[lam["h"] == 1].set_index("g")["lam"].reindex(fut["game_id"]).to_numpy()
    lam_a = lam[lam["h"] == 0].set_index("g")["lam"].reindex(fut["game_id"]).to_numpy()
    home = fut["home_team_id"].map(idx).to_numpy()
    away = fut["away_team_id"].map(idx).to_numpy()

    feats = _team_features(fut, team_ids)
    if len(feats) < len(team_ids):
        return None   # a team with nothing left to play: the regular season is ending
    beta = dict(zip(fc.glm.cols, fc.glm.beta[1:]))
    kf = load_params().kf
    remaining = np.bincount(np.concatenate([home, away]), minlength=len(team_ids))
    sd_off, sd_def = np.zeros(len(team_ids)), np.zeros(len(team_ids))
    for t, i in idx.items():
        vo = vd = 0.0
        for m in ("xg5", "xga", "g"):
            if f"t_off_{m}" in beta:
                drift = kf[m].q * remaining[i] / 2.0   # average extra variance over the games left
                vo += beta[f"t_off_{m}"] ** 2 * (feats[t][f"poff_{m}"] + drift)
                vd += beta[f"o_def_{m}"] ** 2 * (feats[t][f"pdef_{m}"] + drift)
        sd_off[i], sd_def[i] = math.sqrt(vo), math.sqrt(vd)

    # playoff games between every ordered pair, on neutral rest
    neutral = {"rest": 2.0, "b2b": 0.0, "g7": 3.0, "travel": 0.0, "tz": 0.0, "road_streak": 0.0,
               "lu_gs": 0.0, "lu_toi": 0.0, "lu_gs_conf": 0.0, "lu_toi_conf": 0.0}
    lg = float(fut["lg_g"].iloc[0])
    pairs = []
    for hi in team_ids:
        for lo in team_ids:
            if hi == lo:
                continue
            r = {"game_id": idx[hi] * 100 + idx[lo], "season": season, "game_date": fut["game_date"].iloc[0], "playoff": 1.0,
                 "lg_g": lg, "home_reg_goals": np.nan, "away_reg_goals": np.nan}
            for c in TEAM_FEATS:
                r[f"h_{c}"] = neutral.get(c, feats[hi][c])
                r[f"a_{c}"] = neutral.get(c, feats[lo][c])
            pairs.append(r)
    prow = team_rows(pd.DataFrame(pairs), "projected")
    ll = fc.glm.log_rate(prow)
    n = len(team_ids)
    llh, lla = np.zeros((n, n)), np.zeros((n, n))
    gid = prow["game_id"].to_numpy()
    is_home = prow["is_home"].to_numpy() == 1
    llh[gid[is_home] // 100, gid[is_home] % 100] = ll[is_home]
    lla[gid[~is_home] // 100, gid[~is_home] % 100] = ll[~is_home]

    return SeasonInputs(
        team_ids=team_ids, conference=[alignment[t][0] for t in team_ids], division=[alignment[t][1] for t in team_ids],
        home=home, away=away, lam_home=lam_h, lam_away=lam_a, sd_off=sd_off, sd_def=sd_def,
        log_lam_home=llh, log_lam_away=lla, sp=fc.sp, **st)


# --------------------------------------------------------------------------
# simulation
# --------------------------------------------------------------------------
def _regular_season(rng, inp: SeasonInputs, off: np.ndarray, dfn: np.ndarray) -> dict[str, np.ndarray]:
    sp = inp.sp
    h_i, a_i = inp.home, inp.away
    lh = inp.lam_home[None, :] * np.exp(off[:, h_i] + dfn[:, a_i])
    la = inp.lam_away[None, :] * np.exp(off[:, a_i] + dfn[:, h_i])
    h, a = _counts(rng, lh, sp.dispersion), _counts(rng, la, sp.dispersion)
    if sp.theta > 1.0:   # draw inflation: keep ties, redraw other scores with probability 1 - 1/theta
        redo = np.flatnonzero(((h != a) & (rng.random(h.shape) >= 1.0 / sp.theta)).ravel())
        hf, af, lhf, laf = h.ravel(), a.ravel(), lh.ravel(), la.ravel()
        while redo.size:
            hh, aa = _counts(rng, lhf[redo], sp.dispersion), _counts(rng, laf[redo], sp.dispersion)
            hf[redo], af[redo] = hh, aa
            redo = redo[(hh != aa) & (rng.random(redo.size) >= 1.0 / sp.theta)]
    tie = h == a
    x = np.log(lh / la)
    so = tie & (rng.random(h.shape) < sp.p_shootout)
    p_extra = np.where(so, _sig(sp.so_strength_slope * x), _sig(sp.ot_home_intercept + sp.ot_strength_slope * x))
    home_extra = tie & (rng.random(h.shape) < p_extra)
    home_win = (h > a) | home_extra
    n = len(inp.team_ids)
    hm = np.zeros((len(h_i), n), dtype=np.float32)
    am = np.zeros((len(a_i), n), dtype=np.float32)
    hm[np.arange(len(h_i)), h_i] = 1.0
    am[np.arange(len(a_i)), a_i] = 1.0

    def tally(home_part, away_part):
        return home_part.astype(np.float32) @ hm + away_part.astype(np.float32) @ am

    return {
        "pts": tally(2 * home_win + (tie & ~home_win), 2 * (~home_win) + (tie & home_win)),
        "rw": tally(h > a, a > h),
        "row": tally((h > a) | (tie & ~so & home_win), (a > h) | (tie & ~so & ~home_win)),
        "w": tally(home_win, ~home_win),
        "gd": tally(h + home_extra - a - (tie & ~home_extra), a + (tie & ~home_extra) - h - home_extra),
    }


def simulate(inp: SeasonInputs, n_sims: int = 10000, seed: int = 0, batch: int = 1000) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(inp.team_ids)
    conf = np.array(inp.conference)
    div = np.array(inp.division)
    xs, grid = win_prob_grid(inp.sp, playoff=True)
    counts = {k: np.zeros(n) for k in ("playoffs", "division", "round2", "conf_final", "final", "cup")}
    points = []

    def bump(key, teams):
        np.add.at(counts[key], teams.ravel(), 1.0)

    for start in range(0, n_sims, batch):
        b = min(batch, n_sims - start)
        rows = np.arange(b)
        off = rng.normal(0.0, 1.0, (b, n)) * inp.sd_off
        dfn = rng.normal(0.0, 1.0, (b, n)) * inp.sd_def
        sim = _regular_season(rng, inp, off, dfn)
        pts = inp.pts + sim["pts"]
        key = (pts * 1e9 + (inp.rw + sim["rw"]) * 1e7 + (inp.row + sim["row"]) * 1e5 + (inp.w + sim["w"]) * 1e3
               + (inp.gf - inp.ga + sim["gd"] + 500.0) + rng.random((b, n)) * 0.5)
        points.append(pts.astype(np.int16))

        def series(hi, lo):
            """Winner of a best-of-seven; `hi` has home ice."""
            p_h = interp_win(xs, grid, inp.log_lam_home[hi, lo] + off[rows, hi] + dfn[rows, lo],
                             inp.log_lam_away[hi, lo] + off[rows, lo] + dfn[rows, hi])
            p_r = 1.0 - interp_win(xs, grid, inp.log_lam_home[lo, hi] + off[rows, lo] + dfn[rows, hi],
                                   inp.log_lam_away[lo, hi] + off[rows, hi] + dfn[rows, lo])
            return np.where(rng.random(b) < series_prob(p_h, p_r), hi, lo)

        def by_record(x, y):
            """(better record, worse record): home ice in the later rounds."""
            better = key[rows, x] >= key[rows, y]
            return np.where(better, x, y), np.where(better, y, x)

        champs = []
        for c in sorted(set(conf)):
            divs = sorted(set(div[conf == c]))
            top, rest = [], []
            for d in divs:
                members = np.flatnonzero((conf == c) & (div == d))
                order = np.argsort(-key[:, members], axis=1)
                top.append(members[order[:, :3]])       # [b, 3]: division seeds 1-3
                rest.append(members[order[:, 3:]])
            pool = np.concatenate(rest, axis=1)
            o = np.argsort(-np.take_along_axis(key, pool, axis=1), axis=1)
            wc1, wc2 = pool[rows, o[:, 0]], pool[rows, o[:, 1]]
            for t in top:
                bump("playoffs", t)
                bump("division", t[:, 0])
            bump("playoffs", wc1)
            bump("playoffs", wc2)
            a1, b1 = top[0][:, 0], top[1][:, 0]
            a_better = key[rows, a1] >= key[rows, b1]
            # the better division winner draws the second wild card
            wc_a = np.where(a_better, wc2, wc1)
            wc_b = np.where(a_better, wc1, wc2)
            ra = [series(a1, wc_a), series(top[0][:, 1], top[0][:, 2])]
            rb = [series(b1, wc_b), series(top[1][:, 1], top[1][:, 2])]
            for w_ in ra + rb:
                bump("round2", w_)
            # second round: the division winner's side has home ice if it advanced
            fa = series(*by_seed(ra[0], ra[1], a1, key, rows))
            fb = series(*by_seed(rb[0], rb[1], b1, key, rows))
            bump("conf_final", fa)
            bump("conf_final", fb)
            cw = series(*by_record(fa, fb))
            bump("final", cw)
            champs.append(cw)
        cup = series(*by_record(champs[0], champs[1]))
        bump("cup", cup)

    allp = np.concatenate(points, axis=0)
    out = pd.DataFrame({
        "team_id": inp.team_ids, "conference": inp.conference, "division": inp.division,
        "gp": inp.gp.astype(int), "w": inp.w.astype(int), "l": inp.l.astype(int), "otl": inp.otl.astype(int),
        "points": inp.pts.astype(int), "games_remaining": np.bincount(np.concatenate([inp.home, inp.away]), minlength=n),
        "proj_points": allp.mean(axis=0), "points_p10": np.percentile(allp, 10, axis=0),
        "points_p50": np.percentile(allp, 50, axis=0), "points_p90": np.percentile(allp, 90, axis=0),
    })
    for k, v in counts.items():
        out[f"p_{k}"] = v / n_sims
    return out


def by_seed(x, y, division_winner, key, rows):
    """Second-round home ice: the division winner if it advanced, otherwise
    the team with the better record."""
    x_is = x == division_winner
    y_is = y == division_winner
    better = np.where(x_is, True, np.where(y_is, False, key[rows, x] >= key[rows, y]))
    return np.where(better, x, y), np.where(better, y, x)


def alignment_from_teams(teams: pd.DataFrame) -> dict[int, tuple[str, str]]:
    """Current conference / division of every active team."""
    t = teams[teams["active"].astype(bool) & teams["division"].notna()]
    return {int(r.id): (r.conference, r.division) for r in t.itertuples()}
