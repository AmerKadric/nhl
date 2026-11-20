"""Design matrices. Each game yields two rows, one per team, whose target is
that team's regulation goals. `t_` columns describe the scoring team, `o_`
columns its opponent."""
from __future__ import annotations

import numpy as np
import pandas as pd

TEAM_FEATS = [
    "elo", "gp", "off_xg5", "def_xg5", "off_xga", "def_xga", "off_g", "def_g",
    "poff_xg5", "pdef_xg5", "poff_xga", "pdef_xga", "poff_g", "pdef_g",
    "pp", "pk", "pen_drawn", "pen_taken", "fin", "rest", "b2b", "g7", "travel", "tz", "road_streak",
    "xgs_l5", "xgs_l10", "xgs_l20", "gd_l5", "gd_l10", "gd_l20",
    "gk", "gk_recent", "gk_n", "gk_idle", "gk_proj",
]

# Feature groups: the unit of ablation testing and of forecast explanations.
GROUPS: dict[str, list[str]] = {
    "home": ["is_home"],
    "env": ["log_lg", "playoff"],
    "xg5": ["t_off_xg5", "o_def_xg5"],
    "xga": ["t_off_xga", "o_def_xga"],
    "goals": ["t_off_g", "o_def_g"],
    "special": ["t_pp", "o_pk", "t_pen_drawn", "o_pen_taken"],
    "finishing": ["t_fin"],
    "goalie": ["o_gk"],
    "rest": ["t_b2b", "o_b2b", "t_g7", "o_g7", "t_travel", "t_tz"],
    "elo": ["elo_diff"],
}

GROUP_LABELS = {
    "home": "Home ice",
    "env": "League scoring environment",
    "xg5": "Five-on-five chance quality",
    "xga": "All-situations chance quality",
    "goals": "Goals scored and allowed",
    "special": "Special teams",
    "finishing": "Finishing",
    "goalie": "Goaltending",
    "rest": "Rest and travel",
    "elo": "Results-based rating",
}

GBM_EXTRA = [
    "t_gp", "o_gp", "t_rest", "o_rest", "t_road_streak", "o_travel", "o_tz",
    "t_xgs_l5", "t_xgs_l10", "t_xgs_l20", "o_xgs_l5", "o_xgs_l10", "o_xgs_l20",
    "t_gd_l10", "o_gd_l10", "o_gk_recent", "o_gk_n", "o_gk_idle",
    "t_def_xg5", "o_off_xg5", "t_def_g", "o_off_g", "t_pk", "o_pp", "o_fin",
]


def team_rows(feat: pd.DataFrame, goalie: str = "confirmed") -> pd.DataFrame:
    """Two rows per game. `goalie` selects which goalie information the
    opponent-goalie feature uses:

    - "confirmed": the actual starter's pre-game rating (information that is
      public shortly before puck drop);
    - "projected": the expectation over the projected-starter distribution,
      using only usage patterns known well before the game."""
    parts = []
    for side, opp, is_home in (("h", "a", 1.0), ("a", "h", 0.0)):
        d = pd.DataFrame({
            "game_id": feat["game_id"].values, "season": feat["season"].values, "game_date": feat["game_date"].values,
            "is_home": is_home, "playoff": feat["playoff"].values, "log_lg": np.log(feat["lg_g"].values),
        })
        for c in TEAM_FEATS:
            d[f"t_{c}"] = feat[f"{side}_{c}"].values
            d[f"o_{c}"] = feat[f"{opp}_{c}"].values
        if goalie == "projected":
            d["o_gk"] = d["o_gk_proj"]
            d["o_gk_recent"] = np.nan
            d["o_gk_n"] = np.nan
            d["o_gk_idle"] = np.nan
        else:
            d["o_gk"] = d["o_gk"].fillna(d["o_gk_proj"])
        d["elo_diff"] = (d["t_elo"] - d["o_elo"]) / 100.0
        d["t_travel"] = d["t_travel"] / 1000.0
        d["o_travel"] = d["o_travel"] / 1000.0
        reg = feat["home_reg_goals" if side == "h" else "away_reg_goals"].values
        d["y"] = pd.to_numeric(pd.Series(reg), errors="coerce").values
        parts.append(d)
    return pd.concat(parts, ignore_index=True)


def columns_for(groups: list[str]) -> list[str]:
    cols: list[str] = []
    for g in groups:
        cols += GROUPS[g]
    return cols
