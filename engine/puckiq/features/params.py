"""Hyper-parameters of the rating systems.

Defaults are overridden by `artifacts/tuning.json`, which is produced by
`puckiq tune` using ONLY seasons before the first backtest season, so none
of the evaluated games influence these values."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .. import config

TUNING_PATH = config.ARTIFACT_DIR / "tuning.json"


@dataclass
class KF:
    """Local-level Kalman filter for one offence/defence rating pair."""
    r: float          # observation noise variance of the per-game metric
    q: float          # per-game random-walk variance of a team's true level
    q_season: float   # extra variance added each off-season
    carry: float      # fraction of the rating carried into a new season
    p0: float         # prior variance for a team with no history


@dataclass
class FeatureParams:
    kf: dict[str, KF] = field(default_factory=lambda: {
        "xg5": KF(r=0.35, q=0.0006, q_season=0.012, carry=0.75, p0=0.04),
        "xga": KF(r=0.45, q=0.0008, q_season=0.015, carry=0.75, p0=0.05),
        "g": KF(r=2.6, q=0.0008, q_season=0.015, carry=0.70, p0=0.05),
    })
    elo_k: float = 6.0
    elo_hfa: float = 40.0
    elo_carry: float = 0.70
    st_hl_games: float = 40.0      # half-life (team games) of special-teams samples
    st_prior_sec: float = 6000.0   # pseudo-seconds at league rate (shrinkage)
    fin_hl_games: float = 82.0
    fin_prior_xg: float = 150.0
    gk_hl_xg: float = 250.0        # half-life of goalie evidence, in xG faced
    gk_prior_xg: float = 120.0     # pseudo-xG at the prior mean (shrinkage)
    gk_prior_mean: float = -0.02   # unseen goalies start slightly below average
    league_hl_games: float = 600.0
    # conditional-logit weights of the starting-goalie projection
    starter_w: dict[str, float] = field(default_factory=lambda: {
        "share10": 3.0, "prev_rested": 0.5, "prev_b2b": -2.0, "share30": 1.0})

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "FeatureParams":
        p = cls()
        for k, v in d.items():
            if k == "kf":
                p.kf = {m: KF(**kv) for m, kv in v.items()}
            elif hasattr(p, k):
                setattr(p, k, v)
        return p


def load_params() -> FeatureParams:
    if TUNING_PATH.exists():
        return FeatureParams.from_json(json.loads(TUNING_PATH.read_text())["params"])
    return FeatureParams()
