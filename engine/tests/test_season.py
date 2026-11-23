import numpy as np
import pandas as pd
import pytest

from puckiq.sim import season as S
from puckiq.sim.simulator import SimParams, exact_forecast

SP = SimParams(theta=1.45, ot_home_intercept=-0.05, ot_strength_slope=1.1, p_shootout=0.34, so_strength_slope=0.2)


def league(strength=None, seed=0, games_per_pair=2, points=None) -> S.SeasonInputs:
    """24 teams: two conferences of two six-team divisions."""
    n = 24
    conf = ["East"] * 12 + ["West"] * 12
    div = ["A"] * 6 + ["B"] * 6 + ["C"] * 6 + ["D"] * 6
    strength = np.zeros(n) if strength is None else np.asarray(strength, float)
    home, away = [], []
    for i in range(n):
        for j in range(n):
            if i != j:
                home += [i] * (games_per_pair // 2 or 1)
                away += [j] * (games_per_pair // 2 or 1)
    home, away = np.array(home), np.array(away)
    base = np.log(3.0)
    llh = base + 0.04 + strength[:, None] - strength[None, :] * 0 + np.zeros((n, n))
    llh = base + 0.04 + 0.5 * (strength[:, None] - strength[None, :])
    lla = base - 0.5 * (strength[:, None] - strength[None, :])
    z = np.zeros(n)
    return S.SeasonInputs(
        team_ids=list(range(100, 100 + n)), conference=conf, division=div,
        gp=z.copy(), pts=z.copy() if points is None else np.asarray(points, float), rw=z.copy(), row=z.copy(), w=z.copy(),
        l=z.copy(), otl=z.copy(), gf=z.copy(), ga=z.copy(), home=home, away=away,
        lam_home=np.exp(llh[home, away]), lam_away=np.exp(lla[home, away]),
        sd_off=np.full(n, 0.03), sd_def=np.full(n, 0.03), log_lam_home=llh, log_lam_away=lla, sp=SP)


def test_series_probability_known_values():
    assert S.series_prob(np.array([0.5]), np.array([0.5]))[0] == pytest.approx(0.5)
    # equal per-game probability 0.6 everywhere: closed form for a best-of-seven
    from math import comb
    exact = sum(comb(3 + k, k) * 0.6 ** 4 * 0.4 ** k for k in range(4))
    assert S.series_prob(np.array([0.6]), np.array([0.6]))[0] == pytest.approx(exact)
    # a team that always wins at home and never on the road wins in seven with home ice
    assert S.series_prob(np.array([1.0]), np.array([0.0]))[0] == pytest.approx(1.0)
    p = S.series_prob(np.array([0.7, 0.55]), np.array([0.5, 0.45]))
    assert p[0] > p[1] and (p > 0).all() and (p < 1).all()


def test_series_requires_four_wins():
    """Never at home, always on the road: only three road games exist, so the series is lost."""
    assert S.series_prob(np.array([0.0]), np.array([1.0]))[0] == pytest.approx(0.0)


def test_interpolated_win_probability_matches_exact():
    xs, grid = S.win_prob_grid(SP, playoff=True)
    for lh, la in ((3.2, 2.6), (2.4, 3.5), (4.1, 2.0), (2.95, 3.05)):
        exact = exact_forecast(lh, la, SP, playoff=True).p_home_win
        got = S.interp_win(xs, grid, np.array([np.log(lh)]), np.array([np.log(la)]))[0]
        assert got == pytest.approx(exact, abs=0.004)


def test_probabilities_are_coherent():
    out = S.simulate(league(strength=np.linspace(-0.2, 0.2, 24)), n_sims=3000, seed=4)
    assert out["p_playoffs"].sum() == pytest.approx(16.0)
    assert out["p_round2"].sum() == pytest.approx(8.0)
    assert out["p_conf_final"].sum() == pytest.approx(4.0)
    assert out["p_final"].sum() == pytest.approx(2.0)
    assert out["p_cup"].sum() == pytest.approx(1.0)
    assert out.groupby("division")["p_division"].sum().to_numpy() == pytest.approx(np.ones(4))
    # each stage is at most as likely as the one before it
    for a, b in (("p_playoffs", "p_round2"), ("p_round2", "p_conf_final"), ("p_conf_final", "p_final"), ("p_final", "p_cup")):
        assert (out[a] >= out[b] - 1e-12).all()
    assert (out["p_division"] <= out["p_playoffs"] + 1e-12).all()
    assert (out["points_p10"] <= out["points_p50"]).all() and (out["points_p50"] <= out["points_p90"]).all()


def test_stronger_teams_get_better_odds():
    strength = np.zeros(24)
    strength[0], strength[1] = 0.35, -0.35
    out = S.simulate(league(strength=strength), n_sims=3000, seed=5).set_index("team_id")
    assert out.loc[100, "p_playoffs"] > 0.9 > 0.3 > out.loc[101, "p_playoffs"]
    assert out.loc[100, "proj_points"] > out.loc[102, "proj_points"] > out.loc[101, "proj_points"]
    assert out.loc[100, "p_cup"] == out["p_cup"].max()


def test_a_clinched_lead_is_certain_and_seed_is_reproducible():
    pts = np.zeros(24)
    pts[3] = 500.0   # more points than anyone else can reach
    inp = league(points=pts)
    a, b, c = S.simulate(inp, 1500, seed=9), S.simulate(inp, 1500, seed=9), S.simulate(inp, 1500, seed=10)
    assert a.loc[3, "p_playoffs"] == 1.0 and a.loc[3, "p_division"] == 1.0
    pd.testing.assert_frame_equal(a, b)
    assert not a["p_cup"].equals(c["p_cup"])


def test_more_strength_uncertainty_pulls_odds_toward_the_middle():
    strength = np.linspace(-0.15, 0.15, 24)
    tight, loose = league(strength=strength), league(strength=strength)
    loose.sd_off, loose.sd_def = loose.sd_off * 6, loose.sd_def * 6
    a, b = S.simulate(tight, 3000, seed=2), S.simulate(loose, 3000, seed=2)
    spread = lambda o: (o["points_p90"] - o["points_p10"]).mean()
    assert spread(b) > spread(a)
    assert (b["p_playoffs"] - 0.5).abs().mean() < (a["p_playoffs"] - 0.5).abs().mean()


def test_current_standings_follow_nhl_points_rules():
    games = pd.DataFrame([
        dict(home_team_id=1, away_team_id=2, home_score=4, away_score=2, last_period_type="REG"),
        dict(home_team_id=2, away_team_id=1, home_score=3, away_score=2, last_period_type="OT"),
        dict(home_team_id=1, away_team_id=3, home_score=2, away_score=3, last_period_type="SO"),
    ])
    st = S.current_standings(games, [1, 2, 3])
    assert st["pts"].tolist() == [4.0, 2.0, 2.0]      # team 1: win, OT loss, SO loss
    assert st["rw"].tolist() == [1.0, 0.0, 0.0]
    assert st["row"].tolist() == [1.0, 1.0, 0.0]      # a shootout win is not a regulation-or-overtime win
    assert st["w"].tolist() == [1.0, 1.0, 1.0]
    assert st["otl"].tolist() == [2.0, 0.0, 0.0] and st["l"].tolist() == [0.0, 1.0, 0.0]
    assert st["gp"].tolist() == [3.0, 2.0, 1.0]


def test_tracking_a_game_does_not_change_the_odds():
    inp = league(strength=np.linspace(-0.15, 0.15, 24))
    plain = S.simulate(inp, 2000, seed=11)
    tracked, detail = S.simulate_detail(inp, 2000, seed=11, track=np.array([0, 5, 40]))
    pd.testing.assert_frame_equal(plain, tracked)
    assert detail["made_win"].shape == (3, 24)
    assert S.simulate_detail(inp, 500, seed=1)[1] is None and S.leverage(inp, None) == []


def test_leverage_is_coherent_and_a_win_helps():
    """Late in a tight race: every team level on points with two games left
    against each opponent's neighbour, so one result moves the odds a lot."""
    n = 24
    inp = league(points=np.full(n, 80.0))
    keep = np.arange(0, len(inp.home), 12)                     # thin the schedule: few games left, each matters
    inp.home, inp.away, inp.lam_home, inp.lam_away = inp.home[keep], inp.away[keep], inp.lam_home[keep], inp.lam_away[keep]
    inp.game_ids = 9000 + np.arange(len(keep))
    inp.game_dates = np.array([0] * 3 + [1] * (len(keep) - 3))
    track = S.next_slate(inp)
    assert list(track) == [0, 1, 2]
    out, detail = S.simulate_detail(inp, 12000, seed=3, track=track)
    lev = S.leverage(inp, detail, adjusted=False)
    assert [g["game_id"] for g in lev] == [9000, 9001, 9002]
    p = out.set_index("team_id")["p_playoffs"]
    for g in lev:
        for side, p_win in (("home", g["p_home_win"]), ("away", 1 - g["p_home_win"])):
            x = g[side]
            # the two conditional odds average back to the team's overall odds
            assert p_win * x["if_win"] + (1 - p_win) * x["if_loss"] == pytest.approx(x["now"], abs=1e-9)
            assert x["now"] == pytest.approx(p[x["team_id"]], abs=1e-9)
            assert x["swing"] > 4 * x["se"] > 0                # winning clearly helps
            assert 0 <= x["if_loss"] < x["if_win"] <= 1
        assert 0.4 < g["p_home_win"] < 0.7


def test_adjusted_leverage_removes_the_strength_guess():
    """With very uncertain team strength, the seasons a team wins tonight are
    the seasons it was drawn strong, so the raw split is worth more than the
    two points alone. The adjusted split must be smaller, still positive, and
    equal to the raw one when strength is known exactly."""
    n = 24

    def run(sd):
        inp = league(points=np.full(n, 40.0))
        inp.sd_off, inp.sd_def = np.full(n, sd), np.full(n, sd)
        inp.game_ids = np.arange(len(inp.home))
        inp.game_dates = np.array([0] + [1] * (len(inp.home) - 1))
        _, detail = S.simulate_detail(inp, 12000, seed=8, track=S.next_slate(inp))
        return S.leverage(inp, detail, adjusted=False)[0], S.leverage(inp, detail, adjusted=True)[0], detail

    raw, adj, detail = run(0.15)
    for side in ("home", "away"):
        assert raw[side]["swing"] > adj[side]["swing"] + 3 * raw[side]["se"]
        assert adj[side]["swing"] > 0
    # the weights are an unbiased split: each group's total weight is the number of simulated seasons
    assert detail["w_win"][0] == pytest.approx(12000, rel=0.03) and detail["w_loss"][0] == pytest.approx(12000, rel=0.03)
    raw0, adj0, _ = run(0.0)
    for side in ("home", "away"):
        assert adj0[side]["swing"] == pytest.approx(raw0[side]["swing"], abs=1e-6)
