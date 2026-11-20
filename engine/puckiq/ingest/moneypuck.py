"""MoneyPuck ingestion (https://moneypuck.com/data.htm).

Licence: free for non-commercial use with clear credit to MoneyPuck.com.
Only the files MoneyPuck lists for download are fetched; nothing is scraped."""
from __future__ import annotations

import logging
import zipfile

import numpy as np
import pandas as pd
import sqlalchemy as sa

from .. import config, db
from .http import client

log = logging.getLogger("puckiq.ingest.moneypuck")

SITUATIONS = ["all", "5on5", "5on4", "4on5"]
TEAM_COLS = [
    "season", "gameId", "home_or_away", "position", "situation", "iceTime",
    "xGoalsFor", "xGoalsAgainst", "flurryScoreVenueAdjustedxGoalsFor", "flurryScoreVenueAdjustedxGoalsAgainst",
    "scoreVenueAdjustedxGoalsFor", "scoreVenueAdjustedxGoalsAgainst",
    "goalsFor", "goalsAgainst", "shotsOnGoalFor", "shotsOnGoalAgainst",
    "shotAttemptsFor", "shotAttemptsAgainst", "highDangerxGoalsFor", "highDangerxGoalsAgainst",
    "highDangerShotsFor", "highDangerShotsAgainst", "penaltiesFor", "penaltiesAgainst",
]


def parse_team_games(path, first_season: int) -> pd.DataFrame:
    """Reshape MoneyPuck's long team file (one row per game x situation) into
    one wide row per team-game."""
    parts = []
    for chunk in pd.read_csv(path, usecols=TEAM_COLS, chunksize=200_000):
        chunk = chunk[(chunk["season"] >= first_season) & (chunk["position"] == "Team Level")
                      & chunk["situation"].isin(SITUATIONS)]
        if len(chunk):
            parts.append(chunk)
    df = pd.concat(parts, ignore_index=True)
    df["is_home"] = df["home_or_away"].str.upper().eq("HOME")
    key = ["gameId", "is_home"]
    s = {k: g.set_index(key) for k, g in df.groupby("situation")}
    a, ev, pp, pk = s["all"], s["5on5"], s["5on4"], s["4on5"]
    out = pd.DataFrame(index=a.index)
    out["toi_all"] = a["iceTime"]
    out["xgf"], out["xga"] = a["xGoalsFor"], a["xGoalsAgainst"]
    out["fsva_xgf"] = a["flurryScoreVenueAdjustedxGoalsFor"]
    out["fsva_xga"] = a["flurryScoreVenueAdjustedxGoalsAgainst"]
    out["gf"], out["ga"] = a["goalsFor"], a["goalsAgainst"]
    out["sog_f"], out["sog_a"] = a["shotsOnGoalFor"], a["shotsOnGoalAgainst"]
    out["hd_xgf"], out["hd_xga"] = a["highDangerxGoalsFor"], a["highDangerxGoalsAgainst"]
    out["hd_shots_f"], out["hd_shots_a"] = a["highDangerShotsFor"], a["highDangerShotsAgainst"]
    # MoneyPuck: penaltiesFor = penalties taken by this team.
    out["pen_taken"], out["pen_drawn"] = a["penaltiesFor"], a["penaltiesAgainst"]
    out["toi_5v5"] = ev["iceTime"].reindex(out.index)
    out["xgf_5v5"], out["xga_5v5"] = ev["xGoalsFor"].reindex(out.index), ev["xGoalsAgainst"].reindex(out.index)
    out["sva_xgf_5v5"] = ev["scoreVenueAdjustedxGoalsFor"].reindex(out.index)
    out["sva_xga_5v5"] = ev["scoreVenueAdjustedxGoalsAgainst"].reindex(out.index)
    out["gf_5v5"], out["ga_5v5"] = ev["goalsFor"].reindex(out.index), ev["goalsAgainst"].reindex(out.index)
    out["cf_5v5"], out["ca_5v5"] = ev["shotAttemptsFor"].reindex(out.index), ev["shotAttemptsAgainst"].reindex(out.index)
    out["toi_pp"] = pp["iceTime"].reindex(out.index)
    out["xgf_pp"], out["gf_pp"] = pp["xGoalsFor"].reindex(out.index), pp["goalsFor"].reindex(out.index)
    out["toi_pk"] = pk["iceTime"].reindex(out.index)
    out["xga_pk"], out["ga_pk"] = pk["xGoalsAgainst"].reindex(out.index), pk["goalsAgainst"].reindex(out.index)
    return out.reset_index().rename(columns={"gameId": "game_id"})


def ingest_team_games(first_season: int | None = None) -> int:
    first_season = first_season or config.FIRST_SEASON
    with db.IngestionRun("moneypuck_teams", "team_games") as run:
        path, changed = client().download(config.MONEYPUCK_TEAMS_URL, config.RAW_DIR / "moneypuck" / "all_teams.csv")
        run.detail = {"changed": changed}
        wide = parse_team_games(path, first_season)
        with db.begin() as c:
            g = pd.read_sql(sa.select(db.games.c.id, db.games.c.home_team_id, db.games.c.away_team_id), c)
        m = wide.merge(g, left_on="game_id", right_on="id", how="inner")
        run.detail["unmatched_rows"] = int(len(wide) - len(m))
        m["team_id"] = np.where(m["is_home"], m["home_team_id"], m["away_team_id"])
        cols = [c.name for c in db.team_game_stats.columns]
        rows = m[cols].to_dict("records")
        with db.begin() as c:
            run.rows = db.upsert(c, db.team_game_stats, rows)
        run.detail["max_game_id"] = int(m["game_id"].max())
        return run.rows


SHOT_COLS = ["season", "isPlayoffGame", "game_id", "team", "period", "goal", "xGoal",
             "goalieIdForShot", "shotOnEmptyNet"]


def parse_shots(path) -> pd.DataFrame:
    """Aggregate shot-level xG to one row per (game, goalie): the unblocked
    attempts each goalie faced, their expected goals and goals allowed."""
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        with z.open(name) as f:
            df = pd.read_csv(f, usecols=SHOT_COLS)
    df = df[(df["shotOnEmptyNet"] == 0) & df["goalieIdForShot"].notna() & (df["goalieIdForShot"] > 0)]
    # Shootout attempts are not part of regulation/OT goaltending workload.
    df = df[~((df["isPlayoffGame"] == 0) & (df["period"] >= 5))]
    gid = df["game_id"].astype("int64")
    # Files carry either the full NHL id (2015020001) or the in-season number (20001).
    df["game_id"] = np.where(gid < 1_000_000, df["season"].astype("int64") * 1_000_000 + gid, gid)
    df["def_home"] = df["team"].str.upper().eq("AWAY")  # shooter away => home goalie
    agg = (df.groupby(["game_id", "goalieIdForShot", "def_home"], as_index=False)
           .agg(xga=("xGoal", "sum"), ga_on_xg=("goal", "sum"), attempts=("goal", "size")))
    agg["player_id"] = agg["goalieIdForShot"].astype("int64")
    return agg.drop(columns="goalieIdForShot")


def ingest_shots(seasons: list[int]) -> int:
    """Attach MoneyPuck expected goals against to the NHL goalie game lines."""
    total = 0
    with db.IngestionRun("moneypuck_shots", "goalie_xg") as run:
        changed_any = {}
        for season in seasons:
            path, changed = client().download(config.MONEYPUCK_SHOTS_URL.format(year=season),
                                              config.RAW_DIR / "moneypuck" / f"shots_{season}.zip")
            changed_any[season] = changed
            agg = parse_shots(path)
            with db.begin() as c:
                existing = pd.read_sql(
                    sa.select(db.goalie_game_stats.c.game_id, db.goalie_game_stats.c.player_id)
                    .join(db.games, db.games.c.id == db.goalie_game_stats.c.game_id)
                    .where(db.games.c.season == season), c)
            m = agg.merge(existing, on=["game_id", "player_id"], how="inner")
            n = 0
            with db.begin() as c:
                stmt = (db.goalie_game_stats.update()
                        .where(db.goalie_game_stats.c.game_id == sa.bindparam("b_game"),
                               db.goalie_game_stats.c.player_id == sa.bindparam("b_player"))
                        .values(xga=sa.bindparam("xga"), ga_on_xg=sa.bindparam("ga_on_xg"),
                                attempts=sa.bindparam("attempts")))
                params = [dict(b_game=int(r.game_id), b_player=int(r.player_id), xga=float(r.xga),
                               ga_on_xg=float(r.ga_on_xg), attempts=int(r.attempts)) for r in m.itertuples()]
                if params:
                    c.execute(stmt, params)
                    n = len(params)
            total += n
            log.info("shots %s: %d goalie-games matched of %d (changed=%s)", season, n, len(agg), changed)
        run.rows = total
        run.detail = {"changed": changed_any}
    return total
