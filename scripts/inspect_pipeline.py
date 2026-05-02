from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
from database import FastTickDatabase
from settings import load_settings


def build_parser() -> argparse.ArgumentParser:
    settings = load_settings()
    parser = argparse.ArgumentParser(
        description="Inspect stored BTC signal logic, outcomes, challenger verdicts, and learning memory."
    )
    parser.add_argument("--db-path", default=settings.db_path, help="Path to the DuckDB database file.")
    parser.add_argument(
        "--snapshot-path",
        default=settings.dashboard_snapshot_path,
        help="Path to the published dashboard snapshot JSON file.",
    )
    parser.add_argument("--symbol", default=settings.symbol, help="Trading symbol to inspect.")
    parser.add_argument("--limit", type=int, default=20, help="How many recent rows to return.")
    parser.add_argument(
        "--signal-id",
        default="",
        help="Inspect one exact signal_id. If omitted, the latest signal is shown.",
    )
    return parser


def _is_db_locked(exc: Exception) -> bool:
    message = str(exc).lower()
    return "cannot open file" in message and "another process" in message


def _load_snapshot(snapshot_path: str) -> dict:
    return json.loads(Path(snapshot_path).read_text(encoding="utf-8"))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = load_settings()

    try:
        db = FastTickDatabase(db_path=args.db_path, read_only=True)
    except (duckdb.IOException, duckdb.InternalException) as exc:
        if not _is_db_locked(exc) and not Path(args.snapshot_path).exists():
            raise
        snapshot = _load_snapshot(args.snapshot_path)
        signal_debug = snapshot.get("latest_signal")
        if args.signal_id:
            signal_debug = snapshot.get("signal_debug_map", {}).get(args.signal_id)
        payload = {
            "source": "snapshot_fallback",
            "snapshot_path": args.snapshot_path,
            "symbol": snapshot.get("symbol", args.symbol),
            "capital_base": snapshot.get("capital_base"),
            "active_signals": snapshot.get("active_signals"),
            "signal_history": snapshot.get("signal_history"),
            "equity_curve": snapshot.get("equity_curve"),
            "performance": snapshot.get("performance"),
            "challenger_performance": snapshot.get("challenger_performance"),
            "challenger_learning": snapshot.get("challenger_learning"),
            "learning_profiles": snapshot.get("learning_profiles"),
            "recent_signals": snapshot.get("recent_signals"),
            "crew_events": snapshot.get("crew_events"),
            "signal_debug": signal_debug,
        }
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return

    try:
        latest_signal = (
            db.get_signal_debug(signal_id=args.signal_id)
            if args.signal_id
            else db.get_latest_signal_debug(symbol=args.symbol)
        )
        payload = {
            "source": "database",
            "symbol": args.symbol,
            "capital_base": settings.reporting_capital_base,
            "active_signals": db.list_active_signals(
                symbol=args.symbol,
                capital_base=settings.reporting_capital_base,
                limit=args.limit,
            ),
            "signal_history": db.list_signal_history_with_capital(
                symbol=args.symbol,
                capital_base=settings.reporting_capital_base,
                limit=max(args.limit * 4, 100),
            ),
            "equity_curve": db.get_signal_equity_curve(
                symbol=args.symbol,
                capital_base=settings.reporting_capital_base,
                limit=max(args.limit * 8, 200),
            ),
            "performance": db.get_signal_performance_summary(symbol=args.symbol),
            "challenger_performance": db.get_challenger_performance_summary(symbol=args.symbol),
            "challenger_learning": db.get_challenger_learning_profiles(symbol=args.symbol, limit=args.limit),
            "learning_profiles": db.get_learning_profiles(symbol=args.symbol, limit=args.limit),
            "recent_signals": db.list_signal_audit_rows(symbol=args.symbol, limit=args.limit),
            "crew_events": db.list_crew_events(symbol=args.symbol, limit=args.limit),
            "signal_debug": latest_signal,
        }
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
