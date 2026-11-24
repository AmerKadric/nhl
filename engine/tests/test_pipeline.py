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
        db.upsert(c, db.skater_game_stats, fr.sgs[~fr.sgs["game_id"].isin(up_ids)].to_dict("records"))
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


def test_unsettled_final_is_graded_provisionally_and_flagged_overdue(published, monkeypatch):
    """When the pipeline has not run, a final from the NHL scoreboard is still
    shown and graded, the grade is marked provisional and kept out of the
    record, the site is told the update is overdue, and settlement later
    records exactly the grade that was shown."""
    import warnings
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

    from puckiq.api import main as api
    from puckiq.api import queries
    from puckiq.pipeline import daily

    gid, day = published["upcoming"][0], published["last_day"]
    real = published["frames"].games.set_index("id").loc[gid]
    h, a, lpt = int(real.home_score), int(real.away_score), real.last_period_type
    with db.begin() as c:   # put the game back to "pipeline never saw it finish"
        n_versions = c.execute(sa.select(sa.func.count()).select_from(db.forecasts).where(db.forecasts.c.game_id == gid)).scalar()
        c.execute(db.evaluations.delete().where(db.evaluations.c.game_id == gid))
        c.execute(db.games.update().where(db.games.c.id == gid).values(
            state="FUT", home_score=None, away_score=None, last_period_type=None, home_reg_goals=None, away_reg_goals=None))

    asked = []

    def scoreboard(d):
        asked.append(d)
        return [dict(id=gid, state="OFF", schedule_state="OK", home_score=h, away_score=a, home_sog=30, away_sog=28, period=3,
                     period_type=lpt, clock="00:00", in_intermission=False, last_period_type=lpt)] if d == day else []

    monkeypatch.setattr(api.nhl, "fetch_scoreboard", scoreboard)
    monkeypatch.setattr(api, "today_et", lambda: day + dt.timedelta(days=1))
    queries.clear_cache()
    c = TestClient(api.app)
    try:
        cards = {g["id"]: g for g in c.get(f"/v1/games?date={day.isoformat()}").json()["games"]}
        card = cards[gid]
        assert card["result"] == {"home_score": h, "away_score": a, "last_period_type": lpt}
        ev, f = card["evaluation"], card["forecast"]
        assert ev["provisional"] is True and ev["p_actual_score"] is None
        assert ev["winner_correct"] == ((f["p_home_win"] >= 0.5) == (h > a))
        assert ev["exact_score_hit"] == (f["ml_home_goals"] == h and f["ml_away_goals"] == a)
        assert all(g["evaluation"]["provisional"] is False for i, g in cards.items() if i != gid)   # settled games are untouched

        detail = c.get(f"/v1/games/{gid}").json()
        shown = detail["game"]["evaluation"]
        assert shown["provisional"] is True
        assert shown["p_actual_score"] == pytest.approx(detail["forecast"]["matrix"]["final"][h][a])
        assert detail["evaluation"] is None                       # nothing is stored yet
        assert all(v["evaluation"] is None for v in detail["versions"])

        # the provisional grade is not counted in the track record
        assert c.get("/v1/accuracy").json()["forward"]["final"]["n"] == len(published["upcoming"]) - 1

        # ...and the site is told the update is overdue
        fresh = daily.freshness()
        assert fresh["ungraded_games"] == 1 and fresh["ungraded_since"] == str(day)
        assert c.get("/health").json()["status"] == "degraded"
        assert c.get("/v1/meta").json()["freshness"]["ungraded_games"] == 1

        # never graded: a forecast published after puck drop, a tie, an unknown ending
        late = {**card, "evaluation": None, "forecast": {**f, "published_at": card["start_time_utc"]}}
        assert api.provisional_evaluation(late) is None
        assert api.provisional_evaluation({**card, "result": {"home_score": 2, "away_score": 2, "last_period_type": "REG"}}) is None
        assert api.provisional_evaluation({**card, "result": {"home_score": h, "away_score": a, "last_period_type": None}}) is None

        # the scoreboard is consulted for a week, then left alone
        asked.clear(); queries.clear_cache()
        monkeypatch.setattr(api, "today_et", lambda: day + dt.timedelta(days=api.LIVE_LOOKBACK_DAYS))
        assert c.get(f"/v1/games?date={day.isoformat()}").json()["games"] and asked == [day]
        asked.clear(); queries.clear_cache()
        monkeypatch.setattr(api, "today_et", lambda: day + dt.timedelta(days=api.LIVE_LOOKBACK_DAYS + 1))
        late_cards = {g["id"]: g for g in c.get(f"/v1/games?date={day.isoformat()}").json()["games"]}
        assert asked == [] and late_cards[gid]["result"] is None and late_cards[gid]["evaluation"] is None
    finally:
        with db.begin() as conn:   # the pipeline catches up
            conn.execute(db.games.update().where(db.games.c.id == gid).values(
                state="OFF", home_score=h, away_score=a, last_period_type=lpt,
                home_reg_goals=int(real.home_reg_goals), away_reg_goals=int(real.away_reg_goals)))
        assert settle() == n_versions
        queries.clear_cache()

    assert daily.freshness()["ungraded_games"] == 0
    monkeypatch.setattr(api, "today_et", lambda: day + dt.timedelta(days=1))
    settled = c.get(f"/v1/games/{gid}").json()["game"]["evaluation"]
    assert settled["provisional"] is False
    for k in ("winner_correct", "exact_score_hit"):
        assert settled[k] == shown[k]
    assert settled["log_loss"] == pytest.approx(shown["log_loss"])
    assert settled["p_actual_score"] == pytest.approx(shown["p_actual_score"])


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


# --- operations ---------------------------------------------------------------
def test_site_documents_are_stored_and_served(published):
    """The API serves the stored season summary instead of re-aggregating."""
    from puckiq.api import queries
    from puckiq.pipeline import daily
    n = daily.store_site_documents(2021)
    assert n >= 2
    doc = db.get_document("season_table:2021")
    assert doc and all(isinstance(k, str) for k in doc)
    queries.clear_cache()
    calls = []
    real = queries.compute_season_table
    queries.compute_season_table = lambda season: calls.append(season) or real(season)
    try:
        tab = queries.season_table(2021)
    finally:
        queries.compute_season_table = real
    assert not calls                       # came from the stored document
    assert set(tab) == {int(k) for k in doc} and all(isinstance(k, int) for k in tab)
    assert tab[6]["gp"] == doc["6"]["gp"]
    assert queries.goalie_season(2021)


def test_refresh_only_republishes_in_the_pregame_window(published):
    from puckiq.pipeline import daily
    gid = published["upcoming"][1]
    start = published["now"] + dt.timedelta(hours=2)
    with db.begin() as c:
        c.execute(db.games.update().values(state=sa.case((db.games.c.state == "PRE", "FUT"), else_=db.games.c.state)))
    assert daily.publish_needed(published["now"]) is False
    with db.begin() as c:
        c.execute(db.games.update().where(db.games.c.id == gid).values(state="PRE", start_time_utc=start))
    assert daily.publish_needed(published["now"]) is True            # game about to start
    assert daily.publish_needed(start + dt.timedelta(minutes=1)) is False   # puck has dropped


def test_copy_db_can_be_limited_to_named_tables(published, tmp_path):
    src = "sqlite:///" + str(tmp_path / "src.db").replace("\\", "/")
    e = sa.create_engine(src)
    db.metadata.create_all(e)
    with e.begin() as c:
        c.execute(db.players.insert().values(id=999001, full_name="Copied Skater", position="C"))
        c.execute(db.teams.insert().values(id=6, abbrev="XXX", name="Should not overwrite", franchise_key=6, active=False))
    e.dispose()
    counts = db.copy_from(src, tables=["players"])
    assert counts == {"players": 1}
    with db.begin() as c:
        assert c.execute(sa.select(db.players.c.full_name).where(db.players.c.id == 999001)).scalar() == "Copied Skater"
        assert c.execute(sa.select(db.teams.c.abbrev).where(db.teams.c.id == 6)).scalar() == "BOS"   # untouched


def test_forecast_version_is_stable_when_an_unused_statistic_changes(published):
    """Only inputs the model uses decide whether a new version is published."""
    am = pub.active_model()
    cols = am[1].glm.cols
    row = {k: v for k, v in published["feat"].iloc[-1].to_dict().items() if k[:2] in ("h_", "a_") or k in ("playoff", "lg_g")}
    goalies = {"home": [{"pid": 1, "prob": 1.0, "rating": 0.0}], "away": [{"pid": 2, "prob": 1.0, "rating": 0.0}]}
    from puckiq.models.forecaster import input_hash
    h = input_hash("v", row, goalies, cols)
    assert h == input_hash("v", dict(row, h_pp=row["h_pp"] + 1.0, a_xgs_l5=0.9), goalies, cols)   # not model inputs
    assert h != input_hash("v", dict(row, h_off_xga=row["h_off_xga"] + 0.1), goalies, cols)        # a model input
    assert h != input_hash("v", dict(row, a_elo=row["a_elo"] + 5), goalies, cols)


def test_lab_missing_skater_scenario(published, monkeypatch):
    """With a lineup-aware model, marking a skater out changes the forecast
    by exactly the model's lineup coefficient; without one it is refused."""
    import warnings
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

    from puckiq.api import main as api
    from puckiq.api import queries
    from puckiq.models.forecaster import GROUPS_LEAN
    gid = published["upcoming"][1]
    c = TestClient(api.app)
    queries.clear_cache()
    d = c.get(f"/v1/games/{gid}").json()
    assert d["lab"]["skaters"] is None       # the active test model has no lineup input
    assert any(u["control"] == "player_unavailable" for u in d["lab"]["unsupported"])
    snap = queries.snapshot_for(gid)
    top = snap["lineups"]["home"]["skaters"][0]
    assert c.post(f"/v1/games/{gid}/lab", json={"home_out": [top["pid"]]}).status_code == 422

    fc = Forecaster.train(published["feat"], GROUPS_LEAN + ["lineup_toi"])
    assert fc.version.startswith("lean-poisson-1.1") and "lu_toi" in fc.domain
    fc.domain["lu_toi"] = [-1e6, 1e6]        # the synthetic league has little lineup variation
    monkeypatch.setattr(api.pub, "active_model", lambda: (1, fc))
    queries.clear_cache()
    d = c.get(f"/v1/games/{gid}").json()
    assert [x["pid"] for x in d["lab"]["skaters"]["home"]][0] == top["pid"]
    assert not any(u["control"] == "player_unavailable" for u in d["lab"]["unsupported"])
    r = c.post(f"/v1/games/{gid}/lab", json={"home_out": [top["pid"]]})
    assert r.status_code == 200
    out = r.json()
    assert out["applied"]["skaters_out"]["home"] == [top["name"]]
    repl = snap["lineups"]["home"]["replacement"]["D" if top["defence"] else "F"]
    drop = max(0.0, top["toi"] - repl)
    assert drop > 0
    beta = dict(zip(fc.glm.cols, fc.glm.beta[1:]))
    assert out["applied"]["overrides"]["h_lu_toi"] == pytest.approx(snap["row"]["h_lu_toi"] - drop)
    # the scoring rates move by exactly the fitted lineup coefficients
    row = {k: (np.nan if v is None else v) for k, v in snap["row"].items() if k != "game_date"}
    a = fc.rates(row, 0.0, 0.0)
    b = fc.rates(row, 0.0, 0.0, {"h_lu_toi": snap["row"]["h_lu_toi"] - drop})
    assert np.log(b["lam_home"] / a["lam_home"]) == pytest.approx(-drop * beta["t_lu_toi"])
    assert np.log(b["lam_away"] / a["lam_away"]) == pytest.approx(-drop * beta["o_lu_toi"])
    assert c.post(f"/v1/games/{gid}/lab", json={"home_out": [123456789]}).status_code == 422       # not in the lineup
    assert c.post(f"/v1/games/{gid}/lab", json={"home_out": [1, 2, 3, 4]}).status_code == 422      # more than three
    fc.domain["lu_toi"] = [snap["row"]["h_lu_toi"] - drop / 2, 1e6]
    assert c.post(f"/v1/games/{gid}/lab", json={"home_out": [top["pid"]]}).status_code == 422      # outside validated range
    queries.clear_cache()


# --- market benchmark ------------------------------------------------------------
def _feed(game_id, home=-205.0, away=170.0, over=114.0, under=-135.0, stamp="2021-12-27T15:00:00Z", name="DraftKings"):
    return {"lastUpdatedUTC": stamp, "bettingPartner": {"name": name}, "games": [{
        "gameId": game_id,
        "homeTeam": {"odds": [{"description": "MONEY_LINE_3_WAY", "value": 330.0, "qualifier": "Draw"},
                              {"description": "MONEY_LINE_3_WAY", "value": -120.0, "qualifier": ""},
                              {"description": "OVER_UNDER", "value": over, "qualifier": "O6.5"},
                              {"description": "MONEY_LINE_2_WAY", "value": home, "qualifier": ""},
                              {"description": "PUCK_LINE", "value": 124.0, "qualifier": "-1.5"},
                              {"description": "SOMETHING_NEW", "value": 1.0, "qualifier": ""}]},
        "awayTeam": {"odds": [{"description": "MONEY_LINE_3_WAY", "value": 330.0, "qualifier": "Draw"},
                              {"description": "MONEY_LINE_3_WAY", "value": 220.0, "qualifier": ""},
                              {"description": "OVER_UNDER", "value": under, "qualifier": "U6.5"},
                              {"description": "MONEY_LINE_2_WAY", "value": away, "qualifier": ""},
                              {"description": "PUCK_LINE", "value": -148.0, "qualifier": "+1.5"}]}}]}


def test_odds_conversion_and_parsing():
    from puckiq import market
    assert market.implied(100) == pytest.approx(0.5) and market.implied(-200) == pytest.approx(2 / 3) and market.implied(150) == pytest.approx(0.4)
    assert market.no_vig(-110, -110) == pytest.approx(0.5)
    assert market.no_vig(-205, 170) + market.no_vig(170, -205) == pytest.approx(1.0)
    assert market.no_vig(-205, 170) < market.implied(-205)          # removing the margin lowers the favourite's figure
    provider, stamp, rows = market.parse_partner_games(_feed(7))
    assert provider == "draftkings" and stamp == dt.datetime(2021, 12, 27, 15, 0)
    key = {(r["market"], r["side"]): r for r in rows}
    assert len(rows) == 9                                           # the draw price appears once; unknown markets are ignored
    assert key[("ML2", "home")]["american"] == -205 and key[("ML2", "away")]["american"] == 170
    assert key[("OU", "over")]["line"] == 6.5 and key[("OU", "under")]["american"] == -135
    assert key[("PL", "home")]["line"] == -1.5 and key[("PL", "away")]["line"] == 1.5
    assert ("ML3", "draw") in key


def test_odds_are_recorded_once_and_only_before_puck_drop(published, monkeypatch):
    from puckiq import market
    gid = published["upcoming"][1]            # still unplayed, starts two hours after the fixture's "now"
    start = published["now"] + dt.timedelta(hours=2)

    class Fake:
        def get_json(self, url, params=None):
            return _feed(gid) if url.endswith("/US/now") else _feed(gid, name="FanDuel", home=-210.0, away=172.0)
    monkeypatch.setattr(market, "client", lambda: Fake())
    with db.begin() as c:
        c.execute(db.market_odds.delete())
    assert market.ingest(now=published["now"]) == 18              # two providers x nine rows
    assert market.ingest(now=published["now"] + dt.timedelta(minutes=5)) == 0   # same feed timestamp: nothing new
    assert market.ingest(now=start + dt.timedelta(minutes=1)) == 0             # the game has started
    with db.begin() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(db.market_odds)).scalar() == 18


def test_market_benchmark_scores_both_on_the_same_games(published):
    """A market that priced every winner at 90% must beat any honest forecast."""
    from puckiq import market
    with db.begin() as c:
        c.execute(db.market_odds.delete())
        done = c.execute(sa.select(db.games.c.id, db.games.c.home_score, db.games.c.away_score, db.games.c.start_time_utc)
                         .join(db.evaluations, db.evaluations.c.game_id == db.games.c.id)
                         .where(db.games.c.home_score.isnot(None)).distinct()).fetchall()
        assert len(done) >= 2
        rows = []
        for g in done:
            home_won = g.home_score > g.away_score
            when = g.start_time_utc - dt.timedelta(minutes=20)
            for side, price in (("home", -900.0 if home_won else 900.0), ("away", 900.0 if home_won else -900.0)):
                rows.append(dict(game_id=g.id, provider="testbook", market="ML2", side=side, line=None, american=price, observed_at=when, source_updated_at=when))
            over = g.home_score + g.away_score > 5.5
            for side, price in (("over", -300.0 if over else 300.0), ("under", 300.0 if over else -300.0)):
                rows.append(dict(game_id=g.id, provider="testbook", market="OU", side=side, line=5.5, american=price, observed_at=when, source_updated_at=when))
        # a later, post-game price must be ignored
        g = done[0]
        rows.append(dict(game_id=g.id, provider="testbook", market="ML2", side="home", line=None, american=100.0,
                         observed_at=g.start_time_utc + dt.timedelta(hours=1), source_updated_at=g.start_time_utc + dt.timedelta(hours=1)))
        c.execute(db.market_odds.insert(), rows)
    b = market.benchmark()
    blk = b["latest"]
    assert b["providers"] == ["testbook"] and blk["n"] == len(done)
    assert blk["market"]["accuracy"] == 1.0
    assert blk["market"]["log_loss"] == pytest.approx(-np.log(0.9), abs=1e-6)
    assert blk["log_loss_diff"] > 0                                 # positive: the market was better
    assert blk["median_minutes_before"] == pytest.approx(20.0)
    assert blk["totals"]["n"] == len(done) and blk["totals"]["market_brier"] == pytest.approx(0.0625)
    assert 0 <= blk["totals"]["puckiq_brier"] <= 1
    assert b["first"]["n"] == len(done)
