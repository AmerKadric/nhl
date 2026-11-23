"""PUCKIQ HTTP API (read-only apart from the stateless scenario lab)."""
from __future__ import annotations

import datetime as dt
import logging
import os
import time
from collections import defaultdict, deque
from zoneinfo import ZoneInfo

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import config, db
from ..ingest import nhl
from ..ingest.http import FetchError
from ..models.forecaster import LAB_DOMAIN, confidence_label
from ..pipeline import publish as pub
from . import queries as q

log = logging.getLogger("puckiq.api")
ET = ZoneInfo("America/New_York")

app = FastAPI(title="PUCKIQ API", version="1.0", docs_url="/docs", redoc_url=None)

origins = [o.strip() for o in os.environ.get("PUCKIQ_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["content-type"], max_age=600)

RATE_LIMIT = int(os.environ.get("PUCKIQ_RATE_LIMIT_PER_MIN", "240"))
LAB_RATE_LIMIT = int(os.environ.get("PUCKIQ_LAB_RATE_LIMIT_PER_MIN", "40"))
_hits: dict[tuple[str, str], deque] = defaultdict(deque)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    """Sliding-window limit per client address (per process)."""
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    bucket = "lab" if request.url.path.endswith("/lab") else "api"
    limit = LAB_RATE_LIMIT if bucket == "lab" else RATE_LIMIT
    now = time.monotonic()
    dq = _hits[(ip, bucket)]
    while dq and dq[0] < now - 60:
        dq.popleft()
    if len(dq) >= limit:
        return JSONResponse({"detail": "Rate limit exceeded. Try again shortly."}, status_code=429, headers={"Retry-After": "30"})
    dq.append(now)
    if len(_hits) > 20000:
        _hits.clear()
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Internal error."}, status_code=500)


def today_et() -> dt.date:
    return dt.datetime.now(ET).date()


def _parse_date(s: str | None, default: dt.date) -> dt.date:
    if not s:
        return default
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise HTTPException(422, "Dates must be YYYY-MM-DD.")


def _live(date: dt.date) -> dict[int, dict]:
    """Live scoreboard, cached briefly. A fetch failure returns nothing
    rather than stale or invented state."""
    def load():
        try:
            return {g["id"]: g for g in nhl.fetch_scoreboard(date)}
        except FetchError as e:
            log.warning("scoreboard unavailable: %s", e)
            return None
    return q.cached(f"live:{date}", 20, load) or {}


def _merge_live(cards: list[dict]) -> list[dict]:
    t = today_et()
    dates = {c["date"] for c in cards if abs((dt.date.fromisoformat(c["date"]) - t).days) <= 1 and c["result"] is None}
    live: dict[int, dict] = {}
    for d in dates:
        live.update(_live(dt.date.fromisoformat(d)))
    for c in cards:
        g = live.get(c["id"])
        if not g:
            continue
        c["state"] = g["state"] or c["state"]
        c["schedule_state"] = g["schedule_state"] or c["schedule_state"]
        if g["state"] in ("LIVE", "CRIT"):
            c["live"] = {k: g[k] for k in ("home_score", "away_score", "home_sog", "away_sog", "period", "period_type", "clock", "in_intermission")}
        elif g["state"] in ("FINAL", "OFF") and g["home_score"] is not None and c["result"] is None:
            c["result"] = {"home_score": g["home_score"], "away_score": g["away_score"], "last_period_type": g["last_period_type"]}
    return cards


# --- endpoints --------------------------------------------------------------
@app.get("/health")
def health():
    try:
        m = q.meta()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "down", "error": type(e).__name__}, status_code=503)
    stale = m["freshness"]["stale"]
    return {"status": "degraded" if stale or not m["model"] else "ok", "stale_sources": stale,
            "model": m["model"] and m["model"]["version"], "results_through": m["freshness"]["results_through"]}


@app.get("/v1/meta")
def meta():
    return q.meta()


@app.get("/v1/games")
def games(date: str | None = None, start: str | None = None, end: str | None = None):
    t = today_et()
    if start or end:
        s, e = _parse_date(start, t), _parse_date(end, t)
    else:
        s = e = _parse_date(date, t)
    if e < s or (e - s).days > 31:
        raise HTTPException(422, "Date range must be 1 to 32 days.")
    cards = _merge_live(q.games_between(s, e))
    return {"start": s.isoformat(), "end": e.isoformat(), "today": t.isoformat(), "games": cards}


@app.get("/v1/live")
def live(date: str | None = None):
    d = _parse_date(date, today_et())
    if abs((d - today_et()).days) > 1:
        raise HTTPException(422, "Live state is only available for yesterday, today and tomorrow.")
    g = _live(d)
    return {"date": d.isoformat(), "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "in_game_win_probability": None,
            "note": "PUCKIQ has no validated in-game model; only observed live state is shown.",
            "games": list(g.values())}


@app.get("/v1/games/{game_id}")
def game(game_id: int):
    g = q.game_row(game_id)
    if g is None:
        raise HTTPException(404, "Game not found.")
    card = _merge_live(q.games_between(g["game_date"], g["game_date"]))
    card = next(c for c in card if c["id"] == game_id)
    detail = q.forecast_detail(game_id)
    tab = q.season_table(g["season"])
    gs = q.goalie_season(g["season"])
    out = {"game": card, "forecast": None, "versions": [], "evaluation": None,
           "comparison": {"home": tab.get(g["home_team_id"]), "away": tab.get(g["away_team_id"])},
           "rating_ranges": q.league_rating_ranges(g["season"]), "goalie_matchup": None, "lab": None}
    if detail:
        out.update(forecast=detail["forecast"], versions=detail["versions"], evaluation=detail["evaluation"])
        gm = {}
        for side in ("home", "away"):
            gm[side] = [{**go, **gs.get(go["pid"], {})} for go in detail["forecast"]["goalies"][side]]
        out["goalie_matchup"] = gm
        snap = q.snapshot_for(game_id)
        if snap and card["result"] is None:
            row = snap["row"]
            am = q.cached("active_model", 60, pub.active_model)
            has_lineup = bool(am and "t_lu_toi" in am[1].glm.cols and snap.get("lineups"))
            out["lab"] = {
                # the ten most-used skaters from each team's last lineup; marking
                # one out re-runs the model with its lineup input reduced
                "skaters": {side: [{"pid": x["pid"], "name": x["name"], "toi": x["toi"], "defence": x["defence"]}
                                   for x in ((snap["lineups"] or {}).get(side) or {}).get("skaters", [])[:10]]
                            for side in ("home", "away")} if has_lineup else None,
                "goalies": {side: [{"pid": go["pid"], "name": go["name"], "prob": go["prob"], "rating": go["rating"]}
                                   for go in snap["goalies"][side]] for side in ("home", "away")},
                "current": {"home_b2b": bool(row.get("h_b2b")), "away_b2b": bool(row.get("a_b2b")),
                            "home_g7": int(row.get("h_g7") or 0), "away_g7": int(row.get("a_g7") or 0),
                            "home_rest_days": row.get("h_rest"), "away_rest_days": row.get("a_rest")},
                "domain": {"g7": list(LAB_DOMAIN["g7"])},
                "unsupported": ([] if has_lineup else [
                    {"control": "player_unavailable", "reason": "This forecast's model has no lineup input, so the effect of a missing skater cannot be computed. Not offered rather than guessed."}]) + [
                    {"control": "rest_days_beyond_back_to_back", "reason": "The model's validated rest inputs are back-to-back status and games in the last 7 days."},
                    {"control": "goalie_injury_or_form", "reason": "Goalies can be swapped, but a goalie playing hurt or on a hot streak beyond his rating is not something the model measures."},
                ],
            }
    return out


class LabRequest(BaseModel):
    home_goalie_id: int | None = Field(None, ge=0)
    away_goalie_id: int | None = Field(None, ge=0)
    home_b2b: bool | None = None
    away_b2b: bool | None = None
    home_g7: int | None = Field(None, ge=LAB_DOMAIN["g7"][0], le=LAB_DOMAIN["g7"][1])
    away_g7: int | None = Field(None, ge=LAB_DOMAIN["g7"][0], le=LAB_DOMAIN["g7"][1])
    home_out: list[int] = Field(default_factory=list, max_length=3)   # skaters to treat as not dressing
    away_out: list[int] = Field(default_factory=list, max_length=3)


def _mini(res: dict) -> dict:
    sim = res["sim"]
    ml = sim.most_likely
    p_fav = max(sim.p_home_win, 1 - sim.p_home_win)
    return {"p_home_win": sim.p_home_win, "p_away_win": 1 - sim.p_home_win, "p_home_reg": sim.p_home_reg, "p_tie_reg": sim.p_tie_reg,
            "p_away_reg": sim.p_away_reg, "p_overtime": sim.p_overtime, "p_shootout": sim.p_shootout,
            "exp_home_goals": sim.exp_home_goals, "exp_away_goals": sim.exp_away_goals, "lam_home": sim.lam_home, "lam_away": sim.lam_away,
            "ml_home_goals": ml[0], "ml_away_goals": ml[1], "ml_score_prob": ml[2], "top_scores": sim.top_scores(5),
            "expected_total": sim.exp_home_goals + sim.exp_away_goals, "extras": sim.extras,
            "confidence_label": confidence_label(p_fav), "mc_standard_error": sim.mc_se_win, "n_sims": sim.n_sims,
            "matrix": np.round(sim.final, 5).tolist(), "drivers": res["drivers"],
            "goalies": {s: [{"pid": g["pid"], "name": g["name"], "prob": g["prob"]} for g in res["goalies"][s]] for s in ("home", "away")}}


@app.post("/v1/games/{game_id}/lab")
def lab(game_id: int, body: LabRequest):
    """Recalculate a forecast under hypothetical inputs using the production
    model. Stateless: nothing is stored or published."""
    snap = q.snapshot_for(game_id)
    am = q.cached("active_model", 60, pub.active_model)
    if snap is None or am is None:
        raise HTTPException(404, "No forecast inputs are stored for this game.")
    _, fc = am
    row = {k: (np.nan if v is None else v) for k, v in snap["row"].items() if k not in ("game_date",)}
    goalies = snap["goalies"]
    warnings: list[str] = []

    def pick(side: str, pid: int | None) -> list[dict]:
        if pid is None:
            return goalies[side]
        m = [g for g in goalies[side] if g["pid"] == pid]
        if not m:
            raise HTTPException(422, f"Goalie {pid} is not among this team's goalies known to the model.")
        if m[0].get("xga_n", 0) < 30:
            warnings.append(f"{m[0]['name']} has a small sample; his rating is mostly the league prior.")
        return [dict(m[0], prob=1.0)]

    scen_goalies = {"home": pick("home", body.home_goalie_id), "away": pick("away", body.away_goalie_id)}
    overrides: dict = {}
    for side, pre in (("home", "h"), ("away", "a")):
        b2b = getattr(body, f"{side}_b2b")
        g7 = getattr(body, f"{side}_g7")
        if b2b is not None:
            overrides[f"{pre}_b2b"] = 1.0 if b2b else 0.0
        if g7 is not None:
            overrides[f"{pre}_g7"] = float(g7)
        eff_b2b = overrides.get(f"{pre}_b2b", row.get(f"{pre}_b2b"))
        eff_g7 = overrides.get(f"{pre}_g7", row.get(f"{pre}_g7"))
        if eff_b2b == 1.0 and eff_g7 is not None and eff_g7 < 1:
            raise HTTPException(422, "A back-to-back implies at least one game in the last 7 days.")
    applied_out: dict[str, list[str]] = {"home": [], "away": []}
    for side, pre in (("home", "h"), ("away", "a")):
        out_ids = getattr(body, f"{side}_out")
        if not out_ids:
            continue
        lu = (snap.get("lineups") or {}).get(side)
        if "t_lu_toi" not in fc.glm.cols or not lu:
            raise HTTPException(422, "This forecast's model has no lineup input, so a missing skater cannot be computed.")
        by_id = {x["pid"]: x for x in lu["skaters"]}
        delta = 0.0
        for pid in dict.fromkeys(out_ids):
            sk = by_id.get(pid)
            if sk is None:
                raise HTTPException(422, f"Skater {pid} was not in this team's most recent lineup.")
            # he is replaced by a skater the model knows nothing about, rated
            # at the league average for the position
            delta -= max(0.0, sk["toi"] - lu["replacement"]["D" if sk["defence"] else "F"])
            applied_out[side].append(sk["name"])
        value = float(row.get(f"{pre}_lu_toi") or 0.0) + delta
        lo = (fc.domain.get("lu_toi") or [-1e9])[0]
        if value < lo:
            raise HTTPException(422, "That many absences is outside the range the model was validated on.")
        overrides[f"{pre}_lu_toi"] = value
    seed = (game_id * 7919) & 0x7FFFFFFF
    base = fc.forecast(row, goalies, seed=seed)
    scen = fc.forecast(row, scen_goalies, seed=seed, overrides=overrides)
    return {"baseline": _mini(base), "scenario": _mini(scen), "warnings": warnings,
            "applied": {"goalies": {s: scen_goalies[s][0]["name"] if len(scen_goalies[s]) == 1 else None for s in ("home", "away")},
                        "overrides": overrides, "skaters_out": applied_out},
            "note": "Same model and simulator as the published forecast, with common random numbers so differences reflect the inputs."}


@app.get("/v1/accuracy")
def accuracy():
    return {"forward": q.forward_record(), "model_versions": q.model_versions(), "backtest": q.backtest_public()}


@app.get("/v1/accuracy/history")
def accuracy_history(source: str = Query("forward", pattern="^(forward|backtest)$"), season: int | None = Query(None, ge=2000, le=2100),
                     team: str | None = Query(None, min_length=2, max_length=4), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    tid = None
    if team:
        t = q.team_by_abbrev(team)
        if not t:
            raise HTTPException(404, "Team not found.")
        tid = t["id"]
    return q.history(source, season, tid, limit, offset)


@app.get("/v1/teams")
def teams():
    season = q.meta()["season"]
    tab = q.season_table(season)
    out = [{**t, "summary": tab.get(t["id"])} for t in q.teams_map().values() if t["active"]]
    return {"season": season, "rating_ranges": q.league_rating_ranges(season), "teams": sorted(out, key=lambda x: x["name"])}


@app.get("/v1/teams/{abbrev}")
def team(abbrev: str):
    t = q.team_by_abbrev(abbrev)
    if not t:
        raise HTTPException(404, "Team not found.")
    season = q.meta()["season"]
    today = today_et()
    upcoming = [c for c in q.games_between(today, today + dt.timedelta(days=21))
                if t["id"] in (c["home"]["id"], c["away"]["id"]) and c["result"] is None][:8]
    recent = [c for c in q.games_between(today - dt.timedelta(days=30), today)
              if t["id"] in (c["home"]["id"], c["away"]["id"]) and c["result"] is not None][-10:][::-1]
    with db.begin() as c:
        import sqlalchemy as sa
        remaining = c.execute(sa.select(sa.func.count()).select_from(db.games).where(
            db.games.c.season == season, db.games.c.game_type == config.REGULAR, db.games.c.home_score.is_(None),
            db.games.c.schedule_state == "OK", sa.or_(db.games.c.home_team_id == t["id"], db.games.c.away_team_id == t["id"]))).scalar()
    return {"team": t, "season": season, "summary": q.season_table(season).get(t["id"]),
            "rating_ranges": q.league_rating_ranges(season), "rating_history": q.rating_history(t["id"], season),
            "goalies": q.team_goalies(t["id"], season), "upcoming": upcoming, "recent": recent, "games_remaining": int(remaining or 0),
            "forward_history": q.history("forward", season, t["id"], 20, 0)["rows"],
            "backtest_history": q.history("backtest", season, t["id"], 20, 0)["rows"]}
