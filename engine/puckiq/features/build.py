"""Pre-game feature construction.

A single chronological pass, one calendar day at a time. Features for ALL of
a day's games are read from the running state first; only then are that
day's results applied. A feature can therefore only depend on games played
on earlier days, never on the game itself or on another game the same
night, which is also how production forecasts are made (before the slate). Schedule-derived
state (rest, travel) is advanced for unplayed games too, because the schedule
is public in advance; performance state is advanced only by final results."""
from __future__ import annotations

import datetime as dt
import itertools
import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import sqlalchemy as sa

from .. import config, db
from ..ingest.reference import TEAM_REF
from .lineups import LINEUP_COLS, LineupBuilder
from .params import FeatureParams, load_params

METRICS = ("xg5", "xga", "g")
LN2 = math.log(2.0)


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


@dataclass
class Frames:
    games: pd.DataFrame
    tgs: pd.DataFrame
    ggs: pd.DataFrame
    teams: pd.DataFrame
    players: pd.DataFrame
    sgs: pd.DataFrame = field(default_factory=pd.DataFrame)   # skater game lines


def _read_frames(conn, min_game_id: int | None = None) -> dict[str, pd.DataFrame]:
    def q(table, col):
        stmt = sa.select(table)
        if min_game_id is not None:
            stmt = stmt.where(col >= min_game_id)
        return pd.read_sql(stmt, conn)
    return {"games": q(db.games, db.games.c.id), "tgs": q(db.team_game_stats, db.team_game_stats.c.game_id),
            "ggs": q(db.goalie_game_stats, db.goalie_game_stats.c.game_id),
            "sgs": q(db.skater_game_stats, db.skater_game_stats.c.game_id)}


def load_frames() -> Frames:
    """All data the feature pass needs.

    Against a remote database, completed seasons never change, so they are
    read once and kept in a local cache file; later runs fetch only the
    current season. Game ids start with the season year, which makes the
    split a simple id range."""
    with db.begin() as c:
        teams = pd.read_sql(sa.select(db.teams), c)
        players = pd.read_sql(sa.select(db.players), c)
        season = c.execute(sa.select(sa.func.max(db.games.c.season))).scalar()
        remote = c.dialect.name != "sqlite"
        cache = config.CACHE_DIR / f"history_before_{season}.pkl"
        hist = None
        if remote and season and cache.exists():
            try:
                hist = pd.read_pickle(cache)
            except Exception:   # unreadable cache (e.g. library upgrade): rebuild it
                hist = None
        if hist is not None:
            cur = _read_frames(c, int(season) * 1_000_000)
            parts = {k: pd.concat([hist[k], cur[k]], ignore_index=True) for k in cur}
        else:
            parts = _read_frames(c)
            if remote and season:
                cut_id = int(season) * 1_000_000
                config.ensure_dirs()
                pd.to_pickle({k: v[v["id" if k == "games" else "game_id"] < cut_id].reset_index(drop=True)
                              for k, v in parts.items()}, cache)
    games = parts["games"]
    games["game_date"] = pd.to_datetime(games["game_date"]).dt.date
    return Frames(games, parts["tgs"], parts["ggs"], teams, players, parts["sgs"])


class TeamState:
    __slots__ = ("elo", "off", "deff", "p_off", "p_def", "pp_num", "pp_den", "pk_num", "pk_den",
                 "pen_drawn", "pen_taken", "pen_den", "fin_num", "fin_den", "dates", "loc", "road_streak",
                 "recent", "season", "season_gp", "starts", "home_loc", "gp_total")

    def __init__(self, params: FeatureParams, home_loc):
        self.elo = 1500.0
        self.off = {m: 0.0 for m in METRICS}
        self.deff = {m: 0.0 for m in METRICS}
        self.p_off = {m: params.kf[m].p0 for m in METRICS}
        self.p_def = {m: params.kf[m].p0 for m in METRICS}
        self.pp_num = self.pp_den = self.pk_num = self.pk_den = 0.0
        self.pen_drawn = self.pen_taken = self.pen_den = 0.0
        self.fin_num = self.fin_den = 0.0
        self.dates: deque = deque(maxlen=12)
        self.loc = home_loc
        self.home_loc = home_loc
        self.road_streak = 0
        self.recent: deque = deque(maxlen=20)   # (xgf5, xga5, gf_reg, ga_reg)
        self.season = None
        self.season_gp = 0
        self.gp_total = 0
        self.starts: deque = deque(maxlen=30)   # (date, starter_pid)


class GoalieState:
    __slots__ = ("xga", "gsax", "last_date", "team", "recent", "apps")

    def __init__(self):
        self.xga = 0.0
        self.gsax = 0.0
        self.last_date = None
        self.team = None
        self.recent: deque = deque(maxlen=10)  # (xga, gsax)
        self.apps = 0


class League:
    """Slow exponentially-weighted league averages used to centre ratings."""

    def __init__(self, hl_games: float):
        self.a = 1.0 - math.exp(-LN2 / hl_games)
        self.mu = {"xg5": 2.45, "xga": 2.95, "g": 2.75}
        self.hfa = {"xg5": 0.03, "xga": 0.08, "g": 0.12}
        self.pp_num, self.pp_den = 6.8 / 3600.0 * 60000.0, 60000.0   # decayed xG and seconds of 5-on-4
        self.pen_pg = 3.3

    @property
    def pp_rate(self) -> float:
        """League 5-on-4 xG per second (time-weighted, so short power plays
        do not bias the average)."""
        return self.pp_num / self.pp_den

    def update_pp(self, xg: float, seconds: float) -> None:
        self.pp_num = self.pp_num * (1 - self.a) + xg
        self.pp_den = self.pp_den * (1 - self.a) + seconds

    def update(self, m: str, home_obs: float, away_obs: float) -> None:
        self.mu[m] += self.a * ((home_obs + away_obs) / 2 - self.mu[m])
        self.hfa[m] += self.a * ((home_obs - away_obs) / 2 - self.hfa[m])


def goalie_rating(gs: GoalieState | None, p: FeatureParams) -> float:
    """Shrunk goals saved above expected per expected goal faced."""
    if gs is None:
        return p.gk_prior_mean
    return (gs.gsax + p.gk_prior_mean * p.gk_prior_xg) / (gs.xga + p.gk_prior_xg)


def goalie_recent(gs: GoalieState | None, p: FeatureParams) -> float:
    if gs is None or not gs.recent:
        return p.gk_prior_mean
    x = sum(r[0] for r in gs.recent)
    g = sum(r[1] for r in gs.recent)
    return (g + p.gk_prior_mean * 30.0) / (x + 30.0)


class FeatureBuilder:
    def __init__(self, frames: Frames, params: FeatureParams | None = None):
        self.f = frames
        self.p = params or load_params()
        t = frames.teams.set_index("id")
        self.fr = t["franchise_key"].to_dict()
        self.abbrev = t["abbrev"].to_dict()
        self.loc = {tid: (TEAM_REF[a]["lat"], TEAM_REF[a]["lon"], TEAM_REF[a]["utc"]) for tid, a in self.abbrev.items()}
        self.teams: dict[int, TeamState] = {}
        self.goalies: dict[int, GoalieState] = {}
        self.league = League(self.p.league_hl_games)
        self.rating_log: list[dict] = []
        self.kf_ll = {m: [0.0, 0] for m in METRICS}   # one-step-ahead log-likelihood (tuning)
        self.elo_ll = [0.0, 0]

    # -- state helpers -----------------------------------------------------
    def team(self, team_id: int) -> TeamState:
        key = self.fr[team_id]
        ts = self.teams.get(key)
        if ts is None:
            ts = self.teams[key] = TeamState(self.p, self.loc[team_id])
        return ts

    def _rollover(self, ts: TeamState, season: int) -> None:
        if ts.season == season:
            return
        if ts.season is not None:
            p = self.p
            ts.elo = 1500.0 + p.elo_carry * (ts.elo - 1500.0)
            for m in METRICS:
                k = p.kf[m]
                ts.off[m] *= k.carry
                ts.deff[m] *= k.carry
                ts.p_off[m] = k.carry ** 2 * ts.p_off[m] + k.q_season
                ts.p_def[m] = k.carry ** 2 * ts.p_def[m] + k.q_season
        ts.season, ts.season_gp = season, 0

    # -- pre-game features ---------------------------------------------------
    def _team_feats(self, ts: TeamState, gdate: dt.date, host_loc, is_home: bool) -> dict:
        p, lg = self.p, self.league
        out = {"elo": ts.elo, "gp": ts.season_gp, "gp_total": ts.gp_total}
        for m in METRICS:
            out[f"off_{m}"] = ts.off[m]
            out[f"def_{m}"] = ts.deff[m]
            out[f"poff_{m}"] = ts.p_off[m]
            out[f"pdef_{m}"] = ts.p_def[m]
        prior = p.st_prior_sec
        out["pp"] = ((ts.pp_num + lg.pp_rate * prior) / (ts.pp_den + prior) - lg.pp_rate) * 3600.0
        out["pk"] = ((ts.pk_num + lg.pp_rate * prior) / (ts.pk_den + prior) - lg.pp_rate) * 3600.0
        pen_prior = 10.0
        out["pen_drawn"] = (ts.pen_drawn + lg.pen_pg * pen_prior) / (ts.pen_den + pen_prior) - lg.pen_pg
        out["pen_taken"] = (ts.pen_taken + lg.pen_pg * pen_prior) / (ts.pen_den + pen_prior) - lg.pen_pg
        out["fin"] = ts.fin_num / (ts.fin_den + p.fin_prior_xg)
        # schedule
        if ts.dates:
            rest = (gdate - ts.dates[-1]).days
        else:
            rest = 7
        fresh = rest > 6
        out["rest"] = float(min(rest, 6))
        out["b2b"] = 1.0 if rest == 1 else 0.0
        out["g7"] = float(sum(1 for d in ts.dates if 0 < (gdate - d).days <= 7))
        origin = ts.home_loc if fresh else ts.loc
        out["travel"] = haversine_km(origin[0], origin[1], host_loc[0], host_loc[1])
        out["tz"] = float(host_loc[2] - origin[2])   # positive = travelled east
        out["road_streak"] = 0.0 if is_home else float(ts.road_streak)
        # recent form windows (for the tree model; NaN until enough games)
        rec = list(ts.recent)
        for n in (5, 10, 20):
            if len(rec) >= n:
                w = rec[-n:]
                xf, xa = sum(r[0] for r in w), sum(r[1] for r in w)
                out[f"xgs_l{n}"] = xf / (xf + xa) if xf + xa > 0 else np.nan
                out[f"gd_l{n}"] = sum(r[2] - r[3] for r in w) / n
            else:
                out[f"xgs_l{n}"] = np.nan
                out[f"gd_l{n}"] = np.nan
        return out

    def _goalie_cands(self, ts: TeamState, team_key: int, gdate: dt.date, b2b: bool) -> list[dict]:
        starts = list(ts.starts)
        if not starts:
            return []
        last10, last30 = starts[-10:], starts
        prev_pid = starts[-1][1]
        pids = []
        for _, pid in reversed(starts[-20:]):
            if pid not in pids:
                pids.append(pid)
        cands = []
        for pid in pids:
            gs = self.goalies.get(pid)
            if gs is None or gs.team != team_key or gs.last_date is None:
                continue
            idle = (gdate - gs.last_date).days
            if idle > 45:
                continue
            prev = 1.0 if pid == prev_pid else 0.0
            cands.append(dict(
                pid=pid, share10=sum(1 for _, q in last10 if q == pid) / len(last10),
                share30=sum(1 for _, q in last30 if q == pid) / len(last30),
                prev_rested=prev * (0.0 if b2b else 1.0), prev_b2b=prev * (1.0 if b2b else 0.0),
                rating=goalie_rating(gs, self.p), recent=goalie_recent(gs, self.p), xga_n=gs.xga, idle=float(min(idle, 30)),
            ))
        return cands

    # -- updates -----------------------------------------------------------
    def _kf_update(self, m: str, h: TeamState, a: TeamState, y_h: float, y_a: float, weight: float = 1.0) -> None:
        k, lg = self.p.kf[m], self.league
        r = k.r / max(weight, 0.25)
        for att, dfn, y, sign in ((h, a, y_h, 1.0), (a, h, y_a, -1.0)):
            exp = lg.mu[m] + att.off[m] - dfn.deff[m] + sign * lg.hfa[m]
            s = att.p_off[m] + dfn.p_def[m] + r
            err = y - exp
            self.kf_ll[m][0] += -0.5 * (math.log(2 * math.pi * s) + err * err / s)
            self.kf_ll[m][1] += 1
            ka, kd = att.p_off[m] / s, dfn.p_def[m] / s
            att.off[m] += ka * err
            dfn.deff[m] -= kd * err
            att.p_off[m] = att.p_off[m] * (1 - ka) + k.q
            dfn.p_def[m] = dfn.p_def[m] * (1 - kd) + k.q
        lg.update(m, y_h, y_a)

    def _elo_update(self, h: TeamState, a: TeamState, home_won: bool, margin: int, shootout: bool) -> None:
        p = self.p
        diff = h.elo + p.elo_hfa - a.elo
        e = 1.0 / (1.0 + 10 ** (-diff / 400.0))
        y = 1.0 if home_won else 0.0
        self.elo_ll[0] += math.log(e if home_won else 1 - e)
        self.elo_ll[1] += 1
        mov = 1.0 if shootout else (0.6686 * math.log(max(margin, 1)) + 0.8048)
        # autocorrelation correction: favourites win by more
        win_diff = diff if home_won else -diff
        mov *= 2.05 / (win_diff * 0.001 + 2.05)
        delta = p.elo_k * mov * (y - e)
        h.elo += delta
        a.elo -= delta

    def _decay(self, hl: float) -> float:
        return math.exp(-LN2 / hl)

    # -- main pass ---------------------------------------------------------
    def run(self) -> pd.DataFrame:
        f, p, lg = self.f, self.p, self.league
        games = f.games[f.games["schedule_state"].isin(["OK", "TBD"]) | f.games["home_score"].notna()].copy()
        games["_t"] = games["start_time_utc"].fillna(pd.Timestamp("1900-01-01"))
        games = games.sort_values(["game_date", "_t", "id"])
        tgs = {(r.game_id, bool(r.is_home)): r for r in f.tgs.itertuples()}
        ggs_by_game: dict[int, list] = {}
        for r in f.ggs.itertuples():
            ggs_by_game.setdefault(r.game_id, []).append(r)
        d_st, d_fin = self._decay(p.st_hl_games), self._decay(p.fin_hl_games)
        rows: list[dict] = []
        cand_rows: list[dict] = []

        def is_final(g) -> bool:
            return g.home_score is not None and not (isinstance(g.home_score, float) and math.isnan(g.home_score))

        def pre(g) -> None:
            h, a = self.team(g.home_team_id), self.team(g.away_team_id)
            hk, ak = self.fr[g.home_team_id], self.fr[g.away_team_id]
            self._rollover(h, g.season)
            self._rollover(a, g.season)
            host = self.loc[g.home_team_id]
            final = is_final(g)
            row = {"game_id": g.id, "season": g.season, "game_type": g.game_type, "game_date": g.game_date,
                   "home_team_id": g.home_team_id, "away_team_id": g.away_team_id, "final": final,
                   "playoff": 1.0 if g.game_type == config.PLAYOFF else 0.0,
                   "lg_g": lg.mu["g"], "lg_xg5": lg.mu["xg5"], "lg_hfa_g": lg.hfa["g"]}
            hf = self._team_feats(h, g.game_date, host, True)
            af = self._team_feats(a, g.game_date, host, False)
            row.update({f"h_{k}": v for k, v in hf.items()})
            row.update({f"a_{k}": v for k, v in af.items()})

            gl = ggs_by_game.get(g.id, [])
            for side, ts, key, tid, feats in (("h", h, hk, g.home_team_id, hf), ("a", a, ak, g.away_team_id, af)):
                cands = self._goalie_cands(ts, key, g.game_date, feats["b2b"] == 1.0)
                starter = next((x for x in gl if x.team_id == tid and x.started), None)
                spid = int(starter.player_id) if starter is not None else None
                for cd in cands:
                    cand_rows.append(dict(cd, game_id=g.id, side=side, season=g.season,
                                          started=(1.0 if cd["pid"] == spid else 0.0) if spid else np.nan))
                if spid is not None:
                    gs = self.goalies.get(spid)
                    row[f"{side}_gk_pid"] = spid
                    row[f"{side}_gk"] = goalie_rating(gs, p)
                    row[f"{side}_gk_recent"] = goalie_recent(gs, p)
                    row[f"{side}_gk_n"] = gs.xga if gs else 0.0
                    row[f"{side}_gk_idle"] = float(min((g.game_date - gs.last_date).days, 30)) if gs and gs.last_date else 30.0
                else:
                    row[f"{side}_gk_pid"] = np.nan
                    row[f"{side}_gk"] = row[f"{side}_gk_recent"] = row[f"{side}_gk_n"] = row[f"{side}_gk_idle"] = np.nan
                row[f"{side}_gk_ncand"] = len(cands)
            rows.append(row)

        def post(g) -> None:
            h, a = self.team(g.home_team_id), self.team(g.away_team_id)
            hk, ak = self.fr[g.home_team_id], self.fr[g.away_team_id]
            host = self.loc[g.home_team_id]
            gl = ggs_by_game.get(g.id, [])
            # --- schedule state (known in advance) ---
            for ts, is_home in ((h, True), (a, False)):
                ts.dates.append(g.game_date)
                ts.loc = host
                ts.road_streak = 0 if is_home else ts.road_streak + 1
            if not is_final(g):
                return

            # --- results ---
            hs, as_ = int(g.home_score), int(g.away_score)
            hr, ar = int(g.home_reg_goals), int(g.away_reg_goals)
            self._elo_update(h, a, hs > as_, abs(hs - as_), g.last_period_type == "SO")
            self._kf_update("g", h, a, float(hr), float(ar))
            sh, sa_ = tgs.get((g.id, True)), tgs.get((g.id, False))
            xg5 = (np.nan, np.nan)
            if sh is not None and sa_ is not None and sh.toi_5v5 and sh.toi_5v5 > 600:
                per60 = 3600.0 / sh.toi_5v5
                xg5 = (sh.sva_xgf_5v5, sa_.sva_xgf_5v5)
                self._kf_update("xg5", h, a, sh.sva_xgf_5v5 * per60, sa_.sva_xgf_5v5 * per60, weight=sh.toi_5v5 / 2900.0)
                toi_all = sh.toi_all if getattr(sh, "toi_all", None) and sh.toi_all > 0 else 3600.0
                per60a = 3600.0 / toi_all
                self._kf_update("xga", h, a, sh.fsva_xgf * per60a, sa_.fsva_xgf * per60a)
                for ts, s in ((h, sh), (a, sa_)):
                    ts.pp_num, ts.pp_den = ts.pp_num * d_st + (s.xgf_pp or 0.0), ts.pp_den * d_st + (s.toi_pp or 0.0)
                    ts.pk_num, ts.pk_den = ts.pk_num * d_st + (s.xga_pk or 0.0), ts.pk_den * d_st + (s.toi_pk or 0.0)
                    ts.pen_drawn = ts.pen_drawn * d_st + (s.pen_drawn or 0.0)
                    ts.pen_taken = ts.pen_taken * d_st + (s.pen_taken or 0.0)
                    ts.pen_den = ts.pen_den * d_st + 1.0
                    ts.fin_num = ts.fin_num * d_fin + ((s.gf or 0.0) - (s.xgf or 0.0))
                    ts.fin_den = ts.fin_den * d_fin + (s.xgf or 0.0)
                lg.update_pp((sh.xgf_pp or 0.0) + (sa_.xgf_pp or 0.0), (sh.toi_pp or 0.0) + (sa_.toi_pp or 0.0))
                lg.pen_pg += lg.a * (((sh.pen_taken or 0.0) + (sa_.pen_taken or 0.0)) / 2 - lg.pen_pg)
            for ts, key, tid, gf, ga, x in ((h, hk, g.home_team_id, hr, ar, xg5), (a, ak, g.away_team_id, ar, hr, xg5[::-1])):
                ts.season_gp += 1
                ts.gp_total += 1
                if not (isinstance(x[0], float) and math.isnan(x[0])):
                    ts.recent.append((x[0], x[1], gf, ga))
                starter = next((q for q in gl if q.team_id == tid and q.started), None)
                if starter is not None:
                    ts.starts.append((g.game_date, int(starter.player_id)))
            for q in gl:
                gs = self.goalies.get(q.player_id)
                if gs is None:
                    gs = self.goalies[int(q.player_id)] = GoalieState()
                gs.team = self.fr[q.team_id]
                gs.last_date = g.game_date
                gs.apps += 1
                if q.xga is not None and not math.isnan(q.xga) and q.xga > 0:
                    dec = math.exp(-LN2 * q.xga / p.gk_hl_xg)
                    gsax = q.xga - q.ga_on_xg
                    gs.xga = gs.xga * dec + q.xga
                    gs.gsax = gs.gsax * dec + gsax
                    gs.recent.append((q.xga, gsax))
            self.rating_log.append(dict(team_id=g.home_team_id, as_of_game_id=g.id, season=g.season, game_date=g.game_date,
                                        elo=h.elo, off_g=h.off["g"], def_g=h.deff["g"], off_xg=h.off["xg5"], def_xg=h.deff["xg5"]))
            self.rating_log.append(dict(team_id=g.away_team_id, as_of_game_id=g.id, season=g.season, game_date=g.game_date,
                                        elo=a.elo, off_g=a.off["g"], def_g=a.deff["g"], off_xg=a.off["xg5"], def_xg=a.deff["xg5"]))

        for _, day in itertools.groupby(games.itertuples(), key=lambda g: g.game_date):
            day = list(day)
            for g in day:
                pre(g)
            for g in day:
                post(g)

        feat = pd.DataFrame(rows)
        # lineup strength: its own day-batched pass over the same games
        self.lineups = LineupBuilder(games, f.sgs, self.fr)
        feat = feat.merge(self.lineups.run(), on="game_id", how="left")
        for side in ("h", "a"):
            for col in LINEUP_COLS:
                feat[f"{side}_{col}"] = feat[f"{side}_{col}"].fillna(0.0)
        self.cands = pd.DataFrame(cand_rows)
        feat = attach_projected_goalies(feat, self.cands, p)
        # targets
        gm = f.games.set_index("id")
        for col in ("home_score", "away_score", "home_reg_goals", "away_reg_goals", "last_period_type", "start_time_utc", "state", "schedule_state"):
            feat[col] = feat["game_id"].map(gm[col])
        return feat


def starter_probabilities(cands: pd.DataFrame, w: dict[str, float]) -> pd.Series:
    """Conditional-logit probability that each candidate goalie starts."""
    if cands.empty:
        return pd.Series(dtype=float)
    u = sum(w[k] * cands[k] for k in ("share10", "prev_rested", "prev_b2b", "share30"))
    u = u - u.groupby([cands["game_id"], cands["side"]]).transform("max")
    e = np.exp(u)
    return e / e.groupby([cands["game_id"], cands["side"]]).transform("sum")


def attach_projected_goalies(feat: pd.DataFrame, cands: pd.DataFrame, p: FeatureParams) -> pd.DataFrame:
    """Expected goalie rating under the projected-starter distribution. Uses
    only pre-game information (recent usage), never the actual starter."""
    for side in ("h", "a"):
        feat[f"{side}_gk_proj"] = p.gk_prior_mean
        feat[f"{side}_gk_proj_top"] = np.nan
    if cands.empty:
        return feat
    cands["prob"] = starter_probabilities(cands, p.starter_w)
    cands["w_rating"] = cands["prob"] * cands["rating"]
    agg = cands.groupby(["game_id", "side"]).agg(proj=("w_rating", "sum"), top=("prob", "max")).reset_index()
    for side in ("h", "a"):
        s = agg[agg["side"] == side].set_index("game_id")
        m = feat["game_id"].map(s["proj"])
        feat[f"{side}_gk_proj"] = m.fillna(p.gk_prior_mean)
        feat[f"{side}_gk_proj_top"] = feat["game_id"].map(s["top"])
    return feat


def build_all(params: FeatureParams | None = None) -> tuple[pd.DataFrame, FeatureBuilder]:
    fb = FeatureBuilder(load_frames(), params)
    feat = fb.run()
    return feat, fb
