"""Forecast publishing: versioned, append-only, pre-game only."""
from __future__ import annotations

import datetime as dt
import logging
import zlib

import numpy as np
import pandas as pd
import sqlalchemy as sa

from .. import config, db
from ..features.build import FeatureBuilder
from ..ingest import nhl
from ..ingest.http import FetchError
from ..models.forecaster import ARCHITECTURE, Forecaster, confidence_label, input_hash

log = logging.getLogger("puckiq.publish")


def register_model(fc: Forecaster, path, backtest_summary: dict | None = None, activate: bool = True) -> int:
    """Insert the model version (idempotent on `version`) and make it active."""
    with db.begin() as c:
        row = c.execute(sa.select(db.model_versions.c.id).where(db.model_versions.c.version == fc.version)).first()
        if row:
            mid = row[0]
        else:
            mid = c.execute(db.model_versions.insert().values(
                version=fc.version, created_at=db.utcnow(), trained_through=fc.trained_through, architecture=ARCHITECTURE,
                config={"groups": fc.glm.groups, "columns": fc.glm.cols, "coefficients": dict(zip(["const"] + fc.glm.cols, fc.glm.beta.tolist())),
                        "simulator": fc.sp.to_json(), "n_sims": config.N_SIMS,
                        # full model, so any runner can load it without the local artifact file
                        "artifact": fc.to_json()},
                backtest_summary=backtest_summary, artifact_path=str(path), is_active=False,
            )).inserted_primary_key[0]
            c.execute(db.training_runs.insert().values(
                model_version_id=mid, started_at=db.utcnow(), finished_at=db.utcnow(), n_train_games=fc.n_train_games,
                params={"first_training_season": config.FIRST_MODEL_SEASON}, metrics=None))
        if activate:
            c.execute(db.model_versions.update().values(is_active=False))
            c.execute(db.model_versions.update().where(db.model_versions.c.id == mid).values(is_active=True))
    return mid


def active_model() -> tuple[int, Forecaster] | None:
    """The active production model: from its artifact file when present,
    otherwise from the copy stored with the model version in the database."""
    import json
    from pathlib import Path
    with db.begin() as c:
        row = c.execute(sa.select(db.model_versions.c.id, db.model_versions.c.artifact_path, db.model_versions.c.version,
                                  db.model_versions.c.config)
                        .where(db.model_versions.c.is_active == sa.true())).first()
    if not row:
        return None
    for path in (Path(row.artifact_path or ""), config.ARTIFACT_DIR / "models" / f"{row.version}.json"):
        if path.is_file():
            return row.id, Forecaster.load(path)
    cfg = json.loads(row.config) if isinstance(row.config, str) else row.config
    if cfg and cfg.get("artifact"):
        return row.id, Forecaster.from_json(cfg["artifact"])
    raise RuntimeError(f"model {row.version} is active but its artifact is missing; run `puckiq train`")


def goalie_options(fb: FeatureBuilder, game_id: int, names: dict[int, str], rosters: dict[str, set[int]] | None,
                   abbrevs: tuple[str, str], confirmed: dict | None = None) -> tuple[dict, str]:
    """Plausible starters per side with probabilities, and the goalie state
    ("projected" | "partial" | "confirmed")."""
    from ..features.build import goalie_rating, starter_probabilities
    p = fb.p
    out, n_conf = {}, 0
    c = fb.cands[fb.cands["game_id"] == game_id] if len(fb.cands) else pd.DataFrame()
    for side, key, abbrev in (("h", "home", abbrevs[0]), ("a", "away", abbrevs[1])):
        conf_pid = (confirmed or {}).get(key)
        if conf_pid:
            gs = fb.goalies.get(int(conf_pid))
            out[key] = [dict(pid=int(conf_pid), name=names.get(int(conf_pid), "Unknown"), prob=1.0,
                             rating=float(goalie_rating(gs, p)), xga_n=float(gs.xga) if gs else 0.0, status="confirmed")]
            n_conf += 1
            continue
        cs = c[c["side"] == side].copy()
        roster = (rosters or {}).get(abbrev)
        if roster and len(cs):
            keep = cs[cs["pid"].isin(roster)]
            if len(keep):
                cs = keep
        if len(cs) == 0:
            # No usable history: a single unknown starter at the prior rating.
            out[key] = [dict(pid=0, name="Unknown", prob=1.0, rating=float(p.gk_prior_mean), xga_n=0.0, status="unknown")]
            continue
        cs["prob"] = starter_probabilities(cs.assign(game_id=game_id, side=side), p.starter_w)
        cs = cs.sort_values("prob", ascending=False)
        out[key] = [dict(pid=int(r.pid), name=names.get(int(r.pid), "Unknown"), prob=float(r.prob), rating=float(r.rating),
                         xga_n=float(r.xga_n), status="projected") for r in cs.itertuples()]
    state = "confirmed" if n_conf == 2 else ("partial" if n_conf == 1 else "projected")
    return out, state


def confirmed_starters(game_id: int, state: str) -> dict:
    """Starters the NHL has published in the boxscore. Only trusted once the
    game is in its pre-game state and exactly one starter is flagged."""
    if state not in ("PRE",):
        return {}
    try:
        box = nhl.fetch_boxscore_goalies(game_id)
    except FetchError as e:
        log.warning("boxscore unavailable for %s: %s", game_id, e)
        return {}
    out = {}
    for side in ("home", "away"):
        st = [g for g in box[side] if g["starter"]]
        if len(st) == 1:
            out[side] = st[0]["player_id"]
    return out


def lineup_watch(fb: FeatureBuilder, team_id: int, names: dict[int, str], limit: int = 6) -> dict:
    """Regular skaters who did not dress in the team's most recent game.
    A statement of fact from box scores, not an injury report: the reason a
    skater sat out is not known."""
    lb = getattr(fb, "lineups", None)
    if lb is None or not lb.by_game:
        return {"available": False, "as_of": None, "absent": []}
    t = lb.team(team_id)
    absent = [dict(a, name=names.get(a["pid"], "Unknown")) for a in lb.absences(team_id)[:limit]]
    return {"available": True, "as_of": t.last_date.isoformat() if t.last_date else None, "absent": absent}


def lineup_snapshot(fb: FeatureBuilder, team_id: int, names: dict[int, str]) -> dict | None:
    """The skaters a team last dressed, with the model's ice-time rating for
    each, and the rating it gives an unknown replacement. Stored with the
    forecast inputs so scenarios can be recomputed later."""
    lb = getattr(fb, "lineups", None)
    if lb is None or not lb.by_game:
        return None
    t = lb.team(team_id)
    skaters = []
    for pid in t.last:
        pl = lb.players.get(pid)
        skaters.append({"pid": pid, "name": names.get(pid, "Unknown"), "toi": round(lb.value(pid)[1], 2),
                        "defence": bool(pl.d) if pl else False})
    skaters.sort(key=lambda x: -x["toi"])
    return {"skaters": skaters, "replacement": {"F": round(lb.lg_toi[0], 2), "D": round(lb.lg_toi[1], 2)}}


def build_summary(res: dict, gstate: str, freshness: dict, lineups: dict | None = None) -> dict:
    sim = res["sim"]
    ml = sim.most_likely
    p_fav = max(sim.p_home_win, 1 - sim.p_home_win)
    tot = np.array(sim.totals())
    return {
        "top_scores": sim.top_scores(5),
        "totals": [round(float(x), 5) for x in tot],
        "diffs": {str(k): round(v, 5) for k, v in sim.diffs().items() if v > 0},
        "expected_total": float(sim.exp_home_goals + sim.exp_away_goals),
        "extras": sim.extras,
        "confidence": {
            "label": confidence_label(p_fav), "favourite_prob": p_fav,
            "mc_standard_error": sim.mc_se_win,
            "rate_sd_home": res["point"]["sd_home"], "rate_sd_away": res["point"]["sd_away"],
            "most_likely_score_prob": ml[2],
        },
        "drivers": res["drivers"],
        "goalies": res["goalies"],
        "goalie_state": gstate,
        "exact_point": {"p_home_win": res["exact"].p_home_win, "lam_home": res["exact"].lam_home, "lam_away": res["exact"].lam_away},
        "data_freshness": freshness,
        "lineup_watch": lineups,
        "availability": {"injuries": "unavailable", "lineups": "most recent dressed lineup only; confirmed game-day lineups unavailable"},
    }


def publish(fb: FeatureBuilder, feat: pd.DataFrame, days_ahead: int = 7, trigger: str = "scheduled",
            freshness: dict | None = None, now: dt.datetime | None = None, use_network: bool = True) -> dict:
    """Publish forecasts for upcoming games. A new version is written only
    when the model inputs changed; earlier versions are kept untouched."""
    am = active_model()
    if am is None:
        raise RuntimeError("no active model version; run `puckiq train` first")
    model_id, fc = am
    now = now or db.utcnow()
    freshness = freshness or {}
    today = (now - dt.timedelta(hours=5)).date()
    up = feat[(~feat["final"]) & (feat["game_date"] >= today) & (feat["game_date"] <= today + dt.timedelta(days=days_ahead))
              & feat["state"].isin(["FUT", "PRE"]) & (feat["schedule_state"] == "OK")]
    up = up[up["start_time_utc"].notna() & (pd.to_datetime(up["start_time_utc"]) > now)]
    names = fb.f.players.set_index("id")["full_name"].to_dict()
    abbrev = fb.abbrev

    with db.begin() as c:
        run_id = c.execute(db.prediction_runs.insert().values(
            model_version_id=model_id, started_at=now, trigger=trigger, status="running", data_freshness=freshness,
        )).inserted_primary_key[0]

    rosters: dict[str, set[int]] = {}
    n_pub = 0
    for g in up.itertuples():
        row = {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in g._asdict().items()
               if k[:2] in ("h_", "a_") or k in ("playoff", "lg_g", "game_id", "season", "game_date")}
        row = {k: (np.nan if v is None else v) for k, v in row.items()}
        ab = (abbrev[g.home_team_id], abbrev[g.away_team_id])
        soon = (pd.Timestamp(g.start_time_utc) - pd.Timestamp(now)) <= pd.Timedelta(hours=36)
        if use_network and soon:
            for a in ab:
                if a not in rosters:
                    try:
                        rosters[a] = {x["player_id"] for x in nhl.fetch_rosters_goalies(a)}
                    except FetchError as e:
                        log.warning("roster unavailable for %s: %s", a, e)
                        rosters[a] = set()
        confirmed = confirmed_starters(g.game_id, g.state) if use_network else {}
        goalies, gstate = goalie_options(fb, g.game_id, names, rosters, ab, confirmed)
        h = input_hash(fc.version, row, goalies, fc.glm.cols)

        with db.begin() as c:
            prev = c.execute(sa.select(db.forecasts.c.id, db.forecasts.c.version_no, db.forecasts.c.input_hash,
                                       db.forecasts.c.goalie_state, db.forecasts.c.model_version_id)
                             .where(db.forecasts.c.game_id == g.game_id).order_by(db.forecasts.c.version_no.desc())).first()
        if prev is not None and prev.input_hash == h:
            continue
        version_no = (prev.version_no + 1) if prev else 1
        seed = zlib.crc32(f"{g.game_id}:{version_no}:{fc.version}".encode()) & 0x7FFFFFFF
        res = fc.forecast(row, goalies, seed=seed)
        sim = res["sim"]
        ml = sim.most_likely
        if prev is None:
            reason = "initial"
        elif gstate != prev.goalie_state and gstate != "projected":
            reason = "goalie_confirmed"
        elif prev.model_version_id != model_id:
            reason = "model_updated"
        else:
            reason = "inputs_changed"
        snap = {"row": {k: (None if isinstance(v, float) and np.isnan(v) else (v.isoformat() if isinstance(v, dt.date) else v))
                        for k, v in row.items()}, "goalies": goalies,
                "lineups": {"home": lineup_snapshot(fb, g.home_team_id, names), "away": lineup_snapshot(fb, g.away_team_id, names)}}
        with db.begin() as c:
            existing = c.execute(sa.select(db.feature_snapshots.c.id).where(
                db.feature_snapshots.c.game_id == g.game_id, db.feature_snapshots.c.input_hash == h)).first()
            snap_id = existing[0] if existing else c.execute(db.feature_snapshots.insert().values(
                game_id=g.game_id, as_of=now, input_hash=h, features=snap)).inserted_primary_key[0]
            c.execute(db.forecasts.update().where(db.forecasts.c.game_id == g.game_id).values(is_latest=False))
            fid = c.execute(db.forecasts.insert().values(
                game_id=g.game_id, version_no=version_no, model_version_id=model_id, prediction_run_id=run_id,
                feature_snapshot_id=snap_id, published_at=now, information_cutoff=now, is_latest=True, published_pregame=True,
                goalie_state=gstate, reason=reason, input_hash=h,
                p_home_win=sim.p_home_win, p_away_win=1 - sim.p_home_win, p_home_reg=sim.p_home_reg, p_tie_reg=sim.p_tie_reg,
                p_away_reg=sim.p_away_reg, p_overtime=sim.p_overtime, p_shootout=sim.p_shootout,
                exp_home_goals=sim.exp_home_goals, exp_away_goals=sim.exp_away_goals, lam_home=sim.lam_home, lam_away=sim.lam_away,
                ml_home_goals=ml[0], ml_away_goals=ml[1], ml_score_prob=ml[2], n_sims=sim.n_sims, seed=seed,
                summary=build_summary(res, gstate, freshness, {
                    "home": lineup_watch(fb, g.home_team_id, names), "away": lineup_watch(fb, g.away_team_id, names)}),
            )).inserted_primary_key[0]
            c.execute(db.score_distributions.insert().values(
                forecast_id=fid, max_goals=config.MAX_GOALS, final_matrix=np.round(sim.final, 5).tolist(),
                regulation_matrix=np.round(sim.regulation, 5).tolist(), overflow_mass=sim.overflow))
            for side, tid in (("home", g.home_team_id), ("away", g.away_team_id)):
                for go in goalies[side]:
                    if go["pid"]:
                        c.execute(db.goalie_status.insert().values(
                            game_id=g.game_id, team_id=tid, player_id=go["pid"],
                            status="confirmed" if go["status"] == "confirmed" else "projected", probability=go["prob"],
                            source="nhl_boxscore" if go["status"] == "confirmed" else "puckiq_usage_model", observed_at=now))
        n_pub += 1

    with db.begin() as c:
        c.execute(db.prediction_runs.update().where(db.prediction_runs.c.id == run_id).values(
            finished_at=db.utcnow(), status="ok", n_games=int(len(up)), n_published=n_pub))
    log.info("published %d new forecast versions for %d upcoming games", n_pub, len(up))
    return {"run_id": run_id, "games": int(len(up)), "published": n_pub}
