"""Relational schema (SQLAlchemy Core). Runs on SQLite locally and PostgreSQL
in production; the only dialect-specific code is the upsert helper."""
from __future__ import annotations

import datetime as dt
import math
from contextlib import contextmanager
from typing import Iterable, Sequence

import sqlalchemy as sa
from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer,
                        MetaData, String, Table, Text, UniqueConstraint)

from . import config

metadata = MetaData()


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# --- reference -------------------------------------------------------------
data_sources = Table(
    "data_sources", metadata,
    Column("key", String(40), primary_key=True),
    Column("name", String(120), nullable=False),
    Column("url", String(300), nullable=False),
    Column("license", Text, nullable=False),
    Column("attribution", Text, nullable=False),
    Column("status", String(20), nullable=False, default="active"),  # active | unavailable
)

ingestion_runs = Table(
    "ingestion_runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source_key", String(40), ForeignKey("data_sources.key"), nullable=False),
    Column("job", String(60), nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime),
    Column("status", String(20), nullable=False),  # running | ok | failed
    Column("rows_written", Integer, default=0),
    Column("detail", JSON),
    Column("error", Text),
    Index("ix_ingestion_source_started", "source_key", "started_at"),
)

teams = Table(
    "teams", metadata,
    Column("id", Integer, primary_key=True),  # NHL team id
    Column("abbrev", String(4), nullable=False),
    Column("name", String(60), nullable=False),
    Column("franchise_key", Integer, nullable=False),  # continuity across relocations
    Column("conference", String(20)),
    Column("division", String(20)),
    Column("color", String(9)),
    Column("timezone", String(40)),
    Column("lat", Float),
    Column("lon", Float),
    Column("active", Boolean, nullable=False, default=False),
)

players = Table(
    "players", metadata,
    Column("id", Integer, primary_key=True),  # NHL player id
    Column("full_name", String(80), nullable=False),
    Column("position", String(2), nullable=False),
)

games = Table(
    "games", metadata,
    Column("id", Integer, primary_key=True),  # NHL game id
    Column("season", Integer, nullable=False),  # start year, e.g. 2025
    Column("game_type", Integer, nullable=False),  # 2 regular, 3 playoff
    Column("game_date", Date, nullable=False),  # local (Eastern) date
    Column("start_time_utc", DateTime),
    Column("home_team_id", Integer, ForeignKey("teams.id"), nullable=False),
    Column("away_team_id", Integer, ForeignKey("teams.id"), nullable=False),
    Column("venue", String(80)),
    Column("state", String(12), nullable=False),  # FUT PRE LIVE CRIT FINAL OFF
    Column("schedule_state", String(12), nullable=False, default="OK"),  # OK PPD CNCL SUSP TBD
    Column("home_score", Integer),
    Column("away_score", Integer),
    Column("last_period_type", String(4)),  # REG OT SO
    Column("home_reg_goals", Integer),  # goals in regulation only
    Column("away_reg_goals", Integer),
    Column("updated_at", DateTime, nullable=False),
    Index("ix_games_date", "game_date"),
    Index("ix_games_season_type", "season", "game_type"),
    Index("ix_games_home", "home_team_id", "game_date"),
    Index("ix_games_away", "away_team_id", "game_date"),
)

team_game_stats = Table(
    "team_game_stats", metadata,
    Column("game_id", Integer, ForeignKey("games.id"), primary_key=True),
    Column("team_id", Integer, ForeignKey("teams.id"), primary_key=True),
    Column("is_home", Boolean, nullable=False),
    # all situations
    Column("toi_all", Float),
    Column("xgf", Float), Column("xga", Float),
    Column("fsva_xgf", Float), Column("fsva_xga", Float),  # flurry+score+venue adjusted
    Column("gf", Float), Column("ga", Float),
    Column("sog_f", Float), Column("sog_a", Float),
    Column("hd_xgf", Float), Column("hd_xga", Float),
    Column("hd_shots_f", Float), Column("hd_shots_a", Float),
    Column("pen_taken", Float), Column("pen_drawn", Float),
    # 5-on-5
    Column("toi_5v5", Float),
    Column("xgf_5v5", Float), Column("xga_5v5", Float),
    Column("sva_xgf_5v5", Float), Column("sva_xga_5v5", Float),
    Column("gf_5v5", Float), Column("ga_5v5", Float),
    Column("cf_5v5", Float), Column("ca_5v5", Float),
    # power play (5-on-4) and penalty kill (4-on-5)
    Column("toi_pp", Float), Column("xgf_pp", Float), Column("gf_pp", Float),
    Column("toi_pk", Float), Column("xga_pk", Float), Column("ga_pk", Float),
)

goalie_game_stats = Table(
    "goalie_game_stats", metadata,
    Column("game_id", Integer, ForeignKey("games.id"), primary_key=True),
    Column("player_id", Integer, ForeignKey("players.id"), primary_key=True),
    Column("team_id", Integer, ForeignKey("teams.id"), nullable=False),
    Column("started", Boolean, nullable=False),
    Column("toi", Float),  # seconds
    Column("shots_against", Integer),  # official shots on goal
    Column("goals_against", Integer),
    Column("xga", Float),  # MoneyPuck xG on unblocked attempts faced
    Column("ga_on_xg", Float),  # goals among those same attempts
    Column("attempts", Integer),
    Index("ix_ggs_player", "player_id", "game_id"),
    Index("ix_ggs_team", "team_id", "game_id"),
)

# Confirmed / projected starters observed before a game (forward only).
goalie_status = Table(
    "goalie_status", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("game_id", Integer, ForeignKey("games.id"), nullable=False),
    Column("team_id", Integer, ForeignKey("teams.id"), nullable=False),
    Column("player_id", Integer, ForeignKey("players.id"), nullable=False),
    Column("status", String(12), nullable=False),  # projected | confirmed
    Column("probability", Float, nullable=False),
    Column("source", String(40), nullable=False),
    Column("observed_at", DateTime, nullable=False),
    Index("ix_goalie_status_game", "game_id", "team_id", "observed_at"),
)

# Reserved for a licensed injury / lineup feed. No such source is integrated;
# the table stays empty and the API reports availability data as unavailable.
availability_reports = Table(
    "availability_reports", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("team_id", Integer, ForeignKey("teams.id"), nullable=False),
    Column("player_id", Integer, nullable=False),
    Column("status", String(20), nullable=False),
    Column("source_key", String(40), ForeignKey("data_sources.key"), nullable=False),
    Column("observed_at", DateTime, nullable=False),
)

# --- modelling ---------------------------------------------------------------
model_versions = Table(
    "model_versions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("version", String(40), nullable=False, unique=True),
    Column("created_at", DateTime, nullable=False),
    Column("trained_through", Date, nullable=False),
    Column("architecture", String(80), nullable=False),
    Column("config", JSON, nullable=False),
    Column("backtest_summary", JSON),
    Column("artifact_path", String(300)),
    Column("is_active", Boolean, nullable=False, default=False),
    Column("notes", Text),
)

training_runs = Table(
    "training_runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model_version_id", Integer, ForeignKey("model_versions.id"), nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime),
    Column("n_train_games", Integer),
    Column("params", JSON),
    Column("metrics", JSON),
)

prediction_runs = Table(
    "prediction_runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model_version_id", Integer, ForeignKey("model_versions.id"), nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime),
    Column("trigger", String(40), nullable=False),  # scheduled | goalie_confirmed | manual
    Column("n_games", Integer, default=0),
    Column("n_published", Integer, default=0),
    Column("status", String(20), nullable=False),
    Column("data_freshness", JSON),
)

feature_snapshots = Table(
    "feature_snapshots", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("game_id", Integer, ForeignKey("games.id"), nullable=False),
    Column("as_of", DateTime, nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("features", JSON, nullable=False),
    UniqueConstraint("game_id", "input_hash", name="uq_snapshot_game_hash"),
)

# Published forecasts are append-only. A new version is written when the
# model inputs change; earlier versions are never modified or deleted.
forecasts = Table(
    "forecasts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("game_id", Integer, ForeignKey("games.id"), nullable=False),
    Column("version_no", Integer, nullable=False),
    Column("model_version_id", Integer, ForeignKey("model_versions.id"), nullable=False),
    Column("prediction_run_id", Integer, ForeignKey("prediction_runs.id")),
    Column("feature_snapshot_id", Integer, ForeignKey("feature_snapshots.id")),
    Column("published_at", DateTime, nullable=False),
    Column("information_cutoff", DateTime, nullable=False),
    Column("is_latest", Boolean, nullable=False, default=True),
    Column("published_pregame", Boolean, nullable=False),
    Column("goalie_state", String(12), nullable=False),  # projected | partial | confirmed
    Column("reason", String(40), nullable=False),  # initial | goalie_confirmed | inputs_changed
    Column("input_hash", String(64), nullable=False),
    Column("p_home_win", Float, nullable=False),
    Column("p_away_win", Float, nullable=False),
    Column("p_home_reg", Float, nullable=False),
    Column("p_tie_reg", Float, nullable=False),
    Column("p_away_reg", Float, nullable=False),
    Column("p_overtime", Float, nullable=False),  # decided in OT
    Column("p_shootout", Float, nullable=False),  # decided in SO
    Column("exp_home_goals", Float, nullable=False),  # expected final goals
    Column("exp_away_goals", Float, nullable=False),
    Column("lam_home", Float, nullable=False),  # regulation scoring rates
    Column("lam_away", Float, nullable=False),
    Column("ml_home_goals", Integer, nullable=False),  # most likely final score
    Column("ml_away_goals", Integer, nullable=False),
    Column("ml_score_prob", Float, nullable=False),
    Column("n_sims", Integer, nullable=False),
    Column("seed", Integer, nullable=False),
    Column("summary", JSON, nullable=False),  # top scores, totals, drivers, goalies, uncertainty
    UniqueConstraint("game_id", "version_no", name="uq_forecast_game_version"),
    Index("ix_forecasts_game_latest", "game_id", "is_latest"),
)

score_distributions = Table(
    "score_distributions", metadata,
    Column("forecast_id", Integer, ForeignKey("forecasts.id"), primary_key=True),
    Column("max_goals", Integer, nullable=False),
    Column("final_matrix", JSON, nullable=False),  # [home][away], final score incl. OT/SO
    Column("regulation_matrix", JSON, nullable=False),
    Column("overflow_mass", Float, nullable=False),
)

# One row per forecast, written exactly once (unique forecast_id).
evaluations = Table(
    "evaluations", metadata,
    Column("forecast_id", Integer, ForeignKey("forecasts.id"), primary_key=True),
    Column("game_id", Integer, ForeignKey("games.id"), nullable=False),
    Column("settled_at", DateTime, nullable=False),
    Column("home_score", Integer, nullable=False),
    Column("away_score", Integer, nullable=False),
    Column("last_period_type", String(4), nullable=False),
    Column("home_won", Boolean, nullable=False),
    Column("winner_correct", Boolean, nullable=False),
    Column("p_winner", Float, nullable=False),
    Column("log_loss", Float, nullable=False),
    Column("brier", Float, nullable=False),
    Column("exact_score_hit", Boolean, nullable=False),
    Column("p_actual_score", Float, nullable=False),
    Column("abs_err_home", Float, nullable=False),
    Column("abs_err_away", Float, nullable=False),
    Column("abs_err_total", Float, nullable=False),
    Index("ix_evaluations_game", "game_id"),
)

# Walk-forward backtest output. Kept apart from `forecasts` so simulated
# history can never be confused with forecasts that were actually published.
backtest_predictions = Table(
    "backtest_predictions", metadata,
    Column("model_key", String(40), primary_key=True),
    Column("game_id", Integer, ForeignKey("games.id"), primary_key=True),
    Column("season", Integer, nullable=False),
    Column("game_date", Date, nullable=False),
    Column("p_home_win", Float, nullable=False),
    Column("lam_home", Float, nullable=False),
    Column("lam_away", Float, nullable=False),
    Column("exp_home_goals", Float, nullable=False),
    Column("exp_away_goals", Float, nullable=False),
    Column("ml_home_goals", Integer, nullable=False),
    Column("ml_away_goals", Integer, nullable=False),
    Column("p_actual_score", Float, nullable=False),
    Column("p_overtime_or_so", Float, nullable=False),
    Index("ix_backtest_season", "model_key", "season"),
)

team_ratings = Table(
    "team_ratings", metadata,
    Column("team_id", Integer, ForeignKey("teams.id"), primary_key=True),
    Column("as_of_game_id", Integer, primary_key=True),  # rating AFTER this game
    Column("season", Integer, nullable=False),
    Column("game_date", Date, nullable=False),
    Column("elo", Float),
    Column("off_g", Float), Column("def_g", Float),     # regulation goals / game vs league average
    Column("off_xg", Float), Column("def_xg", Float),   # 5v5 xG / 60 vs league average
    Index("ix_team_ratings_season", "team_id", "season"),
)

_engine: sa.Engine | None = None


def engine() -> sa.Engine:
    global _engine
    if _engine is None:
        config.ensure_dirs()
        url = config.DATABASE_URL
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        kwargs = {"connect_args": {"timeout": 60}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        _engine = sa.create_engine(url, future=True, **kwargs)
        if url.startswith("sqlite"):
            @sa.event.listens_for(_engine, "connect")
            def _pragmas(dbapi_conn, _):  # pragma: no cover - driver hook
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
    return _engine


def reset_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def create_all() -> None:
    metadata.create_all(engine())


@contextmanager
def begin():
    with engine().begin() as conn:
        yield conn


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            v = v.item()
        except Exception:
            pass
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
    return v


def upsert(conn, table: Table, rows: Iterable[dict], keys: Sequence[str] | None = None,
           update: bool = True, chunk: int = 400) -> int:
    """Idempotent insert-or-update on the table's primary key (or `keys`)."""
    rows = [{k: _clean(v) for k, v in r.items()} for r in rows]
    if not rows:
        return 0
    keys = list(keys or [c.name for c in table.primary_key.columns])
    dialect = conn.dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:  # pragma: no cover
        raise RuntimeError(f"unsupported dialect {dialect}")
    # A multi-row INSERT needs identical columns in every row, so rows are
    # grouped by the set of columns they carry.
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(tuple(sorted(r)), []).append(r)
    n = 0
    for group in groups.values():
        # stay under SQLite's bound-parameter limit
        step = max(1, min(chunk, 30000 // max(len(group[0]), 1)))
        for i in range(0, len(group), step):
            part = group[i:i + step]
            stmt = insert(table).values(part)
            cols = [c for c in part[0].keys() if c not in keys]
            if update and cols:
                stmt = stmt.on_conflict_do_update(index_elements=keys, set_={c: stmt.excluded[c] for c in cols})
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=keys)
            n += conn.execute(stmt).rowcount or 0
    return n


class IngestionRun:
    """Context manager that records an ingestion attempt and its outcome."""

    def __init__(self, source_key: str, job: str):
        self.source_key, self.job = source_key, job
        self.rows = 0
        self.detail: dict = {}
        self.id: int | None = None

    def __enter__(self):
        with begin() as c:
            self.id = c.execute(ingestion_runs.insert().values(
                source_key=self.source_key, job=self.job, started_at=utcnow(), status="running",
            )).inserted_primary_key[0]
        return self

    def __exit__(self, exc_type, exc, tb):
        with begin() as c:
            c.execute(ingestion_runs.update().where(ingestion_runs.c.id == self.id).values(
                finished_at=utcnow(), status="failed" if exc else "ok", rows_written=self.rows,
                detail=self.detail or None, error=repr(exc)[:2000] if exc else None,
            ))
        return False


def last_success(conn, source_key: str) -> dt.datetime | None:
    return conn.execute(
        sa.select(sa.func.max(ingestion_runs.c.finished_at))
        .where(ingestion_runs.c.source_key == source_key, ingestion_runs.c.status == "ok")
    ).scalar()
