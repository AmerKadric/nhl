"""Betting-market benchmark.

The NHL's own feed carries same-day odds from its sportsbook partners. They
are recorded before each game and used for ONE purpose: to measure PUCKIQ
against the strongest public reference there is. The model never sees them,
individual prices are never displayed, and nothing here is betting advice.

Odds are turned into probabilities by removing the bookmaker's margin
(normalising the two sides of a market so they sum to one)."""
from __future__ import annotations

import datetime as dt
import logging
import math

import numpy as np
import pandas as pd
import sqlalchemy as sa

from . import config, db
from .ingest.http import FetchError, client

log = logging.getLogger("puckiq.market")

# country code in the NHL feed -> the partner it returns
FEEDS = ("US", "CA")
MARKETS = {"MONEY_LINE_2_WAY": "ML2", "MONEY_LINE_3_WAY": "ML3", "PUCK_LINE": "PL", "OVER_UNDER": "OU"}


def implied(american: float) -> float:
    """Raw implied probability of American odds (still includes the margin)."""
    a = float(american)
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def no_vig(a: float, b: float) -> float:
    """Margin-free probability of side `a` in a two-sided market."""
    pa, pb = implied(a), implied(b)
    return pa / (pa + pb)


def parse_partner_games(payload: dict) -> tuple[str, dt.datetime | None, list[dict]]:
    """Flatten one partner feed into rows: one per game, market and side."""
    provider = ((payload.get("bettingPartner") or {}).get("name") or "unknown").lower()
    updated = payload.get("lastUpdatedUTC")
    updated_at = dt.datetime.fromisoformat(updated.replace("Z", "+00:00")).replace(tzinfo=None) if updated else None
    rows = []
    for g in payload.get("games", []):
        for side, key in (("home", "homeTeam"), ("away", "awayTeam")):
            for o in (g.get(key) or {}).get("odds", []):
                market = MARKETS.get(o.get("description"))
                if market is None or o.get("value") is None:
                    continue
                q = (o.get("qualifier") or "").strip()
                s, line = side, None
                if market == "ML3" and q.lower() == "draw":
                    if side == "away":
                        continue   # the draw price is repeated on both teams
                    s = "draw"
                elif market == "OU":
                    s = "over" if q[:1].upper() == "O" else "under"
                    line = float(q[1:]) if len(q) > 1 else None
                elif market == "PL":
                    line = float(q) if q else None
                rows.append({"game_id": g["gameId"], "provider": provider, "market": market, "side": s, "line": line,
                             "american": float(o["value"])})
    return provider, updated_at, rows


def ingest(now: dt.datetime | None = None) -> int:
    """Record today's pre-game odds. A feed whose timestamp is already stored
    is skipped, and nothing is recorded for a game once it has started."""
    now = now or db.utcnow()
    total = 0
    with db.IngestionRun("nhl_odds", "market_odds") as run:
        with db.begin() as c:
            starts = {r.id: r.start_time_utc for r in c.execute(sa.select(db.games.c.id, db.games.c.start_time_utc)
                                                                .where(db.games.c.game_date >= now.date() - dt.timedelta(days=1)))}
        for country in FEEDS:
            try:
                payload = client().get_json(f"{config.NHL_WEB_BASE}/partner-game/{country}/now")
            except FetchError as e:
                log.warning("odds feed %s unavailable: %s", country, e)
                continue
            provider, updated_at, rows = parse_partner_games(payload)
            stamp = updated_at or now
            rows = [dict(r, observed_at=now, source_updated_at=stamp) for r in rows
                    if starts.get(r["game_id"]) is not None and starts[r["game_id"]] > now]
            if not rows:
                continue
            with db.begin() as c:
                seen = c.execute(sa.select(sa.func.count()).select_from(db.market_odds).where(
                    db.market_odds.c.provider == provider, db.market_odds.c.source_updated_at == stamp)).scalar()
                if seen:
                    continue
                c.execute(db.market_odds.insert(), rows)
            total += len(rows)
        run.rows = total
    return total


def consensus(odds: pd.DataFrame, games: pd.DataFrame, which: str) -> pd.DataFrame:
    """Margin-free market probabilities per game, averaged over providers.

    `which` is "latest" (the last prices recorded before puck drop) or
    "first" (the first prices recorded, usually the morning of the game)."""
    if odds.empty:
        return pd.DataFrame(columns=["game_id", "p_home", "providers", "minutes_before", "ou_line", "p_over"])
    o = odds.merge(games[["id", "start_time_utc"]], left_on="game_id", right_on="id")
    o = o[pd.to_datetime(o["observed_at"]) < pd.to_datetime(o["start_time_utc"])]
    if o.empty:
        return pd.DataFrame(columns=["game_id", "p_home", "providers", "minutes_before", "ou_line", "p_over"])
    pick = o.groupby(["game_id", "provider"])["observed_at"].transform("max" if which == "latest" else "min")
    o = o[o["observed_at"] == pick]
    out = []
    for gid, g in o.groupby("game_id"):
        ph, po, lines = [], [], []
        for _, p in g.groupby("provider"):
            ml = p[p["market"] == "ML2"].set_index("side")["american"]
            if {"home", "away"} <= set(ml.index):
                ph.append(no_vig(ml["home"], ml["away"]))
            ou = p[p["market"] == "OU"]
            if {"over", "under"} <= set(ou["side"]) and ou["line"].nunique() == 1:
                s = ou.set_index("side")["american"]
                po.append(no_vig(s["over"], s["under"]))
                lines.append(float(ou["line"].iloc[0]))
        if not ph:
            continue
        mins = (pd.to_datetime(g["start_time_utc"]) - pd.to_datetime(g["observed_at"])).dt.total_seconds().min() / 60.0
        same_line = len(set(lines)) == 1
        out.append({"game_id": int(gid), "p_home": float(np.mean(ph)), "providers": len(ph), "minutes_before": float(mins),
                    "ou_line": lines[0] if same_line else None, "p_over": float(np.mean(po)) if same_line else None})
    return pd.DataFrame(out)


def _score(p_home: np.ndarray, home_won: np.ndarray) -> dict:
    p = np.clip(p_home, 1e-9, 1 - 1e-9)
    ll = -(home_won * np.log(p) + (1 - home_won) * np.log(1 - p))
    return {"accuracy": float(((p >= 0.5) == (home_won == 1)).mean()), "log_loss": float(ll.mean()),
            "brier": float(((p - home_won) ** 2).mean()), "_ll": ll}


def benchmark() -> dict:
    """PUCKIQ and the market scored on exactly the same finished games."""
    F, G, E = db.forecasts, db.games, db.evaluations
    with db.begin() as c:
        odds = pd.read_sql(sa.select(db.market_odds), c)
        fc = pd.read_sql(sa.select(F.c.game_id, F.c.p_home_win, F.c.summary, G.c.home_score, G.c.away_score, G.c.start_time_utc, G.c.id)
                         .join(G, G.c.id == F.c.game_id).join(E, E.c.forecast_id == F.c.id)
                         .where(F.c.is_latest == sa.true()), c)
        games = pd.read_sql(sa.select(G.c.id, G.c.start_time_utc).where(G.c.id.in_(odds["game_id"].unique().tolist())), c) if len(odds) else pd.DataFrame(columns=["id", "start_time_utc"])
    out: dict = {"generated_at": db.utcnow().isoformat(timespec="seconds"), "games_with_odds": int(odds["game_id"].nunique()) if len(odds) else 0,
                 "providers": sorted(odds["provider"].unique().tolist()) if len(odds) else [], "latest": None, "first": None}
    if odds.empty or fc.empty:
        return out
    fc["home_won"] = (fc["home_score"] > fc["away_score"]).astype(float)
    fc["total"] = fc["home_score"] + fc["away_score"]
    for which in ("latest", "first"):
        m = consensus(odds, games, which).merge(fc, on="game_id")
        if m.empty:
            continue
        y = m["home_won"].to_numpy()
        ours, mkt = _score(m["p_home_win"].to_numpy(), y), _score(m["p_home"].to_numpy(), y)
        diff = ours.pop("_ll") - mkt.pop("_ll")
        block = {"n": int(len(m)), "puckiq": ours, "market": mkt,
                 "log_loss_diff": float(diff.mean()),   # negative = PUCKIQ better
                 "log_loss_diff_se": float(diff.std(ddof=1) / math.sqrt(len(diff))) if len(diff) > 1 else None,
                 "median_minutes_before": float(m["minutes_before"].median()),
                 "mean_abs_gap": float((m["p_home_win"] - m["p_home"]).abs().mean())}
        t = m[m["p_over"].notna()].copy()
        if len(t):
            def p_over(row) -> float:
                s = row["summary"] if isinstance(row["summary"], dict) else __import__("json").loads(row["summary"])
                totals = s.get("totals") or []
                return float(sum(totals[int(math.floor(row["ou_line"])) + 1:]))
            t["ours_over"] = t.apply(p_over, axis=1)
            over = (t["total"] > t["ou_line"]).astype(float).to_numpy()
            block["totals"] = {"n": int(len(t)), "puckiq_brier": float(((t["ours_over"] - over) ** 2).mean()),
                               "market_brier": float(((t["p_over"] - over) ** 2).mean()), "over_rate": float(over.mean())}
        out[which] = block
    return out
