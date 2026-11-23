"""Command line entry point: `puckiq <command>`."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys

from . import config, db


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(timespec="seconds"),
               "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out)


def setup_logging() -> None:
    h = logging.StreamHandler(sys.stderr)
    if os.environ.get("PUCKIQ_LOG_JSON", "0") == "1":
        h.setFormatter(JsonFormatter())
    else:
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=os.environ.get("PUCKIQ_LOG_LEVEL", "INFO"), handlers=[h], force=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="puckiq", description="PUCKIQ forecasting engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="create tables and seed reference data")
    p = sub.add_parser("ingest-history", help="load all historical seasons (one-off bootstrap)")
    p.add_argument("--from-season", type=int, default=config.FIRST_SEASON)
    sub.add_parser("tune", help="tune rating hyper-parameters on pre-backtest seasons")
    sub.add_parser("backtest", help="walk-forward backtest of all candidate models")
    sub.add_parser("train", help="train and activate the production model")
    p = sub.add_parser("daily", help="full daily pipeline")
    p.add_argument("--retrain", action="store_true")
    sub.add_parser("refresh", help="light refresh: states, goalie confirmations, forecasts, settlement")
    sub.add_parser("settle", help="settle finished forecasts")
    sub.add_parser("status", help="print data freshness")
    p = sub.add_parser("copy-db", help="copy all tables from another database into DATABASE_URL")
    p.add_argument("--from", dest="source", required=True, help="source SQLAlchemy URL, e.g. sqlite:///data/puckiq.db")
    p = sub.add_parser("serve", help="run the API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    setup_logging()
    config.ensure_dirs()

    if args.cmd == "init-db":
        from .ingest import nhl
        db.create_all()
        nhl.seed_reference()
        print("teams:", nhl.ingest_teams())
    elif args.cmd == "ingest-history":
        from .ingest import moneypuck, nhl
        from .pipeline.daily import current_season
        db.create_all()
        nhl.seed_reference()
        nhl.ingest_teams()
        seasons = list(range(args.from_season, current_season() + 1))
        print("games:", nhl.ingest_games(seasons))
        print("schedule:", nhl.sync_schedule(dt.date.today() - dt.timedelta(days=3), weeks=3))
        print("team games:", moneypuck.ingest_team_games(args.from_season))
        print("goalie logs:", nhl.ingest_goalie_logs(seasons))
        print("goalie xG:", moneypuck.ingest_shots(seasons))
    elif args.cmd == "tune":
        from . import tune
        print(json.dumps(tune.run()["report"], indent=1))
    elif args.cmd == "backtest":
        from .pipeline.daily import run_backtest
        s = run_backtest()
        m = s["models"][s["chosen_model"]]
        print(json.dumps({"chosen_model": s["chosen_model"], "reason": s["selection_reason"], "overall": m["overall"],
                          "confirmation_window": m["confirmation_window"]}, indent=1))
    elif args.cmd == "train":
        from .pipeline.daily import train
        print(train().version)
    elif args.cmd == "daily":
        from .pipeline.daily import run_daily
        r = run_daily(retrain=True if args.retrain else None)
        print(json.dumps({k: r[k] for k in ("status", "failures", "steps")}, indent=1, default=str))
        return 0 if r["status"] != "aborted" else 1
    elif args.cmd == "refresh":
        from .pipeline.daily import run_refresh
        r = run_refresh()
        print(json.dumps({k: r[k] for k in ("status", "failures", "steps")}, indent=1, default=str))
        return 0 if r["status"] != "aborted" else 1
    elif args.cmd == "settle":
        from .pipeline.settle import settle
        print("settled:", settle())
    elif args.cmd == "status":
        from .pipeline.daily import freshness
        print(json.dumps(freshness(), indent=1))
    elif args.cmd == "copy-db":
        target = db.engine().url.render_as_string(hide_password=True)
        print(f"copying into {target}")
        for name, n in db.copy_from(args.source).items():
            print(f"  {name}: {n}")
    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("puckiq.api.main:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
