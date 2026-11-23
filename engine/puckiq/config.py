"""Central configuration. Everything environment-specific comes from env vars."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = Path(os.environ.get("PUCKIQ_DATA_DIR", REPO_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
ARTIFACT_DIR = Path(os.environ.get("PUCKIQ_ARTIFACT_DIR", REPO_ROOT / "artifacts"))

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'puckiq.db').as_posix()}")

# True on serverless hosts (read-only filesystem, many short-lived instances).
SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("PUCKIQ_SERVERLESS"))

# Identifies us honestly to the data providers. No personal details.
USER_AGENT = os.environ.get("PUCKIQ_USER_AGENT", "PUCKIQ/0.1 (non-commercial hockey research)")

NHL_STATS_BASE = "https://api.nhle.com/stats/rest/en"
NHL_WEB_BASE = "https://api-web.nhle.com/v1"
MONEYPUCK_TEAMS_URL = "https://moneypuck.com/moneypuck/playerData/careers/gameByGame/all_teams.csv"
MONEYPUCK_SHOTS_URL = "https://peter-tanner.com/moneypuck/downloads/shots_{year}.zip"

# First season (start year) ingested. 2011-12 is the first season with the
# current Winnipeg franchise, which keeps team identities continuous.
FIRST_SEASON = int(os.environ.get("PUCKIQ_FIRST_SEASON", "2011"))

# Seasons before this are used only to warm up ratings; models never train or
# test on them.
FIRST_MODEL_SEASON = 2013
# First season scored in the walk-forward backtest.
FIRST_TEST_SEASON = 2017

N_SIMS = int(os.environ.get("PUCKIQ_N_SIMS", "20000"))
MAX_GOALS = 12  # score grid is 0..MAX_GOALS per team; mass beyond is tracked

REGULAR, PLAYOFF = 2, 3

# Data older than this (hours) is flagged stale in the API and UI.
STALE_AFTER_HOURS = {"nhl_games": 30, "moneypuck_teams": 54, "moneypuck_shots": 54, "nhl_goalies": 54}


def season_id(start_year: int) -> int:
    return start_year * 10000 + start_year + 1


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[2:]}"


def ensure_dirs() -> None:
    for d in (DATA_DIR, RAW_DIR, CACHE_DIR, ARTIFACT_DIR):
        d.mkdir(parents=True, exist_ok=True)
