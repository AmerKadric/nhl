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
from ..features.build import FeatureBuilder, build_all
from ..ingest import moneypuck, nhl
from ..models.forecaster import Forecaster
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


def train(feat=None, backtest_summary: dict | None = None) -> Forecaster:
    if feat is None:
        feat, _ = build_all()
    fc = Forecaster.train(feat)
    path = fc.save()
    if backtest_summary is None:
        p = config.ARTIFACT_DIR / "backtest" / "summary.json"
        if p.exists():
            s = json.loads(p.read_text())
            backtest_summary = {"chosen_model": s["chosen_model"], "generated_at": s["generated_at"],
                                "overall": s["models"][s["chosen_model"]]["overall"],
                                "confirmation_window": s["models"][s["chosen_model"]]["confirmation_window"]}
    pub.register_model(fc, path, backtest_summary)
    log.info("trained %s on %d games", fc.version, fc.n_train_games)
    return fc


def seed_backtest_report() -> int:
    """Copy the committed backtest report into the database if it is absent,
    so hosts without the repository files can serve the Accuracy page."""
    if db.get_document("backtest_summary") is not None:
        return 0
    p = config.ARTIFACT_DIR / "backtest" / "summary.json"
    if not p.exists():
        return 0
    db.put_document("backtest_summary", json.loads(p.read_text()))
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
        _step(report, "backtest_report", seed_backtest_report)
        _step(report, "games", lambda: nhl.ingest_games([season]), critical=True)
        _step(report, "schedule", lambda: nhl.sync_schedule(today - dt.timedelta(days=3), weeks=3), critical=True)
        _step(report, "goalie_logs", lambda: nhl.ingest_goalie_logs([season], since=today - dt.timedelta(days=14)))
        _step(report, "moneypuck_teams", lambda: moneypuck.ingest_team_games())
        _step(report, "moneypuck_shots", lambda: moneypuck.ingest_shots([season]))
        built = _step(report, "features", build_all, critical=True)
        feat, fb = built
        report["steps"][-1]["result"] = {"games": int(len(feat))}
        _step(report, "ratings", lambda: store_ratings(fb, season))
        am = pub.active_model()
        need = retrain if retrain is not None else (am is None or (today - am[1].trained_through).days >= 7)
        if need:
            _step(report, "train", lambda: train(feat).version and 1, critical=am is None)
        fresh = freshness()
        report["freshness"] = fresh
        _step(report, "publish", lambda: pub.publish(fb, feat, days_ahead=days_ahead, trigger="scheduled", freshness=fresh), critical=True)
        _step(report, "settle", settle)
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
        _step(report, "scores", lambda: sum(nhl.finalize_from_scoreboard(d) for d in (today - dt.timedelta(days=1), today)))
        built = _step(report, "features", build_all, critical=True)
        feat, fb = built
        report["steps"][-1]["result"] = {"games": int(len(feat))}
        fresh = freshness()
        report["freshness"] = fresh
        if pub.active_model() is not None:
            _step(report, "publish", lambda: pub.publish(fb, feat, days_ahead=days_ahead, trigger="refresh", freshness=fresh), critical=True)
        _step(report, "settle", settle)
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
