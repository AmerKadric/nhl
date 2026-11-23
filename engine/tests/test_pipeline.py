"""Database, ingestion-parsing, publishing, settlement and API tests against
a temporary SQLite database."""
import datetime as dt
import io
import zipfile

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa

from puckiq import config, db
from puckiq.features.build import FeatureBuilder, Frames
from puckiq.features.params import FeatureParams
from puckiq.ingest import moneypuck, nhl
from puckiq.ingest.reference import DATA_SOURCES, TEAM_REF
from puckiq.models.forecaster import Forecaster
from puckiq.pipeline import publish as pub
from puckiq.pipeline.settle import settle

from .conftest import make_frames


@pytest.fixture(scope="module")
def seeded():
    """A database holding the synthetic league, with the last day unplayed."""
    db.reset_engine()
    db.metadata.drop_all(db.engine())
    db.create_all()
    fr = make_frames()
    g = fr.games.copy()
    last_day = g["game_date"].max()
    upcoming = g["game_date"] == last_day
    for c in ("home_score", "away_score", "home_reg_goals", "away_reg_goals", "last_period_type"):
        g[c] = g[c].astype(object)
        g.loc[upcoming, c] = None
    g.loc[upcoming, "state"] = "FUT"
    g["updated_at"] = db.utcnow()
    up_ids = set(g.loc[upcoming, "id"])
    with db.begin() as c:
        db.upsert(c, db.data_sources, DATA_SOURCES)
        db.upsert(c, db.teams, fr.teams.to_dict("records"))
        db.upsert(c, db.players, fr.players.to_dict("records"))
        db.upsert(c, db.games, g.to_dict("records"))
        db.upsert(c, db.team_game_stats, fr.tgs[~fr.tgs["game_id"].isin(up_ids)].to_dict("records"))
        db.upsert(c, db.goalie_game_stats, fr.ggs[~fr.ggs["game_id"].isin(up_ids)].to_dict("records"))
    return {"frames": fr, "upcoming": sorted(up_ids), "last_day": last_day}


@pytest.fixture(scope="module")
def published(seeded):
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "FIRST_MODEL_SEASON", 2020)
    from puckiq.features.build import load_frames
    fb = FeatureBuilder(load_frames(), FeatureParams())
    feat = fb.run()
    fc = Forecaster.train(feat)
    pub.register_model(fc, fc.save())
    now = dt.datetime.combine(seeded["last_day"], dt.time(12, 0))
    res = pub.publish(fb, feat, days_ahead=3, now=now, use_network=False)
    yield {"fb": fb, "feat": feat, "now": now, "res": res, **seeded}
    mp.undo()


def test_upsert_is_idempotent(seeded):
    rows = seeded["frames"].teams.to_dict("records")
    with db.begin() as c:
        before = c.execute(sa.select(sa.func.count()).select_from(db.teams)).scalar()
        db.upsert(c, db.teams, rows)
        db.upsert(c, db.teams, rows)
        assert c.execute(sa.select(sa.func.count()).select_from(db.teams)).scalar() == before


def test_upsert_handles_rows_with_different_columns(seeded):
    gid = seeded["upcoming"][0]
    with db.begin() as c:
        # rows carrying different column sets in one call (as the schedule sync does)
        base = {r["id"]: r for r in seeded["frames"].teams.to_dict("records")}
        r6 = {k: v for k, v in base[6].items() if k not in ("division", "conference")}
        db.upsert(c, db.teams, [dict(r6, color="#111111"), dict(base[3], color="#222222", division="Test")])
        got = dict(c.execute(sa.select(db.teams.c.id, db.teams.c.color).where(db.teams.c.id.in_([6, 3]))).fetchall())
        assert got == {6: "#111111", 3: "#222222"}
        assert c.execute(sa.select(db.teams.c.division).where(db.teams.c.id == 3)).scalar() == "Test"
        assert c.execute(sa.select(db.teams.c.abbrev).where(db.teams.c.id == 6)).scalar() == "BOS"
    assert gid


def test_publish_writes_one_version_per_upcoming_game(published):
    assert published["res"]["published"] == len(published["upcoming"])
    with db.begin() as c:
        rows = c.execute(sa.select(db.forecasts)).mappings().all()
        assert {r["game_id"] for r in rows} == set(published["upcoming"])
        for r in rows:
            assert r["version_no"] == 1 and r["is_latest"] and r["published_pregame"]
            assert r["p_home_win"] + r["p_away_win"] == pytest.approx(1.0)
            assert r["ml_home_goals"] != r["ml_away_goals"]
            assert r["p_overtime"] + r["p_shootout"] == pytest.approx(r["p_tie_reg"], abs=1e-9)
        n_dist = c.execute(sa.select(sa.func.count()).select_from(db.score_distributions)).scalar()
        assert n_dist == len(rows)


def test_republish_with_same_inputs_adds_nothing(published):
    res = pub.publish(published["fb"], published["feat"], days_ahead=3, now=published["now"], use_network=False)
    assert res["published"] == 0


def test_changed_inputs_create_a_new_version_and_keep_the_old(published):
    feat = published["feat"].copy()
    gid = published["upcoming"][0]
    feat.loc[feat["game_id"] == gid, "h_b2b"] = 1.0 - feat.loc[feat["game_id"] == gid, "h_b2b"]
    with db.begin() as c:
        old = dict(c.execute(sa.select(db.forecasts).where(db.forecasts.c.game_id == gid)).mappings().first())
    res = pub.publish(published["fb"], feat, days_ahead=3, now=published["now"] + dt.timedelta(minutes=5), use_network=False)
    assert res["published"] == 1
    with db.begin() as c:
        vs = c.execute(sa.select(db.forecasts).where(db.forecasts.c.game_id == gid).order_by(db.forecasts.c.version_no)).mappings().all()
    assert [v["version_no"] for v in vs] == [1, 2]
    assert [bool(v["is_latest"]) for v in vs] == [False, True]
    assert vs[1]["reason"] == "inputs_changed"
    # the original forecast is preserved exactly as published
    for k in ("p_home_win", "exp_home_goals", "ml_home_goals", "published_at", "input_hash", "seed"):
        assert vs[0][k] == old[k]


def test_no_forecast_is_published_after_puck_drop(published):
    late = dt.datetime.combine(published["last_day"] + dt.timedelta(days=2), dt.time(12, 0))
    res = pub.publish(published["fb"], published["feat"], days_ahead=3, now=late, use_network=False)
    assert res["games"] == 0 and res["published"] == 0


def test_settlement_is_exactly_once(published):
    assert settle() == 0   # nothing is final yet
    fr = published["frames"].games.set_index("id")
    with db.begin() as c:
        n_fc = c.execute(sa.select(sa.func.count()).select_from(db.forecasts)).scalar()
        for gid in published["upcoming"]:
            r = fr.loc[gid]
            c.execute(db.games.update().where(db.games.c.id == gid).values(
                state="OFF", home_score=int(r.home_score), away_score=int(r.away_score), last_period_type=r.last_period_type,
                home_reg_goals=int(r.home_reg_goals), away_reg_goals=int(r.away_reg_goals)))
    assert settle() == n_fc          # every version of every forecast is scored
    assert settle() == 0             # ...once
    assert settle() == 0
    with db.begin() as c:
        ev = c.execute(sa.select(db.evaluations)).mappings().all()
        assert len(ev) == n_fc
        for e in ev:
            f = c.execute(sa.select(db.forecasts).where(db.forecasts.c.id == e["forecast_id"])).mappings().first()
            home_won = e["home_score"] > e["away_score"]
            assert bool(e["winner_correct"]) == ((f["p_home_win"] >= 0.5) == home_won)
            assert e["log_loss"] == pytest.approx(-np.log(f["p_home_win"] if home_won else f["p_away_win"]))
            assert bool(e["exact_score_hit"]) == (f["ml_home_goals"] == e["home_score"] and f["ml_away_goals"] == e["away_score"])
            assert 0 <= e["p_actual_score"] < 1


def test_api_endpoints(published):
    import warnings
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

    from puckiq.api import main as api
    from puckiq.api import queries
    queries.clear_cache()
    api.nhl.fetch_scoreboard = lambda d: []   # no network in tests
    c = TestClient(api.app)
    assert c.get("/health").status_code == 200
    m = c.get("/v1/meta").json()
    assert m["model"]["version"].startswith("lean-poisson")
    assert any(s["key"] == "injuries" and s["status"] == "unavailable" for s in m["sources"])
    day = published["last_day"].isoformat()
    games = c.get(f"/v1/games?date={day}").json()["games"]
    assert {g["id"] for g in games} == set(published["upcoming"])
    assert all(g["forecast"] and g["result"] and g["evaluation"] for g in games)
    gid = published["upcoming"][0]
    d = c.get(f"/v1/games/{gid}").json()
    assert len(d["versions"]) == 2 and d["forecast"]["version_no"] == 2
    mat = np.array(d["forecast"]["matrix"]["final"])
    assert mat.shape == (13, 13) and mat.sum() == pytest.approx(1.0, abs=2e-3) and np.all(np.diag(mat) == 0)
    assert d["lab"] is None   # finished games cannot be re-run in the lab
    assert c.get("/v1/games/1").status_code == 404
    assert c.get("/v1/games?date=nope").status_code == 422
    assert c.get("/v1/games?start=2020-01-01&end=2021-01-01").status_code == 422
    acc = c.get("/v1/accuracy").json()
    assert acc["forward"]["final"]["n"] == len(published["upcoming"])
    assert acc["backtest"] is None   # no backtest artifact in the test environment: reported as absent, not invented
    h = c.get("/v1/accuracy/history?source=forward").json()
    assert h["total"] == len(published["upcoming"])
    assert c.get("/v1/accuracy/history?source=bogus").status_code == 422
    assert c.get("/v1/teams/BOS").status_code == 200
    assert c.get("/v1/teams/ZZZ").status_code == 404
    assert c.post(f"/v1/games/{gid}/lab", json={"home_g7": 99}).status_code == 422


def test_lab_recalculates_with_the_real_model(seeded, published):
    """Uses a still-unplayed copy of a game so the lab is available."""
    import warnings
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

    from puckiq.api import main as api
    from puckiq.api import queries
    gid = published["upcoming"][1]
    with db.begin() as c:
        c.execute(db.games.update().where(db.games.c.id == gid).values(state="FUT", home_score=None, away_score=None, last_period_type=None))
    queries.clear_cache()
    c = TestClient(api.app)
    d = c.get(f"/v1/games/{gid}").json()
    assert d["lab"] and d["lab"]["unsupported"]
    same = c.post(f"/v1/games/{gid}/lab", json={}).json()
    assert same["baseline"]["p_home_win"] == same["scenario"]["p_home_win"]
    cur = d["lab"]["current"]["away_b2b"]
    alt = c.post(f"/v1/games/{gid}/lab", json={"away_b2b": not cur, "away_g7": 2}).json()
    assert alt["scenario"]["p_home_win"] != alt["baseline"]["p_home_win"]
    assert c.post(f"/v1/games/{gid}/lab", json={"home_goalie_id": 999999}).status_code == 422
    assert c.post(f"/v1/games/{gid}/lab", json={"away_b2b": True, "away_g7": 0}).status_code == 422


def test_rate_limit(monkeypatch):
    import warnings
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

    from puckiq.api import main as api
    monkeypatch.setattr(api, "RATE_LIMIT", 3)
    api._hits.clear()
    c = TestClient(api.app)
    codes = [c.get("/health").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
    api._hits.clear()


# --- ingestion parsing --------------------------------------------------------
def test_reference_data_covers_every_team():
    for abbrev, ref in TEAM_REF.items():
        assert -180 < ref["lon"] < 0 and 20 < ref["lat"] < 60 and ref["color"].startswith("#"), abbrev
    assert len({s["key"] for s in DATA_SOURCES}) == len(DATA_SOURCES)


def test_schedule_row_parsing():
    known = {6, 3}
    g = {"id": 2026020001, "season": 20262027, "gameType": 2, "startTimeUTC": "2026-11-22T00:00:00Z", "gameState": "OFF",
         "gameScheduleState": "OK", "venue": {"default": "TD Garden"}, "homeTeam": {"id": 6, "score": 3},
         "awayTeam": {"id": 3, "score": 2}, "gameOutcome": {"lastPeriodType": "SO"}}
    row = nhl._game_row_from_web(g, known)
    assert row["season"] == 2026 and row["game_date"] == dt.date(2026, 11, 21)   # 7pm ET the previous calendar day
    assert (row["home_reg_goals"], row["away_reg_goals"]) == (2, 2) and row["last_period_type"] == "SO"
    assert nhl._game_row_from_web(dict(g, gameType=1), known) is None          # preseason ignored
    assert nhl._game_row_from_web(dict(g, homeTeam={"id": 99, "score": 1}), known) is None   # unknown team ignored
    fut = nhl._game_row_from_web({**g, "gameState": "FUT", "homeTeam": {"id": 6}, "awayTeam": {"id": 3}}, known)
    assert "home_score" not in fut and fut["state"] == "FUT"
    ppd = nhl._game_row_from_web({**g, "gameState": "FUT", "gameScheduleState": "PPD", "homeTeam": {"id": 6}, "awayTeam": {"id": 3}}, known)
    assert ppd["schedule_state"] == "PPD"


def test_moneypuck_team_file_parsing(tmp_path):
    cols = moneypuck.TEAM_COLS
    recs = []
    for sit, toi in (("all", 3600), ("5on5", 2900), ("5on4", 300), ("4on5", 280), ("other", 120)):
        for side in ("HOME", "AWAY"):
            r = {c: 1.0 for c in cols}
            r.update(season=2024, gameId=2024020001, home_or_away=side, position="Team Level", situation=sit, iceTime=toi,
                     xGoalsFor=3.0 if side == "HOME" else 2.0)
            recs.append(r)
    recs.append({**recs[0], "season": 2009})          # before the window
    recs.append({**recs[0], "position": "line"})      # not team level
    p = tmp_path / "t.csv"
    pd.DataFrame(recs).to_csv(p, index=False)
    out = moneypuck.parse_team_games(p, 2011)
    assert len(out) == 2
    h = out[out["is_home"]].iloc[0]
    assert h["toi_all"] == 3600 and h["toi_5v5"] == 2900 and h["toi_pp"] == 300 and h["toi_pk"] == 280 and h["xgf"] == 3.0


def test_moneypuck_shot_parsing(tmp_path):
    rows = [
        dict(season=2024, isPlayoffGame=0, game_id=20001, team="AWAY", period=1, goal=0, xGoal=0.1, goalieIdForShot=11, shotOnEmptyNet=0),
        dict(season=2024, isPlayoffGame=0, game_id=20001, team="AWAY", period=2, goal=1, xGoal=0.3, goalieIdForShot=11, shotOnEmptyNet=0),
        dict(season=2024, isPlayoffGame=0, game_id=20001, team="HOME", period=3, goal=1, xGoal=0.9, goalieIdForShot=0, shotOnEmptyNet=1),
        dict(season=2024, isPlayoffGame=0, game_id=20001, team="HOME", period=5, goal=1, xGoal=0.3, goalieIdForShot=22, shotOnEmptyNet=0),
        dict(season=2024, isPlayoffGame=0, game_id=20001, team="HOME", period=1, goal=0, xGoal=0.2, goalieIdForShot=22, shotOnEmptyNet=0),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("shots_2024.csv", pd.DataFrame(rows).to_csv(index=False))
    p = tmp_path / "s.zip"
    p.write_bytes(buf.getvalue())
    agg = moneypuck.parse_shots(p).set_index("player_id")
    assert set(agg.index) == {11, 22}
    assert agg.loc[11, "game_id"] == 2024020001 and bool(agg.loc[11, "def_home"])
    assert agg.loc[11, "xga"] == pytest.approx(0.4) and agg.loc[11, "ga_on_xg"] == 1
    assert agg.loc[22, "attempts"] == 1   # empty-net and shootout attempts excluded


def test_model_loads_from_database_when_artifact_file_is_missing(published, tmp_path, monkeypatch):
    mid, fc = pub.active_model()
    with db.begin() as c:
        c.execute(db.model_versions.update().where(db.model_versions.c.id == mid).values(artifact_path=str(tmp_path / "gone.json")))
    monkeypatch.setattr(config, "ARTIFACT_DIR", tmp_path)
    mid2, fc2 = pub.active_model()
    assert mid2 == mid and fc2.version == fc.version
    np.testing.assert_allclose(fc2.glm.beta, fc.glm.beta)
    assert fc2.sp.theta == fc.sp.theta


def test_missing_values_do_not_break_prediction(published):
    """A feature row with gaps falls back to training means, never NaN."""
    am = pub.active_model()
    row = {k: v for k, v in published["feat"].iloc[-1].to_dict().items() if k[:2] in ("h_", "a_") or k in ("playoff", "lg_g")}
    row["h_off_xga"] = np.nan
    row["a_b2b"] = np.nan
    r = am[1].rates(row, np.nan, 0.0)
    assert np.isfinite(r["lam_home"]) and np.isfinite(r["lam_away"])
