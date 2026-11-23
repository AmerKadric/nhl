import numpy as np
import pandas as pd
import pytest

from puckiq import config
from puckiq.backtest import metrics as M
from puckiq.backtest import walkforward
from puckiq.features.build import FeatureBuilder
from puckiq.features.params import FeatureParams
from puckiq.models.design import team_rows
from puckiq.models.forecaster import GROUPS_LEAN, Forecaster, confidence_label, input_hash
from puckiq.models.rates import GLMRateModel
from puckiq.sim.simulator import SimParams


@pytest.fixture(scope="module")
def feat(frames):
    return FeatureBuilder(frames, FeatureParams()).run()


@pytest.fixture(scope="module")
def forecaster(feat, monkeypatch_module):
    monkeypatch_module.setattr(config, "FIRST_MODEL_SEASON", 2020)
    return Forecaster.train(feat)


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def _row(feat, i=-1):
    r = feat.iloc[i].to_dict()
    return {k: v for k, v in r.items() if k[:2] in ("h_", "a_") or k in ("playoff", "lg_g")}


GOALIES = {"home": [{"pid": 1, "name": "A", "prob": 0.7, "rating": 0.02}, {"pid": 2, "name": "B", "prob": 0.3, "rating": -0.03}],
           "away": [{"pid": 3, "name": "C", "prob": 1.0, "rating": 0.0}]}


def test_team_rows_shape_and_targets(feat):
    rows = team_rows(feat, "confirmed")
    assert len(rows) == 2 * len(feat)
    h = rows[rows["is_home"] == 1].set_index("game_id")
    np.testing.assert_array_equal(h["y"].values, feat["home_reg_goals"].astype(float).values)
    np.testing.assert_allclose(h["elo_diff"].values, (feat["h_elo"] - feat["a_elo"]).values / 100)


def test_projected_rows_never_use_actual_starter(feat):
    proj = team_rows(feat, "projected")
    h = proj[proj["is_home"] == 1]
    np.testing.assert_allclose(h["o_gk"].values, feat["a_gk_proj"].values)


def test_glm_contributions_are_exactly_additive(feat):
    rows = team_rows(feat, "confirmed")
    m = GLMRateModel(GROUPS_LEAN).fit(rows)
    c = m.contributions(rows.tail(50))
    np.testing.assert_allclose(m.baseline_log_rate() + c.sum(axis=1).values, m.log_rate(rows.tail(50)), atol=1e-10)
    assert (m.log_rate_sd(rows.tail(50)) > 0).all()


def test_glm_serialisation_roundtrip(feat):
    rows = team_rows(feat, "confirmed")
    m = GLMRateModel(GROUPS_LEAN).fit(rows)
    m2 = GLMRateModel.from_json(m.to_json())
    np.testing.assert_allclose(m.predict(rows.tail(20)), m2.predict(rows.tail(20)))


def test_forecast_is_coherent_and_reproducible(forecaster, feat):
    row = _row(feat)
    a = forecaster.forecast(row, GOALIES, n_sims=20000, seed=11)
    b = forecaster.forecast(row, GOALIES, n_sims=20000, seed=11)
    s = a["sim"]
    assert s.p_home_win == b["sim"].p_home_win
    np.testing.assert_array_equal(s.final, b["sim"].final)
    assert s.final.sum() + s.overflow == pytest.approx(1.0, abs=1e-9)
    assert s.p_home_reg + s.p_tie_reg + s.p_away_reg == pytest.approx(1.0, abs=1e-9)
    assert s.p_overtime + s.p_shootout == pytest.approx(s.p_tie_reg, abs=1e-9)
    assert abs(s.p_home_win - a["exact"].p_home_win) < 0.02
    assert len(a["scenarios"]) == 2 and sum(x["weight"] for x in a["scenarios"]) == pytest.approx(1.0)
    assert {d["key"] for d in a["drivers"]} == set(GROUPS_LEAN)


def test_opposing_goalie_moves_only_the_shooting_teams_rate(forecaster, feat):
    """The away goalie's rating changes the home rate by exactly the fitted
    coefficient and leaves the away rate untouched. (The sign is learned from
    data, so it is not asserted on this synthetic league.)"""
    row = _row(feat)
    weak = forecaster.rates(row, 0.0, -0.08)
    strong = forecaster.rates(row, 0.0, 0.08)
    beta = dict(zip(forecaster.glm.cols, forecaster.glm.beta[1:]))["o_gk"]
    assert np.log(strong["lam_home"] / weak["lam_home"]) == pytest.approx(beta * 0.16)
    assert strong["lam_away"] == pytest.approx(weak["lam_away"])


def test_forecaster_roundtrip(forecaster, feat, tmp_path):
    p = forecaster.save(tmp_path / "m.json")
    f2 = Forecaster.load(p)
    row = _row(feat)
    assert f2.rates(row, 0.0, 0.0)["lam_home"] == pytest.approx(forecaster.rates(row, 0.0, 0.0)["lam_home"])
    assert f2.version == forecaster.version


def test_input_hash_is_stable_and_sensitive(feat):
    row = _row(feat)
    h1 = input_hash("v1", row, GOALIES)
    assert h1 == input_hash("v1", dict(reversed(list(row.items()))), GOALIES)
    assert h1 != input_hash("v2", row, GOALIES)
    changed = dict(row, h_b2b=1.0 - row["h_b2b"])
    assert h1 != input_hash("v1", changed, GOALIES)
    conf = {"home": [dict(GOALIES["home"][0], prob=1.0)], "away": GOALIES["away"]}
    assert h1 != input_hash("v1", row, conf)


def test_confidence_labels():
    assert confidence_label(0.51) == "Toss-up"
    assert confidence_label(0.72) == "Strong favourite"


def test_metrics_on_known_values():
    sp = SimParams(theta=1.4)
    p = M.game_predictions(np.array([3.5, 2.5]), np.array([2.5, 3.5]), sp, np.array([False, False]), [4, 1], [2, 3])
    s = M.add_scores(p, [4, 1], [2, 3], [0, 0])
    assert s["correct"].tolist() == [1.0, 1.0]
    assert (s["p_actual_score"] > 0).all() and (s["p_actual_score"] < 0.2).all()
    assert s["p_home_win"].iloc[0] == pytest.approx(1 - s["p_home_win"].iloc[1], abs=0.03)
    out = M.summarise(s)
    assert out["accuracy"] == 1.0 and out["log_loss"] < np.log(2)
    worse = M.paired_bootstrap(s["ll"].values + 0.1, s["ll"].values)
    assert worse["mean_diff"] == pytest.approx(0.1) and worse["significant"]


def test_calibration_bins_cover_all_games():
    df = pd.DataFrame({"p_home_win": np.linspace(0.05, 0.95, 200), "home_won": (np.arange(200) % 2).astype(float)})
    assert sum(b["n"] for b in M.calibration(df)) == 200


def test_walkforward_never_trains_on_test_season(feat, monkeypatch):
    """Train rows for test season S must all come from seasons before S."""
    monkeypatch.setattr(config, "FIRST_MODEL_SEASON", 2020)
    monkeypatch.setattr(config, "FIRST_TEST_SEASON", 2021)
    seen = []
    orig = GLMRateModel.fit

    def spy(self, rows):
        seen.append(int(rows["season"].max()))
        return orig(self, rows)
    monkeypatch.setattr(GLMRateModel, "fit", spy)
    out = walkforward.run(feat, with_gbm=False, persist=False)
    assert seen and max(seen) == 2020
    assert out["summary"]["test_seasons"] == [2021]
    assert set(out["preds"]["xg_lean"]["season"]) == {2021}


# --- plain-English summaries ---------------------------------------------------
def _d(key, effect):
    return {"key": key, "home_win_prob_effect": effect}


def test_headline_names_only_factors_that_matter():
    from puckiq.explain import headline
    drivers = [_d("env", 0.2), _d("xga", 0.053), _d("home", 0.036), _d("lineup_toi", 0.022), _d("goalie", 0.001), _d("rest", -0.015)]
    assert headline(drivers, "NJD", "CBJ", 0.644) == "NJD favoured, mainly on chance quality and home ice. CBJ's best argument is rest and travel."
    # the same game seen from the other side: the away team is the favourite
    flipped = [dict(d, home_win_prob_effect=-d["home_win_prob_effect"]) for d in drivers]
    assert headline(flipped, "CBJ", "NJD", 0.356).startswith("NJD favoured, mainly on chance quality and home ice.")
    # the league scoring environment is never presented as a matchup factor
    assert "environment" not in headline(drivers, "NJD", "CBJ", 0.644)
    # a factor worth under half a point is not mentioned
    assert "goaltending" not in headline(drivers, "NJD", "CBJ", 0.644)


def test_headline_strength_wording_and_edge_cases():
    from puckiq.explain import headline
    assert headline([_d("xga", 0.12), _d("home", 0.04)], "CAR", "VAN", 0.78).startswith("CAR strongly favoured")
    assert headline([_d("home", 0.035), _d("xga", 0.01)], "BOS", "WSH", 0.55) == "Slight edge to BOS, mainly on home ice and chance quality."
    assert headline([_d("home", 0.03), _d("xga", -0.02)], "TOR", "CBJ", 0.51) == "Close to even. Home ice tilts it slightly toward TOR. CBJ's best argument is chance quality."
    assert headline([_d("home", 0.001)], "TOR", "CBJ", 0.5) == "Close to even: nothing separates these teams."
    assert headline([_d("goalie", 0.004)], "DAL", "STL", 0.62) == "DAL favoured."        # favoured, but no single factor is worth naming
    assert headline(None, "A", "B", 0.6) is None and headline([], "A", "B", 0.6) is None
    # two groups with the same wording are not repeated
    assert headline([_d("lineup_toi", 0.03), _d("lineup_gs", 0.02), _d("home", 0.01)], "MIN", "CHI", 0.66) == "MIN favoured, mainly on a healthier lineup and home ice."
