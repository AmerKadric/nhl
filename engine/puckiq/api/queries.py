"""Read models for the API. Everything here is derived from stored data;
nothing is generated or estimated at request time except Lab scenarios."""
from __future__ import annotations

import datetime as dt
import json
import math
import threading
import time
from typing import Any, Callable

import numpy as np
import pandas as pd
import sqlalchemy as sa

from .. import config, db
from ..explain import headline
from ..pipeline.daily import current_season, freshness

LOGO = "https://assets.nhle.com/logos/nhl/svg/{abbrev}_dark.svg"

_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now + ttl, val)
    return val


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _iso(v) -> str | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, (dt.datetime, pd.Timestamp)):
        return pd.Timestamp(v).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(v)


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def teams_map() -> dict[int, dict]:
    def load():
        with db.begin() as c:
            rows = c.execute(sa.select(db.teams)).mappings().all()
        return {r["id"]: {"id": r["id"], "abbrev": r["abbrev"], "name": r["name"], "color": r["color"],
                          "conference": r["conference"], "division": r["division"], "active": bool(r["active"]),
                          "logo": LOGO.format(abbrev=r["abbrev"])} for r in rows}
    return cached("teams", 300, load)


def team_by_abbrev(abbrev: str) -> dict | None:
    for t in teams_map().values():
        if t["abbrev"] == abbrev.upper() and t["active"]:
            return t
    return None


def _team_brief(tid: int) -> dict:
    t = teams_map()[tid]
    return {k: t[k] for k in ("id", "abbrev", "name", "color", "logo")}


def model_versions() -> list[dict]:
    with db.begin() as c:
        rows = c.execute(sa.select(db.model_versions).order_by(db.model_versions.c.created_at.desc())).mappings().all()
        counts = dict(c.execute(sa.select(db.forecasts.c.model_version_id, sa.func.count()).group_by(db.forecasts.c.model_version_id)).fetchall())
    out = []
    for r in rows:
        cfg = _j(r["config"])
        out.append({"id": r["id"], "version": r["version"], "architecture": r["architecture"], "created_at": _iso(r["created_at"]),
                    "trained_through": str(r["trained_through"]), "is_active": bool(r["is_active"]),
                    "n_forecasts": int(counts.get(r["id"], 0)), "feature_groups": cfg.get("groups"),
                    "coefficients": cfg.get("coefficients"), "simulator": cfg.get("simulator"), "n_sims": cfg.get("n_sims"),
                    "backtest": _j(r["backtest_summary"])})
    return out


def sources() -> list[dict]:
    with db.begin() as c:
        return [dict(r) for r in c.execute(sa.select(db.data_sources)).mappings().all()]


def meta() -> dict:
    def load():
        mv = model_versions()
        active = next((m for m in mv if m["is_active"]), None)
        with db.begin() as c:
            n_fc = c.execute(sa.select(sa.func.count()).select_from(db.forecasts)).scalar()
            n_games = c.execute(sa.select(sa.func.count(sa.distinct(db.forecasts.c.game_id)))).scalar()
            n_ev = c.execute(sa.select(sa.func.count(sa.distinct(db.evaluations.c.game_id)))).scalar()
        return {"brand": "PUCKIQ", "season": current_season(), "today": dt.datetime.now(dt.timezone.utc).astimezone(
                    __import__("zoneinfo").ZoneInfo("America/New_York")).date().isoformat(),
                "model": active and {k: active[k] for k in ("version", "architecture", "trained_through", "n_sims", "feature_groups")},
                "freshness": freshness(), "sources": sources(),
                "counts": {"forecast_versions": int(n_fc or 0), "games_forecast": int(n_games or 0), "games_evaluated": int(n_ev or 0)}}
    return cached("meta", 30, load)


# --- games ------------------------------------------------------------------
def _forecast_card(f: dict | None, mv: dict[int, str], home: str = "", away: str = "") -> dict | None:
    if f is None:
        return None
    s = _j(f["summary"])
    return {
        "id": f["id"], "version_no": f["version_no"], "published_at": _iso(f["published_at"]),
        "goalie_state": f["goalie_state"], "model_version": mv.get(f["model_version_id"]),
        "p_home_win": f["p_home_win"], "p_away_win": f["p_away_win"],
        "exp_home_goals": f["exp_home_goals"], "exp_away_goals": f["exp_away_goals"],
        "ml_home_goals": f["ml_home_goals"], "ml_away_goals": f["ml_away_goals"], "ml_score_prob": f["ml_score_prob"],
        "expected_total": s["expected_total"], "p_extra_time": f["p_overtime"] + f["p_shootout"],
        "confidence_label": s["confidence"]["label"],
        "headline": headline(s.get("drivers"), home, away, f["p_home_win"]) if home else None,
        "goalies": {side: [{"pid": g["pid"], "name": g["name"], "prob": g["prob"], "status": g["status"]} for g in s["goalies"][side]]
                    for side in ("home", "away")},
        "stale_sources": (s.get("data_freshness") or {}).get("stale", []),
    }


def _game_card(g: dict, f: dict | None, ev: dict | None, mv: dict[int, str]) -> dict:
    final = g["home_score"] is not None and g["state"] == "OFF"
    return {
        "id": g["id"], "season": g["season"], "game_type": g["game_type"], "date": str(g["game_date"]),
        "start_time_utc": _iso(g["start_time_utc"]), "venue": g["venue"], "state": g["state"], "schedule_state": g["schedule_state"],
        "home": _team_brief(g["home_team_id"]), "away": _team_brief(g["away_team_id"]),
        "result": {"home_score": g["home_score"], "away_score": g["away_score"], "last_period_type": g["last_period_type"]} if final else None,
        "live": None,
        "forecast": _forecast_card(f, mv, teams_map()[g["home_team_id"]]["abbrev"], teams_map()[g["away_team_id"]]["abbrev"]),
        "evaluation": ev and {"winner_correct": bool(ev["winner_correct"]), "exact_score_hit": bool(ev["exact_score_hit"]),
                              "p_actual_score": ev["p_actual_score"], "log_loss": ev["log_loss"]},
    }


def games_between(start: dt.date, end: dt.date) -> list[dict]:
    G, F, E = db.games, db.forecasts, db.evaluations
    with db.begin() as c:
        games = c.execute(sa.select(G).where(G.c.game_date >= start, G.c.game_date <= end)
                          .order_by(G.c.start_time_utc, G.c.id)).mappings().all()
        ids = [g["id"] for g in games]
        fc = {r["game_id"]: dict(r) for r in c.execute(sa.select(F).where(F.c.game_id.in_(ids), F.c.is_latest == sa.true())).mappings().all()} if ids else {}
        ev = {r["forecast_id"]: dict(r) for r in c.execute(sa.select(E).where(E.c.game_id.in_(ids))).mappings().all()} if ids else {}
        mv = dict(c.execute(sa.select(db.model_versions.c.id, db.model_versions.c.version)).fetchall())
    out = []
    for g in games:
        f = fc.get(g["id"])
        out.append(_game_card(dict(g), f, ev.get(f["id"]) if f else None, mv))
    return out


def game_row(game_id: int) -> dict | None:
    with db.begin() as c:
        r = c.execute(sa.select(db.games).where(db.games.c.id == game_id)).mappings().first()
    return dict(r) if r else None


def season_table(season: int) -> dict[int, dict]:
    """Season-to-date descriptive statistics for every team. Served from the
    summary the pipeline stores after each update; computed from the raw
    tables only when no stored summary exists (local development)."""
    def load():
        doc = db.get_document(f"season_table:{season}")
        if doc is not None:
            return {int(k): v for k, v in doc.items()}
        return compute_season_table(season)
    return cached(f"season_table:{season}", 300, load)


def compute_season_table(season: int) -> dict[int, dict]:
    """Season-to-date descriptive statistics for every team (regular season)."""
    if True:
        with db.begin() as c:
            g = pd.read_sql(sa.select(db.games).where(db.games.c.season == season, db.games.c.game_type == config.REGULAR,
                                                      db.games.c.home_score.isnot(None)), c)
            t = pd.read_sql(sa.select(db.team_game_stats).join(db.games, db.games.c.id == db.team_game_stats.c.game_id)
                            .where(db.games.c.season == season, db.games.c.game_type == config.REGULAR), c)
            latest = c.execute(sa.text(
                "select r.team_id, r.elo, r.off_g, r.def_g, r.off_xg, r.def_xg from team_ratings r join "
                "(select team_id, max(as_of_game_id) m from team_ratings group by team_id) x "
                "on x.team_id = r.team_id and x.m = r.as_of_game_id")).mappings().all()
        ratings = {r["team_id"]: dict(r) for r in latest}
        out: dict[int, dict] = {}
        if g.empty:
            return out
        g = g.sort_values(["game_date", "id"])
        long = pd.concat([
            g.assign(team_id=g["home_team_id"], is_home=True, gf=g["home_score"], ga=g["away_score"]),
            g.assign(team_id=g["away_team_id"], is_home=False, gf=g["away_score"], ga=g["home_score"]),
        ]).sort_values(["game_date", "id"])
        long["win"] = long["gf"] > long["ga"]
        long["otl"] = (~long["win"]) & (long["last_period_type"] != "REG")
        long["loss"] = (~long["win"]) & (long["last_period_type"] == "REG")
        ts = t.set_index(["game_id", "team_id"])

        def rec(d: pd.DataFrame) -> dict:
            return {"w": int(d["win"].sum()), "l": int(d["loss"].sum()), "otl": int(d["otl"].sum())}

        for tid, d in long.groupby("team_id"):
            s = ts.reindex(list(zip(d["id"], [tid] * len(d)))).reset_index(drop=True)
            have = s["toi_5v5"].notna()
            sx = s[have]
            toi5, toipp, toipk = sx["toi_5v5"].sum(), sx["toi_pp"].sum(), sx["toi_pk"].sum()
            last = d.tail(10)
            sl = s.tail(10)
            sl = sl[sl["toi_5v5"].notna()]
            r = rec(d)
            out[int(tid)] = {
                "gp": int(len(d)), "record": r, "points": 2 * r["w"] + r["otl"],
                "point_pct": (2 * r["w"] + r["otl"]) / (2 * len(d)),
                "gf_pg": float(d["gf"].mean()), "ga_pg": float(d["ga"].mean()),
                "xgf60_5v5": float(sx["xgf_5v5"].sum() / toi5 * 3600) if toi5 else None,
                "xga60_5v5": float(sx["xga_5v5"].sum() / toi5 * 3600) if toi5 else None,
                "xg_share_5v5": float(sx["xgf_5v5"].sum() / (sx["xgf_5v5"].sum() + sx["xga_5v5"].sum())) if toi5 else None,
                "xgf_pg": float(sx["xgf"].mean()) if len(sx) else None, "xga_pg": float(sx["xga"].mean()) if len(sx) else None,
                "pp_xg60": float(sx["xgf_pp"].sum() / toipp * 3600) if toipp else None,
                "pp_g60": float(sx["gf_pp"].sum() / toipp * 3600) if toipp else None,
                "pk_xga60": float(sx["xga_pk"].sum() / toipk * 3600) if toipk else None,
                "pk_ga60": float(sx["ga_pk"].sum() / toipk * 3600) if toipk else None,
                "sh_pct": float(sx["gf"].sum() / sx["sog_f"].sum()) if len(sx) and sx["sog_f"].sum() else None,
                "sv_pct": float(1 - sx["ga"].sum() / sx["sog_a"].sum()) if len(sx) and sx["sog_a"].sum() else None,
                "goals_above_expected": float((sx["gf"] - sx["xgf"]).sum()) if len(sx) else None,
                "goals_saved_above_expected": float((sx["xga"] - sx["ga"]).sum()) if len(sx) else None,
                "last10": {**rec(last), "gf_pg": float(last["gf"].mean()), "ga_pg": float(last["ga"].mean()),
                           "xg_share_5v5": float(sl["xgf_5v5"].sum() / (sl["xgf_5v5"].sum() + sl["xga_5v5"].sum())) if len(sl) else None,
                           "results": ["W" if w else ("OTL" if o else "L") for w, o in zip(last["win"], last["otl"])]},
                "home": rec(d[d["is_home"]]), "away": rec(d[~d["is_home"]]),
                "xg_games": int(have.sum()),
                "ratings": ratings.get(int(tid)),
            }
        return out


def goalie_season(season: int) -> dict[int, dict]:
    def load():
        doc = db.get_document(f"goalie_season:{season}")
        if doc is not None:
            return {int(k): v for k, v in doc.items()}
        return compute_goalie_season(season)
    return cached(f"goalie_season:{season}", 300, load)


def compute_goalie_season(season: int) -> dict[int, dict]:
    if True:
        with db.begin() as c:
            d = pd.read_sql(sa.select(db.goalie_game_stats, db.games.c.game_date).join(db.games, db.games.c.id == db.goalie_game_stats.c.game_id)
                            .where(db.games.c.season == season, db.games.c.game_type == config.REGULAR), c)
        out: dict[int, dict] = {}
        for pid, g in d.sort_values("game_date").groupby("player_id"):
            def agg(x: pd.DataFrame) -> dict:
                sa_, ga = x["shots_against"].sum(), x["goals_against"].sum()
                xg = x["xga"].sum()
                return {"gp": int(len(x)), "gs": int(x["started"].sum()), "shots_against": int(sa_), "goals_against": int(ga),
                        "sv_pct": float(1 - ga / sa_) if sa_ else None, "xga": float(xg),
                        "gsax": float(xg - x["ga_on_xg"].sum()) if xg else None,
                        "gsax_per_60": float((xg - x["ga_on_xg"].sum()) / x["toi"].sum() * 3600) if x["toi"].sum() else None}
            out[int(pid)] = {"season": agg(g), "last5": agg(g.tail(5)), "last_start": str(g[g["started"] == 1]["game_date"].max()) if g["started"].any() else None}
        return out


def forecast_detail(game_id: int) -> dict | None:
    F, S = db.forecasts, db.score_distributions
    with db.begin() as c:
        versions = [dict(r) for r in c.execute(sa.select(F).where(F.c.game_id == game_id).order_by(F.c.version_no)).mappings().all()]
        if not versions:
            return None
        latest = next(v for v in versions if v["is_latest"])
        dist = c.execute(sa.select(S).where(S.c.forecast_id == latest["id"])).mappings().first()
        evs = {r["forecast_id"]: dict(r) for r in c.execute(sa.select(db.evaluations).where(db.evaluations.c.game_id == game_id)).mappings().all()}
        mv = dict(c.execute(sa.select(db.model_versions.c.id, db.model_versions.c.version)).fetchall())
    s = _j(latest["summary"])
    detail = {
        "id": latest["id"], "version_no": latest["version_no"], "published_at": _iso(latest["published_at"]),
        "information_cutoff": _iso(latest["information_cutoff"]), "model_version": mv.get(latest["model_version_id"]),
        "goalie_state": latest["goalie_state"], "n_sims": latest["n_sims"], "seed": latest["seed"],
        **{k: latest[k] for k in ("p_home_win", "p_away_win", "p_home_reg", "p_tie_reg", "p_away_reg", "p_overtime", "p_shootout",
                                  "exp_home_goals", "exp_away_goals", "lam_home", "lam_away", "ml_home_goals", "ml_away_goals", "ml_score_prob")},
        "top_scores": s["top_scores"], "totals": s["totals"], "diffs": s["diffs"], "expected_total": s["expected_total"],
        "extras": s["extras"], "confidence": s["confidence"], "drivers": s["drivers"], "goalies": s["goalies"],
        "data_freshness": s.get("data_freshness"), "availability": s.get("availability"),
        "lineup_watch": s.get("lineup_watch"),
        "matrix": {"max_goals": dist["max_goals"], "final": _j(dist["final_matrix"]), "regulation": _j(dist["regulation_matrix"]),
                   "overflow": dist["overflow_mass"]},
    }
    hist = []
    for v in versions:
        e = evs.get(v["id"])
        hist.append({"version_no": v["version_no"], "published_at": _iso(v["published_at"]), "reason": v["reason"],
                     "goalie_state": v["goalie_state"], "model_version": mv.get(v["model_version_id"]), "p_home_win": v["p_home_win"],
                     "exp_home_goals": v["exp_home_goals"], "exp_away_goals": v["exp_away_goals"],
                     "ml_home_goals": v["ml_home_goals"], "ml_away_goals": v["ml_away_goals"], "is_latest": bool(v["is_latest"]),
                     "evaluation": e and {"winner_correct": bool(e["winner_correct"]), "exact_score_hit": bool(e["exact_score_hit"]),
                                          "log_loss": e["log_loss"], "p_actual_score": e["p_actual_score"]}})
    return {"forecast": detail, "versions": hist, "evaluation": evs.get(latest["id"]) and {
        k: (bool(v) if isinstance(v, (bool, np.bool_)) else v) for k, v in evs[latest["id"]].items() if k not in ("settled_at",)}}


def snapshot_for(game_id: int) -> dict | None:
    F, SN = db.forecasts, db.feature_snapshots
    with db.begin() as c:
        r = c.execute(sa.select(SN.c.features).join(F, F.c.feature_snapshot_id == SN.c.id)
                      .where(F.c.game_id == game_id, F.c.is_latest == sa.true())).first()
    return _j(r[0]) if r else None


# --- accuracy ----------------------------------------------------------------
def backtest_summary() -> dict | None:
    """The backtest report: from the database, else the committed file."""
    def load():
        doc = db.get_document("backtest_summary")
        if doc:
            return doc
        p = config.ARTIFACT_DIR / "backtest" / "summary.json"
        return json.loads(p.read_text()) if p.exists() else None
    return cached("backtest", 300, load)


PUBLIC_MODELS = ["baseline_home", "elo_raw", "elo_goals", "scoring_avg", "xg_lean_proj", "xg_lean", "xg_glm", "xg_glm_elo", "gbm", "blend"]


def public_backtest(bt: dict) -> dict:
    """The part of the backtest report the site shows (about a fifth of the
    full report)."""
    chosen = bt["chosen_model"]
    proj = f"{chosen}_proj"
    show = list(dict.fromkeys(PUBLIC_MODELS[:4] + [proj, chosen] + PUBLIC_MODELS[4:]))
    return {
        **{k: bt.get(k) for k in ("generated_at", "method", "first_training_season", "test_seasons", "selection_seasons",
                                  "confirmation_seasons", "chosen_model", "chosen_groups", "selection_reason", "significance",
                                  "ablation", "additions")},
        "primary": proj if proj in bt["models"] else chosen,
        "models": {k: {x: bt["models"][k][x] for x in ("description", "overall", "selection_window", "confirmation_window",
                                                        "regular_season", "playoffs")} for k in show if k in bt["models"]},
        "detail": {k: bt["models"][k] for k in (proj, chosen) if k in bt["models"]},
        "selection_tests": bt["selection_tests"],
    }


def backtest_public() -> dict | None:
    def load():
        doc = db.get_document("backtest_public")
        if doc is not None:
            return doc
        full = backtest_summary()
        return public_backtest(full) if full else None
    return cached("backtest_public", 1800, load)


def market_benchmark() -> dict | None:
    """PUCKIQ against the betting market on the same games (precomputed)."""
    return cached("market_benchmark", 600, lambda: db.get_document("market_benchmark"))


def backtest_primary() -> str:
    """Model whose backtest rows are shown: the chosen model with projected
    goalies, i.e. what was knowable well before each game."""
    return (backtest_public() or {}).get("primary", "xg_lean_proj")


def _metrics(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"n": 0}
    return {"n": int(len(d)), "accuracy": float(d["winner_correct"].mean()), "log_loss": float(d["log_loss"].mean()),
            "brier": float(d["brier"].mean()), "exact_score_rate": float(d["exact_score_hit"].mean()),
            "score_log_loss": float(-np.log(np.clip(d["p_actual_score"], 1e-6, 1)).mean()),
            "mae_team_goals": float((d["abs_err_home"].mean() + d["abs_err_away"].mean()) / 2),
            "mae_total_goals": float(d["abs_err_total"].mean())}


def forward_record() -> dict:
    """Performance of forecasts that were actually published before games."""
    def load():
        E, F, G = db.evaluations, db.forecasts, db.games
        with db.begin() as c:
            d = pd.read_sql(sa.select(E, F.c.is_latest, F.c.goalie_state, F.c.p_home_win, F.c.version_no, F.c.published_at,
                                      G.c.game_date, G.c.season)
                            .join(F, F.c.id == E.c.forecast_id).join(G, G.c.id == E.c.game_id), c)
            first = c.execute(sa.select(sa.func.min(F.c.published_at))).scalar()
            pending = c.execute(sa.select(sa.func.count(sa.distinct(F.c.game_id))).select_from(
                F.outerjoin(E, E.c.forecast_id == F.c.id)).where(E.c.forecast_id.is_(None), F.c.is_latest == sa.true())).scalar()
        out = {"first_forecast_at": _iso(first), "games_pending": int(pending or 0), "final": _metrics(d[d["is_latest"] == 1] if len(d) else d)}
        if len(d):
            fin = d[d["is_latest"] == 1].copy()
            firsts = d.sort_values("version_no").groupby("game_id").head(1)
            out["first_version"] = _metrics(firsts)
            fin["month"] = pd.to_datetime(fin["game_date"]).dt.strftime("%Y-%m")
            out["by_month"] = {k: _metrics(v) for k, v in fin.groupby("month")}
            fin["conf"] = np.maximum(fin["p_home_win"], 1 - fin["p_home_win"])
            buckets = []
            for lo, hi in ((0.5, 0.55), (0.55, 0.6), (0.6, 0.65), (0.65, 0.7), (0.7, 1.01)):
                b = fin[(fin["conf"] >= lo) & (fin["conf"] < hi)]
                if len(b):
                    buckets.append({"lo": lo, "hi": min(hi, 1.0), "n": int(len(b)), "mean_confidence": float(b["conf"].mean()),
                                    "accuracy": float(b["winner_correct"].mean())})
            out["by_confidence"] = buckets
            cal = []
            for lo in np.arange(0, 1, 0.1):
                b = fin[(fin["p_home_win"] >= lo) & (fin["p_home_win"] < lo + 0.1)]
                if len(b):
                    cal.append({"lo": float(lo), "hi": float(lo + 0.1), "n": int(len(b)), "mean_pred": float(b["p_home_win"].mean()),
                                "observed": float(b["home_won"].mean())})
            out["calibration"] = cal
        return out
    return cached("forward", 60, load)


def history(source: str, season: int | None, team_id: int | None, limit: int, offset: int) -> dict:
    """Predictions next to actual outcomes."""
    G = db.games
    with db.begin() as c:
        if source == "forward":
            F, E = db.forecasts, db.evaluations
            q = (sa.select(G.c.id, G.c.game_date, G.c.home_team_id, G.c.away_team_id, G.c.home_score, G.c.away_score, G.c.last_period_type,
                           F.c.p_home_win, F.c.exp_home_goals, F.c.exp_away_goals, F.c.ml_home_goals, F.c.ml_away_goals,
                           F.c.goalie_state, F.c.version_no, F.c.published_at, E.c.winner_correct, E.c.exact_score_hit, E.c.p_actual_score)
                 .join(F, F.c.game_id == G.c.id).join(E, E.c.forecast_id == F.c.id).where(F.c.is_latest == sa.true()))
        else:
            B = db.backtest_predictions
            q = (sa.select(G.c.id, G.c.game_date, G.c.home_team_id, G.c.away_team_id, G.c.home_score, G.c.away_score, G.c.last_period_type,
                           B.c.p_home_win, B.c.exp_home_goals, B.c.exp_away_goals, B.c.ml_home_goals, B.c.ml_away_goals, B.c.p_actual_score)
                 .join(B, B.c.game_id == G.c.id).where(B.c.model_key == backtest_primary()))
        if season:
            q = q.where(G.c.season == season)
        if team_id:
            q = q.where(sa.or_(G.c.home_team_id == team_id, G.c.away_team_id == team_id))
        total = c.execute(sa.select(sa.func.count()).select_from(q.subquery())).scalar()
        rows = c.execute(q.order_by(G.c.game_date.desc(), G.c.id.desc()).limit(limit).offset(offset)).mappings().all()
    out = []
    for r in rows:
        home_won = r["home_score"] > r["away_score"]
        item = {"game_id": r["id"], "date": str(r["game_date"]), "home": _team_brief(r["home_team_id"]), "away": _team_brief(r["away_team_id"]),
                "home_score": r["home_score"], "away_score": r["away_score"], "last_period_type": r["last_period_type"],
                "p_home_win": r["p_home_win"], "exp_home_goals": r["exp_home_goals"], "exp_away_goals": r["exp_away_goals"],
                "ml_home_goals": r["ml_home_goals"], "ml_away_goals": r["ml_away_goals"], "p_actual_score": r["p_actual_score"],
                "winner_correct": bool((r["p_home_win"] >= 0.5) == home_won),
                "exact_score_hit": bool(r["ml_home_goals"] == r["home_score"] and r["ml_away_goals"] == r["away_score"])}
        if source == "forward":
            item.update(goalie_state=r["goalie_state"], version_no=r["version_no"], published_at=_iso(r["published_at"]))
        out.append(item)
    return {"source": source, "total": int(total or 0), "limit": limit, "offset": offset, "rows": out}


def season_outlook(season: int) -> dict:
    """The stored season simulation, its day-by-day history and its
    validation report. Nothing is simulated at request time."""
    def load():
        sim = db.get_document(f"season_sim:{season}")
        if sim:
            tm = teams_map()
            for t in sim["teams"]:
                t["team"] = _team_brief(t["team_id"]) if t["team_id"] in tm else None
            for g in (sim.get("stakes") or {}).get("games") or []:
                for side in ("home", "away"):
                    g[side]["team"] = _team_brief(g[side]["team_id"]) if g[side]["team_id"] in tm else None
            for sr in sim.get("series") or []:
                sr["top_team"] = _team_brief(sr["top"]) if sr["top"] in tm else None
                sr["bottom_team"] = _team_brief(sr["bottom"]) if sr["bottom"] in tm else None
        return {"season": season, "simulation": sim, "history": db.get_document(f"season_sim_history:{season}"),
                "validation": db.get_document("season_sim_validation"), "playoff_validation": db.get_document("playoff_validation"),
                "leverage_validation": db.get_document("leverage_validation")}
    return cached(f"season_outlook:{season}", 600, load)


def game_stakes(season: int, game_id: int) -> dict | None:
    """What this game means for each side's playoff odds, if it is on the
    slate the latest season simulation looked at."""
    stakes = ((season_outlook(season).get("simulation") or {}).get("stakes")) or {}
    g = next((x for x in stakes.get("games") or [] if x["game_id"] == game_id), None)
    return None if g is None else {"date": stakes["date"], **g}


def team_odds_history(season: int, team_id: int) -> dict | None:
    """One team's daily playoff odds, Cup odds and projected points."""
    hist = season_outlook(season).get("history")
    t = (hist or {}).get("teams", {}).get(str(team_id))
    if not t or len(hist["dates"]) < 2:
        return None
    return {"dates": hist["dates"], "reconstructed_through": hist.get("reconstructed_through"), **t}


# --- teams -------------------------------------------------------------------
def rating_history(team_id: int, season: int) -> list[dict]:
    R = db.team_ratings
    with db.begin() as c:
        rows = c.execute(sa.select(R).where(R.c.team_id == team_id, R.c.season == season).order_by(R.c.game_date, R.c.as_of_game_id)).mappings().all()
    return [{"date": str(r["game_date"]), "elo": r["elo"], "off_g": r["off_g"], "def_g": r["def_g"], "off_xg": r["off_xg"], "def_xg": r["def_xg"]} for r in rows]


def league_rating_ranges(season: int) -> dict:
    """Current spread of each rating across the league, for context."""
    tab = season_table(season)
    out = {}
    for k in ("elo", "off_g", "def_g", "off_xg", "def_xg"):
        vals = sorted(v["ratings"][k] for v in tab.values() if v.get("ratings") and v["ratings"].get(k) is not None)
        if vals:
            out[k] = {"min": vals[0], "max": vals[-1], "median": vals[len(vals) // 2]}
    return out


def team_goalies(team_id: int, season: int) -> list[dict]:
    with db.begin() as c:
        rows = c.execute(sa.select(db.goalie_game_stats.c.player_id, db.players.c.full_name, sa.func.max(db.games.c.game_date).label("last"))
                         .join(db.games, db.games.c.id == db.goalie_game_stats.c.game_id)
                         .join(db.players, db.players.c.id == db.goalie_game_stats.c.player_id)
                         .where(db.goalie_game_stats.c.team_id == team_id, db.games.c.season == season)
                         .group_by(db.goalie_game_stats.c.player_id, db.players.c.full_name)).mappings().all()
    gs = goalie_season(season)
    return sorted([{"pid": r["player_id"], "name": r["full_name"], "last_game": str(r["last"]), **gs.get(r["player_id"], {})} for r in rows],
                  key=lambda x: -(x.get("season", {}).get("gs", 0)))
