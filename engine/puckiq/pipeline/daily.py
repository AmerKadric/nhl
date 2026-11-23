"""Pipeline orchestration.

`run_daily`   full refresh: results, advanced stats, features, (re)training,
              forecast publishing, settlement.
`run_refresh` light, frequent: schedule/state sync, goalie confirmations,
              forecast updates, settlement.

Every step is idempotent, so a failed or repeated run is safe to retry. A
failure in a critical feed aborts publishing; a stale non-critical feed is
recorded with the forecasts so the site can show it."""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from .. import config, db
from ..backtest import walkforward
from .. import market
from ..features.build import FeatureBuilder, build_all
from ..ingest import moneypuck, nhl
from ..models.forecaster import Forecaster
from ..sim import playoffs as playoff_sim
from ..sim import season as season_sim
from . import publish as pub
from .settle import settle

log = logging.getLogger("puckiq.pipeline")
ET = ZoneInfo("America/New_York")


class PipelineAbort(RuntimeError):
    pass


def current_season(today: dt.date | None = None) -> int:
    today = today or dt.datetime.now(ET).date()
    return today.year if today.month >= 9 else today.year - 1


def freshness() -> dict:
    """Age of each data feed and how far the data actually reaches."""
    now = db.utcnow()
    out: dict = {"checked_at": now.isoformat(timespec="seconds"), "sources": {}, "stale": []}
    with db.begin() as c:
        for key, limit in config.STALE_AFTER_HOURS.items():
            last = db.last_success(c, key)
            age = (now - last).total_seconds() / 3600 if last else None
            stale = age is None or age > limit
            out["sources"][key] = {"last_success": last.isoformat(timespec="seconds") if last else None,
                                   "age_hours": round(age, 2) if age is not None else None, "stale_after_hours": limit, "stale": stale}
            if stale:
                out["stale"].append(key)
        out["results_through"] = str(c.execute(sa.select(sa.func.max(db.games.c.game_date)).where(db.games.c.home_score.isnot(None))).scalar())
        out["xg_through"] = str(c.execute(sa.select(sa.func.max(db.games.c.game_date)).select_from(
            db.team_game_stats.join(db.games, db.games.c.id == db.team_game_stats.c.game_id))).scalar())
        missing = c.execute(sa.select(sa.func.count()).select_from(
            db.games.outerjoin(db.team_game_stats, sa.and_(db.team_game_stats.c.game_id == db.games.c.id, db.team_game_stats.c.is_home == sa.true())))
            .where(db.games.c.home_score.isnot(None), db.games.c.season == current_season(), db.team_game_stats.c.game_id.is_(None))).scalar()
        out["finals_missing_xg"] = int(missing or 0)
    return out


def store_ratings(fb: FeatureBuilder, season: int) -> int:
    with db.begin() as c:
        empty = c.execute(sa.select(sa.func.count()).select_from(db.team_ratings)).scalar() == 0
        rows = fb.rating_log if empty else [r for r in fb.rating_log if r["season"] >= season]
        return db.upsert(c, db.team_ratings, rows)


def backtest_report() -> dict | None:
    """The current backtest report: the committed file, else the database."""
    p = config.ARTIFACT_DIR / "backtest" / "summary.json"
    if p.exists():
        return json.loads(p.read_text())
    return db.get_document("backtest_summary")


def train(feat=None, backtest_summary: dict | None = None) -> Forecaster:
    """Train and activate the production model with the feature groups the
    backtest selected."""
    if feat is None:
        feat, _ = build_all()
    report = backtest_report()
    fc = Forecaster.train(feat, (report or {}).get("chosen_groups"))
    path = fc.save()
    if backtest_summary is None and report:
        chosen = report["chosen_model"]
        backtest_summary = {"chosen_model": chosen, "generated_at": report["generated_at"],
                            "overall": report["models"][chosen]["overall"],
                            "confirmation_window": report["models"][chosen]["confirmation_window"]}
    pub.register_model(fc, path, backtest_summary)
    log.info("trained %s on %d games", fc.version, fc.n_train_games)
    return fc


def store_site_documents(season: int) -> int:
    """Precompute the summaries the site reads on every page view (team
    season tables, goalie season lines, the public part of the backtest
    report) and store them as small documents. The API then reads a few
    kilobytes instead of re-aggregating raw tables, which keeps database
    traffic far below the hosting plan's monthly transfer allowance."""
    from ..api import queries as q
    db.put_document(f"season_table:{season}", {str(k): v for k, v in q.compute_season_table(season).items()})
    db.put_document(f"goalie_season:{season}", {str(k): v for k, v in q.compute_goalie_season(season).items()})
    report = backtest_report()
    if report:
        db.put_document("backtest_public", q.public_backtest(report))
    db.put_document("market_benchmark", market.benchmark())
    return 4 if report else 3


SEASON_SIMS = 20000


def run_season_sim(feat, fb: FeatureBuilder, season: int, n_sims: int = SEASON_SIMS) -> dict:
    """Simulate the rest of the season with the active model and store the
    result, plus one line per day of history, as documents for the site."""
    am = pub.active_model()
    if am is None:
        return {"status": "no model"}
    _, fc = am
    inp = season_sim.prepare(feat, fb.f.games, fc, season, season_sim.alignment_from_teams(fb.f.teams))
    if inp is None:
        return run_playoff_sim(feat, fb, season, fc, n_sims)
    inp.sd_off, inp.sd_def = inp.sd_off * season_sim.STRENGTH_SD_SCALE, inp.sd_def * season_sim.STRENGTH_SD_SCALE
    today = dt.datetime.now(ET).date()
    seed = int(today.strftime("%Y%m%d"))
    out = season_sim.simulate(inp, n_sims=n_sims, seed=seed)
    through = max(d for d, sc in zip(fb.f.games["game_date"], fb.f.games["home_score"]) if sc is not None and sc == sc)
    teams = [{k: (v.item() if hasattr(v, "item") else v) for k, v in r.items()} for r in out.to_dict("records")]
    db.put_document(f"season_sim:{season}", {
        "phase": "regular", "season": season, "generated_at": db.utcnow().isoformat(timespec="seconds"), "results_through": str(through),
        "model_version": fc.version, "n_sims": n_sims, "seed": seed, "games_remaining": int(len(inp.home)),
        "strength_sd_scale": season_sim.STRENGTH_SD_SCALE, "teams": teams})
    hist = db.get_document(f"season_sim_history:{season}") or {"dates": [], "teams": {}}
    day = str(today)
    if hist["dates"] and hist["dates"][-1] == day:      # re-run on the same day replaces that day's line
        hist["dates"].pop()
        for v in hist["teams"].values():
            for series in v.values():
                series.pop()
    hist["dates"].append(day)
    for t in teams:
        h = hist["teams"].setdefault(str(t["team_id"]), {"p_playoffs": [], "p_cup": [], "proj_points": []})
        h["p_playoffs"].append(round(t["p_playoffs"], 4))
        h["p_cup"].append(round(t["p_cup"], 4))
        h["proj_points"].append(round(t["proj_points"], 1))
    db.put_document(f"season_sim_history:{season}", hist)
    return {"status": "ok", "teams": len(teams), "games_remaining": int(len(inp.home))}


def playoff_document(inp, out: dict, season: int, model_version: str, n_sims: int, seed: int, through) -> dict:
    """The stored form of a playoff simulation: every series so far, the
    current round's series odds and each remaining team's round-by-round odds."""
    r0 = out["round"]
    series = [{"round": s.round, "matchup": s.matchup, "top": s.top, "bottom": s.bottom, "wins_top": s.wins_top,
               "wins_bottom": s.wins_bottom, "done": True, "winner": s.winner, "p_top": None} for s in inp.earlier]
    for s, p in zip(inp.series, out["p_top"]):
        series.append({"round": s.round, "matchup": s.matchup, "top": s.top, "bottom": s.bottom, "wins_top": s.wins_top,
                       "wins_bottom": s.wins_bottom, "done": s.done, "winner": s.winner, "p_top": None if s.done else p})
    out_ids = {t for s in inp.series if s.done for t in (s.top, s.bottom) if t != s.winner}
    teams = [{"team_id": t, "alive": t not in out_ids, "p_round": {str(r): p for r, p in v.items()}, "p_cup": v[4]}
             for t, v in out["teams"].items()]
    final = inp.series[0]
    champion = final.winner if r0 == 4 and final.done else None
    return {"phase": "complete" if champion else "playoffs", "season": season, "generated_at": db.utcnow().isoformat(timespec="seconds"),
            "results_through": str(through), "model_version": model_version, "n_sims": n_sims, "seed": seed, "round": r0,
            "round_name": playoff_sim.ROUND_NAMES[r0], "strength_sd_scale": playoff_sim.STRENGTH_SD_SCALE,
            "series": series, "teams": sorted(teams, key=lambda x: -x["p_cup"]), "champion": champion}


def run_playoff_sim(feat, fb: FeatureBuilder, season: int, fc, n_sims: int = SEASON_SIMS) -> dict:
    """Once the regular season is over: series and Cup odds from the bracket
    as it stands. Until the NHL lists every first-round series the stored
    regular-season outlook is kept and marked as waiting."""
    inp = playoff_sim.prepare(feat, fb.f.games, fc, season)
    if inp is None:
        old = db.get_document(f"season_sim:{season}")
        if old and old.get("phase") == "regular":
            db.put_document(f"season_sim:{season}", {**old, "phase": "awaiting_bracket"})
        return {"status": "regular season complete; bracket not listed yet"}
    today = dt.datetime.now(ET).date()
    seed = int(today.strftime("%Y%m%d"))
    out = playoff_sim.simulate(inp, n_sims=n_sims, seed=seed)
    through = max(d for d, sc in zip(fb.f.games["game_date"], fb.f.games["home_score"]) if sc is not None and sc == sc)
    doc = playoff_document(inp, out, season, fc.version, n_sims, seed, through)
    db.put_document(f"season_sim:{season}", doc)
    hist = db.get_document(f"season_sim_history:{season}")
    if hist:   # keep the Cup-odds line going through the playoffs
        day = str(today)
        if hist["dates"] and hist["dates"][-1] == day:
            hist["dates"].pop()
            for v in hist["teams"].values():
                for series in v.values():
                    series.pop()
        hist["dates"].append(day)
        cup = {str(t["team_id"]): t["p_cup"] for t in doc["teams"]}
        for tid, v in hist["teams"].items():
            v["p_cup"].append(round(cup.get(tid, 0.0), 4))
            v["p_playoffs"].append(None)
            v["proj_points"].append(None)
        db.put_document(f"season_sim_history:{season}", hist)
    return {"status": "ok", "phase": doc["phase"], "round": doc["round"], "teams": len(doc["teams"])}


def publish_needed(now: dt.datetime | None = None) -> bool:
    """True when a game is in its pre-game window without a forecast based
    on confirmed goalies: the only thing a refresh can change."""
    now = now or db.utcnow()
    G, F = db.games, db.forecasts
    with db.begin() as c:
        n = c.execute(sa.select(sa.func.count()).select_from(
            G.outerjoin(F, sa.and_(F.c.game_id == G.c.id, F.c.is_latest == sa.true())))
            .where(G.c.state == "PRE", G.c.schedule_state == "OK", G.c.start_time_utc > now,
                   sa.or_(F.c.id.is_(None), F.c.goalie_state != "confirmed"))).scalar()
    return bool(n)


def seed_backtest_report() -> int:
    """Make the database's copy of the backtest report match the one
    committed with the code, so every host serves the report that belongs to
    the model definition it is running."""
    p = config.ARTIFACT_DIR / "backtest" / "summary.json"
    if not p.exists():
        return 0
    committed = json.loads(p.read_text())
    stored = db.get_document("backtest_summary")
    if stored is not None and stored.get("generated_at") == committed.get("generated_at"):
        return 0
    db.put_document("backtest_summary", committed)
    return 1


def seed_season_validation() -> int:
    """Same for the season-simulation validation report."""
    p = config.ARTIFACT_DIR / "backtest" / "season_sim.json"
    if not p.exists():
        return 0
    committed = json.loads(p.read_text())
    stored = db.get_document("season_sim_validation")
    if stored is not None and stored.get("generated_at") == committed.get("generated_at"):
        return 0
    db.put_document("season_sim_validation", committed)
    return 1


def seed_playoff_validation() -> int:
    """Same for the playoff-mode validation report."""
    p = config.ARTIFACT_DIR / "backtest" / "playoffs.json"
    if not p.exists():
        return 0
    committed = json.loads(p.read_text())
    stored = db.get_document("playoff_validation")
    if stored is not None and stored.get("generated_at") == committed.get("generated_at"):
        return 0
    db.put_document("playoff_validation", committed)
    return 1


def _step(report: dict, name: str, fn, critical: bool = False):
    t = time.time()
    try:
        res = fn()
        report["steps"].append({"step": name, "status": "ok", "seconds": round(time.time() - t, 2), "result": res if isinstance(res, (int, dict)) else None})
        return res
    except Exception as e:  # noqa: BLE001 - every failure must be recorded
        log.exception("step %s failed", name)
        report["steps"].append({"step": name, "status": "failed", "seconds": round(time.time() - t, 2), "error": repr(e)[:500]})
        report["failures"].append(name)
        if critical:
            raise PipelineAbort(name) from e
        return None


def run_daily(retrain: bool | None = None, days_ahead: int = 7) -> dict:
    db.create_all()
    report: dict = {"job": "daily", "started_at": db.utcnow().isoformat(timespec="seconds"), "steps": [], "failures": []}
    season = current_season()
    today = dt.datetime.now(ET).date()
    try:
        _step(report, "reference", lambda: (nhl.seed_reference(), nhl.ingest_teams())[1], critical=True)
        _step(report, "backtest_report", lambda: seed_backtest_report() + seed_season_validation() + seed_playoff_validation())
        _step(report, "games", lambda: nhl.ingest_games([season]), critical=True)
        _step(report, "schedule", lambda: nhl.sync_schedule(today - dt.timedelta(days=3), weeks=3), critical=True)
        _step(report, "market_odds", market.ingest)
        _step(report, "goalie_logs", lambda: nhl.ingest_goalie_logs([season], since=today - dt.timedelta(days=14)))
        _step(report, "skater_logs", lambda: nhl.ingest_skater_logs([season], since=today - dt.timedelta(days=10)))
        # only this season's rows change, so only they are rewritten
        _step(report, "moneypuck_teams", lambda: moneypuck.ingest_team_games(season))
        _step(report, "moneypuck_shots", lambda: moneypuck.ingest_shots([season]))
        built = _step(report, "features", build_all, critical=True)
        feat, fb = built
        report["steps"][-1]["result"] = {"games": int(len(feat))}
        _step(report, "ratings", lambda: store_ratings(fb, season))
        am = pub.active_model()
        # Retrain weekly, and at once if the backtest now selects different inputs.
        chosen = (backtest_report() or {}).get("chosen_groups")
        changed = bool(am and chosen and set(am[1].glm.groups) != set(chosen))
        need = retrain if retrain is not None else (am is None or changed or (today - am[1].trained_through).days >= 7)
        if need:
            _step(report, "train", lambda: train(feat).version and 1, critical=am is None)
        fresh = freshness()
        report["freshness"] = fresh
        _step(report, "publish", lambda: pub.publish(fb, feat, days_ahead=days_ahead, trigger="scheduled", freshness=fresh), critical=True)
        _step(report, "settle", settle)
        _step(report, "site_documents", lambda: store_site_documents(season))
        _step(report, "season_simulation", lambda: run_season_sim(feat, fb, season))
    except PipelineAbort as e:
        report["aborted_at"] = str(e)
    report["finished_at"] = db.utcnow().isoformat(timespec="seconds")
    report["status"] = "aborted" if "aborted_at" in report else ("degraded" if report["failures"] else "ok")
    _write_status(report)
    return report


def run_refresh(days_ahead: int = 2) -> dict:
    db.create_all()
    report: dict = {"job": "refresh", "started_at": db.utcnow().isoformat(timespec="seconds"), "steps": [], "failures": []}
    today = dt.datetime.now(ET).date()
    try:
        _step(report, "schedule", lambda: nhl.sync_schedule(today - dt.timedelta(days=1), weeks=1), critical=True)
        finalized = _step(report, "scores", lambda: sum(nhl.finalize_from_scoreboard(d) for d in (today - dt.timedelta(days=1), today)))
        _step(report, "market_odds", market.ingest)
        fresh = freshness()
        report["freshness"] = fresh
        # Rebuilding features and re-forecasting is only worthwhile when a
        # game has entered its pre-game window (starters may be confirmed).
        if pub.active_model() is not None and publish_needed():
            built = _step(report, "features", build_all, critical=True)
            feat, fb = built
            report["steps"][-1]["result"] = {"games": int(len(feat))}
            _step(report, "publish", lambda: pub.publish(fb, feat, days_ahead=days_ahead, trigger="refresh", freshness=fresh), critical=True)
        else:
            report["steps"].append({"step": "publish", "status": "skipped", "result": "no game in its pre-game window"})
        _step(report, "settle", settle)
        if finalized:
            _step(report, "site_documents", lambda: store_site_documents(current_season()))
    except PipelineAbort as e:
        report["aborted_at"] = str(e)
    report["finished_at"] = db.utcnow().isoformat(timespec="seconds")
    report["status"] = "aborted" if "aborted_at" in report else ("degraded" if report["failures"] else "ok")
    _write_status(report)
    return report


def _write_status(report: dict) -> None:
    config.ensure_dirs()
    (config.DATA_DIR / f"pipeline_status_{report['job']}.json").write_text(json.dumps(report, indent=1, default=str))


def run_backtest() -> dict:
    feat, _ = build_all()
    return walkforward.run(feat)["summary"]
