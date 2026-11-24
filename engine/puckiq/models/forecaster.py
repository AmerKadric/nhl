"""Production forecaster: the architecture selected by the walk-forward
backtest (lean Poisson rate model + rules-based score simulator)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config
from ..sim.simulator import ScoreForecast, SimParams, exact_forecast, fit_sim_params, simulate
from .design import GROUP_LABELS, GROUPS, team_rows
from .rates import GLMRateModel

ARCHITECTURE = "lean-poisson"
ARCH_VERSION = "1.0"
GROUPS_LEAN = ["home", "env", "xga", "goals", "goalie", "rest", "elo"]

CONFIDENCE_BANDS = [(0.55, "Toss-up"), (0.60, "Slight lean"), (0.65, "Lean"), (0.70, "Clear favourite"), (1.01, "Strong favourite")]

# Scenario-lab domain: ranges actually observed in training data.
LAB_DOMAIN = {"g7": (0, 4)}


def confidence_label(p_fav: float) -> str:
    for hi, label in CONFIDENCE_BANDS:
        if p_fav < hi:
            return label
    return CONFIDENCE_BANDS[-1][1]


@dataclass
class Forecaster:
    version: str
    glm: GLMRateModel
    sp: SimParams
    trained_through: dt.date
    n_train_games: int
    domain: dict = field(default_factory=dict)   # validated input ranges for scenarios

    # ---- training ---------------------------------------------------------
    @classmethod
    def train(cls, feat: pd.DataFrame, groups: list[str] | None = None) -> "Forecaster":
        """Fit on every finished game. `groups` are the feature groups the
        walk-forward backtest selected (default: the lean set)."""
        groups = groups or GROUPS_LEAN
        arch = "1.1" if any(g.startswith("lineup") for g in groups) else ARCH_VERSION
        f = feat[feat["final"] & (feat["season"] >= config.FIRST_MODEL_SEASON)]
        rows = team_rows(f, "confirmed")
        glm = GLMRateModel(groups).fit(rows)
        lam = pd.DataFrame({"g": rows["game_id"].values, "h": rows["is_home"].values, "lam": glm.predict(rows)})
        lh = lam[lam["h"] == 1].set_index("g")["lam"]
        la = lam[lam["h"] == 0].set_index("g")["lam"]
        G = f.set_index("game_id").loc[lh.index]

        def fit(g: pd.DataFrame) -> SimParams:
            return fit_sim_params(
                lh.loc[g.index].values, la.loc[g.index].values,
                g["home_reg_goals"].astype(int).values, g["away_reg_goals"].astype(int).values,
                (g["last_period_type"] != "REG").values, (g["home_score"] > g["away_score"]).values,
                (g["last_period_type"] == "SO").values, (g["playoff"] == 1).values)
        sp = fit(G)
        era = fit(G[G["season"] >= 2015])   # 3-on-3 overtime era
        sp.p_shootout, sp.ot_home_intercept = era.p_shootout, era.ot_home_intercept
        sp.ot_strength_slope, sp.so_strength_slope = era.ot_strength_slope, era.so_strength_slope
        through = max(f["game_date"])
        domain = {}
        if "h_lu_toi" in f:
            v = pd.concat([f["h_lu_toi"], f["a_lu_toi"]])
            domain["lu_toi"] = [float(v.quantile(0.005)), float(v.quantile(0.995))]
        return cls(version=f"{ARCHITECTURE}-{arch}+{through:%Y%m%d}", glm=glm, sp=sp,
                   trained_through=through, n_train_games=int(len(G)), domain=domain)

    # ---- persistence ------------------------------------------------------
    def to_json(self) -> dict:
        return {"version": self.version, "architecture": ARCHITECTURE, "glm": self.glm.to_json(), "sim": self.sp.to_json(),
                "trained_through": self.trained_through.isoformat(), "n_train_games": self.n_train_games,
                "domain": self.domain}

    def save(self, path: Path | None = None) -> Path:
        path = path or (config.ARTIFACT_DIR / "models" / f"{self.version}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json()))
        return path

    @classmethod
    def from_json(cls, d: dict) -> "Forecaster":
        return cls(version=d["version"], glm=GLMRateModel.from_json(d["glm"]), sp=SimParams(**d["sim"]),
                   trained_through=dt.date.fromisoformat(d["trained_through"]), n_train_games=d["n_train_games"],
                   domain=d.get("domain", {}))

    @classmethod
    def load(cls, path: Path) -> "Forecaster":
        return cls.from_json(json.loads(Path(path).read_text()))

    # ---- inference --------------------------------------------------------
    def _rows(self, row: dict, gk_home: float, gk_away: float, overrides: dict | None = None) -> pd.DataFrame:
        """Two design rows (home, away) for one game."""
        r = dict(row)
        r["h_gk"], r["a_gk"] = gk_home, gk_away
        r["h_gk_proj"], r["a_gk_proj"] = gk_home, gk_away
        for k, v in (overrides or {}).items():
            r[k] = v
        for k in ("home_reg_goals", "away_reg_goals"):
            r.setdefault(k, np.nan)
        for k in ("game_id", "season", "game_date"):
            r.setdefault(k, None)
        return team_rows(pd.DataFrame([r]), "confirmed")

    def rates(self, row: dict, gk_home: float, gk_away: float, overrides: dict | None = None) -> dict:
        rows = self._rows(row, gk_home, gk_away, overrides)
        lam = self.glm.predict(rows)
        sd = self.glm.log_rate_sd(rows)
        return {"lam_home": float(lam[0]), "lam_away": float(lam[1]), "sd_home": float(sd[0]), "sd_away": float(sd[1]), "rows": rows}

    def forecast(self, row: dict, goalies: dict, n_sims: int = config.N_SIMS, seed: int = 0,
                 overrides: dict | None = None) -> dict:
        """Full forecast for one game.

        `row`: wide pre-game feature row (h_*/a_* columns).
        `goalies`: {"home": [{"pid", "name", "prob", "rating"}], "away": [...]},
        the plausible starters and their probabilities."""
        playoff = bool(row.get("playoff", 0) == 1)
        gh, ga = _norm(goalies["home"]), _norm(goalies["away"])
        scenarios = []
        for h in gh:
            for a in ga:
                r = self.rates(row, h["rating"], a["rating"], overrides)
                scenarios.append({"weight": h["prob"] * a["prob"], "lam_home": r["lam_home"], "lam_away": r["lam_away"],
                                  "sd_home": r["sd_home"], "sd_away": r["sd_away"]})
        sim = simulate(scenarios, self.sp, playoff=playoff, n_sims=n_sims, seed=seed)

        # Point forecast at the expected goalie ratings: basis of the explanation.
        gk_h = sum(g["prob"] * g["rating"] for g in gh)
        gk_a = sum(g["prob"] * g["rating"] for g in ga)
        point = self.rates(row, gk_h, gk_a, overrides)
        exact = exact_forecast(point["lam_home"], point["lam_away"], self.sp, playoff)
        drivers = self.drivers(point, exact, playoff)
        return {"sim": sim, "exact": exact, "drivers": drivers, "scenarios": scenarios,
                "goalies": {"home": gh, "away": ga}, "point": {k: v for k, v in point.items() if k != "rows"}}

    def drivers(self, point: dict, exact: ScoreForecast, playoff: bool) -> list[dict]:
        """What moves this forecast away from a league-average matchup.

        For each feature group: its exact multiplicative effect on each team's
        expected regulation goals, and the change in home win probability when
        that group alone is set to its league-average value (a
        one-factor-at-a-time counterfactual, so the effects need not sum)."""
        contrib = self.glm.contributions(point["rows"])
        out = []
        for g in self.glm.groups:
            ch, ca = float(contrib[g].iloc[0]), float(contrib[g].iloc[1])
            alt = exact_forecast(point["lam_home"] * np.exp(-ch), point["lam_away"] * np.exp(-ca), self.sp, playoff)
            out.append({
                "key": g, "label": GROUP_LABELS[g],
                "home_goal_effect": float(np.exp(ch) - 1.0),   # +0.06 => +6% home scoring rate
                "away_goal_effect": float(np.exp(ca) - 1.0),
                "home_win_prob_effect": float(exact.p_home_win - alt.p_home_win),
                "inputs": {c: _f(point["rows"][c].iloc[0]) for c in GROUPS[g]} | {f"away:{c}": _f(point["rows"][c].iloc[1]) for c in GROUPS[g]},
            })
        return out


def _f(v) -> float | None:
    v = float(v)
    return None if np.isnan(v) else round(v, 5)


def _norm(gs: list[dict]) -> list[dict]:
    tot = sum(g["prob"] for g in gs)
    return [dict(g, prob=g["prob"] / tot) for g in gs if g["prob"] / tot >= 0.02] or gs[:1]


_SPECIAL = {"elo_diff": ["h_elo", "a_elo"], "log_lg": ["lg_g"], "playoff": ["playoff"], "is_home": []}


def model_inputs(cols: list[str]) -> list[str]:
    """Wide feature-row keys that the model's design columns are built from."""
    keys: set[str] = set()
    for c in cols:
        if c in _SPECIAL:
            keys.update(_SPECIAL[c])
        elif c[:2] in ("t_", "o_") and c != "o_gk":   # goalies are hashed separately
            keys.update((f"h_{c[2:]}", f"a_{c[2:]}"))
    return sorted(keys)


def input_hash(model_version: str, row: dict, goalies: dict, cols: list[str] | None = None) -> str:
    """Stable fingerprint of everything a forecast depends on. With `cols`
    (the model's design columns) only the inputs the model uses are hashed,
    so an unrelated statistic changing does not create a new forecast version."""
    def val(k):
        v = row.get(k)
        return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 4)
    if cols is not None:
        keep = model_inputs(cols)
    else:
        skip = ("_gk_pid", "_gk", "_gk_recent", "_gk_n", "_gk_idle", "_gk_proj", "_gk_proj_top", "_gk_ncand")
        keep = sorted(k for k in row if (k[:2] in ("h_", "a_") or k in ("playoff", "lg_g")) and not k.endswith(skip))
    payload = {
        "model": model_version,
        "row": {k: val(k) for k in keep},
        "goalies": {s: [(int(g["pid"]), round(g["prob"], 3), round(g["rating"], 4)) for g in goalies[s]] for s in ("home", "away")},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
