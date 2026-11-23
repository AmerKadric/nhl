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

# How a game's stakes are computed (see simulate_detail). The strength-balanced
# split looked like the more honest one on paper, but the check in
# backtest/season.py::leverage_check said otherwise. Over 754 team-games of
# 2026-27, odds moved +3.5 points for winners and -3.3 for losers by the next
# morning; the raw split had said about 4.0 either way (18% high, RMSE 0.0258)
# and the balanced one about 2.0 (40% low, RMSE 0.0294). A result really is
# evidence about a team, and the ratings move on it. artifacts/backtest/
# leverage.json holds the numbers; `puckiq season-history` re-runs the check.
LEVERAGE_ADJUSTED = False


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


def series_prob(p_home: np.ndarray, p_road: np.ndarray, wins_needed: int = 4, a0: int = 0, b0: int = 0) -> np.ndarray:
    """Probability the team with home ice wins a best-of-seven, given its
    per-game win probability at home and on the road. `a0` and `b0` are the
    wins each side already has, so a series can be priced mid-way."""
    p_home, p_road = np.asarray(p_home, float), np.asarray(p_road, float)
    zero = np.zeros_like(p_home)
    if a0 >= wins_needed:
        return zero + 1.0
    if b0 >= wins_needed:
        return zero
    state = {(a0, b0): np.ones_like(p_home)}
    won = zero.copy()
    for g in range(a0 + b0, 2 * wins_needed - 1):
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


def series_win_prob(xs, grid, llh, lla, off, dfn, rows, hi, lo, a0: int = 0, b0: int = 0) -> np.ndarray:
    """P(`hi` wins the series) in each simulated world; `hi` has home ice.
    `llh[i, j]` / `lla[i, j]` are the log scoring rates of home team i and
    away team j; `off` / `dfn` are that world's team-strength draws."""
    p_h = interp_win(xs, grid, llh[hi, lo] + off[rows, hi] + dfn[rows, lo], lla[hi, lo] + off[rows, lo] + dfn[rows, hi])
    p_r = 1.0 - interp_win(xs, grid, llh[lo, hi] + off[rows, lo] + dfn[rows, hi], lla[lo, hi] + off[rows, hi] + dfn[rows, lo])
    return series_prob(p_h, p_r, a0=a0, b0=b0)


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
    # identity of each remaining game, for per-game questions (what does tonight mean?)
    game_ids: np.ndarray | None = None
    game_dates: np.ndarray | None = None


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
    remaining = np.bincount(np.concatenate([home, away]), minlength=len(team_ids))
    sd_off, sd_def = strength_sd(fc, feats, team_ids, remaining)
    llh, lla = pair_rates(fc, feats, team_ids, season, fut["game_date"].iloc[0], float(fut["lg_g"].iloc[0]))

    return SeasonInputs(
        team_ids=team_ids, conference=[alignment[t][0] for t in team_ids], division=[alignment[t][1] for t in team_ids],
        home=home, away=away, lam_home=lam_h, lam_away=lam_a, sd_off=sd_off, sd_def=sd_def,
        log_lam_home=llh, log_lam_away=lla, sp=fc.sp, game_ids=fut["game_id"].to_numpy(),
        game_dates=fut["game_date"].to_numpy(), **st)


# --------------------------------------------------------------------------
# simulation
# --------------------------------------------------------------------------
def _regular_season(rng, inp: SeasonInputs, off: np.ndarray, dfn: np.ndarray, track: np.ndarray | None = None,
                    reg_grid: tuple[np.ndarray, np.ndarray] | None = None) -> dict[str, np.ndarray]:
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
        "home_win": None if track is None else home_win[:, track],
        # the home side's chance of winning each tracked game given this simulated season's team strengths
        "p_home": None if track is None else interp_win(*reg_grid, np.log(lh[:, track]), np.log(la[:, track])),
        "pts": tally(2 * home_win + (tie & ~home_win), 2 * (~home_win) + (tie & home_win)),
        "rw": tally(h > a, a > h),
        "row": tally((h > a) | (tie & ~so & home_win), (a > h) | (tie & ~so & ~home_win)),
        "w": tally(home_win, ~home_win),
        "gd": tally(h + home_extra - a - (tie & ~home_extra), a + (tie & ~home_extra) - h - home_extra),
    }


def simulate(inp: SeasonInputs, n_sims: int = 10000, seed: int = 0, batch: int = 1000) -> pd.DataFrame:
    return simulate_detail(inp, n_sims, seed, batch)[0]


def simulate_detail(inp: SeasonInputs, n_sims: int = 10000, seed: int = 0, batch: int = 1000,
                    track: np.ndarray | None = None) -> tuple[pd.DataFrame, dict | None]:
    """The season simulation, plus (for the games whose indices are in
    `track`) how often every team made the playoffs in the simulated seasons
    where the home side won that game and in those where it lost. Tracking
    draws no random numbers, so the odds are the same with or without it.

    Two tallies are kept. The raw one simply splits the simulated seasons by
    the game's result. A season in which a team wins tonight is also, on
    average, one in which it was drawn stronger for the whole year, so the raw
    split combines the value of the two points with what the result says
    about the team. The adjusted tally weights each season by the inverse of
    the chance its result had given that season's strengths, which makes the
    winning and losing groups alike in strength and leaves only the points.
    Which one is published is decided by LEVERAGE_ADJUSTED, on evidence."""
    rng = np.random.default_rng(seed)
    n = len(inp.team_ids)
    if track is not None and len(track) == 0:
        track = None
    k = 0 if track is None else len(track)
    made_win, made_loss, n_win = np.zeros((k, n)), np.zeros((k, n)), np.zeros(k)
    adj_win, adj_loss, w_win, w_loss = np.zeros((k, n)), np.zeros((k, n)), np.zeros(k), np.zeros(k)
    reg_grid = win_prob_grid(inp.sp, playoff=False) if track is not None else None
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
        sim = _regular_season(rng, inp, off, dfn, track, reg_grid)
        pts = inp.pts + sim["pts"]
        key = (pts * 1e9 + (inp.rw + sim["rw"]) * 1e7 + (inp.row + sim["row"]) * 1e5 + (inp.w + sim["w"]) * 1e3
               + (inp.gf - inp.ga + sim["gd"] + 500.0) + rng.random((b, n)) * 0.5)
        points.append(pts.astype(np.int16))

        def series(hi, lo):
            """Winner of a best-of-seven; `hi` has home ice."""
            p = series_win_prob(xs, grid, inp.log_lam_home, inp.log_lam_away, off, dfn, rows, hi, lo)
            return np.where(rng.random(b) < p, hi, lo)

        def by_record(x, y):
            """(better record, worse record): home ice in the later rounds."""
            better = key[rows, x] >= key[rows, y]
            return np.where(better, x, y), np.where(better, y, x)

        champs = []
        made = np.zeros((b, n), dtype=np.float32)      # 1 where a team made the playoffs in that simulated season
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
                made[rows[:, None], t] = 1.0
            bump("playoffs", wc1)
            bump("playoffs", wc2)
            made[rows, wc1] = 1.0
            made[rows, wc2] = 1.0
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
        if track is not None:
            hw = sim["home_win"].astype(np.float32)        # [b, tracked games]
            made_win += hw.T @ made
            made_loss += (1.0 - hw).T @ made
            n_win += hw.sum(axis=0)
            p = np.clip(sim["p_home"], 0.05, 0.95)
            ww, wl = hw / p, (1.0 - hw) / (1.0 - p)
            adj_win += ww.T @ made
            adj_loss += wl.T @ made
            w_win += ww.sum(axis=0)
            w_loss += wl.sum(axis=0)

    allp = np.concatenate(points, axis=0)
    out = pd.DataFrame({
        "team_id": inp.team_ids, "conference": inp.conference, "division": inp.division,
        "gp": inp.gp.astype(int), "w": inp.w.astype(int), "l": inp.l.astype(int), "otl": inp.otl.astype(int),
        "points": inp.pts.astype(int), "games_remaining": np.bincount(np.concatenate([inp.home, inp.away]), minlength=n),
        "proj_points": allp.mean(axis=0), "points_p10": np.percentile(allp, 10, axis=0),
        "points_p50": np.percentile(allp, 50, axis=0), "points_p90": np.percentile(allp, 90, axis=0),
    })
    for name, v in counts.items():
        out[f"p_{name}"] = v / n_sims
    detail = None if track is None else {"track": np.asarray(track), "n_sims": n_sims, "n_win": n_win,
                                         "made_win": made_win, "made_loss": made_loss,
                                         "adj_win": adj_win, "adj_loss": adj_loss, "w_win": w_win, "w_loss": w_loss}
    return out, detail


def next_slate(inp: SeasonInputs) -> np.ndarray:
    """Indices of the games on the first date that still has games to play."""
    if inp.game_dates is None or len(inp.game_dates) == 0:
        return np.array([], dtype=int)
    return np.flatnonzero(inp.game_dates == inp.game_dates.min())


def leverage(inp: SeasonInputs, detail: dict | None, adjusted: bool = LEVERAGE_ADJUSTED) -> list[dict]:
    """What each tracked game means for the two teams playing it: playoff
    odds if the team wins that game against odds if it loses. `adjusted`
    chooses the strength-balanced tally (see simulate_detail); the raw split
    is kept for comparison. `se` is the standard error of the difference, so
    a swing smaller than about twice that is simulation noise, not a finding."""
    if detail is None:
        return []
    n = detail["n_sims"]
    mw, ml = (detail["adj_win"], detail["adj_loss"]) if adjusted else (detail["made_win"], detail["made_loss"])
    out = []
    for j, gi in enumerate(detail["track"]):
        nw = float(detail["n_win"][j])
        nl = n - nw
        if nw < 1 or nl < 1:
            continue
        dw, dl = (float(detail["w_win"][j]), float(detail["w_loss"][j])) if adjusted else (nw, nl)
        now = (detail["made_win"][j] + detail["made_loss"][j]) / n
        h, a = int(inp.home[gi]), int(inp.away[gi])

        def side(t: int, p_if_win: float, n_if_win: float, p_if_loss: float, n_if_loss: float) -> dict:
            se = math.sqrt(p_if_win * (1 - p_if_win) / n_if_win + p_if_loss * (1 - p_if_loss) / n_if_loss)
            return {"team_id": int(inp.team_ids[t]), "now": float(now[t]), "if_win": p_if_win, "if_loss": p_if_loss,
                    "swing": p_if_win - p_if_loss, "se": se}

        out.append({"game_id": None if inp.game_ids is None else int(inp.game_ids[gi]), "p_home_win": nw / n,
                    "home": side(h, float(mw[j][h] / dw), nw, float(ml[j][h] / dl), nl),
                    "away": side(a, float(ml[j][a] / dl), nl, float(mw[j][a] / dw), nw)})
    return out


def by_seed(x, y, division_winner, key, rows):
    """Second-round home ice: the division winner if it advanced, otherwise
    the team with the better record."""
    x_is = x == division_winner
    y_is = y == division_winner
    better = np.where(x_is, True, np.where(y_is, False, key[rows, x] >= key[rows, y]))
    return np.where(better, x, y), np.where(better, y, x)


def strength_sd(fc, feats: dict[int, dict], team_ids: list[int], remaining: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Standard deviation of each team's offence and defence on the log
    scoring-rate scale: the rating filter's posterior spread plus the drift
    expected over the games remaining."""
    beta = dict(zip(fc.glm.cols, fc.glm.beta[1:]))
    kf = load_params().kf
    sd_off, sd_def = np.zeros(len(team_ids)), np.zeros(len(team_ids))
    for i, t in enumerate(team_ids):
        vo = vd = 0.0
        for m in ("xg5", "xga", "g"):
            if f"t_off_{m}" in beta:
                drift = kf[m].q * remaining[i] / 2.0   # average extra variance over the games left
                vo += beta[f"t_off_{m}"] ** 2 * (feats[t][f"poff_{m}"] + drift)
                vd += beta[f"o_def_{m}"] ** 2 * (feats[t][f"pdef_{m}"] + drift)
        sd_off[i], sd_def[i] = math.sqrt(vo), math.sqrt(vd)
    return sd_off, sd_def


NEUTRAL_REST = {"rest": 2.0, "b2b": 0.0, "g7": 3.0, "travel": 0.0, "tz": 0.0, "road_streak": 0.0}
NEUTRAL_LINEUP = {"lu_gs": 0.0, "lu_toi": 0.0, "lu_gs_conf": 0.0, "lu_toi_conf": 0.0}


def pair_rates(fc, feats: dict[int, dict], team_ids: list[int], season: int, game_date, lg: float,
               current_lineups: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Log scoring rates of a playoff game between every ordered pair of
    teams, [home, away], on neutral rest. Lineups are taken as usual unless
    `current_lineups` is set (playoffs are near enough for today's absences
    to matter; a season months ahead is not)."""
    neutral = dict(NEUTRAL_REST) if current_lineups else {**NEUTRAL_REST, **NEUTRAL_LINEUP}
    idx = {t: i for i, t in enumerate(team_ids)}
    pairs = []
    for hi in team_ids:
        for lo in team_ids:
            if hi == lo:
                continue
            r = {"game_id": idx[hi] * 100 + idx[lo], "season": season, "game_date": game_date, "playoff": 1.0,
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
    return llh, lla


def alignment_from_teams(teams: pd.DataFrame) -> dict[int, tuple[str, str]]:
    """Current conference / division of every active team."""
    t = teams[teams["active"].astype(bool) & teams["division"].notna()]
    return {int(r.id): (r.conference, r.division) for r in t.itertuples()}
