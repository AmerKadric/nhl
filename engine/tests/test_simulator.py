import numpy as np
import pytest

from puckiq.ingest.nhl import _period_type, regulation_goals
from puckiq.sim.simulator import SimParams, batch_matrices, exact_forecast, fit_sim_params, simulate

SP = SimParams(theta=1.45, dispersion=0.0, ot_home_intercept=-0.05, ot_strength_slope=1.1, p_shootout=0.34, so_strength_slope=0.2)


@pytest.mark.parametrize("lh,la", [(3.1, 2.7), (2.2, 3.9), (4.5, 1.8)])
def test_probabilities_are_normalised(lh, la):
    f = exact_forecast(lh, la, SP)
    # the 0..12 grid deliberately leaves a small, tracked tail outside it
    assert 0.999 < f.regulation.sum() <= 1.0
    assert f.final.sum() + f.overflow == pytest.approx(1.0, abs=1e-9)
    assert f.p_home_reg + f.p_tie_reg + f.p_away_reg == pytest.approx(f.regulation.sum(), abs=1e-12)
    assert sum(f.totals()) == pytest.approx(f.final.sum(), abs=1e-12)
    assert sum(f.diffs().values()) == pytest.approx(f.final.sum(), abs=1e-12)
    assert 0 < f.p_home_win < 1


def test_final_score_is_never_a_tie():
    f = exact_forecast(3.0, 3.0, SP)
    assert np.all(np.diag(f.final) == 0.0)
    assert f.most_likely[0] != f.most_likely[1]


def test_overtime_and_shootout_add_exactly_one_goal():
    """All regulation-tie mass must land on one-goal final margins."""
    f = exact_forecast(2.9, 3.2, SP)
    n = f.final.shape[0]
    one_goal_final = sum(f.final[i, j] for i in range(n) for j in range(n) if abs(i - j) == 1)
    one_goal_reg = sum(f.regulation[i, j] for i in range(n) for j in range(n) if abs(i - j) == 1)
    assert one_goal_final - one_goal_reg == pytest.approx(f.p_tie_reg - f.regulation[n - 1, n - 1], abs=1e-9)
    assert f.p_overtime + f.p_shootout == pytest.approx(f.p_tie_reg, abs=1e-12)


def test_playoffs_have_no_shootout():
    f = exact_forecast(3.0, 2.8, SP, playoff=True)
    assert f.p_shootout == 0.0
    assert f.p_overtime == pytest.approx(f.p_tie_reg)
    s = simulate([{"weight": 1, "lam_home": 3.0, "lam_away": 2.8}], SP, playoff=True, n_sims=5000, seed=1)
    assert s.p_shootout == 0.0


def test_most_likely_score_is_not_rounded_expected_goals():
    """Rounding expected goals can give a tie, which is impossible."""
    f = exact_forecast(3.02, 2.98, SP)
    assert round(f.exp_home_goals) == round(f.exp_away_goals)
    h, a, p = f.most_likely
    assert h != a and p == pytest.approx(f.final.max())


def test_batch_matches_single_game():
    lh, la = np.array([3.1, 2.2, 4.0]), np.array([2.7, 3.9, 2.0])
    po = np.array([False, True, False])
    reg, fin = batch_matrices(lh, la, SP, po)
    for i in range(3):
        f = exact_forecast(lh[i], la[i], SP, playoff=bool(po[i]))
        np.testing.assert_allclose(reg[i], f.regulation, atol=1e-12)
        np.testing.assert_allclose(fin[i], f.final, atol=1e-12)


def test_monte_carlo_converges_to_exact():
    f = exact_forecast(3.3, 2.6, SP)
    s = simulate([{"weight": 1.0, "lam_home": 3.3, "lam_away": 2.6}], SP, n_sims=400_000, seed=5)
    assert abs(s.p_home_win - f.p_home_win) < 4 * s.mc_se_win
    assert np.abs(s.final - f.final).max() < 0.003
    assert s.p_tie_reg == pytest.approx(f.p_tie_reg, abs=0.004)
    assert s.exp_home_goals == pytest.approx(f.exp_home_goals, abs=0.02)
    assert s.final.sum() + s.overflow == pytest.approx(1.0, abs=1e-9)


def test_monte_carlo_is_reproducible_and_seed_sensitive():
    sc = [{"weight": 0.7, "lam_home": 3.3, "lam_away": 2.6, "sd_home": 0.05, "sd_away": 0.05},
          {"weight": 0.3, "lam_home": 3.0, "lam_away": 2.9, "sd_home": 0.05, "sd_away": 0.05}]
    a, b, c = simulate(sc, SP, n_sims=20000, seed=42), simulate(sc, SP, n_sims=20000, seed=42), simulate(sc, SP, n_sims=20000, seed=43)
    np.testing.assert_array_equal(a.final, b.final)
    assert a.p_home_win == b.p_home_win
    assert not np.array_equal(a.final, c.final)


def test_goalie_mixture_is_weighted_average():
    """A mixture over starters equals the probability-weighted exact results."""
    f1, f2 = exact_forecast(3.4, 2.5, SP), exact_forecast(3.0, 2.9, SP)
    s = simulate([{"weight": 0.6, "lam_home": 3.4, "lam_away": 2.5}, {"weight": 0.4, "lam_home": 3.0, "lam_away": 2.9}],
                 SP, n_sims=400_000, seed=9)
    assert s.p_home_win == pytest.approx(0.6 * f1.p_home_win + 0.4 * f2.p_home_win, abs=0.004)


def test_negative_binomial_is_supported_and_wider():
    nb = SimParams(theta=1.3, dispersion=0.08)
    f, g = exact_forecast(3.0, 3.0, nb), exact_forecast(3.0, 3.0, SimParams(theta=1.3))
    assert f.final.sum() + f.overflow == pytest.approx(1.0, abs=1e-9)
    assert f.final[6:, :].sum() > g.final[6:, :].sum()


def test_fit_recovers_draw_inflation():
    rng = np.random.default_rng(0)
    n = 30000
    lh, la = rng.uniform(2.4, 3.6, n), rng.uniform(2.4, 3.6, n)
    truth = SimParams(theta=1.5, p_shootout=0.3, ot_strength_slope=1.0)
    reg, _ = batch_matrices(lh, la, truth, np.zeros(n, bool))
    flat = reg.reshape(n, -1)
    flat = flat / flat.sum(axis=1, keepdims=True)
    u = rng.random(n)
    idx = np.minimum((flat.cumsum(axis=1) < u[:, None]).sum(axis=1), flat.shape[1] - 1)
    rh, ra = idx // 13, idx % 13
    extra = rh == ra
    so = extra & (rng.random(n) < 0.3)
    home_won = np.where(extra, rng.random(n) < 0.5, rh > ra)
    sp = fit_sim_params(lh, la, rh, ra, extra, home_won, so, np.zeros(n, bool))
    assert sp.theta == pytest.approx(1.5, abs=0.06)
    assert sp.p_shootout == pytest.approx(0.3, abs=0.02)
    assert sp.dispersion < 0.02


def test_regulation_goal_rules():
    assert regulation_goals(4, 2, "REG") == (4, 2)
    assert regulation_goals(3, 2, "OT") == (2, 2)
    assert regulation_goals(2, 3, "SO") == (2, 2)
    assert _period_type(2, 3) == "REG" and _period_type(2, 4) == "OT" and _period_type(2, 5) == "SO"
    assert _period_type(3, 6) == "OT"   # playoffs: any extra period is overtime
