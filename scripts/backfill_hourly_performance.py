"""
Backfill Script — Populate hourly_performance from existing trade_signals.

Run this ONCE after migration to seed the Kelly criterion with historical data.

Usage:
    python backfill_hourly_performance.py
"""

import logging
import shutil
import sys
import time
from pathlib import Path

import duckdb

# Ensure project root is importable
PROJECT_ROOT = str(Path(__file__).resolve().parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from database import FastTickDatabase
from settings import load_settings
import config as C

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
LOGGER = logging.getLogger(__name__)


def _archive_corrupt_wal(db_path: str) -> bool:
    """Archive a corrupt WAL file so DuckDB can open the base DB cleanly."""
    wal_file = Path(f"{db_path}.wal")
    if not wal_file.exists():
        LOGGER.info("No WAL file found at %s — nothing to archive.", wal_file)
        return False

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    recovery_dir = Path(db_path).parent / "wal_recovery"
    recovery_dir.mkdir(parents=True, exist_ok=True)

    archived_wal = recovery_dir / f"{Path(db_path).stem}_{timestamp}.duckdb.wal"
    shutil.move(str(wal_file), str(archived_wal))
    LOGGER.warning("Archived corrupt WAL to: %s", archived_wal)
    return True


def backfill():
    settings = load_settings()

    # Open DB with WAL recovery (same logic as main.py)
    try:
        db = FastTickDatabase(db_path=settings.db_path)
    except duckdb.InternalException as exc:
        if "failure while replaying wal" in str(exc).lower():
            LOGGER.error("Corrupt WAL detected. Archiving and retrying...")
            if _archive_corrupt_wal(settings.db_path):
                db = FastTickDatabase(db_path=settings.db_path)
            else:
                raise
        else:
            raise

    LOGGER.info("Starting hourly_performance backfill from existing trade_signals...")

    # Step 1: Backfill gross_pnl_percent and net_pnl_percent for existing closed trades
    with db._lock:
        rows = db._conn.execute(
            """
            SELECT signal_id, side, entry_price, close_price, pnl_pct, status,
                   generated_at, gross_pnl_percent, net_pnl_percent
            FROM trade_signals
            WHERE side IN ('LONG', 'SHORT')
              AND status IN ('WIN', 'LOSS', 'EXPIRED')
            ORDER BY COALESCE(closed_at, generated_at) ASC
            """
        ).fetchall()

    updated_pnl = 0
    for row in rows:
        signal_id = str(row[0])
        side = str(row[1]).upper()
        entry_price = float(row[2])
        close_price = float(row[3]) if row[3] else 0
        existing_gross = row[7]
        existing_net = row[8]

        # Skip if already backfilled
        if existing_gross and float(existing_gross) != 0:
            continue

        if entry_price <= 0 or close_price <= 0:
            continue

        if side == "LONG":
            gross_pnl = (close_price - entry_price) / entry_price
        else:
            gross_pnl = (entry_price - close_price) / entry_price

        net_pnl = gross_pnl - C.ROUND_TRIP_FEE

        with db._lock:
            db._conn.execute(
                """
                UPDATE trade_signals
                SET gross_pnl_percent = ?, net_pnl_percent = ?
                WHERE signal_id = ?
                """,
                [round(gross_pnl, 6), round(net_pnl, 6), signal_id],
            )
        updated_pnl += 1

    LOGGER.info("Backfilled gross/net PnL for %d trades.", updated_pnl)

    # Step 2: Populate hourly_performance table
    # Clear existing data to rebuild from scratch
    with db._lock:
        db._conn.execute("DELETE FROM hourly_performance")

    # Re-read with updated PnL
    with db._lock:
        rows = db._conn.execute(
            """
            SELECT signal_id, side, status, generated_at,
                   gross_pnl_percent, net_pnl_percent,
                   mfe_percent, mae_percent, regime
            FROM trade_signals
            WHERE side IN ('LONG', 'SHORT')
              AND status IN ('WIN', 'LOSS', 'EXPIRED')
            ORDER BY COALESCE(closed_at, generated_at) ASC
            """
        ).fetchall()

    processed = 0
    for row in rows:
        generated_at = row[3]
        if not generated_at:
            continue

        hour_utc = generated_at.hour if hasattr(generated_at, 'hour') else 0
        side = str(row[1]).upper()
        status = str(row[2]).upper()
        mfe = float(row[6] or 0)
        mae = float(row[7] or 0)
        regime = str(row[8] or "UNKNOWN").upper()

        # Determine regime from ADX if not set
        if not regime or regime == "UNKNOWN":
            regime = "TRENDING"  # Default assumption for backfill

        db.update_hourly_performance({
            "hour_utc": hour_utc,
            "side": side,
            "regime": regime,
            "status": "WIN" if status == "WIN" else "LOSS",
            "mfe_percent": mfe,
            "mae_percent": mae,
        })
        processed += 1

    LOGGER.info("Populated hourly_performance from %d closed trades.", processed)

    # Step 3: Also backfill session_tag for existing signals
    with db._lock:
        all_signals = db._conn.execute(
            """
            SELECT signal_id, generated_at, session_tag
            FROM trade_signals
            WHERE side IN ('LONG', 'SHORT')
              AND (session_tag IS NULL OR session_tag = 'UNKNOWN')
            """
        ).fetchall()

    tagged = 0
    for row in all_signals:
        signal_id = str(row[0])
        generated_at = row[1]
        if not generated_at:
            continue

        hour = generated_at.hour if hasattr(generated_at, 'hour') else 0
        tag = FastTickDatabase.get_session_tag(hour)

        with db._lock:
            db._conn.execute(
                "UPDATE trade_signals SET session_tag = ? WHERE signal_id = ?",
                [tag, signal_id],
            )
        tagged += 1

    LOGGER.info("Backfilled session_tag for %d signals.", tagged)

    # Summary
    with db._lock:
        perf_count = db._conn.execute("SELECT COUNT(*) FROM hourly_performance").fetchone()

    LOGGER.info(
        "Backfill complete! hourly_performance has %d rows. "
        "Kelly criterion now has data for immediate use.",
        perf_count[0] if perf_count else 0,
    )
    db.close()


if __name__ == "__main__":
    backfill()
