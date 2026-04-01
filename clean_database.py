import duckdb
import os
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# Connect exactly to the active database
DB_PATH = os.getenv("DB_PATH", "./binance_ticks.duckdb")
CUTOFF_DATE = "2026-03-29"

def clean_database():
    logging.info(f"Opening database: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        logging.error("Database file not found!")
        return

    try:
        # Read-write connection
        conn = duckdb.connect(DB_PATH)
    except Exception as e:
        logging.error(f"Failed to connect to duckdb. Is the main engine still running and holding a lock? Error: {e}")
        logging.error("Please run '.\\stop.ps1' before running this script.")
        return

    logging.info(f"Connected. Phase 1: Deleting all trades before cutoff date: {CUTOFF_DATE}")
    
    # 1. DELETE TRADES
    res = conn.execute(
        f"DELETE FROM trade_signals WHERE COALESCE(closed_at, generated_at) < '{CUTOFF_DATE}'"
    ).fetchall()
    deleted_signals = res[0][0] if res else "Unknown"
    logging.info(f"Deleted {deleted_signals} old trades from trade_signals.")

    # 2. DELETE ORPHANS
    res = conn.execute(
        "DELETE FROM signal_evaluations WHERE signal_id NOT IN (SELECT signal_id FROM trade_signals)"
    ).fetchall()
    deleted_evals = res[0][0] if res else "Unknown"
    logging.info(f"Deleted {deleted_evals} orphaned evaluations.")

    res = conn.execute(
        "DELETE FROM signal_challenges WHERE signal_id NOT IN (SELECT signal_id FROM trade_signals)"
    ).fetchall()
    deleted_challenges = res[0][0] if res else "Unknown"
    logging.info(f"Deleted {deleted_challenges} orphaned challenges.")
    
    res = conn.execute(
        f"DELETE FROM crew_events WHERE event_type = 'trade_closed' AND event_time < '{CUTOFF_DATE}'"
    ).fetchall()
    deleted_events = res[0][0] if res else "Unknown"
    logging.info(f"Deleted {deleted_events} old crew events.")

    # 3. VERIFY PNL INTEGRITY (PHASE 9 Validation)
    capital_base = 2000.0  # 20% of 10k default
    logging.info(f"Phase 9: Validating PnL math for remaining trades... (Position Size: ${capital_base})")
    
    rows = conn.execute(
        """
        SELECT signal_id, pnl_pct, status, side 
        FROM trade_signals 
        WHERE status IN ('WIN', 'LOSS', 'EXPIRED')
        ORDER BY closed_at ASC
        """
    ).fetchall()
    
    cumulative = 0.0
    for row in rows:
        signal_id, pnl_pct, status, side = row
        pnl_pct = float(pnl_pct or 0.0)
        
        # trade_pnl = position_size * (pnl_pct / 100)
        trade_pnl = capital_base * (pnl_pct / 100.0)
        
        # Anomaly Detection
        if abs(trade_pnl) > capital_base * 0.5:
            logging.error(f"ANOMALY: Trade {signal_id} has unrealistic PnL: ${trade_pnl:.2f} ({pnl_pct}%)")
            
        cumulative += trade_pnl
        logging.debug(f"Trade {signal_id}: {pnl_pct:.2f}% -> ${trade_pnl:.2f}. Cumulative: ${cumulative:.2f}")

    logging.info(f"Database clean complete! Total Realized PnL of remaining dataset: ${cumulative:.2f}")
    
    conn.close()
    logging.info("Disconnected. You may now restart the platform with '.\\start.ps1'.")

if __name__ == "__main__":
    clean_database()
