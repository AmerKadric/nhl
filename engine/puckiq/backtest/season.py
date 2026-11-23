"""Validation of the season simulation against past seasons.

For each checkpoint date in each past season, everything from that date on
is hidden: later results, later box scores, later lineups. Features are
rebuilt and the model retrained from what was known, the rest of the season
is simulated, and the simulated odds are compared with what happened:
which teams made the playoffs, how many points each finished with, and who
won the Cup."""
from __future__ import annotations

import datetime as dt
import json
import logging

import numpy as np
import pandas as pd

from .. import config
from ..features.build import FeatureBuilder, Frames, load_frames
from ..models.forecaster import Forecaster
from ..sim import season as S

log = logging.getLogger("puckiq.backtest.season")

# Seasons with the standard 16-team format and a full schedule. 2019-20 was
# cut short and 2020-21 used temporary divisions, so both are left out.
SEASONS = [2017, 2018, 2021, 2022, 2023, 2024, 2025]
CHECKPOINTS = [(12, 1), (2, 1)]
BINS = [(0.0, 0.1), (0.1, 0.3), (0.3, 0.7), (0.7, 0.9), (0.9, 1.0001)]
# The strength-uncertainty multiplier is chosen on these seasons and checked
# on the rest.
SCALE_FIT_SEASONS = [2017, 2018, 2021, 2022]
SCALE_GRID = (1.0, 1.5, 2.0, 2.5, 3.0)


def alignment_for(season: int, fr: Frames) -> dict[int, tuple[str, str]]:
    """Conference and division of each team in a past season, from today's
    alignment with the known changes undone (Arizona moved from the Pacific
    to the Central in 2021-22; the Utah club inherits Arizona's slot)."""
    t = fr.teams
    cur = {int(r.franchise_key): (r.conference, r.division) for r in t.itertuples() if bool(r.active) and r.division}
    abbrev = t.set_index("id")["abbrev"].to_dict()
    fk = t.set_index("id")["franchise_key"].to_dict()
    g = fr.games[(fr.games["season"] == season) & (fr.games["game_type"] == config.REGULAR)]
    out = {}
    for tid in sorted(set(g["home_team_id"]) | set(g["away_team_id"])):
        conf_div = cur[int(fk[tid])]
        if season <= 2020 and abbrev[tid] == "ARI":
            conf_div = ("Western", "Pacific")
        out[int(tid)] = conf_div
    return out


def truncate(fr: Frames, cut: dt.date) -> Frames:
    """The data as it stood on the morning of `cut`."""
    g = fr.games.copy()
    later = g["game_date"] >= cut
    for c in ("home_score", "away_score", "home_reg_goals", "away_reg_goals", "last_period_type"):
        g[c] = g[c].astype(object)
        g.loc[later, c] = None
    g.loc[later, "state"] = "FUT"
    g.loc[later, "schedule_state"] = "OK"
    ids = set(g.loc[later, "id"])
    return Frames(g, fr.tgs[~fr.tgs["game_id"].isin(ids)], fr.ggs[~fr.ggs["game_id"].isin(ids)], fr.teams, fr.players,
                  fr.sgs[~fr.sgs["game_id"].isin(ids)])


def actuals(fr: Frames, season: int, team_ids: list[int]) -> pd.DataFrame:
    g = fr.games[(fr.games["season"] == season) & fr.games["home_score"].notna()]
    st = S.current_standings(g[g["game_type"] == config.REGULAR], team_ids)
    po = g[g["game_type"] == config.PLAYOFF].sort_values(["game_date", "id"])
    made = set(po["home_team_id"]) | set(po["away_team_id"])
    last = po.iloc[-1]
    champ = int(last["home_team_id"] if last["home_score"] > last["away_score"] else last["away_team_id"])
    return pd.DataFrame({"team_id": team_ids, "final_points": st["pts"], "made_playoffs": [t in made for t in team_ids],
                         "champion": [t == champ for t in team_ids]})


def in_position(d: pd.DataFrame) -> pd.Series:
    """Naive baseline: is the team in a playoff position today (by points
    percentage, under the real format)?"""
    d = d.copy()
    d["pp"] = d["points"] / (2 * d["gp"].clip(lower=1))
    inpos = pd.Series(False, index=d.index)
    for _, c in d.groupby("conference"):
        top = c.sort_values("pp", ascending=False).groupby("division").head(3).index
        inpos[top] = True
        rest = c.drop(index=top).sort_values("pp", ascending=False).head(2).index
        inpos[rest] = True
    return inpos


def run(groups: list[str] | None = None, n_sims: int = 10000, seasons: list[int] | None = None, scale: float = 1.0,
        persist: bool = True, study: bool = True) -> dict:
    fr = load_frames()
    cases = []
    for season in seasons or SEASONS:
        al = alignment_for(season, fr)
        act = actuals(fr, season, sorted(al))
        for month, day in CHECKPOINTS:
            cut = dt.date(season if month >= 9 else season + 1, month, day)
            tr = truncate(fr, cut)
            feat = FeatureBuilder(tr).run()
            fc = Forecaster.train(feat, groups)
            inp = S.prepare(feat, tr.games, fc, season, al)
            if inp is not None:
                cases.append((season, month, day, inp, act, inp.sd_off.copy(), inp.sd_def.copy()))
            log.info("prepared %s %s", season, cut)

    def simulate_all(k: float, sims: int) -> pd.DataFrame:
        rows = []
        for season, month, day, inp, act, so, sd in cases:
            inp.sd_off, inp.sd_def = so * k, sd * k
            out = S.simulate(inp, n_sims=sims, seed=season * 100 + month).merge(act, on="team_id")
            out["season"], out["checkpoint"] = season, f"{month:02d}-{day:02d}"
            out["in_position"] = in_position(out)
            rows.append(out)
        return pd.concat(rows, ignore_index=True)

    d = simulate_all(scale, n_sims)
    summary = summarise(d)
    summary.update({"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "seasons": seasons or SEASONS,
                    "checkpoints": [f"{m:02d}-{dd:02d}" for m, dd in CHECKPOINTS], "n_sims": n_sims, "strength_sd_scale": scale})
    if study:
        table = []
        for k in SCALE_GRID:
            x = simulate_all(k, 6000)
            fit, later = x[x["season"].isin(SCALE_FIT_SEASONS)], x[~x["season"].isin(SCALE_FIT_SEASONS)]

            def cover(v):
                return float(((v["final_points"] >= v["points_p10"]) & (v["final_points"] <= v["points_p90"])).mean()) if len(v) else None

            def brier(v):
                return float(((v["p_playoffs"] - v["made_playoffs"].astype(float)) ** 2).mean()) if len(v) else None
            table.append({"scale": k, "fit_coverage": cover(fit), "later_coverage": cover(later), "fit_brier": brier(fit), "later_brier": brier(later)})
        summary["scale_study"] = {"fit_seasons": SCALE_FIT_SEASONS, "nominal_coverage": 0.8, "grid": table}
    if persist:
        p = config.ARTIFACT_DIR / "backtest"
        p.mkdir(parents=True, exist_ok=True)
        (p / "season_sim.json").write_text(json.dumps(summary, indent=1))
    return {"summary": summary, "rows": d}


def summarise(d: pd.DataFrame) -> dict:
    y = d["made_playoffs"].astype(float)
    p = d["p_playoffs"]

    def block(x: pd.DataFrame) -> dict:
        yy, pp = x["made_playoffs"].astype(float), x["p_playoffs"]
        champs = x[x["champion"]]
        return {
            "team_seasons": int(len(x)),
            "playoff_brier": float(((pp - yy) ** 2).mean()),
            "playoff_brier_in_position_baseline": float(((x["in_position"].astype(float) - yy) ** 2).mean()),
            "playoff_brier_coin_flip": 0.25,
            "playoff_log_loss": float(-(yy * np.log(pp.clip(1e-4, 1 - 1e-4)) + (1 - yy) * np.log((1 - pp).clip(1e-4, 1 - 1e-4))).mean()),
            "points_mae": float((x["proj_points"] - x["final_points"]).abs().mean()),
            "points_interval_coverage": float(((x["final_points"] >= x["points_p10"]) & (x["final_points"] <= x["points_p90"])).mean()),
            "points_interval_nominal": 0.8,
            "champion_mean_prob": float(champs["p_cup"].mean()),
            "champion_log_prob": float(np.log(champs["p_cup"].clip(1e-4)).mean()),
            "champion_uniform_prob": 1.0 / 16.0,
            "n_champions": int(len(champs)),
        }
    cal = []
    for lo, hi in BINS:
        m = (p >= lo) & (p < hi)
        if m.sum():
            cal.append({"lo": lo, "hi": min(hi, 1.0), "n": int(m.sum()), "mean_pred": float(p[m].mean()), "observed": float(y[m].mean())})
    return {"overall": block(d), "by_checkpoint": {k: block(v) for k, v in d.groupby("checkpoint")},
            "by_season": {str(k): block(v) for k, v in d.groupby("season")}, "calibration": cal}


def reconstruct_history(season: int, groups: list[str] | None, before: dt.date, every: int = 1, n_sims: int = 20000) -> dict:
    """What the season simulation would have said on each earlier morning of
    `season`, for dates before the first one it was actually published.

    Each date is treated exactly like a backtest checkpoint: results, box
    scores and lineups from that date on are hidden, features are rebuilt and
    the model is retrained from what was known. These are reconstructions,
    not forecasts that were on the site at the time, and are stored and
    labelled as such."""
    fr = load_frames()
    al = S.alignment_from_teams(fr.teams)
    reg = fr.games[(fr.games["season"] == season) & (fr.games["game_type"] == config.REGULAR)]
    day = reg["game_date"].min()
    dates, teams, stakes = [], {}, {}
    while day < before:
        tr = truncate(fr, day)
        feat = FeatureBuilder(tr).run()
        fc = Forecaster.train(feat, groups)
        inp = S.prepare(feat, tr.games, fc, season, al)
        if inp is not None:
            inp.sd_off, inp.sd_def = inp.sd_off * S.STRENGTH_SD_SCALE, inp.sd_def * S.STRENGTH_SD_SCALE
            track = S.next_slate(inp)
            out, detail = S.simulate_detail(inp, n_sims=n_sims, seed=int(day.strftime("%Y%m%d")), track=track)
            if detail is not None and inp.game_dates[track[0]] == day:      # games played that very day
                stakes[str(day)] = {"raw": S.leverage(inp, detail, adjusted=False), "adjusted": S.leverage(inp, detail, adjusted=True)}
            dates.append(str(day))
            for r in out.itertuples():
                h = teams.setdefault(str(int(r.team_id)), {"p_playoffs": [], "p_cup": [], "proj_points": []})
                h["p_playoffs"].append(round(float(r.p_playoffs), 4))
                h["p_cup"].append(round(float(r.p_cup), 4))
                h["proj_points"].append(round(float(r.proj_points), 1))
            log.info("reconstructed %s", day)
        day += dt.timedelta(days=every)
    return {"dates": dates, "teams": teams, "stakes": stakes}


def leverage_check(dates: list[str], teams: dict, stakes: dict, games: pd.DataFrame) -> dict:
    """Did the stakes shown for a game match what then happened to the odds?

    For every game with stakes on day D and a simulation on day D+1, the
    predicted move for each team (odds if it won, or if it lost, minus its
    odds that morning) is compared with the actual move in its odds by the
    next morning. Other results that night also move a team's odds, which
    adds scatter but no bias. Both ways of splitting the simulation are
    scored so the better one can be chosen on evidence."""
    idx = {d: i for i, d in enumerate(dates)}
    res = games.set_index("id")[["home_score", "away_score"]]
    rows = []
    for day, both in stakes.items():
        nxt = str(dt.date.fromisoformat(day) + dt.timedelta(days=1))
        if day not in idx or nxt not in idx:
            continue
        raw = {g["game_id"]: g for g in both["raw"]}
        for g in both["adjusted"]:
            gid = g["game_id"]
            if gid not in res.index or pd.isna(res.at[gid, "home_score"]):
                continue                                           # postponed or not played
            home_won = res.at[gid, "home_score"] > res.at[gid, "away_score"]
            for side, won in (("home", home_won), ("away", not home_won)):
                a, r = g[side], raw[gid][side]
                h = teams.get(str(a["team_id"]))
                if not h or h["p_playoffs"][idx[day]] is None or h["p_playoffs"][idx[nxt]] is None:
                    continue
                rows.append({"won": bool(won), "actual": h["p_playoffs"][idx[nxt]] - h["p_playoffs"][idx[day]],
                             "adjusted": (a["if_win"] if won else a["if_loss"]) - a["now"],
                             "raw": (r["if_win"] if won else r["if_loss"]) - r["now"]})
    d = pd.DataFrame(rows)
    if d.empty:
        return {"n": 0}

    def score(col: str) -> dict:
        x, y = d[col], d["actual"]
        return {"rmse": float(np.sqrt(((y - x) ** 2).mean())), "slope": float((x * y).sum() / (x * x).sum()),
                "mean_predicted_winners": float(x[d["won"]].mean()), "mean_predicted_losers": float(x[~d["won"]].mean())}

    return {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "n": int(len(d)), "days": int(len(stakes)),
            "first_day": min(stakes), "last_day": max(stakes),
            "mean_actual_winners": float(d.loc[d["won"], "actual"].mean()), "mean_actual_losers": float(d.loc[~d["won"], "actual"].mean()),
            "rmse_no_change": float(np.sqrt((d["actual"] ** 2).mean())),
            "adjusted": score("adjusted"), "raw": score("raw")}
