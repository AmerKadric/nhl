"""Lineup-strength features: who a team is actually icing.

Team ratings describe a club's average strength over many games. They cannot
see that a first-pair defenceman has been out for a week. These features
measure how the lineup a team most recently dressed compares with the lineup
its ratings were earned with.

Two player measures are tracked, both exponentially weighted per player:

- game score: a box-score summary of a skater's game (the published Game
  Score formula restricted to the components the NHL stats API provides);
- ice time: minutes per game, i.e. how much the coach relies on the player.

For each team the feature compares the lineup it MOST RECENTLY dressed with
the lineup it usually dresses, both valued at the players' current ratings:

    sum over skaters of (dressed last game - usual dressing rate) * rating

A healthy, unchanged lineup scores exactly zero; a regular who sat out
counts against the team by his rating, less that of whoever replaced him. Everything is computed one calendar day at a time from games
played on earlier days only. The `_conf` variants use the lineup actually
dressed in the game being predicted; that is game-day information and is
used only to measure how much confirmed lineups would be worth."""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

LN2 = math.log(2.0)


@dataclass
class LineupParams:
    hl_score: float = 40.0     # half-life (player games) of the game-score rating
    hl_toi: float = 20.0       # half-life (player games) of the ice-time rating
    hl_team: float = 30.0      # half-life (team games) of a team's baseline
    prior_games: float = 10.0  # shrinkage of a player's rating toward the position average
    stale_days: int = 60       # a "last lineup" older than this carries no information
    regular_rate: float = 0.6  # dressed share above which a skater counts as a regular


def game_score(goals, assists, shots, plus_minus, pim) -> np.ndarray:
    """Game Score (Luszczyszyn) using the components available per game:
    goals, assists (primary/secondary averaged), shots, on-ice goal
    differential and penalties taken."""
    return (0.75 * goals + 0.625 * assists + 0.075 * shots + 0.15 * plus_minus
            - 0.075 * np.minimum(pim, 4.0))


class _Player:
    __slots__ = ("gs", "toi", "w_gs", "w_toi", "d")

    def __init__(self, d: int):
        self.gs = 0.0      # decayed sum of game score
        self.toi = 0.0     # decayed sum of minutes
        self.w_gs = 0.0    # decayed game counts
        self.w_toi = 0.0
        self.d = d         # 1 = defenceman


class _Team:
    __slots__ = ("last", "last_date", "w", "dress", "n")

    def __init__(self):
        self.last: list[int] = []            # skaters dressed in the most recent game
        self.last_date = None
        self.w = 0.0                         # decayed count of team games
        self.dress: dict[int, float] = {}    # decayed count of games each skater dressed
        self.n = 0


class LineupBuilder:
    def __init__(self, games: pd.DataFrame, sgs: pd.DataFrame, franchise: dict[int, int], params: LineupParams | None = None):
        self.p = params or LineupParams()
        self.fr = franchise
        self.players: dict[int, _Player] = {}
        self.teams: dict[int, _Team] = {}
        # league position averages (index 0 forwards, 1 defence), slow moving
        self.lg_gs = [0.45, 0.30]
        self.lg_toi = [15.5, 20.0]
        self._a = 1.0 - math.exp(-LN2 / 20000.0)
        self.d_gs = math.exp(-LN2 / self.p.hl_score)
        self.d_toi = math.exp(-LN2 / self.p.hl_toi)
        self.d_team = math.exp(-LN2 / self.p.hl_team)
        g = games.copy()
        g["_t"] = g["start_time_utc"].fillna(pd.Timestamp("1900-01-01"))
        self.games = g.sort_values(["game_date", "_t", "id"])
        self.by_game: dict[int, dict[int, list]] = {}
        if len(sgs):
            s = sgs.copy()
            s["gsc"] = game_score(s["goals"].fillna(0).to_numpy(float), s["assists"].fillna(0).to_numpy(float),
                                  s["shots"].fillna(0).to_numpy(float), s["plus_minus"].fillna(0).to_numpy(float),
                                  s["pim"].fillna(0).to_numpy(float))
            s["min"] = s["toi"].fillna(0).to_numpy(float) / 60.0
            s["isd"] = (s["position"] == "D").astype(int)
            for gid, tid, pid, isd, gsc, mins in zip(s["game_id"].to_numpy(), s["team_id"].to_numpy(), s["player_id"].to_numpy(),
                                                     s["isd"].to_numpy(), s["gsc"].to_numpy(), s["min"].to_numpy()):
                self.by_game.setdefault(int(gid), {}).setdefault(int(tid), []).append((int(pid), int(isd), float(gsc), float(mins)))

    # -- ratings -------------------------------------------------------------
    def value(self, pid: int, d_hint: int = 0) -> tuple[float, float]:
        """(game-score rating, ice-time rating) for a skater, shrunk toward
        the position average. An unseen skater is rated at that average."""
        pl = self.players.get(pid)
        k = self.p.prior_games
        if pl is None:
            return self.lg_gs[d_hint], self.lg_toi[d_hint]
        return ((pl.gs + k * self.lg_gs[pl.d]) / (pl.w_gs + k), (pl.toi + k * self.lg_toi[pl.d]) / (pl.w_toi + k))

    def strength(self, pids: list[int], hints: dict[int, int] | None = None) -> tuple[float, float]:
        gs = toi = 0.0
        for pid in pids:
            a, b = self.value(pid, (hints or {}).get(pid, 0))
            gs += a
            toi += b
        return gs, toi

    def team(self, team_id: int) -> _Team:
        key = self.fr[team_id]
        t = self.teams.get(key)
        if t is None:
            t = self.teams[key] = _Team()
        return t

    # -- features --------------------------------------------------------------
    def _dev(self, t: _Team, pids: list[int], hints=None) -> tuple[float, float]:
        """Strength of lineup `pids` minus the team's usual lineup, both at
        current player ratings."""
        if not pids or t.w <= 0:
            return 0.0, 0.0
        gs, toi = self.strength(pids, hints)
        for pid, cnt in t.dress.items():
            rate = cnt / t.w
            a, b = self.value(pid)
            gs -= rate * a
            toi -= rate * b
        shrink = t.n / (t.n + 3.0)   # little weight until usage is established
        return gs * shrink, toi * shrink

    def absences(self, team_id: int) -> list[dict]:
        """Regular skaters who did not dress in the team's most recent game."""
        t = self.team(team_id)
        last = set(t.last)
        out = []
        if t.n < 5 or t.w <= 0:
            return out
        for pid, cnt in t.dress.items():
            rate = cnt / t.w
            if rate >= self.p.regular_rate and pid not in last:
                gs, toi = self.value(pid)
                out.append({"pid": pid, "dress_rate": round(rate, 3), "toi": round(toi, 2), "game_score": round(gs, 3),
                            "defence": bool(self.players[pid].d) if pid in self.players else False})
        return sorted(out, key=lambda x: -x["toi"])

    def run(self) -> pd.DataFrame:
        rows: list[dict] = []
        p = self.p

        def pre(g) -> None:
            row = {"game_id": g.id}
            lines = self.by_game.get(g.id, {})
            for side, tid in (("h", g.home_team_id), ("a", g.away_team_id)):
                t = self.team(tid)
                fresh = t.last_date is not None and (g.game_date - t.last_date).days <= p.stale_days
                gs, toi = self._dev(t, t.last) if fresh else (0.0, 0.0)
                row[f"{side}_lu_gs"], row[f"{side}_lu_toi"] = gs, toi
                actual = lines.get(tid)
                if actual:
                    cg, ct = self._dev(t, [x[0] for x in actual], {x[0]: x[1] for x in actual})
                else:
                    cg, ct = gs, toi   # unknown at forecast time: fall back to the projection
                row[f"{side}_lu_gs_conf"], row[f"{side}_lu_toi_conf"] = cg, ct
                row[f"{side}_lu_n"] = float(len(t.last)) if fresh else 0.0
            rows.append(row)

        def post(g) -> None:
            lines = self.by_game.get(g.id)
            if not lines:
                return
            for tid in (g.home_team_id, g.away_team_id):
                line = lines.get(tid)
                if not line:
                    continue
                t = self.team(tid)
                pids = [x[0] for x in line]
                t.w = t.w * self.d_team + 1.0
                t.n += 1
                dressed = set(pids)
                for pid in list(t.dress):
                    t.dress[pid] *= self.d_team
                    if t.dress[pid] < 0.02 * t.w and pid not in dressed:
                        del t.dress[pid]   # long gone: traded or out for months
                for pid in pids:
                    t.dress[pid] = t.dress.get(pid, 0.0) + 1.0
                t.last, t.last_date = pids, g.game_date
                for pid, isd, gsc, mins in line:
                    pl = self.players.get(pid)
                    if pl is None:
                        pl = self.players[pid] = _Player(isd)
                    pl.d = isd
                    pl.gs = pl.gs * self.d_gs + gsc
                    pl.w_gs = pl.w_gs * self.d_gs + 1.0
                    pl.toi = pl.toi * self.d_toi + mins
                    pl.w_toi = pl.w_toi * self.d_toi + 1.0
                    self.lg_gs[isd] += self._a * (gsc - self.lg_gs[isd])
                    self.lg_toi[isd] += self._a * (mins - self.lg_toi[isd])

        for _, day in itertools.groupby(self.games.itertuples(), key=lambda g: g.game_date):
            day = list(day)
            for g in day:
                pre(g)
            for g in day:
                post(g)
        return pd.DataFrame(rows)


LINEUP_COLS = ["lu_gs", "lu_toi", "lu_gs_conf", "lu_toi_conf", "lu_n"]
