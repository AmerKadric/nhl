"""Temporal-leakage and consistency tests for the feature pipeline."""
import numpy as np
import pandas as pd
import pytest

from puckiq import tune
from puckiq.features.build import METRICS, FeatureBuilder, Frames, League, starter_probabilities
from puckiq.features.params import FeatureParams

FEATURE_PREFIXES = ("h_", "a_", "lg_")


def _feature_cols(df: pd.DataFrame) -> list[str]:
    # the actual starter's identity/rating is by definition game-day info
    skip = ("_gk_pid", "_gk", "_gk_recent", "_gk_n", "_gk_idle", "_lu_gs_conf", "_lu_toi_conf")
    return [c for c in df.columns if c.startswith(FEATURE_PREFIXES) and not c.endswith(skip)]


def _truncate(fr: Frames, cut_id: int) -> Frames:
    """Pretend every game from `cut_id` onward has not been played yet."""
    g = fr.games.copy()
    later = g["id"] >= cut_id
    for c in ("home_score", "away_score", "home_reg_goals", "away_reg_goals", "last_period_type"):
        g[c] = g[c].astype(object)
        g.loc[later, c] = None
    g.loc[later, "state"] = "FUT"
    return Frames(g, fr.tgs[fr.tgs["game_id"] < cut_id], fr.ggs[fr.ggs["game_id"] < cut_id], fr.teams, fr.players,
                  fr.sgs[fr.sgs["game_id"] < cut_id])


@pytest.fixture(scope="module")
def full(frames):
    return FeatureBuilder(frames, FeatureParams()).run().set_index("game_id")


@pytest.mark.parametrize("q", [0.2, 0.5, 0.75, 0.95])
def test_features_do_not_depend_on_the_future(frames, full, q):
    """Features for a game must be identical whether or not that game and
    all later games have been played."""
    ids = sorted(frames.games["id"])
    cut = ids[int(len(ids) * q)]
    part = FeatureBuilder(_truncate(frames, cut), FeatureParams()).run().set_index("game_id")
    cols = _feature_cols(full)
    a, b = full.loc[cut, cols].astype(float), part.loc[cut, cols].astype(float)
    np.testing.assert_allclose(a.values, b.values, rtol=0, atol=1e-12, equal_nan=True)
    # ...and all earlier games are unchanged too
    earlier = [i for i in ids if i < cut]
    np.testing.assert_allclose(full.loc[earlier, cols].astype(float).values, part.loc[earlier, cols].astype(float).values,
                               atol=1e-12, equal_nan=True)


def test_features_do_not_depend_on_same_day_games(frames, full):
    """A game's features must not change when OTHER games on the same date
    have or have not been played (no intra-day look-ahead)."""
    g = frames.games
    counts = g.groupby("game_date")["id"].count()
    busy = [d for d in counts[counts >= 2].index][len(counts[counts >= 2]) // 2:][:5]
    cols = _feature_cols(full)
    for date in busy:
        cut = int(g.loc[g["game_date"] == date, "id"].min())
        assert (g.loc[g["id"] >= cut, "game_date"] >= date).all()
        part = FeatureBuilder(_truncate(frames, cut), FeatureParams()).run().set_index("game_id")
        day = list(g.loc[g["game_date"] == date, "id"])
        np.testing.assert_allclose(full.loc[day, cols].astype(float).values, part.loc[day, cols].astype(float).values,
                                   rtol=0, atol=1e-12, equal_nan=True)


def test_features_ignore_the_games_own_result(frames, full):
    """Changing a game's score must not change that game's features."""
    ids = sorted(frames.games["id"])
    target = ids[len(ids) // 2]
    g = frames.games.copy()
    m = g["id"] == target
    g.loc[m, ["home_score", "away_score", "home_reg_goals", "away_reg_goals"]] = [9, 0, 9, 0]
    alt = FeatureBuilder(Frames(g, frames.tgs, frames.ggs, frames.teams, frames.players, frames.sgs), FeatureParams()).run().set_index("game_id")
    cols = _feature_cols(full)
    np.testing.assert_allclose(full.loc[target, cols].astype(float).values, alt.loc[target, cols].astype(float).values, atol=1e-12, equal_nan=True)
    # but the next games of those teams do change
    nxt = [i for i in ids if i > target][:40]
    assert not np.allclose(full.loc[nxt, cols].astype(float).values, alt.loc[nxt, cols].astype(float).values, equal_nan=True)


def test_first_game_has_no_history(full):
    first = full.iloc[0]
    assert first["h_elo"] == 1500.0 and first["a_elo"] == 1500.0
    assert first["h_off_g"] == 0.0 and first["h_gp"] == 0


def test_season_rollover_regresses_ratings(frames):
    p = FeatureParams()
    fb = FeatureBuilder(frames, p)
    feat = fb.run()
    s2 = feat[feat["season"] == 2021].iloc[0]
    s1_last = feat[(feat["season"] == 2020) & ((feat["home_team_id"] == s2["home_team_id"]) | (feat["away_team_id"] == s2["home_team_id"]))].iloc[-1]
    assert s2["h_gp"] == 0
    assert abs(s2["h_elo"] - 1500) <= abs(1500 - 1500) + 200   # sane
    assert s2["h_poff_g"] > s1_last["h_poff_g" if s1_last["home_team_id"] == s2["home_team_id"] else "a_poff_g"] * p.kf["g"].carry ** 2


def test_rest_features(full):
    assert set(full["h_b2b"].unique()) <= {0.0, 1.0}
    assert ((full["h_rest"] >= 1) & (full["h_rest"] <= 6)).all()
    assert (full.loc[full["h_b2b"] == 1, "h_rest"] == 1).all()
    assert (full["h_g7"] >= 0).all() and (full["h_g7"] <= 7).all()


def test_starter_probabilities_sum_to_one(frames):
    fb = FeatureBuilder(frames, FeatureParams())
    fb.run()
    c = fb.cands
    pr = starter_probabilities(c, FeatureParams().starter_w)
    sums = pr.groupby([c["game_id"], c["side"]]).sum()
    np.testing.assert_allclose(sums.values, 1.0, atol=1e-12)
    assert (pr > 0).all()


def test_tuner_replicates_builder_likelihood(frames):
    """The stand-alone tuning loops must be the same maths as the builder."""
    p = FeatureParams()
    fb = FeatureBuilder(frames, p)
    fb.run()
    arr = tune.metric_arrays(frames, 2100)
    lg = League(p.league_hl_games)
    for m in METRICS:
        ll, n = tune.kf_loglik(arr[m], p.kf[m], lg.mu[m], lg.hfa[m], lg.a, arr["n"])
        assert n == fb.kf_ll[m][1]
        assert ll == pytest.approx(fb.kf_ll[m][0], rel=1e-10)
    assert tune.elo_loglik(arr["elo"], p.elo_k, p.elo_hfa, p.elo_carry, arr["n"], 0) == pytest.approx(fb.elo_ll[0] / fb.elo_ll[1], rel=1e-10)


def test_lineup_features_respond_to_absences(frames):
    """Removing a team's heaviest-used skaters from its latest game lowers
    that team's lineup strength for its NEXT game, and nothing earlier."""
    fb = FeatureBuilder(frames, FeatureParams())
    base = fb.run().set_index("game_id")
    assert {"h_lu_gs", "a_lu_toi", "h_lu_gs_conf"} <= set(base.columns)
    g = frames.games.sort_values(["game_date", "id"])
    team = 6
    mine = g[(g["home_team_id"] == team) | (g["away_team_id"] == team)]
    prev, nxt = mine.iloc[-2], mine.iloc[-1]
    s = frames.sgs
    top = (s[(s["game_id"] == prev["id"]) & (s["team_id"] == team)].sort_values("toi", ascending=False)["player_id"].head(4))
    cut = s[~((s["game_id"] == prev["id"]) & s["player_id"].isin(top))]
    alt = FeatureBuilder(Frames(frames.games, frames.tgs, frames.ggs, frames.teams, frames.players, cut), FeatureParams()).run().set_index("game_id")
    side = "h" if nxt["home_team_id"] == team else "a"
    assert alt.loc[nxt["id"], f"{side}_lu_toi"] < base.loc[nxt["id"], f"{side}_lu_toi"] - 1.0
    earlier = [i for i in base.index if i < prev["id"]]
    cols = [c for c in base.columns if "_lu_" in c and not c.endswith("_conf")]
    np.testing.assert_allclose(base.loc[earlier, cols].astype(float).values, alt.loc[earlier, cols].astype(float).values, atol=1e-12)


def test_healthy_unchanged_lineup_scores_zero():
    """A team that always dresses the same skaters has no lineup deviation."""
    import datetime as dt

    from puckiq.features.lineups import LineupBuilder
    games = pd.DataFrame([dict(id=2020020000 + i, game_date=dt.date(2020, 10, 1) + dt.timedelta(days=2 * i),
                               start_time_utc=pd.Timestamp("2020-10-01") + pd.Timedelta(days=2 * i),
                               home_team_id=1, away_team_id=2) for i in range(12)])
    rows = []
    for g in games.itertuples():
        for tid in (1, 2):
            for k in range(18):
                rows.append(dict(game_id=g.id, team_id=tid, player_id=tid * 100 + k, position="D" if k >= 12 else "C",
                                 toi=900.0, goals=0, assists=0, shots=2, plus_minus=0, pim=0))
    out = LineupBuilder(games, pd.DataFrame(rows), {1: 1, 2: 2}).run().set_index("game_id")
    last = out.iloc[-1]
    assert abs(last["h_lu_toi"]) < 1e-9 and abs(last["a_lu_toi"]) < 1e-9
    assert abs(last["h_lu_gs"]) < 1e-9 and abs(last["a_lu_gs"]) < 1e-9
