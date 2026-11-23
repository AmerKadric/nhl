"""Test configuration. Tests never touch the real database or the network:
the environment is pointed at a temporary directory before `puckiq` loads."""
import datetime as dt
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="puckiq-test-")
os.environ["PUCKIQ_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["PUCKIQ_ARTIFACT_DIR"] = os.path.join(_TMP, "artifacts")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "test.db").replace("\\", "/")
os.environ["PUCKIQ_RATE_LIMIT_PER_MIN"] = "100000"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from puckiq.features.build import Frames  # noqa: E402

TEAMS = [(6, "BOS"), (3, "NYR"), (17, "DET"), (10, "TOR"), (8, "MTL"), (16, "CHI")]


def make_frames(n_days: int = 80, seed: int = 3, seasons=(2020, 2021)) -> Frames:
    """A small synthetic league with realistic-looking box scores, spanning
    two seasons so that season roll-over logic is exercised."""
    rng = np.random.default_rng(seed)
    strength = {tid: rng.normal(0, 0.25) for tid, _ in TEAMS}
    games, tgs, ggs, sgs = [], [], [], []
    gid = 0
    for season in seasons:
        day0 = dt.date(season, 10, 10)
        for d in range(n_days):
            ids = [t for t, _ in TEAMS]
            rng.shuffle(ids)
            for k in range(0, 6, 2):
                if rng.random() < 0.45:
                    continue
                h, a = ids[k], ids[k + 1]
                gid += 1
                game_id = season * 1_000_000 + 20000 + gid
                lam_h = np.exp(1.05 + 0.5 * (strength[h] - strength[a]) + 0.05)
                lam_a = np.exp(1.05 + 0.5 * (strength[a] - strength[h]))
                rh, ra = int(rng.poisson(lam_h)), int(rng.poisson(lam_a))
                lpt, hs, as_ = "REG", rh, ra
                if rh == ra:
                    lpt = "SO" if rng.random() < 0.33 else "OT"
                    if rng.random() < 0.5:
                        hs += 1
                    else:
                        as_ += 1
                date = day0 + dt.timedelta(days=d)
                games.append(dict(id=game_id, season=season, game_type=2, game_date=date,
                                  start_time_utc=pd.Timestamp(date) + pd.Timedelta(hours=23 + k), home_team_id=h, away_team_id=a,
                                  venue="Test Arena", state="OFF", schedule_state="OK", home_score=hs, away_score=as_,
                                  last_period_type=lpt, home_reg_goals=rh, away_reg_goals=ra))
                xh, xa = lam_h * rng.uniform(0.7, 1.3), lam_a * rng.uniform(0.7, 1.3)
                for is_home, tid, xf, xag, gf, ga in ((True, h, xh, xa, hs, as_), (False, a, xa, xh, as_, hs)):
                    tgs.append(dict(game_id=game_id, team_id=tid, is_home=is_home, toi_all=3600.0, xgf=xf, xga=xag,
                                    fsva_xgf=xf * 0.97, fsva_xga=xag * 0.97, gf=float(gf), ga=float(ga), sog_f=30.0, sog_a=30.0,
                                    hd_xgf=xf / 2, hd_xga=xag / 2, hd_shots_f=8.0, hd_shots_a=8.0, pen_taken=3.0, pen_drawn=3.0,
                                    toi_5v5=2900.0, xgf_5v5=xf * 0.7, xga_5v5=xag * 0.7, sva_xgf_5v5=xf * 0.7, sva_xga_5v5=xag * 0.7,
                                    gf_5v5=gf * 0.7, ga_5v5=ga * 0.7, cf_5v5=50.0, ca_5v5=50.0, toi_pp=300.0, xgf_pp=0.5, gf_pp=0.0,
                                    toi_pk=300.0, xga_pk=0.5, ga_pk=0.0))
                    # two goalies per team; the starter rotates
                    pid = tid * 100 + (1 if rng.random() < 0.7 else 2)
                    ggs.append(dict(game_id=game_id, player_id=pid, team_id=tid, started=True, toi=3600.0, shots_against=30,
                                    goals_against=int(ga), xga=float(xag), ga_on_xg=float(ga), attempts=45))
                    # 20 rostered skaters per team, 18 dress; the two scratches rotate
                    roster = [tid * 1000 + i for i in range(20)]
                    out = set(rng.choice(20, size=2, replace=False))
                    for i, sk in enumerate(roster):
                        if i in out:
                            continue
                        sgs.append(dict(game_id=game_id, player_id=sk, team_id=tid, position="D" if i >= 14 else "C",
                                        toi=float(600 + 60 * (i % 12)), goals=int(rng.random() < 0.08), assists=int(rng.random() < 0.15),
                                        shots=int(rng.integers(0, 4)), plus_minus=int(rng.integers(-1, 2)), pim=0, pp_points=0))
    teams = pd.DataFrame([dict(id=t, abbrev=a, name=a, franchise_key=t, conference=None, division=None, color="#000000",
                               timezone="America/New_York", lat=0.0, lon=0.0, active=True) for t, a in TEAMS])
    players = pd.DataFrame([dict(id=t * 100 + i, full_name=f"Goalie {a}{i}", position="G") for t, a in TEAMS for i in (1, 2)])
    skaters = pd.DataFrame([dict(id=t * 1000 + i, full_name=f"Skater {a}{i}", position="D" if i >= 14 else "C") for t, a in TEAMS for i in range(20)])
    return Frames(pd.DataFrame(games), pd.DataFrame(tgs), pd.DataFrame(ggs), teams, pd.concat([players, skaters], ignore_index=True), pd.DataFrame(sgs))


@pytest.fixture(scope="session")
def frames() -> Frames:
    return make_frames()
