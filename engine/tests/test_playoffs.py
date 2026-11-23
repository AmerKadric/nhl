import numpy as np
import pandas as pd
import pytest

from puckiq.sim import playoffs as P
from puckiq.sim import season as S
from puckiq.sim.simulator import SimParams

SP = SimParams(theta=1.45, ot_home_intercept=-0.05, ot_strength_slope=1.1, p_shootout=0.34)


def game(season, rnd, matchup, gno, home, away, hs=None, as_=None):
    return dict(id=season * 1_000_000 + 30000 + rnd * 100 + matchup * 10 + gno, season=season, game_type=3,
                home_team_id=home, away_team_id=away, home_score=hs, away_score=as_)


def inputs(strength=None, series=None) -> P.PlayoffInputs:
    n = 16
    strength = np.zeros(n) if strength is None else np.asarray(strength, float)
    base = np.log(3.0)
    llh = base + 0.04 + 0.5 * (strength[:, None] - strength[None, :])
    lla = base - 0.5 * (strength[:, None] - strength[None, :])
    series = series or [P.Series(1, m + 1, 100 + 2 * m, 101 + 2 * m) for m in range(8)]
    return P.PlayoffInputs(season=2025, team_ids=list(range(100, 116)), series=series, earlier=[], log_lam_home=llh, log_lam_away=lla,
                           sd_off=np.full(n, 0.03), sd_def=np.full(n, 0.03), record=np.arange(n, 0, -1).astype(float), sp=SP)


def test_series_are_read_from_game_ids():
    games = pd.DataFrame([
        game(2025, 1, 1, 1, 10, 20, 3, 1), game(2025, 1, 1, 2, 10, 20, 2, 3), game(2025, 1, 1, 3, 20, 10, 1, 4),
        game(2025, 1, 1, 4, 20, 10),                                  # scheduled, not played
        game(2025, 1, 2, 1, 30, 40, 1, 2), game(2025, 1, 2, 2, 30, 40, 0, 1), game(2025, 1, 2, 3, 40, 30, 5, 2), game(2025, 1, 2, 4, 40, 30, 2, 1),
        dict(id=2025020001, season=2025, game_type=2, home_team_id=10, away_team_id=20, home_score=1, away_score=0),   # regular season: ignored
        game(2024, 1, 1, 1, 10, 20, 9, 0),                            # another season: ignored
    ])
    t = P.series_table(games, 2025)
    assert [(s.round, s.matchup, s.top, s.bottom, s.wins_top, s.wins_bottom) for s in t] == [(1, 1, 10, 20, 2, 1), (1, 2, 30, 40, 0, 4)]
    assert not t[0].done and t[0].winner is None
    assert t[1].done and t[1].winner == 40                          # the lower seed swept
    assert P.current_round(t) is None                               # only two of the eight first-round series are listed
    assert P.series_table(games, 2023) == []


def test_a_partly_listed_next_round_leaves_the_previous_round_current():
    first = [P.Series(1, m + 1, 100 + 2 * m, 101 + 2 * m, 4, 1) for m in range(8)]
    first[7] = P.Series(1, 8, 114, 115, 3, 3)                       # one series still going
    assert P.current_round(first)[0].round == 1
    early = first + [P.Series(2, 1, 100, 102)]                      # a second-round series is already listed
    assert [s.round for s in P.current_round(early)] == [1] * 8
    full = first + [P.Series(2, m + 1, 100 + 4 * m, 102 + 4 * m) for m in range(4)]
    assert [s.round for s in P.current_round(full)] == [2] * 4
    assert P.current_round([]) is None


def test_series_probability_from_a_series_already_under_way():
    half = np.array([0.5])
    assert S.series_prob(half, half, a0=3, b0=0)[0] == pytest.approx(1 - 0.5 ** 4)
    assert S.series_prob(half, half, a0=0, b0=3)[0] == pytest.approx(0.5 ** 4)
    assert S.series_prob(half, half, a0=4, b0=1)[0] == 1.0 and S.series_prob(half, half, a0=2, b0=4)[0] == 0.0
    # 3-3: game seven is at the higher seed's rink, so only its home probability matters
    assert S.series_prob(np.array([0.63]), np.array([0.10]), a0=3, b0=3)[0] == pytest.approx(0.63)
    # 2-2: game five at home, six away, seven at home
    ph, pr = 0.6, 0.45
    exact = ph * pr + ph * (1 - pr) * ph + (1 - ph) * pr * ph
    assert S.series_prob(np.array([ph]), np.array([pr]), a0=2, b0=2)[0] == pytest.approx(exact)
    # from 0-0 the generalised function is the original one
    assert S.series_prob(np.array([0.58]), np.array([0.47]), a0=0, b0=0)[0] == pytest.approx(S.series_prob(np.array([0.58]), np.array([0.47]))[0])


def test_playoff_odds_are_coherent():
    out = P.simulate(inputs(strength=np.linspace(0.2, -0.2, 16)), n_sims=6000, seed=3)
    teams = out["teams"]
    for rnd, n_winners in ((1, 8), (2, 4), (3, 2), (4, 1)):
        assert sum(v[rnd] for v in teams.values()) == pytest.approx(n_winners)
    for v in teams.values():
        assert v[1] >= v[2] >= v[3] >= v[4] >= 0
    assert len(out["p_top"]) == 8 and all(0 < p < 1 for p in out["p_top"])
    assert teams[100][4] == max(v[4] for v in teams.values())       # the strongest team is the Cup favourite
    assert out["p_top"][0] > 0.5                                    # stronger and with home ice


def test_series_score_moves_the_odds_and_finished_series_are_fixed():
    level = P.simulate(inputs(), n_sims=6000, seed=5)
    series = [P.Series(1, m + 1, 100 + 2 * m, 101 + 2 * m) for m in range(8)]
    series[0] = P.Series(1, 1, 100, 101, wins_top=3, wins_bottom=0)
    series[1] = P.Series(1, 2, 102, 103, wins_top=1, wins_bottom=4)   # finished: the lower seed won
    led = P.simulate(inputs(series=series), n_sims=6000, seed=5)
    assert led["p_top"][0] > 0.9 > level["p_top"][0]
    assert led["p_top"][1] == 0.0
    assert led["teams"][103][1] == 1.0 and led["teams"][102][1] == 0.0 and led["teams"][102][4] == 0.0
    assert sum(v[4] for v in led["teams"].values()) == pytest.approx(1.0)


def test_later_rounds_start_from_the_current_round():
    """Standing in the conference finals: four teams, two series."""
    series = [P.Series(3, 1, 100, 104, 2, 1), P.Series(3, 2, 108, 112, 0, 0)]
    out = P.simulate(inputs(series=series), n_sims=4000, seed=1)
    assert out["round"] == 3
    alive = [t for t, v in out["teams"].items() if v.get(3, 0) > 0]
    assert sorted(alive) == [100, 104, 108, 112]
    assert sum(out["teams"][t][3] for t in alive) == pytest.approx(2.0)
    assert sum(out["teams"][t][4] for t in alive) == pytest.approx(1.0)
    a, b = P.simulate(inputs(series=series), 4000, seed=1), P.simulate(inputs(series=series), 4000, seed=1)
    assert a == b
