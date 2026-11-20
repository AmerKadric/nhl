"""Settlement: score every published forecast against the final result.

Idempotent by construction: `evaluations.forecast_id` is the primary key and
rows are inserted with ON CONFLICT DO NOTHING, so retries and overlapping
runs can never settle a forecast twice."""
from __future__ import annotations

import logging
import math

import sqlalchemy as sa

from .. import db

log = logging.getLogger("puckiq.settle")


def evaluate(f, matrix: list[list[float]], home_score: int, away_score: int, last_period_type: str) -> dict:
    home_won = home_score > away_score
    p_w = f.p_home_win if home_won else f.p_away_win
    p_w = min(max(p_w, 1e-9), 1 - 1e-9)
    n = len(matrix)
    p_score = matrix[home_score][away_score] if home_score < n and away_score < n else 0.0
    return dict(
        forecast_id=f.id, game_id=f.game_id, settled_at=db.utcnow(), home_score=home_score, away_score=away_score,
        last_period_type=last_period_type, home_won=home_won, winner_correct=(f.p_home_win >= 0.5) == home_won,
        p_winner=p_w, log_loss=-math.log(p_w), brier=(f.p_home_win - (1.0 if home_won else 0.0)) ** 2,
        exact_score_hit=(f.ml_home_goals == home_score and f.ml_away_goals == away_score), p_actual_score=float(p_score),
        abs_err_home=abs(f.exp_home_goals - home_score), abs_err_away=abs(f.exp_away_goals - away_score),
        abs_err_total=abs(f.exp_home_goals + f.exp_away_goals - home_score - away_score),
    )


def settle() -> int:
    """Evaluate all unsettled forecasts whose game is final. Returns the
    number of evaluations written."""
    F, G, E, S = db.forecasts, db.games, db.evaluations, db.score_distributions
    with db.begin() as c:
        pending = c.execute(
            sa.select(F.c.id, F.c.game_id, F.c.p_home_win, F.c.p_away_win, F.c.exp_home_goals, F.c.exp_away_goals,
                      F.c.ml_home_goals, F.c.ml_away_goals, G.c.home_score, G.c.away_score, G.c.last_period_type,
                      S.c.final_matrix)
            .join(G, G.c.id == F.c.game_id).join(S, S.c.forecast_id == F.c.id)
            .outerjoin(E, E.c.forecast_id == F.c.id)
            .where(E.c.forecast_id.is_(None), G.c.state == "OFF", G.c.home_score.isnot(None),
                   G.c.last_period_type.isnot(None))).fetchall()
        rows = [evaluate(f, f.final_matrix, int(f.home_score), int(f.away_score), f.last_period_type) for f in pending]
        n = db.upsert(c, E, rows, update=False)
    if n:
        log.info("settled %d forecasts", n)
    return n
