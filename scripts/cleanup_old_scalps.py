"""Archive old scalp trades and clean up signal history.
Uses the actual DB path from settings.
"""
from database import FastTickDatabase

DB_PATH = "./binance_ticks.duckdb"
db = FastTickDatabase(db_path=DB_PATH)

with db._lock:
    # Count old vs new
    old_count = db._conn.execute(
        "SELECT COUNT(*) FROM trade_signals WHERE generated_at < '2026-05-01T00:00:00'"
    ).fetchone()[0]
    new_count = db._conn.execute(
        "SELECT COUNT(*) FROM trade_signals WHERE generated_at >= '2026-05-01T00:00:00'"
    ).fetchone()[0]
    print(f"Old scalp trades (before May 1): {old_count}")
    print(f"New directional trades (May 1+): {new_count}")

    if old_count == 0:
        print("No old trades to archive.")
    else:
        # Archive old trades to a backup table
        db._conn.execute(
            "CREATE TABLE IF NOT EXISTS trade_signals_archive AS "
            "SELECT * FROM trade_signals WHERE 1=0"
        )
        db._conn.execute(
            "INSERT INTO trade_signals_archive "
            "SELECT * FROM trade_signals WHERE generated_at < '2026-05-01T00:00:00'"
        )
        archived = db._conn.execute(
            "SELECT COUNT(*) FROM trade_signals_archive"
        ).fetchone()[0]
        print(f"Archived to trade_signals_archive: {archived}")

        # Remove from active table
        db._conn.execute(
            "DELETE FROM trade_signals WHERE generated_at < '2026-05-01T00:00:00'"
        )
        remaining = db._conn.execute(
            "SELECT COUNT(*) FROM trade_signals"
        ).fetchone()[0]
        print(f"Remaining active signals: {remaining}")

        # Clean orphan evaluations
        db._conn.execute(
            "DELETE FROM signal_evaluations WHERE signal_id NOT IN "
            "(SELECT signal_id FROM trade_signals)"
        )
        print("Cleaned orphan evaluations")

db.close()
print("Done!")
