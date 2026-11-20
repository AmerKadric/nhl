"""Scoring-rate models: log-linear Poisson GLM and gradient-boosted trees.

Both predict the regulation scoring rate (lambda) of a team in a game and
both are additive on the log scale, which makes per-factor contributions
exact rather than approximate."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import statsmodels.api as sm

from .design import GBM_EXTRA, GROUPS, columns_for


@dataclass
class GLMRateModel:
    groups: list[str]
    cols: list[str] = field(default_factory=list)
    beta: np.ndarray | None = None
    cov: np.ndarray | None = None
    means: np.ndarray | None = None   # training means: the reference point for contributions
    n_train: int = 0

    def fit(self, rows: pd.DataFrame) -> "GLMRateModel":
        self.cols = columns_for(self.groups)
        d = rows.dropna(subset=["y"] + self.cols)
        X = np.column_stack([np.ones(len(d)), d[self.cols].to_numpy(float)])
        res = sm.GLM(d["y"].to_numpy(float), X, family=sm.families.Poisson()).fit()
        self.beta, self.cov = np.asarray(res.params), np.asarray(res.cov_params())
        self.means = d[self.cols].to_numpy(float).mean(axis=0)
        self.n_train = len(d) // 2
        return self

    def _X(self, rows: pd.DataFrame) -> np.ndarray:
        X = rows[self.cols].to_numpy(float)
        # A missing input falls back to the training mean (no information).
        X = np.where(np.isnan(X), self.means, X)
        return np.column_stack([np.ones(len(X)), X])

    def log_rate(self, rows: pd.DataFrame) -> np.ndarray:
        return self._X(rows) @ self.beta

    def predict(self, rows: pd.DataFrame) -> np.ndarray:
        return np.exp(self.log_rate(rows))

    def log_rate_sd(self, rows: pd.DataFrame) -> np.ndarray:
        """Standard deviation of the log scoring rate from (a) coefficient
        uncertainty and (b) the Kalman posterior variance of the team
        ratings that feed the model (delta method)."""
        X = self._X(rows)
        var = np.einsum("ij,jk,ik->i", X, self.cov, X)
        b = dict(zip(self.cols, self.beta[1:]))
        for m in ("xg5", "xga", "g"):
            if f"t_off_{m}" in b:
                var = var + b[f"t_off_{m}"] ** 2 * rows[f"t_poff_{m}"].to_numpy(float)
                var = var + b[f"o_def_{m}"] ** 2 * rows[f"o_pdef_{m}"].to_numpy(float)
        return np.sqrt(np.maximum(var, 0.0))

    def contributions(self, rows: pd.DataFrame) -> pd.DataFrame:
        """Exact additive contribution of each feature group to log(lambda),
        relative to a league-average team in an average situation."""
        X = self._X(rows)[:, 1:] - self.means
        contrib = X * self.beta[1:]
        out, i = {}, 0
        for g in self.groups:
            n = len(GROUPS[g])
            out[g] = contrib[:, i:i + n].sum(axis=1)
            i += n
        return pd.DataFrame(out, index=rows.index)

    def baseline_log_rate(self) -> float:
        return float(self.beta[0] + self.means @ self.beta[1:])

    def to_json(self) -> dict:
        return {"kind": "glm", "groups": self.groups, "cols": self.cols, "beta": self.beta.tolist(),
                "cov": self.cov.tolist(), "means": self.means.tolist(), "n_train": self.n_train}

    @classmethod
    def from_json(cls, d: dict) -> "GLMRateModel":
        return cls(groups=d["groups"], cols=d["cols"], beta=np.array(d["beta"]), cov=np.array(d["cov"]),
                   means=np.array(d["means"]), n_train=d["n_train"])


@dataclass
class GBMRateModel:
    groups: list[str]
    params: dict = field(default_factory=lambda: dict(
        objective="poisson", learning_rate=0.02, num_leaves=8, min_data_in_leaf=400,
        feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0,
        max_depth=4, verbose=-1, seed=7, deterministic=True, force_row_wise=True, num_threads=4))
    n_rounds: int = 350
    cols: list[str] = field(default_factory=list)
    booster: object = None

    def fit(self, rows: pd.DataFrame) -> "GBMRateModel":
        import lightgbm as lgb
        self.cols = columns_for(self.groups) + [c for c in GBM_EXTRA if c not in columns_for(self.groups)]
        d = rows.dropna(subset=["y"])
        ds = lgb.Dataset(d[self.cols].to_numpy(float), label=d["y"].to_numpy(float), feature_name=self.cols)
        self.booster = lgb.train(self.params, ds, num_boost_round=self.n_rounds)
        return self

    def log_rate(self, rows: pd.DataFrame) -> np.ndarray:
        return self.booster.predict(rows[self.cols].to_numpy(float), raw_score=True)

    def predict(self, rows: pd.DataFrame) -> np.ndarray:
        return np.exp(self.log_rate(rows))

    def contributions(self, rows: pd.DataFrame) -> pd.DataFrame:
        """TreeSHAP contributions on the log scale, summed by feature group."""
        sv = self.booster.predict(rows[self.cols].to_numpy(float), pred_contrib=True)[:, :-1]
        col_group = {}
        for g in self.groups:
            for c in GROUPS[g]:
                col_group[c] = g
        extra_map = {"gk": "goalie", "xgs": "xg5", "xg5": "xg5", "gd": "goals", "_g": "goals", "rest": "rest",
                     "travel": "rest", "tz": "rest", "road": "rest", "pk": "special", "pp": "special",
                     "fin": "finishing", "gp": "env"}
        out = {g: np.zeros(len(rows)) for g in self.groups}
        for i, c in enumerate(self.cols):
            g = col_group.get(c)
            if g is None:
                g = next((v for k, v in extra_map.items() if k in c), "env")
                if g not in out:
                    g = "env" if "env" in out else self.groups[0]
            out[g] += sv[:, i]
        return pd.DataFrame(out, index=rows.index)


@dataclass
class BlendRateModel:
    """Log-linear blend: lambda = glm^(1-w) * gbm^w."""
    glm: GLMRateModel
    gbm: GBMRateModel
    w: float

    def log_rate(self, rows):
        return (1 - self.w) * self.glm.log_rate(rows) + self.w * self.gbm.log_rate(rows)

    def predict(self, rows):
        return np.exp(self.log_rate(rows))

    def log_rate_sd(self, rows):
        return self.glm.log_rate_sd(rows)

    def contributions(self, rows):
        a, b = self.glm.contributions(rows), self.gbm.contributions(rows)
        return (1 - self.w) * a + self.w * b.reindex(columns=a.columns, fill_value=0.0)
