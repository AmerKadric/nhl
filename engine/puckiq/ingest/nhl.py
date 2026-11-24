"""NHL public API ingestion: teams, games/results, schedule, goalie logs,
live scores and current rosters."""
from __future__ import annotations

import datetime as dt
import json
import logging
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from .. import config, db
from .http import FetchError, client
from .reference import DATA_SOURCES, FRANCHISE_CONTINUITY, TEAM_REF

log = logging.getLogger("puckiq.ingest.nhl")
ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

FINAL_STATES = {"FINAL", "OFF"}
LIVE_STATES = {"LIVE", "CRIT"}
SCHEDULE_STATE_BY_ID = {1: "OK", 2: "TBD", 3: "PPD", 4: "SUSP", 5: "CNCL", 6: "CNCL"}


def seed_reference() -> None:
    with db.begin() as c:
        db.upsert(c, db.data_sources, DATA_SOURCES)


def ingest_teams() -> int:
    """Teams from the stats API, enriched with static reference data and the
    current standings (conference / division / active flag)."""
    with db.IngestionRun("nhl_web", "teams") as run:
        raw = client().get_json(f"{config.NHL_STATS_BASE}/team", params={"limit": -1})["data"]
        active: dict[str, dict] = {}
        try:
            st = client().get_json(f"{config.NHL_WEB_BASE}/standings/now")["standings"]
            for s in st:
                active[s["teamAbbrev"]["default"]] = s
        except FetchError as e:  # standings are an enrichment, not required
            log.warning("standings unavailable: %s", e)
        rows = []
        for t in raw:
            abbrev = t["triCode"]
            ref = TEAM_REF.get(abbrev)
            if ref is None or not t.get("franchiseId"):
                continue  # defunct clubs outside our data window
            s = active.get(abbrev)
            # Two ids share UTA; only the one in the standings is active.
            is_active = bool(s) and s["teamName"]["default"] == t["fullName"]
            fk = FRANCHISE_CONTINUITY.get(t["franchiseId"], t["franchiseId"])
            rows.append(dict(
                id=t["id"], abbrev=abbrev, name=t["fullName"], franchise_key=fk,
                conference=s["conferenceName"] if is_active else None,
                division=s["divisionName"] if is_active else None,
                color=ref["color"], timezone=ref["tz"], lat=ref["lat"], lon=ref["lon"], active=is_active,
            ))
        with db.begin() as c:
            run.rows = db.upsert(c, db.teams, rows)
        return run.rows


def _known_team_ids(conn) -> set[int]:
    return {r[0] for r in conn.execute(sa.select(db.teams.c.id))}


def _period_type(game_type: int, period: int | None) -> str | None:
    if period is None:
        return None
    if period <= 3:
        return "REG"
    if game_type == config.REGULAR:
        return "OT" if period == 4 else "SO"
    return "OT"


def regulation_goals(home: int, away: int, last_period_type: str) -> tuple[int, int]:
    """Goals scored in regulation. An OT or shootout win adds exactly one
    goal to the winner, so regulation ended level at the loser's total."""
    if last_period_type == "REG":
        return home, away
    m = min(home, away)
    return m, m


def ingest_games(seasons: list[int]) -> int:
    """Bulk game results for whole seasons from the stats API."""
    total = 0
    with db.IngestionRun("nhl_games", "games") as run:
        with db.begin() as c:
            known = _known_team_ids(c)
        today = dt.datetime.now(ET).date()
        for season in seasons:
            data = client().get_json(f"{config.NHL_STATS_BASE}/game", params={
                "limit": -1, "cayenneExp": f"season={config.season_id(season)}"})["data"]
            finals, pending = [], []
            for g in data:
                if g["gameType"] not in (config.REGULAR, config.PLAYOFF):
                    continue
                if g["homeTeamId"] not in known or g["visitingTeamId"] not in known:
                    continue
                gdate = dt.date.fromisoformat(g["gameDate"])
                start = None
                if g.get("easternStartTime"):
                    start = (dt.datetime.fromisoformat(g["easternStartTime"]).replace(tzinfo=ET)
                             .astimezone(UTC).replace(tzinfo=None))
                base = dict(id=g["id"], season=season, game_type=g["gameType"], game_date=gdate,
                            start_time_utc=start, home_team_id=g["homeTeamId"],
                            away_team_id=g["visitingTeamId"], updated_at=db.utcnow())
                if g["gameStateId"] == 7 and g["homeScore"] is not None and g["homeScore"] != g["visitingScore"]:
                    lpt = _period_type(g["gameType"], g.get("period"))
                    hr, ar = regulation_goals(g["homeScore"], g["visitingScore"], lpt)
                    finals.append(dict(base, state="OFF", schedule_state="OK", home_score=g["homeScore"],
                                       away_score=g["visitingScore"], last_period_type=lpt,
                                       home_reg_goals=hr, away_reg_goals=ar))
                elif gdate >= today - dt.timedelta(days=2):
                    sched = SCHEDULE_STATE_BY_ID.get(g.get("gameScheduleStateId"), "OK")
                    pending.append(dict(base, state="FUT", schedule_state=sched))
            with db.begin() as c:
                total += db.upsert(c, db.games, finals)
                # never clobber live state written by the schedule sync
                db.upsert(c, db.games, pending, update=False)
            log.info("season %s: %d finals, %d upcoming", season, len(finals), len(pending))
        run.rows = total
        run.detail = {"seasons": seasons}
    return total


def _game_row_from_web(g: dict, known: set[int]) -> dict | None:
    if g.get("gameType") not in (config.REGULAR, config.PLAYOFF):
        return None
    h, a = g["homeTeam"], g["awayTeam"]
    if h["id"] not in known or a["id"] not in known:
        return None
    start = dt.datetime.fromisoformat(g["startTimeUTC"].replace("Z", "+00:00"))
    season = int(str(g["season"])[:4])
    row = dict(
        id=g["id"], season=season, game_type=g["gameType"],
        game_date=start.astimezone(ET).date(), start_time_utc=start.replace(tzinfo=None),
        home_team_id=h["id"], away_team_id=a["id"], venue=(g.get("venue") or {}).get("default"),
        state=g.get("gameState", "FUT"), schedule_state=g.get("gameScheduleState", "OK"),
        updated_at=db.utcnow(),
    )
    if row["state"] in FINAL_STATES and "score" in h and "score" in a and h["score"] != a["score"]:
        lpt = (g.get("gameOutcome") or {}).get("lastPeriodType") or \
            (g.get("periodDescriptor") or {}).get("periodType")
        if lpt in ("REG", "OT", "SO"):
            hr, ar = regulation_goals(h["score"], a["score"], lpt)
            row.update(home_score=h["score"], away_score=a["score"], last_period_type=lpt,
                       home_reg_goals=hr, away_reg_goals=ar, state="OFF")
    return row


def sync_schedule(start: dt.date, weeks: int = 2) -> int:
    """Upcoming / recent schedule with states, start times and final scores."""
    n = 0
    with db.IngestionRun("nhl_web", "schedule") as run:
        with db.begin() as c:
            known = _known_team_ids(c)
        d = start
        for _ in range(weeks):
            data = client().get_json(f"{config.NHL_WEB_BASE}/schedule/{d.isoformat()}")
            rows = []
            for day in data.get("gameWeek", []):
                for g in day.get("games", []):
                    row = _game_row_from_web(g, known)
                    if row:
                        rows.append(row)
            with db.begin() as c:
                n += db.upsert(c, db.games, rows)
            nxt = data.get("nextStartDate")
            d = dt.date.fromisoformat(nxt) if nxt else d + dt.timedelta(days=7)
        run.rows = n
    return n


def fetch_scoreboard(date: dt.date) -> list[dict]:
    """Live scoreboard for a date (not persisted; used for live state)."""
    data = client().get_json(f"{config.NHL_WEB_BASE}/score/{date.isoformat()}")
    out = []
    for g in data.get("games", []):
        if g.get("gameType") not in (config.REGULAR, config.PLAYOFF):
            continue
        pd_ = g.get("periodDescriptor") or {}
        clock = g.get("clock") or {}
        out.append(dict(
            id=g["id"], state=g.get("gameState"), schedule_state=g.get("gameScheduleState", "OK"),
            home_score=g["homeTeam"].get("score"), away_score=g["awayTeam"].get("score"),
            home_sog=g["homeTeam"].get("sog"), away_sog=g["awayTeam"].get("sog"),
            period=pd_.get("number"), period_type=pd_.get("periodType"),
            clock=clock.get("timeRemaining"), in_intermission=clock.get("inIntermission"),
            last_period_type=(g.get("gameOutcome") or {}).get("lastPeriodType"),
        ))
    return out


def finalize_from_scoreboard(date: dt.date) -> int:
    """Persist state and final scores for a date from the live scoreboard.
    Returns the number of games that became final in this call."""
    rows = []
    for g in fetch_scoreboard(date):
        row = dict(id=g["id"], state=g["state"], schedule_state=g["schedule_state"], updated_at=db.utcnow())
        if g["state"] in FINAL_STATES and g["home_score"] is not None and g["last_period_type"] in ("REG", "OT", "SO") \
                and g["home_score"] != g["away_score"]:
            hr, ar = regulation_goals(g["home_score"], g["away_score"], g["last_period_type"])
            row.update(state="OFF", home_score=g["home_score"], away_score=g["away_score"],
                       last_period_type=g["last_period_type"], home_reg_goals=hr, away_reg_goals=ar)
        rows.append(row)
    n = 0
    with db.begin() as c:
        ids = [r["id"] for r in rows]
        was_final = {r.id for r in c.execute(sa.select(db.games.c.id).where(db.games.c.id.in_(ids), db.games.c.state == "OFF"))} if ids else set()
        for row in rows:
            gid = row.pop("id")
            c.execute(db.games.update().where(db.games.c.id == gid).values(**row))
            if row.get("state") == "OFF" and gid not in was_final:
                n += 1
    return n


def ingest_goalie_logs(seasons: list[int], since: dt.date | None = None) -> int:
    """Per-game goalie lines (starts, shots, goals, TOI) from the stats API."""
    total = 0
    with db.IngestionRun("nhl_goalies", "goalie_logs") as run:
        with db.begin() as c:
            abbrev_by_season: dict[int, dict[str, int]] = {}
            team_rows = list(c.execute(sa.select(db.teams.c.id, db.teams.c.abbrev, db.teams.c.active)))
            game_teams = {r.id: (r.home_team_id, r.away_team_id) for r in c.execute(
                sa.select(db.games.c.id, db.games.c.home_team_id, db.games.c.away_team_id)
                .where(db.games.c.season.in_(seasons)))}
        ids_by_abbrev: dict[str, list[int]] = {}
        for r in team_rows:
            ids_by_abbrev.setdefault(r.abbrev, []).append(r.id)
        sort = json.dumps([{"property": "gameId", "direction": "ASC"}, {"property": "playerId", "direction": "ASC"}])
        for season in seasons:
            for gtype in (config.REGULAR, config.PLAYOFF):
                expr = f"seasonId={config.season_id(season)} and gameTypeId={gtype}"
                if since:
                    expr += f' and gameDate>="{since.isoformat()}"'
                start, rows, people = 0, [], {}
                while True:
                    page = client().get_json(f"{config.NHL_STATS_BASE}/goalie/summary", params={
                        "isGame": "true", "limit": 100, "start": start, "sort": sort, "cayenneExp": expr})
                    data = page.get("data", [])
                    for g in data:
                        gt = game_teams.get(g["gameId"])
                        if gt is None:
                            continue
                        cands = [i for i in ids_by_abbrev.get(g["teamAbbrev"], []) if i in gt]
                        team_id = cands[0] if cands else (gt[0] if g.get("homeRoad") == "H" else gt[1])
                        people[g["playerId"]] = dict(id=g["playerId"], full_name=g["goalieFullName"], position="G")
                        rows.append(dict(game_id=g["gameId"], player_id=g["playerId"], team_id=team_id,
                                         started=bool(g.get("gamesStarted")), toi=g.get("timeOnIce"),
                                         shots_against=g.get("shotsAgainst"), goals_against=g.get("goalsAgainst")))
                    start += len(data)
                    if not data or start >= page.get("total", 0):
                        break
                if rows:
                    with db.begin() as c:
                        db.upsert(c, db.players, list(people.values()))
                        total += db.upsert(c, db.goalie_game_stats, rows)
                log.info("goalie logs %s type %s: %d rows", season, gtype, len(rows))
        run.rows = total
    return total


def ingest_skater_logs(seasons: list[int], since: dt.date | None = None, window_days: int = 7) -> int:
    """Per-game skater lines (who dressed, ice time, box-score stats).

    The stats API returns at most 10,000 rows per query and 100 per page, so
    each season is read in short date windows."""
    total = 0
    with db.IngestionRun("nhl_skaters", "skater_logs") as run:
        with db.begin() as c:
            team_rows = list(c.execute(sa.select(db.teams.c.id, db.teams.c.abbrev)))
            g = c.execute(sa.select(db.games.c.id, db.games.c.season, db.games.c.game_date, db.games.c.home_team_id,
                                    db.games.c.away_team_id)
                          .where(db.games.c.season.in_(seasons), db.games.c.home_score.isnot(None))).fetchall()
        ids_by_abbrev: dict[str, list[int]] = {}
        for r in team_rows:
            ids_by_abbrev.setdefault(r.abbrev, []).append(r.id)
        game_teams = {r.id: (r.home_team_id, r.away_team_id) for r in g}
        sort = json.dumps([{"property": "gameId", "direction": "ASC"}, {"property": "playerId", "direction": "ASC"}])
        for season in seasons:
            dates = [r.game_date for r in g if r.season == season]
            if not dates:
                continue
            start = max(min(dates), since) if since else min(dates)
            end = max(dates)
            n_season = 0
            d = start
            while d <= end:
                hi = min(d + dt.timedelta(days=window_days - 1), end)
                expr = f'gameDate>="{d.isoformat()}" and gameDate<="{hi.isoformat()}" and gameTypeId in (2,3)'
                offset, rows, people = 0, [], {}
                while True:
                    page = client().get_json(f"{config.NHL_STATS_BASE}/skater/summary", params={
                        "isGame": "true", "limit": 100, "start": offset, "sort": sort, "cayenneExp": expr})
                    data = page.get("data", [])
                    if page.get("total", 0) >= 10000:
                        raise FetchError(f"skater window {d}..{hi} hit the 10,000-row cap; shorten window_days")
                    for x in data:
                        gt = game_teams.get(x["gameId"])
                        if gt is None:
                            continue
                        cands = [i for i in ids_by_abbrev.get(x["teamAbbrev"], []) if i in gt]
                        team_id = cands[0] if cands else (gt[0] if x.get("homeRoad") == "H" else gt[1])
                        people[x["playerId"]] = dict(id=x["playerId"], full_name=x["skaterFullName"], position=x["positionCode"])
                        rows.append(dict(game_id=x["gameId"], player_id=x["playerId"], team_id=team_id,
                                         position=x["positionCode"], toi=x.get("timeOnIcePerGame"), goals=x.get("goals"),
                                         assists=x.get("assists"), shots=x.get("shots"), plus_minus=x.get("plusMinus"),
                                         pim=x.get("penaltyMinutes"), pp_points=x.get("ppPoints")))
                    offset += len(data)
                    if not data or offset >= page.get("total", 0):
                        break
                if rows:
                    with db.begin() as c:
                        db.upsert(c, db.players, list(people.values()))
                        n_season += db.upsert(c, db.skater_game_stats, rows)
                d = hi + dt.timedelta(days=1)
            total += n_season
            log.info("skater logs %s: %d rows", season, n_season)
        run.rows = total
    return total


def fetch_rosters_goalies(team_abbrev: str) -> list[dict]:
    d = client().get_json(f"{config.NHL_WEB_BASE}/roster/{team_abbrev}/current")
    return [dict(player_id=g["id"], name=f'{g["firstName"]["default"]} {g["lastName"]["default"]}')
            for g in d.get("goalies", [])]
