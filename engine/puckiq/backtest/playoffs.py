"""Validation of playoff mode on past playoffs.

At the first game date of each round in each past season, everything from
that date on is hidden, features are rebuilt and the model retrained, and
the round's series are priced before a puck is dropped. The stated chance
that the team with home ice wins is then compared with who actually won.
Cup odds are checked the same way from the start of the first round."""
from __future__ import annotations

import datetime as dt
import json
import logging

import numpy as np
import pandas as pd

from .. import config
from ..features.build import FeatureBuilder, load_frames
from ..models.forecaster import Forecaster
from ..sim import playoffs as P
from .season import SEASONS, truncate

log = logging.getLogger("puckiq.backtest.playoffs")

SCALE_FIT_SEASONS = [2017, 2018, 2021, 2022]
SCALE_GRID = (0.0, 1.0, 2.0, 3.0)
BINS = [(0.0, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 1.0001)]


def run(groups: list[str] | None = None, n_sims: int = 8000, seasons: list[int] | None = None, persist: bool = True) -> dict:
    fr = load_frames()
    cases = []
    for season in seasons or SEASONS:
        actual = {(s.round, s.matchup): s for s in P.series_table(fr.games, season)}
        champion = actual[(4, 1)].winner
        po = fr.games[(fr.games["season"] == season) & (fr.games["game_type"] == config.PLAYOFF)].copy()
        po["round"] = (po["id"] // 100) % 10
        for r in (1, 2, 3, 4):
            cut = po[po["round"] == r]["game_date"].min()
            tr = truncate(fr, cut)
            feat = FeatureBuilder(tr).run()
            fc = Forecaster.train(feat, groups)
            inp = P.prepare(feat, tr.games, fc, season, scale=1.0, only_round=r)
            if inp is None:
                log.warning("no bracket for %s round %s", season, r)
                continue
            assert all(s.wins_top == 0 and s.wins_bottom == 0 for s in inp.series), "a series had already started at the cut"
            cases.append((season, r, inp, inp.sd_off.copy(), inp.sd_def.copy(), actual, champion))
            log.info("prepared %s round %s (%s)", season, r, cut)

    def price(k: float) -> tuple[pd.DataFrame, pd.DataFrame]:
        srows, crows = [], []
        for season, r, inp, so, sd, actual, champion in cases:
            inp.sd_off, inp.sd_def = so * k, sd * k
            out = P.simulate(inp, n_sims=n_sims, seed=season * 10 + r)
            for s, p in zip(inp.series, out["p_top"]):
                srows.append({"season": season, "round": r, "p_top": p, "top_won": float(actual[(s.round, s.matchup)].winner == s.top)})
            if r == 1:
                for t, v in out["teams"].items():
                    crows.append({"season": season, "p_cup": v[4], "champion": float(t == champion)})
        return pd.DataFrame(srows), pd.DataFrame(crows)

    def stats(s: pd.DataFrame) -> dict:
        if s.empty:
            return {"n": 0}
        y, p = s["top_won"], s["p_top"]
        return {"n": int(len(s)), "brier": float(((p - y) ** 2).mean()), "accuracy": float(((p >= 0.5) == (y == 1)).mean()),
                "home_ice_win_rate": float(y.mean()), "brier_home_ice_always": float(((1.0 - y) ** 2).mean()),
                "brier_coin_flip": 0.25, "brier_base_rate": float(y.mean() * (1.0 - y.mean())), "mean_stated": float(p.mean())}

    study = []
    for k in SCALE_GRID:
        s, _ = price(k)
        fit, later = s[s["season"].isin(SCALE_FIT_SEASONS)], s[~s["season"].isin(SCALE_FIT_SEASONS)]
        study.append({"scale": k, "fit_brier": stats(fit).get("brier"), "later_brier": stats(later).get("brier")})
    s, c = price(P.STRENGTH_SD_SCALE)
    cal = []
    for lo, hi in BINS:
        m = (s["p_top"] >= lo) & (s["p_top"] < hi)
        if m.sum():
            cal.append({"lo": lo, "hi": min(hi, 1.0), "n": int(m.sum()), "mean_pred": float(s.loc[m, "p_top"].mean()), "observed": float(s.loc[m, "top_won"].mean())})
    champs = c[c["champion"] == 1]
    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "seasons": seasons or SEASONS, "n_sims": n_sims,
        "strength_sd_scale": P.STRENGTH_SD_SCALE, "series": stats(s),
        "by_round": {str(r): stats(v) for r, v in s.groupby("round")},
        "calibration": cal,
        "cup": {"team_seasons": int(len(c)), "n_champions": int(len(champs)), "champion_mean_prob": float(champs["p_cup"].mean()),
                "champion_uniform_prob": 1.0 / 16.0, "brier": float(((c["p_cup"] - c["champion"]) ** 2).mean()),
                "brier_uniform": float(((1.0 / 16.0 - c["champion"]) ** 2).mean())},
        "scale_study": {"fit_seasons": SCALE_FIT_SEASONS, "grid": study},
    }
    if persist:
        p = config.ARTIFACT_DIR / "backtest"
        p.mkdir(parents=True, exist_ok=True)
        (p / "playoffs.json").write_text(json.dumps(summary, indent=1))
    return {"summary": summary, "series": s, "cup": c}
