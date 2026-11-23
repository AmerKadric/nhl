"""Playoff mode: series and Cup odds from the bracket as it actually stands.

Series are read from the playoff games themselves. An NHL playoff game id
ends in round, series and game number, and the winners of series 2m-1 and 2m
of one round meet in series m of the next, so the bracket never has to be
entered by hand.

Only the current round's matchups are taken as known. Later rounds are
played out between simulated winners, and nothing scheduled beyond the
current round is read, which keeps historical tests honest.

Each simulated world draws every team's strength, then prices each series
from its current score with the single-game playoff model (no shootouts) and
the 2-2-1-1-1 home pattern."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .. import config
from ..models.design import TEAM_FEATS
from . import season as S

SERIES_PER_ROUND = {1: 8, 2: 4, 3: 2, 4: 1}
ROUND_NAMES = {1: "First round", 2: "Second round", 3: "Conference finals", 4: "Stanley Cup Final"}

# Multiplier on the rating filter's team-strength uncertainty for playoff
# series. The study in backtest/playoffs.py found no measurable difference
# between 0 and 3 over 105 past series (series Brier moved by about 0.001),
# so the filter's own uncertainty is used unscaled.
STRENGTH_SD_SCALE = 1.0


@dataclass
class Series:
    round: int
    matchup: int
    top: int          # team with home ice (hosts games 1, 2, 5 and 7)
    bottom: int
    wins_top: int = 0
    wins_bottom: int = 0

    @property
    def done(self) -> bool:
        return self.wins_top >= 4 or self.wins_bottom >= 4

    @property
    def winner(self) -> int | None:
        return self.top if self.wins_top >= 4 else self.bottom if self.wins_bottom >= 4 else None


def series_table(games: pd.DataFrame, season: int) -> list[Series]:
    """Every playoff series of a season that has at least one game listed."""
    g = games[(games["season"] == season) & (games["game_type"] == config.PLAYOFF)].copy()
    if g.empty:
        return []
    g["round"] = (g["id"] // 100) % 10
    g["matchup"] = (g["id"] // 10) % 10
    g["gno"] = g["id"] % 10
    out = []
    for (r, m), x in g[g["round"].between(1, 4)].groupby(["round", "matchup"]):
        x = x.sort_values("gno")
        top, bottom = int(x.iloc[0]["home_team_id"]), int(x.iloc[0]["away_team_id"])
        played = x[x["home_score"].notna()]
        home_won = played["home_score"] > played["away_score"]
        wt = int(((played["home_team_id"] == top) & home_won).sum() + ((played["away_team_id"] == top) & ~home_won).sum())
        out.append(Series(int(r), int(m), top, bottom, wt, int(len(played) - wt)))
    return sorted(out, key=lambda s: (s.round, s.matchup))


def current_round(series: list[Series]) -> list[Series] | None:
    """The series of the latest round whose bracket is fully listed, or None
    if not even the first round is. The next round's series are listed one at
    a time while others are still being played; until all of them are, the
    round before stays current and its winners are paired by the simulation."""
    for r in sorted({s.round for s in series}, reverse=True):
        cur = [s for s in series if s.round == r]
        if len(cur) == SERIES_PER_ROUND[r]:
            return cur
    return None


def team_features(feat: pd.DataFrame, season: int, team_ids: list[int]) -> dict[int, dict]:
    """Each team's most recent pre-game features: from its next scheduled
    game if it has one, otherwise from the last game it played."""
    f = feat[feat["season"] == season].sort_values(["game_date", "game_id"])
    out: dict[int, dict] = {}
    want = set(team_ids)
    for rows in (f[~f["final"]].itertuples(), reversed(list(f[f["final"]].itertuples()))):
        for g in rows:
            for side, tid in (("h", g.home_team_id), ("a", g.away_team_id)):
                if tid in want and tid not in out:
                    out[tid] = {c: getattr(g, f"{side}_{c}") for c in TEAM_FEATS}
        if len(out) == len(want):
            break
    return out


@dataclass
class PlayoffInputs:
    season: int
    team_ids: list[int]
    series: list[Series]                # the current round, in bracket order
    earlier: list[Series]               # completed earlier rounds, for display
    log_lam_home: np.ndarray
    log_lam_away: np.ndarray
    sd_off: np.ndarray
    sd_def: np.ndarray
    record: np.ndarray                  # regular-season record, decides home ice in later rounds
    sp: object


def prepare(feat: pd.DataFrame, games: pd.DataFrame, fc, season: int, scale: float = STRENGTH_SD_SCALE,
            only_round: int | None = None) -> PlayoffInputs | None:
    """Inputs for the current round (or `only_round`, used by the backtest to
    stand at the start of an earlier round)."""
    table = series_table(games, season)
    if only_round is not None:
        table = [s for s in table if s.round <= only_round]
    cur = current_round(table)
    if cur is None:
        return None
    team_ids = sorted({t for s in cur for t in (s.top, s.bottom)})
    feats = team_features(feat, season, team_ids)
    if len(feats) < len(team_ids):
        return None
    f = feat[feat["season"] == season]
    lg = float(f["lg_g"].iloc[-1])
    llh, lla = S.pair_rates(fc, feats, team_ids, season, f["game_date"].iloc[-1], lg, current_lineups=True)
    sd_off, sd_def = S.strength_sd(fc, feats, team_ids, np.zeros(len(team_ids)))
    reg = games[(games["season"] == season) & (games["game_type"] == config.REGULAR) & games["home_score"].notna()]
    st = S.current_standings(reg, team_ids)
    record = st["pts"] * 1e9 + st["rw"] * 1e7 + st["row"] * 1e5 + st["w"] * 1e3 + (st["gf"] - st["ga"] + 500.0)
    r0 = cur[0].round
    return PlayoffInputs(season=season, team_ids=team_ids, series=cur, earlier=[s for s in table if s.round < r0],
                         log_lam_home=llh, log_lam_away=lla, sd_off=sd_off * scale, sd_def=sd_def * scale, record=record, sp=fc.sp)


def simulate(inp: PlayoffInputs, n_sims: int = 20000, seed: int = 0, batch: int = 2000) -> dict:
    """Series odds for the current round and each team's odds of winning each
    remaining round."""
    rng = np.random.default_rng(seed)
    idx = {t: i for i, t in enumerate(inp.team_ids)}
    n = len(inp.team_ids)
    xs, grid = S.win_prob_grid(inp.sp, playoff=True)
    r0 = inp.series[0].round
    won = {r: np.zeros(n) for r in range(r0, 5)}      # times each team won round r
    p_top = np.zeros(len(inp.series))
    for start in range(0, n_sims, batch):
        b = min(batch, n_sims - start)
        rows = np.arange(b)
        off = rng.normal(0.0, 1.0, (b, n)) * inp.sd_off
        dfn = rng.normal(0.0, 1.0, (b, n)) * inp.sd_def
        winners = []
        for k, s in enumerate(inp.series):
            hi, lo = idx[s.top], idx[s.bottom]
            if s.done:
                w = np.full(b, idx[s.winner])
                p_top[k] += b * (1.0 if s.winner == s.top else 0.0)
            else:
                p = S.series_win_prob(xs, grid, inp.log_lam_home, inp.log_lam_away, off, dfn, rows, hi, lo, s.wins_top, s.wins_bottom)
                p_top[k] += p.sum()
                w = np.where(rng.random(b) < p, hi, lo)
            winners.append(w)
            np.add.at(won[r0], w, 1.0)
        r = r0
        while len(winners) > 1:
            r += 1
            nxt = []
            for m in range(0, len(winners), 2):
                x, y = winners[m], winners[m + 1]
                better = inp.record[x] >= inp.record[y]      # home ice to the better regular-season record
                hi, lo = np.where(better, x, y), np.where(better, y, x)
                p = S.series_win_prob(xs, grid, inp.log_lam_home, inp.log_lam_away, off, dfn, rows, hi, lo)
                w = np.where(rng.random(b) < p, hi, lo)
                nxt.append(w)
                np.add.at(won[r], w, 1.0)
            winners = nxt
    return {"round": r0, "p_top": (p_top / n_sims).tolist(),
            "teams": {t: {r: float(won[r][i] / n_sims) for r in won} for t, i in idx.items()}}
