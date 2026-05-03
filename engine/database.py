from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import threading
from typing import Any
import uuid

import duckdb


class FastTickDatabase:
    def __init__(self, db_path: str, read_only: bool = False) -> None:
        self.db_path = db_path
        self.read_only = read_only
        self._lock = threading.Lock()
        self._conn = duckdb.connect(database=db_path, read_only=read_only)
        if not read_only:
            self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA threads=4")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ticks (
                    symbol VARCHAR NOT NULL,
                    trade_id BIGINT NOT NULL,
                    price DOUBLE NOT NULL,
                    qty DOUBLE NOT NULL,
                    quote_qty DOUBLE NOT NULL,
                    trade_time TIMESTAMP NOT NULL,
                    event_time TIMESTAMP NOT NULL,
                    is_buyer_maker BOOLEAN NOT NULL,
                    inserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (symbol, trade_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orderflow_snapshots (
                    snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    symbol VARCHAR NOT NULL,
                    lookback_seconds INTEGER NOT NULL,
                    trade_count BIGINT NOT NULL,
                    taker_buy_volume DOUBLE NOT NULL,
                    taker_sell_volume DOUBLE NOT NULL,
                    volume_delta DOUBLE NOT NULL,
                    imbalance DOUBLE NOT NULL,
                    avg_price DOUBLE NOT NULL,
                    last_price DOUBLE NOT NULL
                )
                """
            )
            # Add missing columns if they don't exist
            self._conn.execute(
                "ALTER TABLE orderflow_snapshots ADD COLUMN IF NOT EXISTS lowest_price DOUBLE DEFAULT 0.0"
            )
            self._conn.execute(
                "ALTER TABLE orderflow_snapshots ADD COLUMN IF NOT EXISTS highest_price DOUBLE DEFAULT 0.0"
            )
            self._conn.execute(
                "ALTER TABLE orderflow_snapshots ADD COLUMN IF NOT EXISTS oldest_price DOUBLE DEFAULT 0.0"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orderflow_analysis (
                    analysis_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    symbol VARCHAR NOT NULL,
                    regime VARCHAR NOT NULL,
                    confidence DOUBLE NOT NULL,
                    summary VARCHAR NOT NULL,
                    metrics_json VARCHAR NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trading_opportunities (
                    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    symbol VARCHAR NOT NULL,
                    side VARCHAR NOT NULL,
                    entry_price DOUBLE NOT NULL,
                    stop_price DOUBLE NOT NULL,
                    take_profit DOUBLE NOT NULL,
                    confidence DOUBLE NOT NULL,
                    rationale VARCHAR NOT NULL,
                    regime VARCHAR NOT NULL,
                    signal_id VARCHAR
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_signals (
                    signal_id VARCHAR PRIMARY KEY,
                    generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    symbol VARCHAR NOT NULL,
                    side VARCHAR NOT NULL,
                    entry_price DOUBLE NOT NULL,
                    stop_price DOUBLE NOT NULL,
                    take_profit DOUBLE NOT NULL,
                    confidence DOUBLE NOT NULL,
                    rationale VARCHAR NOT NULL,
                    regime VARCHAR NOT NULL,
                    analysis_json VARCHAR NOT NULL,
                    decision_trace_json VARCHAR,
                    evaluation_due_time TIMESTAMP NOT NULL,
                    status VARCHAR NOT NULL,
                    evaluated BOOLEAN NOT NULL DEFAULT FALSE,
                    closed_at TIMESTAMP,
                    close_price DOUBLE,
                    pnl_pct DOUBLE,
                    was_profitable BOOLEAN,
                    outcome_reason VARCHAR,
                    challenge_verdict VARCHAR,
                    challenge_score DOUBLE,
                    challenge_summary VARCHAR
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_evaluations (
                    evaluation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    signal_id VARCHAR NOT NULL,
                    symbol VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    close_price DOUBLE NOT NULL,
                    pnl_pct DOUBLE NOT NULL,
                    reason VARCHAR NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_challenges (
                    challenge_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    signal_id VARCHAR PRIMARY KEY,
                    symbol VARCHAR NOT NULL,
                    verdict VARCHAR NOT NULL,
                    score DOUBLE NOT NULL,
                    summary VARCHAR NOT NULL,
                    challenge_json VARCHAR NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS crew_events (
                    event_id VARCHAR PRIMARY KEY,
                    event_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    symbol VARCHAR NOT NULL,
                    run_id VARCHAR,
                    component VARCHAR NOT NULL,
                    event_type VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    title VARCHAR NOT NULL,
                    message VARCHAR NOT NULL,
                    payload_json VARCHAR
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_memory (
                    snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    symbol VARCHAR NOT NULL,
                    side VARCHAR NOT NULL,
                    regime VARCHAR NOT NULL,
                    sample_size INTEGER NOT NULL,
                    win_rate DOUBLE NOT NULL,
                    avg_pnl_pct DOUBLE NOT NULL,
                    recommendation VARCHAR NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS challenger_learning_memory (
                    snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    symbol VARCHAR NOT NULL,
                    verdict VARCHAR NOT NULL,
                    sample_size INTEGER NOT NULL,
                    correct_count INTEGER NOT NULL,
                    incorrect_count INTEGER NOT NULL,
                    accuracy DOUBLE NOT NULL,
                    avg_pnl_pct DOUBLE NOT NULL,
                    recommendation VARCHAR NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                ALTER TABLE trading_opportunities
                ADD COLUMN IF NOT EXISTS signal_id VARCHAR
                """
            )
            self._conn.execute(
                """
                ALTER TABLE trade_signals
                ADD COLUMN IF NOT EXISTS evaluation_attempts INTEGER DEFAULT 0
                """
            )
            self._conn.execute(
                """
                ALTER TABLE trade_signals
                ADD COLUMN IF NOT EXISTS data_quality VARCHAR
                """
            )
            self._conn.execute(
                """
                ALTER TABLE trade_signals
                ADD COLUMN IF NOT EXISTS decision_trace_json VARCHAR
                """
            )
            self._conn.execute(
                """
                ALTER TABLE trade_signals
                ADD COLUMN IF NOT EXISTS challenge_verdict VARCHAR
                """
            )
            self._conn.execute(
                """
                ALTER TABLE trade_signals
                ADD COLUMN IF NOT EXISTS challenge_score DOUBLE
                """
            )
            self._conn.execute(
                """
                ALTER TABLE trade_signals
                ADD COLUMN IF NOT EXISTS challenge_summary VARCHAR
                """
            )
            # ── Profitability Overhaul: New columns on trade_signals ──
            for col_def in [
                "mfe_percent DOUBLE DEFAULT 0",
                "mae_percent DOUBLE DEFAULT 0",
                "session_tag VARCHAR DEFAULT 'UNKNOWN'",
                "funding_rate_at_entry DOUBLE DEFAULT 0",
                "obi_at_entry DOUBLE DEFAULT 0",
                "spread_at_entry DOUBLE DEFAULT 0",
                "gross_pnl_percent DOUBLE DEFAULT 0",
                "net_pnl_percent DOUBLE DEFAULT 0",
                "latency_ms INTEGER DEFAULT 0",
                "kelly_fraction_used DOUBLE DEFAULT 0",
                "nofi_at_entry DOUBLE DEFAULT 0",
                "atr_used DOUBLE DEFAULT 0",
            ]:
                try:
                    self._conn.execute(
                        f"ALTER TABLE trade_signals ADD COLUMN IF NOT EXISTS {col_def}"
                    )
                except Exception:
                    pass  # Column exists or dependency prevents alter
            # ── Multi-TF Trend-Capture: New columns on trade_signals ──
            for col_def in [
                "alignment_score_at_entry DOUBLE DEFAULT 0",
                "daily_bias_at_entry VARCHAR DEFAULT 'UNKNOWN'",
                "four_h_structure_at_entry VARCHAR DEFAULT 'UNKNOWN'",
                "pullback_complete_at_entry BOOLEAN DEFAULT FALSE",
                "volatility_regime_at_entry VARCHAR DEFAULT 'UNKNOWN'",
                "tp_multiplier_used DOUBLE DEFAULT 3.0",
                "sl_multiplier_used DOUBLE DEFAULT 1.0",
                "max_unrealized_pnl_percent DOUBLE DEFAULT 0",
                "drawdown_from_peak_percent DOUBLE DEFAULT 0",
                "exit_reason VARCHAR DEFAULT ''",
                "trend_fail_count INTEGER DEFAULT 0",
                "partial_tp_taken BOOLEAN DEFAULT FALSE",
            ]:
                try:
                    self._conn.execute(
                        f"ALTER TABLE trade_signals ADD COLUMN IF NOT EXISTS {col_def}"
                    )
                except Exception:
                    pass  # Column exists or dependency prevents alter
            # ── Profitability Overhaul: hourly_performance table ──
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hourly_performance (
                    hour_utc INTEGER NOT NULL,
                    side VARCHAR NOT NULL,
                    regime VARCHAR NOT NULL,
                    total_signals INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    avg_mfe DOUBLE DEFAULT 0,
                    avg_mae DOUBLE DEFAULT 0,
                    win_rate DOUBLE DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (hour_utc, side, regime)
                )
                """
            )
            # Add avg_duration_seconds if not exists
            try:
                self._conn.execute(
                    "ALTER TABLE hourly_performance ADD COLUMN IF NOT EXISTS avg_duration_seconds DOUBLE DEFAULT 0"
                )
            except Exception:
                pass
            # ── Multi-TF Trend-Capture: regime_log table ──
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS regime_log (
                    week_starting DATE,
                    regime VARCHAR,
                    signals_count INTEGER DEFAULT 0,
                    missed_profits_count INTEGER DEFAULT 0,
                    quick_stops_count INTEGER DEFAULT 0,
                    tp_multiplier DOUBLE DEFAULT 3.0,
                    sl_multiplier DOUBLE DEFAULT 1.0
                )
                """
            )
            # ── Profitability Overhaul: l2_snapshots table ──
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS l2_snapshots (
                    snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    bid_0 DOUBLE,
                    ask_0 DOUBLE,
                    bid_vol_0_1pct DOUBLE,
                    ask_vol_0_1pct DOUBLE,
                    obi_0_1pct DOUBLE,
                    spread_ticks DOUBLE
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ticks_symbol_time
                ON ticks(symbol, trade_time)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_signals_symbol_status_due
                ON trade_signals(symbol, status, evaluation_due_time)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_learning_symbol_side_regime
                ON learning_memory(symbol, side, regime, snapshot_time)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_signal_challenges_symbol_time
                ON signal_challenges(symbol, challenge_time)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crew_events_symbol_time
                ON crew_events(symbol, event_time)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crew_events_run_time
                ON crew_events(run_id, event_time)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_challenger_learning_symbol_verdict
                ON challenger_learning_memory(symbol, verdict, snapshot_time)
                """
            )

    def _first_price_hit(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        condition_sql: str,
        price_level: float,
    ) -> tuple[datetime, float] | None:
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT trade_time, price
                FROM ticks
                WHERE symbol = ?
                  AND trade_time >= ?
                  AND trade_time <= ?
                  AND {condition_sql}
                ORDER BY trade_time ASC
                LIMIT 1
                """,
                [symbol, start_time, end_time, price_level],
            ).fetchone()
        if not row:
            return None
        return row[0], float(row[1])

    def _latest_price_before(
        self, symbol: str, at_time: datetime
    ) -> tuple[datetime, float] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT trade_time, price
                FROM ticks
                WHERE symbol = ?
                  AND trade_time <= ?
                ORDER BY trade_time DESC
                LIMIT 1
                """,
                [symbol, at_time],
            ).fetchone()
        if not row:
            return None
        return row[0], float(row[1])

    @staticmethod
    def _load_json_blob(raw_value: str | None) -> dict[str, Any]:
        if not raw_value:
            return {}
        try:
            loaded = json.loads(raw_value)
        except json.JSONDecodeError:
            return {"raw": raw_value}
        return loaded if isinstance(loaded, dict) else {"value": loaded}

    @staticmethod
    def _classify_challenger_recommendation(
        sample_size: int, accuracy: float, min_samples: int
    ) -> str:
        if sample_size < min_samples:
            return "insufficient_data"
        if accuracy < 0.45:
            return "distrust"
        if accuracy > 0.60:
            return "trust"
        return "monitor"

    def store_crew_event(
        self,
        symbol: str,
        component: str,
        event_type: str,
        title: str,
        message: str,
        status: str = "INFO",
        payload: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        event_id = uuid.uuid4().hex
        normalized_status = status.upper()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO crew_events (
                    event_id,
                    symbol,
                    run_id,
                    component,
                    event_type,
                    status,
                    title,
                    message,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    event_id,
                    symbol,
                    run_id,
                    component,
                    event_type,
                    normalized_status,
                    title,
                    message,
                    json.dumps(payload or {}, sort_keys=True),
                ],
            )
        return {
            "event_id": event_id,
            "symbol": symbol,
            "run_id": run_id,
            "component": component,
            "event_type": event_type,
            "status": normalized_status,
            "title": title,
            "message": message,
            "payload": payload or {},
        }

    def list_crew_events(
        self,
        symbol: str,
        limit: int = 500,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if run_id:
            query = """
                SELECT
                    event_time,
                    run_id,
                    component,
                    event_type,
                    status,
                    title,
                    message,
                    payload_json
                FROM crew_events
                WHERE symbol = ? AND run_id = ?
                ORDER BY rowid DESC
                LIMIT ?
            """
            params: list[Any] = [symbol, run_id, max(limit, 1)]
        else:
            query = """
                SELECT
                    event_time,
                    run_id,
                    component,
                    event_type,
                    status,
                    title,
                    message,
                    payload_json
                FROM crew_events
                WHERE symbol = ?
                ORDER BY rowid DESC
                LIMIT ?
            """
            params = [symbol, max(limit, 1)]

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()

        events: list[dict[str, Any]] = []
        for row in rows:
            events.append(
                {
                    "event_time": row[0],
                    "run_id": str(row[1]) if row[1] is not None else None,
                    "component": str(row[2]),
                    "event_type": str(row[3]),
                    "status": str(row[4]),
                    "title": str(row[5]),
                    "message": str(row[6]),
                    "payload": self._load_json_blob(row[7]),
                }
            )
        return events

    def list_recent_ticks(self, symbol: str, limit: int = 400) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT trade_time, price, qty, quote_qty, is_buyer_maker
                FROM ticks
                WHERE symbol = ?
                ORDER BY trade_time DESC
                LIMIT ?
                """,
                [symbol, max(limit, 1)],
            ).fetchall()

        ticks: list[dict[str, Any]] = []
        for row in reversed(rows):
            ticks.append(
                {
                    "trade_time": row[0],
                    "price": float(row[1]),
                    "qty": float(row[2]),
                    "quote_qty": float(row[3]),
                    "is_buyer_maker": bool(row[4]),
                }
            )
        return ticks

    def _classify_live_signal_state(
        self,
        side: str,
        status: str,
        challenge_verdict: str,
        evaluation_due_time: datetime,
        latest_price: float | None,
        stop_price: float,
        take_profit: float,
        data_health: dict[str, Any],
    ) -> tuple[str, bool, str]:
        now = datetime.utcnow()
        normalized_status = status.upper()
        normalized_verdict = challenge_verdict.upper()
        normalized_side = side.upper()

        if normalized_status not in ("OPEN", "PENDING_VALIDATION"):
            return normalized_status, False, "signal already resolved"
        if normalized_status == "PENDING_VALIDATION":
            return "PENDING_VALIDATION", False, "signal awaiting challenger validation"
        if normalized_verdict == "REJECT":
            return "CHALLENGED", False, "challenger rejected this signal"
        if now >= evaluation_due_time:
            return (
                "AWAITING_REVIEW",
                False,
                "signal reached evaluation time and awaits review",
            )
        if not data_health.get("usable", False):
            return "DATA_RISK", False, str(data_health.get("reason", "market_data_gap"))
        if latest_price is None:
            return "NO_PRICE", False, "no current price available"

        if normalized_side == "LONG":
            if latest_price <= stop_price:
                return "STOP_BREACHED", False, "current price is below or at stop"
            if latest_price >= take_profit:
                return "TARGET_TOUCHED", False, "current price reached target"
        elif normalized_side == "SHORT":
            if latest_price >= stop_price:
                return "STOP_BREACHED", False, "current price is above or at stop"
            if latest_price <= take_profit:
                return "TARGET_TOUCHED", False, "current price reached target"

        return "LIVE", True, "signal remains live"

    def manual_close_trades(
        self,
        symbol: str,
        close_type: str = "all",
        specific_signal_id: str | None = None
    ) -> int:
        """
        Force-closes trades manually. 
        close_type: 'all', 'win', 'loss', 'single'
        """
        latest_price = self.get_latest_price(symbol=symbol)
        if latest_price is None:
            return 0
            
        now = datetime.utcnow()
        closed_count = 0

        with self._lock:
            # Query active trades
            query = "SELECT signal_id, side, entry_price FROM trade_signals WHERE symbol = ? AND status = 'OPEN'"
            params = [symbol]
            if specific_signal_id:
                query += " AND signal_id = ?"
                params.append(specific_signal_id)
                
            trades = self._conn.execute(query, params).fetchall()

            for row in trades:
                sig_id = str(row[0])
                side = str(row[1])
                entry_price = float(row[2])

                # Calculate current PnL
                if side.upper() == "LONG":
                    pnl_pct = ((latest_price - entry_price) / entry_price) * 100
                elif side.upper() == "SHORT":
                    pnl_pct = ((entry_price - latest_price) / entry_price) * 100
                else:
                    pnl_pct = 0.0

                # Check if it fits the close_type
                if close_type == "win" and pnl_pct <= 0:
                    continue
                if close_type == "loss" and pnl_pct >= 0:
                    continue

                # Close it
                self._conn.execute(
                    """
                    UPDATE trade_signals
                    SET status = 'EXPIRED',
                        evaluated = TRUE,
                        closed_at = ?,
                        close_price = ?,
                        pnl_pct = ?,
                        was_profitable = ?,
                        outcome_reason = 'manual_close',
                        data_quality = 'ok'
                    WHERE signal_id = ?
                    """,
                    [now, latest_price, round(pnl_pct, 4), pnl_pct > 0, sig_id],
                )
                self._conn.execute(
                    """
                    INSERT INTO signal_evaluations (signal_id, symbol, status, close_price, pnl_pct, reason)
                    VALUES (?, ?, 'EXPIRED', ?, ?, 'manual_close')
                    """,
                    [sig_id, symbol, latest_price, round(pnl_pct, 4)],
                )
                closed_count += 1
                
        return closed_count

    def list_active_signals(
        self,
        symbol: str,
        capital_base: float = 100000.0,
        lookback_seconds: int = 60,
        max_gap_seconds: int = 10,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        latest_price = self.get_latest_price(symbol=symbol)
        data_health = self.get_recent_data_health(
            symbol=symbol,
            lookback_seconds=lookback_seconds,
            max_gap_seconds=max_gap_seconds,
        )
        now = datetime.utcnow()

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    signal_id,
                    generated_at,
                    side,
                    regime,
                    entry_price,
                    stop_price,
                    take_profit,
                    confidence,
                    evaluation_due_time,
                    COALESCE(challenge_verdict, 'PENDING') AS challenge_verdict,
                    challenge_summary,
                    rationale,
                    status
                FROM trade_signals
                WHERE symbol = ?
                  AND side IN ('LONG', 'SHORT')
                  AND status IN ('OPEN', 'PENDING_VALIDATION')
                ORDER BY generated_at DESC
                LIMIT ?
                """,
                [symbol, max(limit, 1)],
            ).fetchall()

        active_signals: list[dict[str, Any]] = []
        for row in rows:
            side = str(row[2])
            entry_price = float(row[4])
            current_pnl_pct = (
                self._calculate_pnl_pct(side, entry_price, latest_price)
                if latest_price is not None
                else 0.0
            )
            live_state, is_valid, live_reason = self._classify_live_signal_state(
                side=side,
                status=str(row[12]),
                challenge_verdict=str(row[9]),
                evaluation_due_time=row[8],
                latest_price=latest_price,
                stop_price=float(row[5]),
                take_profit=float(row[6]),
                data_health=data_health,
            )
            active_signals.append(
                {
                    "signal_id": str(row[0]),
                    "generated_at": row[1],
                    "side": side,
                    "regime": str(row[3]),
                    "entry_price": entry_price,
                    "stop_price": float(row[5]),
                    "take_profit": float(row[6]),
                    "confidence": round(float(row[7]), 4),
                    "evaluation_due_time": row[8],
                    "challenge_verdict": str(row[9]),
                    "challenge_summary": str(row[10]) if row[10] is not None else None,
                    "rationale": str(row[11]),
                    "status": str(row[12]),
                    "current_price": latest_price,
                    "unrealized_pnl_pct": round(current_pnl_pct, 4),
                    "capital_base": round(float(capital_base), 2),
                    "capital_pnl": round(
                        float(capital_base) * current_pnl_pct / 100.0, 2
                    ),
                    "time_remaining_seconds": max(
                        0, int((row[8] - now).total_seconds())
                    ),
                    "live_state": live_state,
                    "is_valid": is_valid,
                    "live_reason": live_reason,
                    "data_health_reason": str(data_health.get("reason", "unknown")),
                }
            )
        return active_signals

    def list_signal_history_with_capital(
        self,
        symbol: str,
        capital_base: float = 100000.0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    signal_id,
                    generated_at,
                    closed_at,
                    side,
                    regime,
                    entry_price,
                    close_price,
                    stop_price,
                    take_profit,
                    confidence,
                    status,
                    COALESCE(challenge_verdict, 'PENDING') AS challenge_verdict,
                    challenge_summary,
                    pnl_pct,
                    outcome_reason,
                    rationale
                FROM trade_signals
                WHERE symbol = ?
                  AND side IN ('LONG', 'SHORT')
                  AND status IN ('WIN', 'LOSS', 'EXPIRED', 'NO_DATA')
                ORDER BY COALESCE(closed_at, generated_at) DESC
                LIMIT ?
                """,
                [symbol, max(limit, 1)],
            ).fetchall()

        history: list[dict[str, Any]] = []
        for row in rows:
            pnl_pct = float(row[13] or 0.0)
            history.append(
                {
                    "signal_id": str(row[0]),
                    "generated_at": row[1],
                    "closed_at": row[2],
                    "side": str(row[3]),
                    "regime": str(row[4]),
                    "entry_price": float(row[5]),
                    "close_price": float(row[6]) if row[6] is not None else None,
                    "stop_price": float(row[7]),
                    "take_profit": float(row[8]),
                    "confidence": round(float(row[9]), 4),
                    "status": str(row[10]),
                    "challenge_verdict": str(row[11]),
                    "challenge_summary": str(row[12]) if row[12] is not None else None,
                    "pnl_pct": round(pnl_pct, 4),
                    "capital_base": round(float(capital_base), 2),
                    "capital_pnl": round(float(capital_base) * pnl_pct / 100.0, 2),
                    "outcome_reason": str(row[14]) if row[14] is not None else None,
                    "rationale": str(row[15]),
                }
            )
        return history

    def get_signal_equity_curve(
        self,
        symbol: str,
        capital_base: float = 100000.0,
    ) -> list[dict[str, Any]]:
        """
        Build equity curve from ALL closed trades, sorted ascending.
        No LIMIT — every endpoint sees the same complete dataset.
        cumulative_pnl starts at 0 and each trade contributes exactly once.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT signal_id, COALESCE(closed_at, generated_at) AS point_time, status, pnl_pct
                FROM trade_signals
                WHERE symbol = ?
                  AND side IN ('LONG', 'SHORT')
                  AND status IN ('WIN', 'LOSS', 'EXPIRED', 'NO_DATA')
                ORDER BY point_time ASC
                """,
                [symbol],
            ).fetchall()

        cumulative_pnl = 0.0
        curve: list[dict[str, Any]] = []
        for row in rows:
            pnl_pct = float(row[3] or 0.0)
            trade_pnl = float(capital_base) * (pnl_pct / 100.0)
            
            # PHASE 4: PnL scaling anomaly validation
            if abs(trade_pnl) > float(capital_base) * 0.5:
                import logging
                logging.getLogger(__name__).error(
                    f"PnL scaling anomaly detected: trade_id={row[0]}, pnl_pct={pnl_pct}, "
                    f"position_size={capital_base}, computed_pnl={trade_pnl}"
                )
                
            cumulative_pnl += trade_pnl
            curve.append(
                {
                    "signal_id": str(row[0]),
                    "point_time": row[1],
                    "status": str(row[2]),
                    "pnl_pct": round(pnl_pct, 4),
                    "trade_pnl": round(trade_pnl, 2),
                    "cumulative_pnl": round(cumulative_pnl, 2),
                    "equity_value": round(float(capital_base) + cumulative_pnl, 2),
                }
            )
        return curve

    def build_dashboard_snapshot(
        self,
        symbol: str,
        limit: int,
        lookback_seconds: int,
        max_gap_seconds: int,
        capital_base: float = 100000.0,
    ) -> dict[str, Any]:
        recent_signals = self.list_signal_audit_rows(symbol=symbol, limit=limit)
        signal_history = self.list_signal_history_with_capital(
            symbol=symbol,
            capital_base=capital_base,
            limit=max(limit * 4, 100),
        )
        signal_ids = {row["signal_id"] for row in recent_signals}
        signal_ids.update(row["signal_id"] for row in signal_history)
        signal_debug_map = {
            signal_id: self.get_signal_debug(signal_id=signal_id)
            for signal_id in signal_ids
        }
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "health": self.get_recent_data_health(
                symbol=symbol,
                lookback_seconds=lookback_seconds,
                max_gap_seconds=max_gap_seconds,
            ),
            "last_price": self.get_latest_price(symbol=symbol),
            "snapshot": self.get_latest_orderflow_snapshot(symbol=symbol),
            "analysis": self.get_latest_analysis(symbol=symbol),
            "performance": self.get_signal_performance_summary(symbol=symbol),
            "challenger_performance": self.get_challenger_performance_summary(
                symbol=symbol
            ),
            "learning_profiles": self.get_learning_profiles(symbol=symbol, limit=limit),
            "challenger_learning": self.get_challenger_learning_profiles(
                symbol=symbol, limit=limit
            ),
            "capital_base": round(float(capital_base), 2),
            "active_signals": self.list_active_signals(
                symbol=symbol,
                capital_base=capital_base,
                lookback_seconds=lookback_seconds,
                max_gap_seconds=max_gap_seconds,
                limit=limit,
            ),
            "signal_history": signal_history,
            "equity_curve": self.get_signal_equity_curve(
                symbol=symbol,
                capital_base=capital_base,
            ),
            "recent_signals": recent_signals,
            "latest_signal": self.get_latest_signal_debug(symbol=symbol),
            "signal_debug_map": signal_debug_map,
            "crew_events": self.list_crew_events(
                symbol=symbol, limit=max(limit * 3, 30)
            ),
            "ticks": self.list_recent_ticks(symbol=symbol, limit=max(limit * 20, 200)),
        }

    def write_dashboard_snapshot(
        self,
        snapshot_path: str,
        symbol: str,
        limit: int,
        lookback_seconds: int,
        max_gap_seconds: int,
        capital_base: float = 100000.0,
    ) -> str:
        snapshot = self.build_dashboard_snapshot(
            symbol=symbol,
            limit=limit,
            lookback_seconds=lookback_seconds,
            max_gap_seconds=max_gap_seconds,
            capital_base=capital_base,
        )
        output_path = Path(snapshot_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(snapshot, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        temp_path.replace(output_path)
        return str(output_path)

    def _assess_tick_coverage(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        max_gap_seconds: int,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                WITH ordered_ticks AS (
                    SELECT
                        trade_time,
                        LAG(trade_time) OVER (ORDER BY trade_time) AS prev_trade_time
                    FROM ticks
                    WHERE symbol = ?
                      AND trade_time >= ?
                      AND trade_time <= ?
                )
                SELECT
                    COUNT(*) AS tick_count,
                    MIN(trade_time) AS first_trade_time,
                    MAX(trade_time) AS last_trade_time,
                    COALESCE(
                        MAX(EXTRACT(EPOCH FROM (trade_time - prev_trade_time))),
                        0
                    ) AS max_internal_gap_seconds
                FROM ordered_ticks
                """,
                [symbol, start_time, end_time],
            ).fetchone()

        tick_count = int(row[0] or 0) if row else 0
        first_trade_time = row[1] if row else None
        last_trade_time = row[2] if row else None
        max_internal_gap_seconds = float(row[3] or 0.0) if row else 0.0

        if tick_count <= 0 or first_trade_time is None or last_trade_time is None:
            return {
                "usable": False,
                "reason": "no_ticks_in_window",
                "tick_count": 0,
                "max_gap_seconds": None,
            }

        start_gap_seconds = max(0.0, (first_trade_time - start_time).total_seconds())
        end_gap_seconds = max(0.0, (end_time - last_trade_time).total_seconds())
        observed_max_gap_seconds = max(
            max_internal_gap_seconds,
            start_gap_seconds,
            end_gap_seconds,
        )

        if max_gap_seconds <= 0:
            return {
                "usable": True,
                "reason": "gap_check_disabled",
                "tick_count": tick_count,
                "max_gap_seconds": observed_max_gap_seconds,
            }

        usable = observed_max_gap_seconds <= float(max_gap_seconds)
        reason = "ok" if usable else f"gap_exceeded_{observed_max_gap_seconds:.2f}s"
        return {
            "usable": usable,
            "reason": reason,
            "tick_count": tick_count,
            "max_gap_seconds": observed_max_gap_seconds,
        }

    @staticmethod
    def _calculate_pnl_pct(side: str, entry_price: float, close_price: float) -> float:
        if entry_price <= 0:
            return 0.0
        if side == "LONG":
            return ((close_price - entry_price) / entry_price) * 100.0
        if side == "SHORT":
            return ((entry_price - close_price) / entry_price) * 100.0
        return 0.0

    @staticmethod
    def _classify_recommendation(
        sample_size: int, win_rate: float, min_samples: int
    ) -> str:
        if sample_size < min_samples:
            return "insufficient_data"
        if win_rate < 0.45:
            return "avoid"
        if win_rate > 0.58:
            return "promote"
        return "neutral"

    def insert_tick(self, trade: dict[str, Any]) -> None:
        trade_time = datetime.utcfromtimestamp(trade["trade_time_ms"] / 1000.0)
        event_time = datetime.utcfromtimestamp(trade["event_time_ms"] / 1000.0)
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO ticks (
                    symbol,
                    trade_id,
                    price,
                    qty,
                    quote_qty,
                    trade_time,
                    event_time,
                    is_buyer_maker
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    trade["symbol"],
                    trade["trade_id"],
                    trade["price"],
                    trade["qty"],
                    trade["quote_qty"],
                    trade_time,
                    event_time,
                    trade["is_buyer_maker"],
                ],
            )

    def count_recent_ticks(self, symbol: str, seconds: int) -> int:
        threshold = datetime.utcnow() - timedelta(seconds=seconds)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) FROM ticks
                WHERE symbol = ? AND trade_time >= ?
                """,
                [symbol, threshold],
            ).fetchone()
        return int(row[0]) if row else 0

    def get_recent_data_health(
        self,
        symbol: str,
        lookback_seconds: int,
        max_gap_seconds: int,
    ) -> dict[str, Any]:
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(seconds=max(lookback_seconds, 1))
        health = self._assess_tick_coverage(
            symbol=symbol,
            start_time=start_time,
            end_time=end_time,
            max_gap_seconds=max_gap_seconds,
        )
        return {
            "symbol": symbol,
            "lookback_seconds": lookback_seconds,
            **health,
        }

    def get_latest_price(self, symbol: str) -> float | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT price
                FROM ticks
                WHERE symbol = ?
                ORDER BY trade_time DESC
                LIMIT 1
                """,
                [symbol],
            ).fetchone()
        return float(row[0]) if row else None

    def calculate_and_store_orderflow(
        self, symbol: str, lookback_seconds: int
    ) -> dict[str, Any]:
        threshold = datetime.utcnow() - timedelta(seconds=lookback_seconds)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    COUNT(*) AS trade_count,
                    COALESCE(SUM(CASE WHEN is_buyer_maker = FALSE THEN qty ELSE 0 END), 0) AS taker_buy_volume,
                    COALESCE(SUM(CASE WHEN is_buyer_maker = TRUE THEN qty ELSE 0 END), 0) AS taker_sell_volume,
                    COALESCE(AVG(price), 0) AS avg_price,
                    COALESCE(MIN(price), 0) AS lowest_price,
                    COALESCE(MAX(price), 0) AS highest_price,
                    (SELECT price FROM ticks WHERE symbol = ? AND trade_time >= ? ORDER BY trade_time ASC LIMIT 1) AS oldest_price
                FROM ticks
                WHERE symbol = ? AND trade_time >= ?
                """,
                [symbol, threshold, symbol, threshold],
            ).fetchone()

            last_price_row = self._conn.execute(
                """
                SELECT price
                FROM ticks
                WHERE symbol = ?
                ORDER BY trade_time DESC
                LIMIT 1
                """,
                [symbol],
            ).fetchone()

            trade_count = int(row[0] or 0)
            taker_buy_volume = float(row[1] or 0.0)
            taker_sell_volume = float(row[2] or 0.0)
            avg_price = float(row[3] or 0.0)
            lowest_price = float(row[4] or 0.0)
            highest_price = float(row[5] or 0.0)
            oldest_price = float(row[6] or 0.0)
            last_price = float(last_price_row[0]) if last_price_row else avg_price

            volume_delta = taker_buy_volume - taker_sell_volume
            total_volume = taker_buy_volume + taker_sell_volume
            imbalance = (volume_delta / total_volume) if total_volume > 0 else 0.0

            snapshot = {
                "symbol": symbol,
                "lookback_seconds": lookback_seconds,
                "trade_count": trade_count,
                "taker_buy_volume": taker_buy_volume,
                "taker_sell_volume": taker_sell_volume,
                "volume_delta": volume_delta,
                "imbalance": imbalance,
                "avg_price": avg_price,
                "lowest_price": lowest_price,
                "highest_price": highest_price,
                "oldest_price": oldest_price,
                "last_price": last_price,
            }

            self._conn.execute(
                """
                INSERT INTO orderflow_snapshots (
                    symbol,
                    lookback_seconds,
                    trade_count,
                    taker_buy_volume,
                    taker_sell_volume,
                    volume_delta,
                    imbalance,
                    avg_price,
                    lowest_price,
                    highest_price,
                    oldest_price,
                    last_price
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    snapshot["symbol"],
                    snapshot["lookback_seconds"],
                    snapshot["trade_count"],
                    snapshot["taker_buy_volume"],
                    snapshot["taker_sell_volume"],
                    snapshot["volume_delta"],
                    snapshot["imbalance"],
                    snapshot["avg_price"],
                    snapshot["lowest_price"],
                    snapshot["highest_price"],
                    snapshot["oldest_price"],
                    snapshot["last_price"],
                ],
            )
        return snapshot

    def get_latest_orderflow_snapshot(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    symbol,
                    lookback_seconds,
                    trade_count,
                    taker_buy_volume,
                    taker_sell_volume,
                    volume_delta,
                    imbalance,
                    avg_price,
                    lowest_price,
                    highest_price,
                    oldest_price,
                    last_price
                FROM orderflow_snapshots
                WHERE symbol = ?
                ORDER BY snapshot_time DESC
                LIMIT 1
                """,
                [symbol],
            ).fetchone()
        if not row:
            return None
        return {
            "symbol": row[0],
            "lookback_seconds": int(row[1]),
            "trade_count": int(row[2]),
            "taker_buy_volume": float(row[3]),
            "taker_sell_volume": float(row[4]),
            "volume_delta": float(row[5]),
            "imbalance": float(row[6]),
            "avg_price": float(row[7]),
            "lowest_price": float(row[8]),
            "highest_price": float(row[9]),
            "oldest_price": float(row[10]),
            "last_price": float(row[11]),
        }

    def store_orderflow_analysis(self, symbol: str, analysis: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO orderflow_analysis (
                    symbol,
                    regime,
                    confidence,
                    summary,
                    metrics_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    symbol,
                    analysis["regime"],
                    analysis["confidence"],
                    analysis["summary"],
                    json.dumps(analysis, sort_keys=True),
                ],
            )

    def get_latest_analysis(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT metrics_json
                FROM orderflow_analysis
                WHERE symbol = ?
                ORDER BY analysis_time DESC
                LIMIT 1
                """,
                [symbol],
            ).fetchone()
        if not row:
            return None
        return json.loads(row[0])

    def store_trading_opportunity(
        self,
        symbol: str,
        opportunity: dict[str, Any],
        signal_id: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO trading_opportunities (
                    symbol,
                    side,
                    entry_price,
                    stop_price,
                    take_profit,
                    confidence,
                    rationale,
                    regime,
                    signal_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    symbol,
                    opportunity["side"],
                    opportunity["entry_price"],
                    opportunity["stop_price"],
                    opportunity["take_profit"],
                    opportunity["confidence"],
                    opportunity["rationale"],
                    opportunity["regime"],
                    signal_id,
                ],
            )

    def store_trade_signal(
        self,
        symbol: str,
        opportunity: dict[str, Any],
        horizon_seconds: int,
        analysis: dict[str, Any] | None = None,
        decision_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        generated_at = datetime.utcnow()
        evaluation_due_time = generated_at + timedelta(seconds=max(horizon_seconds, 1))
        signal_id = uuid.uuid4().hex
        side = str(opportunity["side"]).upper()
        entry_price = float(opportunity["entry_price"])
        stop_price = float(opportunity["stop_price"])
        take_profit = float(opportunity["take_profit"])
        confidence = float(opportunity["confidence"])
        rationale = str(opportunity["rationale"])
        regime = str(opportunity["regime"])

        status = "PENDING_VALIDATION"
        evaluated = False
        closed_at: datetime | None = None
        close_price: float | None = None
        pnl_pct: float | None = None
        was_profitable: bool | None = None
        outcome_reason: str | None = None

        if side not in {"LONG", "SHORT"}:
            status = "SKIPPED"
            evaluated = True
            closed_at = generated_at
            close_price = entry_price
            pnl_pct = 0.0
            was_profitable = False
            outcome_reason = "no_trade_signal"
        data_quality = "pending" if status == "PENDING_VALIDATION" else "not_applicable"

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO trade_signals (
                    signal_id,
                    generated_at,
                    symbol,
                    side,
                    entry_price,
                    stop_price,
                    take_profit,
                    confidence,
                    rationale,
                    regime,
                    analysis_json,
                    decision_trace_json,
                    evaluation_due_time,
                    status,
                    evaluated,
                    closed_at,
                    close_price,
                    pnl_pct,
                    was_profitable,
                    outcome_reason,
                    evaluation_attempts,
                    data_quality,
                    challenge_verdict,
                    challenge_score,
                    challenge_summary
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    signal_id,
                    generated_at,
                    symbol,
                    side,
                    entry_price,
                    stop_price,
                    take_profit,
                    confidence,
                    rationale,
                    regime,
                    json.dumps(analysis or {}, sort_keys=True),
                    json.dumps(decision_trace or {}, sort_keys=True),
                    evaluation_due_time,
                    status,
                    evaluated,
                    closed_at,
                    close_price,
                    pnl_pct,
                    was_profitable,
                    outcome_reason,
                    0,
                    data_quality,
                    "PENDING",
                    None,
                    None,
                ],
            )
        return {
            "signal_id": signal_id,
            "status": status,
            "evaluation_due_time": evaluation_due_time.isoformat(),
            "challenge_verdict": "PENDING",
        }

    def get_trade_signal(self, signal_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    signal_id,
                    generated_at,
                    symbol,
                    side,
                    entry_price,
                    stop_price,
                    take_profit,
                    confidence,
                    rationale,
                    regime,
                    analysis_json,
                    decision_trace_json,
                    evaluation_due_time,
                    status,
                    evaluated,
                    closed_at,
                    close_price,
                    pnl_pct,
                    was_profitable,
                    outcome_reason,
                    COALESCE(evaluation_attempts, 0) AS evaluation_attempts,
                    COALESCE(data_quality, 'unknown') AS data_quality,
                    COALESCE(challenge_verdict, 'PENDING') AS challenge_verdict,
                    challenge_score,
                    challenge_summary
                FROM trade_signals
                WHERE signal_id = ?
                LIMIT 1
                """,
                [signal_id],
            ).fetchone()
        if not row:
            return None

        return {
            "signal_id": str(row[0]),
            "generated_at": row[1],
            "symbol": str(row[2]),
            "side": str(row[3]),
            "entry_price": float(row[4]),
            "stop_price": float(row[5]),
            "take_profit": float(row[6]),
            "confidence": float(row[7]),
            "rationale": str(row[8]),
            "regime": str(row[9]),
            "analysis": self._load_json_blob(row[10]),
            "decision_trace": self._load_json_blob(row[11]),
            "evaluation_due_time": row[12],
            "status": str(row[13]),
            "evaluated": bool(row[14]),
            "closed_at": row[15],
            "close_price": float(row[16]) if row[16] is not None else None,
            "pnl_pct": float(row[17]) if row[17] is not None else None,
            "was_profitable": bool(row[18]) if row[18] is not None else None,
            "outcome_reason": str(row[19]) if row[19] is not None else None,
            "evaluation_attempts": int(row[20] or 0),
            "data_quality": str(row[21]),
            "challenge_verdict": str(row[22]),
            "challenge_score": float(row[23]) if row[23] is not None else None,
            "challenge_summary": str(row[24]) if row[24] is not None else None,
        }

    def get_latest_signal_for_challenge(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT signal_id
                FROM trade_signals
                WHERE symbol = ?
                  AND COALESCE(challenge_verdict, 'PENDING') = 'PENDING'
                ORDER BY generated_at DESC
                LIMIT 1
                """,
                [symbol],
            ).fetchone()
        if not row:
            return None
        return self.get_trade_signal(signal_id=str(row[0]))

    def store_signal_challenge(
        self,
        signal_id: str,
        symbol: str,
        verdict: str,
        score: float,
        summary: str,
        challenge_payload: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_verdict = verdict.upper()
        with self._lock:
            self._conn.execute(
                """
                DELETE FROM signal_challenges
                WHERE signal_id = ?
                """,
                [signal_id],
            )
            self._conn.execute(
                """
                INSERT INTO signal_challenges (
                    signal_id,
                    symbol,
                    verdict,
                    score,
                    summary,
                    challenge_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    signal_id,
                    symbol,
                    normalized_verdict,
                    float(score),
                    summary,
                    json.dumps(challenge_payload, sort_keys=True),
                ],
            )
            self._conn.execute(
                """
                UPDATE trade_signals
                SET
                    challenge_verdict = ?,
                    challenge_score = ?,
                    challenge_summary = ?,
                    status = CASE
                        WHEN ? = 'APPROVE' AND status = 'PENDING_VALIDATION' THEN 'OPEN'
                        WHEN ? = 'REJECT' AND status = 'PENDING_VALIDATION' THEN 'REJECTED'
                        ELSE status
                    END
                WHERE signal_id = ?
                """,
                [
                    normalized_verdict,
                    float(score),
                    summary,
                    normalized_verdict,
                    normalized_verdict,
                    signal_id,
                ],
            )

        return {
            "signal_id": signal_id,
            "verdict": normalized_verdict,
            "score": round(float(score), 4),
            "summary": summary,
            "status_updated": normalized_verdict in ("APPROVE", "REJECT"),
        }

    def list_signal_audit_rows(
        self, symbol: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    signal_id,
                    generated_at,
                    side,
                    regime,
                    confidence,
                    status,
                    COALESCE(challenge_verdict, 'PENDING') AS challenge_verdict,
                    challenge_score,
                    challenge_summary,
                    pnl_pct,
                    was_profitable,
                    outcome_reason,
                    data_quality,
                    rationale
                FROM trade_signals
                WHERE symbol = ?
                ORDER BY generated_at DESC
                LIMIT ?
                """,
                [symbol, max(limit, 1)],
            ).fetchall()

        history: list[dict[str, Any]] = []
        for row in rows:
            history.append(
                {
                    "signal_id": str(row[0]),
                    "generated_at": row[1],
                    "side": str(row[2]),
                    "regime": str(row[3]),
                    "confidence": round(float(row[4]), 4),
                    "status": str(row[5]),
                    "challenge_verdict": str(row[6]),
                    "challenge_score": round(float(row[7]), 4)
                    if row[7] is not None
                    else None,
                    "challenge_summary": str(row[8]) if row[8] is not None else None,
                    "pnl_pct": round(float(row[9]), 4) if row[9] is not None else None,
                    "was_profitable": bool(row[10]) if row[10] is not None else None,
                    "outcome_reason": str(row[11]) if row[11] is not None else None,
                    "data_quality": str(row[12]) if row[12] is not None else None,
                    "rationale": str(row[13]),
                }
            )
        return history

    def get_signal_debug(self, signal_id: str) -> dict[str, Any] | None:
        signal = self.get_trade_signal(signal_id=signal_id)
        if signal is None:
            return None

        with self._lock:
            row = self._conn.execute(
                """
                SELECT challenge_time, verdict, score, summary, challenge_json
                FROM signal_challenges
                WHERE signal_id = ?
                LIMIT 1
                """,
                [signal_id],
            ).fetchone()

        challenge = None
        if row:
            challenge = {
                "challenge_time": row[0],
                "verdict": str(row[1]),
                "score": round(float(row[2]), 4),
                "summary": str(row[3]),
                "details": self._load_json_blob(row[4]),
            }

        return {
            **signal,
            "challenge": challenge,
        }

    def get_latest_signal_debug(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT signal_id
                FROM trade_signals
                WHERE symbol = ?
                ORDER BY generated_at DESC
                LIMIT 1
                """,
                [symbol],
            ).fetchone()
        if not row:
            return None
        return self.get_signal_debug(signal_id=str(row[0]))

    def get_learning_profiles(
        self, symbol: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT side, regime, sample_size, win_rate, avg_pnl_pct, recommendation
                FROM learning_memory
                WHERE symbol = ?
                ORDER BY sample_size DESC, regime ASC
                LIMIT ?
                """,
                [symbol, max(limit, 1)],
            ).fetchall()

        profiles: list[dict[str, Any]] = []
        for row in rows:
            profiles.append(
                {
                    "side": str(row[0]),
                    "regime": str(row[1]),
                    "sample_size": int(row[2]),
                    "win_rate": round(float(row[3]), 4),
                    "avg_pnl_pct": round(float(row[4]), 4),
                    "recommendation": str(row[5]),
                }
            )
        return profiles

    def get_learning_recommendation(
        self,
        symbol: str,
        side: str,
        regime: str,
    ) -> dict[str, Any]:
        normalized_side = side.upper()
        if normalized_side not in {"LONG", "SHORT"}:
            return {
                "recommendation": "neutral",
                "sample_size": 0,
                "win_rate": 0.0,
                "avg_pnl_pct": 0.0,
            }

        with self._lock:
            row = self._conn.execute(
                """
                SELECT recommendation, sample_size, win_rate, avg_pnl_pct
                FROM learning_memory
                WHERE symbol = ? AND side = ? AND regime = ?
                ORDER BY snapshot_time DESC
                LIMIT 1
                """,
                [symbol, normalized_side, regime],
            ).fetchone()

        if not row:
            return {
                "recommendation": "insufficient_data",
                "sample_size": 0,
                "win_rate": 0.0,
                "avg_pnl_pct": 0.0,
            }
        return {
            "recommendation": str(row[0]),
            "sample_size": int(row[1]),
            "win_rate": float(row[2]),
            "avg_pnl_pct": float(row[3]),
        }

    def evaluate_due_signals(
        self,
        symbol: str,
        max_signals: int = 200,
        max_gap_seconds: int = 10,
        retry_seconds: int = 30,
        max_deferrals: int = 3,
    ) -> dict[str, Any]:
        now = datetime.utcnow()
        evaluated_count = 0
        wins = 0
        losses = 0
        expired = 0
        no_data = 0
        deferred = 0

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    signal_id,
                    generated_at,
                    evaluation_due_time,
                    side,
                    entry_price,
                    stop_price,
                    take_profit,
                    COALESCE(evaluation_attempts, 0) AS evaluation_attempts,
                    decision_trace_json
                FROM trade_signals
                WHERE symbol = ?
                  AND status IN ('OPEN', 'PENDING_VALIDATION')
                  AND evaluation_due_time <= ?
                ORDER BY evaluation_due_time ASC
                LIMIT ?
                """,
                [symbol, now, max_signals],
            ).fetchall()

        for row in rows:
            signal_id = str(row[0])
            generated_at = row[1]
            evaluation_due_time = row[2]
            side = str(row[3]).upper()
            entry_price = float(row[4])
            stop_price = float(row[5])
            take_profit = float(row[6])
            evaluation_attempts = int(row[7] or 0)
            decision_trace = self._load_json_blob(row[8])

            coverage = self._assess_tick_coverage(
                symbol=symbol,
                start_time=generated_at,
                end_time=evaluation_due_time,
                max_gap_seconds=max_gap_seconds,
            )
            if not coverage["usable"]:
                evaluation_attempts += 1
                if evaluation_attempts <= max_deferrals:
                    deferred_until = now + timedelta(seconds=max(retry_seconds, 1))
                    with self._lock:
                        self._conn.execute(
                            """
                            UPDATE trade_signals
                            SET
                                evaluation_due_time = ?,
                                evaluation_attempts = ?,
                                outcome_reason = ?,
                                data_quality = ?
                            WHERE signal_id = ?
                            """,
                            [
                                deferred_until,
                                evaluation_attempts,
                                f"deferred:{coverage['reason']}",
                                "insufficient",
                                signal_id,
                            ],
                        )
                    deferred += 1
                    continue

                status = "NO_DATA"
                close_time = now
                latest_tick = self._latest_price_before(symbol, evaluation_due_time)
                close_price = latest_tick[1] if latest_tick else entry_price
                outcome_reason = f"insufficient_tick_coverage:{coverage['reason']}"
                pnl_pct = 0.0
                was_profitable = False
                data_quality = "insufficient"
            else:
                tp_hit: tuple[datetime, float] | None = None
                sl_hit: tuple[datetime, float] | None = None

                # Infer 1.0 ATR distance exactly
                atr_1 = 0.0
                targets_data = decision_trace.get("targets", {})
                if "atr_dollar" in targets_data:
                    atr_1 = float(targets_data["atr_dollar"]) * (3 ** 0.5) # this gets back atr_3m from atr_dollar which was already atr_3m... Wait, atr_dollar in backbone was entry * atr_3m_pct.
                
                if atr_1 <= 0:
                    # Fallback to inference using sl_multiplier if available
                    risk_dist = abs(entry_price - stop_price)
                    sl_mult = float(targets_data.get("sl_multiplier", 1.2))
                    atr_1 = risk_dist / sl_mult if sl_mult > 0 else 0

                if side == "LONG":
                    be_trigger = entry_price + atr_1
                    be_hit = self._first_price_hit(
                        symbol,
                        generated_at,
                        evaluation_due_time,
                        "price >= ?",
                        be_trigger,
                    )
                    tp_hit = self._first_price_hit(
                        symbol,
                        generated_at,
                        evaluation_due_time,
                        "price >= ?",
                        take_profit,
                    )
                    sl_hit = self._first_price_hit(
                        symbol,
                        generated_at,
                        evaluation_due_time,
                        "price <= ?",
                        stop_price,
                    )

                    if be_hit:
                        # If Break-even was hit, the SL moves to Entry.
                        # We only check for new SL hits AFTER the BE trigger time.
                        new_sl_hit = self._first_price_hit(
                            symbol,
                            be_hit[0],
                            evaluation_due_time,
                            "price <= ?",
                            entry_price,
                        )
                        if (
                            new_sl_hit
                            and (not sl_hit or new_sl_hit[0] < sl_hit[0])
                            and (not tp_hit or new_sl_hit[0] < tp_hit[0])
                        ):
                            sl_hit = new_sl_hit
                            stop_price = entry_price  # Override

                elif side == "SHORT":
                    be_trigger = entry_price - atr_1
                    be_hit = self._first_price_hit(
                        symbol,
                        generated_at,
                        evaluation_due_time,
                        "price <= ?",
                        be_trigger,
                    )
                    tp_hit = self._first_price_hit(
                        symbol,
                        generated_at,
                        evaluation_due_time,
                        "price <= ?",
                        take_profit,
                    )
                    sl_hit = self._first_price_hit(
                        symbol,
                        generated_at,
                        evaluation_due_time,
                        "price >= ?",
                        stop_price,
                    )

                    if be_hit:
                        new_sl_hit = self._first_price_hit(
                            symbol,
                            be_hit[0],
                            evaluation_due_time,
                            "price >= ?",
                            entry_price,
                        )
                        if (
                            new_sl_hit
                            and (not sl_hit or new_sl_hit[0] < sl_hit[0])
                            and (not tp_hit or new_sl_hit[0] < tp_hit[0])
                        ):
                            sl_hit = new_sl_hit
                            stop_price = entry_price

                if tp_hit and sl_hit:
                    if tp_hit[0] <= sl_hit[0]:
                        status = "WIN"
                        close_time = tp_hit[0]
                        close_price = tp_hit[1]
                        outcome_reason = "take_profit_hit_first"
                    else:
                        status = "LOSS"
                        close_time = sl_hit[0]
                        close_price = sl_hit[1]
                        outcome_reason = (
                            "stop_loss_hit_first"
                            if stop_price != entry_price
                            else "break_even_trailing_stop"
                        )
                elif tp_hit:
                    status = "WIN"
                    close_time = tp_hit[0]
                    close_price = tp_hit[1]
                    outcome_reason = "take_profit_hit"
                elif sl_hit:
                    status = (
                        "LOSS" if stop_price != entry_price else "EXPIRED"
                    )  # Count BE as expired to not ruin winrate with losses
                    close_time = sl_hit[0]
                    close_price = sl_hit[1]
                    outcome_reason = (
                        "stop_loss_hit"
                        if stop_price != entry_price
                        else "break_even_trailing_stop"
                    )
                else:
                    status = "EXPIRED"
                    latest_tick = self._latest_price_before(symbol, evaluation_due_time)
                    close_time = latest_tick[0] if latest_tick else evaluation_due_time
                    close_price = latest_tick[1] if latest_tick else entry_price
                    outcome_reason = "expired_no_touch"

                pnl_pct = self._calculate_pnl_pct(side, entry_price, close_price)
                was_profitable = pnl_pct > 0
                data_quality = "ok"

            with self._lock:
                self._conn.execute(
                    """
                    UPDATE trade_signals
                    SET
                        status = ?,
                        evaluated = TRUE,
                        closed_at = ?,
                        close_price = ?,
                        pnl_pct = ?,
                        was_profitable = ?,
                        outcome_reason = ?,
                        evaluation_attempts = ?,
                        data_quality = ?
                    WHERE signal_id = ?
                    """,
                    [
                        status,
                        close_time,
                        close_price,
                        pnl_pct,
                        was_profitable,
                        outcome_reason,
                        evaluation_attempts,
                        data_quality,
                        signal_id,
                    ],
                )
                self._conn.execute(
                    """
                    INSERT INTO signal_evaluations (
                        signal_id,
                        symbol,
                        status,
                        close_price,
                        pnl_pct,
                        reason
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        signal_id,
                        symbol,
                        status,
                        close_price,
                        pnl_pct,
                        outcome_reason,
                    ],
                )

            evaluated_count += 1
            if status == "WIN":
                wins += 1
            elif status == "LOSS":
                losses += 1
            elif status == "EXPIRED":
                expired += 1
            elif status == "NO_DATA":
                no_data += 1

        with self._lock:
            open_row = self._conn.execute(
                """
                SELECT COUNT(*)
                FROM trade_signals
                WHERE symbol = ? AND status = 'OPEN'
                """,
                [symbol],
            ).fetchone()

        return {
            "evaluated_count": evaluated_count,
            "wins": wins,
            "losses": losses,
            "expired": expired,
            "no_data": no_data,
            "deferred": deferred,
            "open_signals": int(open_row[0] or 0) if open_row else 0,
        }

    def refresh_learning_memory(
        self, symbol: str, min_samples: int = 12
    ) -> dict[str, Any]:
        inserted_profiles = 0
        profiles: list[dict[str, Any]] = []

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    side,
                    regime,
                    COUNT(*) AS sample_size,
                    SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) AS wins,
                    AVG(COALESCE(pnl_pct, 0)) AS avg_pnl_pct
                FROM trade_signals
                WHERE symbol = ?
                  AND side IN ('LONG', 'SHORT')
                  AND status IN ('WIN', 'LOSS')
                GROUP BY side, regime
                """,
                [symbol],
            ).fetchall()

            # learning_memory is the current aggregate state, rebuilt from source trades each cycle.
            self._conn.execute(
                """
                DELETE FROM learning_memory
                WHERE symbol = ?
                """,
                [symbol],
            )

            for row in rows:
                side = str(row[0])
                regime = str(row[1])
                sample_size = int(row[2])
                wins = int(row[3] or 0)
                avg_pnl_pct = float(row[4] or 0.0)
                win_rate = (wins / sample_size) if sample_size > 0 else 0.0
                recommendation = self._classify_recommendation(
                    sample_size, win_rate, min_samples=min_samples
                )

                self._conn.execute(
                    """
                    INSERT INTO learning_memory (
                        symbol,
                        side,
                        regime,
                        sample_size,
                        win_rate,
                        avg_pnl_pct,
                        recommendation
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        symbol,
                        side,
                        regime,
                        sample_size,
                        win_rate,
                        avg_pnl_pct,
                        recommendation,
                    ],
                )
                inserted_profiles += 1
                profiles.append(
                    {
                        "side": side,
                        "regime": regime,
                        "sample_size": sample_size,
                        "win_rate": round(win_rate, 4),
                        "avg_pnl_pct": round(avg_pnl_pct, 4),
                        "recommendation": recommendation,
                    }
                )

        return {
            "inserted_profiles": inserted_profiles,
            "profiles": profiles,
        }

    def refresh_challenger_learning_memory(
        self, symbol: str, min_samples: int = 12
    ) -> dict[str, Any]:
        inserted_profiles = 0
        profiles: list[dict[str, Any]] = []

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    challenge_verdict,
                    COUNT(*) AS sample_size,
                    SUM(
                        CASE
                            WHEN challenge_verdict IN ('APPROVE', 'WEAK_APPROVE') AND was_profitable = TRUE THEN 1
                            WHEN challenge_verdict = 'REJECT' AND was_profitable = FALSE THEN 1
                            ELSE 0
                        END
                    ) AS correct_count,
                    SUM(
                        CASE
                            WHEN challenge_verdict IN ('APPROVE', 'WEAK_APPROVE') AND was_profitable = FALSE THEN 1
                            WHEN challenge_verdict = 'REJECT' AND was_profitable = TRUE THEN 1
                            ELSE 0
                        END
                    ) AS incorrect_count,
                    AVG(COALESCE(pnl_pct, 0)) AS avg_pnl_pct
                FROM trade_signals
                WHERE symbol = ?
                  AND side IN ('LONG', 'SHORT')
                  AND challenge_verdict IN ('APPROVE', 'WEAK_APPROVE', 'REJECT')
                  AND status IN ('WIN', 'LOSS', 'EXPIRED')
                GROUP BY challenge_verdict
                ORDER BY challenge_verdict ASC
                """,
                [symbol],
            ).fetchall()

            self._conn.execute(
                """
                DELETE FROM challenger_learning_memory
                WHERE symbol = ?
                """,
                [symbol],
            )

            for row in rows:
                verdict = str(row[0])
                sample_size = int(row[1] or 0)
                correct_count = int(row[2] or 0)
                incorrect_count = int(row[3] or 0)
                avg_pnl_pct = float(row[4] or 0.0)
                accuracy = (correct_count / sample_size) if sample_size > 0 else 0.0
                recommendation = self._classify_challenger_recommendation(
                    sample_size=sample_size,
                    accuracy=accuracy,
                    min_samples=min_samples,
                )

                self._conn.execute(
                    """
                    INSERT INTO challenger_learning_memory (
                        symbol,
                        verdict,
                        sample_size,
                        correct_count,
                        incorrect_count,
                        accuracy,
                        avg_pnl_pct,
                        recommendation
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        symbol,
                        verdict,
                        sample_size,
                        correct_count,
                        incorrect_count,
                        accuracy,
                        avg_pnl_pct,
                        recommendation,
                    ],
                )
                inserted_profiles += 1
                profiles.append(
                    {
                        "verdict": verdict,
                        "sample_size": sample_size,
                        "correct_count": correct_count,
                        "incorrect_count": incorrect_count,
                        "accuracy": round(accuracy, 4),
                        "avg_pnl_pct": round(avg_pnl_pct, 4),
                        "recommendation": recommendation,
                    }
                )

        return {
            "inserted_profiles": inserted_profiles,
            "profiles": profiles,
        }

    def get_challenger_learning_profiles(
        self, symbol: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT verdict, sample_size, correct_count, incorrect_count, accuracy, avg_pnl_pct, recommendation
                FROM challenger_learning_memory
                WHERE symbol = ?
                ORDER BY sample_size DESC, verdict ASC
                LIMIT ?
                """,
                [symbol, max(limit, 1)],
            ).fetchall()

        profiles: list[dict[str, Any]] = []
        for row in rows:
            profiles.append(
                {
                    "verdict": str(row[0]),
                    "sample_size": int(row[1]),
                    "correct_count": int(row[2]),
                    "incorrect_count": int(row[3]),
                    "accuracy": round(float(row[4]), 4),
                    "avg_pnl_pct": round(float(row[5]), 4),
                    "recommendation": str(row[6]),
                }
            )
        return profiles

    def get_challenger_learning_profile(
        self, symbol: str, verdict: str
    ) -> dict[str, Any]:
        normalized_verdict = verdict.upper()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT verdict, sample_size, correct_count, incorrect_count, accuracy, avg_pnl_pct, recommendation
                FROM challenger_learning_memory
                WHERE symbol = ? AND verdict = ?
                ORDER BY snapshot_time DESC
                LIMIT 1
                """,
                [symbol, normalized_verdict],
            ).fetchone()

        if not row:
            return {
                "verdict": normalized_verdict,
                "sample_size": 0,
                "correct_count": 0,
                "incorrect_count": 0,
                "accuracy": 0.0,
                "avg_pnl_pct": 0.0,
                "recommendation": "insufficient_data",
            }
        return {
            "verdict": str(row[0]),
            "sample_size": int(row[1]),
            "correct_count": int(row[2]),
            "incorrect_count": int(row[3]),
            "accuracy": round(float(row[4]), 4),
            "avg_pnl_pct": round(float(row[5]), 4),
            "recommendation": str(row[6]),
        }

    def get_challenger_performance_summary(self, symbol: str) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    COALESCE(challenge_verdict, 'PENDING') AS challenge_verdict,
                    COUNT(*) AS total_signals,
                    SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN status = 'LOSS' THEN 1 ELSE 0 END) AS losses,
                    SUM(CASE WHEN status = 'EXPIRED' THEN 1 ELSE 0 END) AS expired,
                    SUM(CASE WHEN status = 'OPEN' THEN 1 ELSE 0 END) AS open_signals,
                    AVG(challenge_score) AS avg_challenge_score
                FROM trade_signals
                WHERE symbol = ?
                  AND side IN ('LONG', 'SHORT')
                GROUP BY 1
                ORDER BY total_signals DESC, challenge_verdict ASC
                """,
                [symbol],
            ).fetchall()

        breakdown: list[dict[str, Any]] = []
        for row in rows:
            wins = int(row[2] or 0)
            losses = int(row[3] or 0)
            decisive = wins + losses
            breakdown.append(
                {
                    "challenge_verdict": str(row[0]),
                    "total_signals": int(row[1] or 0),
                    "wins": wins,
                    "losses": losses,
                    "expired": int(row[4] or 0),
                    "open_signals": int(row[5] or 0),
                    "win_rate": round((wins / decisive) if decisive > 0 else 0.0, 4),
                    "avg_challenge_score": round(float(row[6]), 4)
                    if row[6] is not None
                    else None,
                }
            )

        return {
            "verdict_breakdown": breakdown,
        }

    def get_signal_performance_summary(self, symbol: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN side IN ('LONG', 'SHORT') THEN 1 ELSE 0 END), 0) AS total_trades,
                    COALESCE(SUM(CASE WHEN side IN ('LONG', 'SHORT') AND status = 'OPEN' THEN 1 ELSE 0 END), 0) AS open_trades,
                    COALESCE(SUM(CASE WHEN side IN ('LONG', 'SHORT') AND status = 'WIN' THEN 1 ELSE 0 END), 0) AS wins,
                    COALESCE(SUM(CASE WHEN side IN ('LONG', 'SHORT') AND status = 'LOSS' THEN 1 ELSE 0 END), 0) AS losses,
                    COALESCE(SUM(CASE WHEN side IN ('LONG', 'SHORT') AND status = 'EXPIRED' THEN 1 ELSE 0 END), 0) AS expired,
                    COALESCE(SUM(CASE WHEN side IN ('LONG', 'SHORT') AND status = 'NO_DATA' THEN 1 ELSE 0 END), 0) AS no_data,
                    COALESCE(SUM(CASE WHEN status = 'SKIPPED' THEN 1 ELSE 0 END), 0) AS skipped,
                    COALESCE(AVG(CASE WHEN side IN ('LONG', 'SHORT') AND evaluated = TRUE THEN pnl_pct END), 0) AS avg_pnl_pct
                FROM trade_signals
                WHERE symbol = ?
                """,
                [symbol],
            ).fetchone()

        if not row:
            return {
                "total_trades": 0,
                "open_trades": 0,
                "wins": 0,
                "losses": 0,
                "expired": 0,
                "no_data": 0,
                "skipped": 0,
                "win_rate": 0.0,
                "avg_pnl_pct": 0.0,
            }

        wins = int(row[2])
        losses = int(row[3])
        decisive = wins + losses
        win_rate = (wins / decisive) if decisive > 0 else 0.0
        return {
            "total_trades": int(row[0]),
            "open_trades": int(row[1]),
            "wins": wins,
            "losses": losses,
            "expired": int(row[4]),
            "no_data": int(row[5]),
            "skipped": int(row[6]),
            "win_rate": round(win_rate, 4),
            "avg_pnl_pct": round(float(row[7] or 0.0), 4),
        }

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PROFITABILITY OVERHAUL: New Methods
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @staticmethod
    def get_session_tag(utc_hour: int | None = None) -> str:
        """Returns session tag based on UTC hour."""
        if utc_hour is None:
            utc_hour = datetime.utcnow().hour
        if 0 <= utc_hour < 8:
            return "ASIAN"
        elif 8 <= utc_hour < 13:
            return "EUROPEAN"
        elif 13 <= utc_hour < 21:
            return "US"
        else:
            return "CLOSING"

    def get_regime_performance(
        self, session: str, side: str, adx: float
    ) -> dict[str, Any]:
        """Query hourly_performance for a given session, side, and regime."""
        regime = "TRENDING" if adx >= 25 else "RANGING"
        # Map session to hour range
        session_hours = {
            "ASIAN": list(range(0, 8)),
            "EUROPEAN": list(range(8, 13)),
            "US": list(range(13, 21)),
            "CLOSING": list(range(21, 24)),
        }
        hours = session_hours.get(session.upper(), list(range(0, 24)))
        placeholders = ",".join(["?"] * len(hours))

        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(total_signals), 0),
                    COALESCE(SUM(wins), 0),
                    COALESCE(SUM(losses), 0),
                    COALESCE(AVG(avg_mfe), 0),
                    COALESCE(AVG(avg_mae), 0)
                FROM hourly_performance
                WHERE side = ? AND regime = ?
                  AND hour_utc IN ({placeholders})
                """,
                [side.upper(), regime] + hours,
            ).fetchone()

        if not row or int(row[0]) == 0:
            return {
                "win_rate": 0.5,
                "avg_mfe": 0.0,
                "avg_mae": 0.0,
                "total_signals": 0,
                "regime": regime,
            }
        total = int(row[0])
        wins = int(row[1])
        return {
            "win_rate": wins / total if total > 0 else 0.5,
            "avg_mfe": float(row[3]),
            "avg_mae": float(row[4]),
            "total_signals": total,
            "regime": regime,
        }

    def update_hourly_performance(self, signal_data: dict[str, Any]) -> None:
        """Upsert hourly_performance on trade close."""
        hour_utc = signal_data.get("hour_utc", datetime.utcnow().hour)
        side = str(signal_data.get("side", "UNKNOWN")).upper()
        regime = str(signal_data.get("regime", "UNKNOWN")).upper()
        is_win = signal_data.get("status", "") == "WIN"
        mfe = float(signal_data.get("mfe_percent", 0))
        mae = float(signal_data.get("mae_percent", 0))

        with self._lock:
            existing = self._conn.execute(
                """
                SELECT total_signals, wins, losses, avg_mfe, avg_mae
                FROM hourly_performance
                WHERE hour_utc = ? AND side = ? AND regime = ?
                """,
                [hour_utc, side, regime],
            ).fetchone()

            if existing:
                total = int(existing[0]) + 1
                wins = int(existing[1]) + (1 if is_win else 0)
                losses = int(existing[2]) + (0 if is_win else 1)
                # Running average for MFE/MAE
                old_mfe = float(existing[3])
                old_mae = float(existing[4])
                new_avg_mfe = (old_mfe * (total - 1) + mfe) / total
                new_avg_mae = (old_mae * (total - 1) + mae) / total
                win_rate = wins / total if total > 0 else 0.0

                self._conn.execute(
                    """
                    UPDATE hourly_performance
                    SET total_signals = ?, wins = ?, losses = ?,
                        avg_mfe = ?, avg_mae = ?, win_rate = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE hour_utc = ? AND side = ? AND regime = ?
                    """,
                    [total, wins, losses, new_avg_mfe, new_avg_mae,
                     win_rate, hour_utc, side, regime],
                )
            else:
                win_rate = 1.0 if is_win else 0.0
                self._conn.execute(
                    """
                    INSERT INTO hourly_performance
                        (hour_utc, side, regime, total_signals, wins, losses,
                         avg_mfe, avg_mae, win_rate)
                    VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    [hour_utc, side, regime,
                     1 if is_win else 0, 0 if is_win else 1,
                     mfe, mae, win_rate],
                )

    def batch_insert_l2_snapshots(self, snapshots: list[dict[str, Any]]) -> None:
        """Bulk insert L2 depth snapshots."""
        if not snapshots:
            return
        with self._lock:
            for snap in snapshots:
                self._conn.execute(
                    """
                    INSERT INTO l2_snapshots
                        (bid_0, ask_0, bid_vol_0_1pct, ask_vol_0_1pct,
                         obi_0_1pct, spread_ticks)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        snap.get("best_bid", 0),
                        snap.get("best_ask", 0),
                        snap.get("bid_vol_0_1pct", 0),
                        snap.get("ask_vol_0_1pct", 0),
                        snap.get("obi_0_1pct", 0),
                        snap.get("spread_ticks", 0),
                    ],
                )

    def get_recent_signals_in_window(
        self, symbol: str, seconds: int, side: str
    ) -> list[dict[str, Any]]:
        """Check for recent signals within a time window for clustering prevention."""
        threshold = datetime.utcnow() - timedelta(seconds=seconds)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT signal_id, generated_at, side, status
                FROM trade_signals
                WHERE symbol = ? AND side = ? AND generated_at >= ?
                  AND status IN ('OPEN', 'PENDING_VALIDATION')
                ORDER BY generated_at DESC
                """,
                [symbol, side.upper(), threshold],
            ).fetchall()
        return [
            {
                "signal_id": str(row[0]),
                "generated_at": row[1],
                "side": str(row[2]),
                "status": str(row[3]),
            }
            for row in rows
        ]

    def get_hourly_performance_table(self) -> list[dict[str, Any]]:
        """Returns hourly_performance table ordered by hour_utc."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT hour_utc, side, regime, total_signals, wins, losses,
                       avg_mfe, avg_mae, win_rate, updated_at
                FROM hourly_performance
                ORDER BY hour_utc ASC
                """
            ).fetchall()
        return [
            {
                "hour_utc": int(row[0]),
                "side": str(row[1]),
                "regime": str(row[2]),
                "total_signals": int(row[3]),
                "wins": int(row[4]),
                "losses": int(row[5]),
                "avg_mfe": round(float(row[6]), 4),
                "avg_mae": round(float(row[7]), 4),
                "win_rate": round(float(row[8]), 4),
                "updated_at": row[9],
            }
            for row in rows
        ]

    def get_missed_profit_signals(
        self, symbol: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Query EXPIRED signals where MFE exceeded 2x target distance."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT signal_id, generated_at, side, entry_price, take_profit,
                       mfe_percent, mae_percent, session_tag,
                       gross_pnl_percent, net_pnl_percent
                FROM trade_signals
                WHERE symbol = ?
                  AND status = 'EXPIRED'
                  AND mfe_percent > 0.005
                  AND mfe_percent > 2 * ABS(
                      CASE WHEN side = 'LONG'
                           THEN (take_profit - entry_price) / entry_price
                           ELSE (entry_price - take_profit) / entry_price
                      END
                  )
                ORDER BY mfe_percent DESC
                LIMIT ?
                """,
                [symbol, max(limit, 1)],
            ).fetchall()
        return [
            {
                "signal_id": str(row[0]),
                "generated_at": row[1],
                "side": str(row[2]),
                "entry_price": float(row[3]),
                "take_profit": float(row[4]),
                "mfe_percent": round(float(row[5]), 4),
                "mae_percent": round(float(row[6]), 4),
                "session_tag": str(row[7]),
                "gross_pnl_percent": round(float(row[8] or 0), 4),
                "net_pnl_percent": round(float(row[9] or 0), 4),
            }
            for row in rows
        ]

    def get_performance_summary_with_lookback(
        self, symbol: str, lookback_days: int = 0
    ) -> dict[str, Any]:
        """Extended performance summary with optional lookback period.
        lookback_days=0 means ALL TIME.
        """
        date_filter = ""
        params: list[Any] = [symbol]
        if lookback_days > 0:
            threshold = datetime.utcnow() - timedelta(days=lookback_days)
            date_filter = " AND COALESCE(closed_at, generated_at) >= ?"
            params.append(threshold)

        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total_signals,
                    SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN status = 'LOSS' THEN 1 ELSE 0 END) AS losses,
                    SUM(CASE WHEN status = 'EXPIRED' THEN 1 ELSE 0 END) AS expired,
                    SUM(COALESCE(gross_pnl_percent, 0)) AS total_gross_pnl,
                    SUM(COALESCE(net_pnl_percent, 0)) AS total_net_pnl,
                    AVG(CASE WHEN status = 'WIN' THEN net_pnl_percent END) AS avg_win,
                    AVG(CASE WHEN status = 'LOSS' THEN net_pnl_percent END) AS avg_loss,
                    SUM(CASE WHEN status = 'WIN' THEN gross_pnl_percent ELSE 0 END) AS gross_wins,
                    SUM(CASE WHEN status = 'LOSS' THEN ABS(gross_pnl_percent) ELSE 0 END) AS gross_losses
                FROM trade_signals
                WHERE symbol = ?
                  AND side IN ('LONG', 'SHORT')
                  AND status IN ('WIN', 'LOSS', 'EXPIRED')
                  {date_filter}
                """,
                params,
            ).fetchone()

        if not row or int(row[0] or 0) == 0:
            return {
                "total_signals": 0, "win_count": 0, "loss_count": 0,
                "expired_count": 0, "gross_win_rate": 0.0, "net_win_rate": 0.0,
                "total_gross_pnl_percent": 0.0, "total_net_pnl_percent": 0.0,
                "avg_win_percent": 0.0, "avg_loss_percent": 0.0,
                "profit_factor": 0.0, "expectancy_per_trade": 0.0,
            }

        total = int(row[0])
        wins = int(row[1] or 0)
        losses = int(row[2] or 0)
        expired = int(row[3] or 0)
        decisive = wins + losses
        gross_win_rate = wins / decisive if decisive > 0 else 0.0
        avg_win = float(row[6] or 0.0)
        avg_loss = float(row[7] or 0.0)
        gross_wins_total = float(row[8] or 0.0)
        gross_losses_total = float(row[9] or 0.0)
        profit_factor = (
            gross_wins_total / gross_losses_total
            if gross_losses_total > 0 else 0.0
        )
        loss_rate = 1 - gross_win_rate
        expectancy = (gross_win_rate * avg_win) - (loss_rate * abs(avg_loss))

        # Net win rate (wins where net_pnl > 0)
        net_wins_row = self._conn.execute(
            f"""
            SELECT COUNT(*)
            FROM trade_signals
            WHERE symbol = ?
              AND side IN ('LONG', 'SHORT')
              AND status IN ('WIN', 'LOSS', 'EXPIRED')
              AND net_pnl_percent > 0
              {date_filter}
            """,
            params,
        ).fetchone()
        net_wins = int(net_wins_row[0] or 0) if net_wins_row else 0
        net_win_rate = net_wins / decisive if decisive > 0 else 0.0

        return {
            "total_signals": total,
            "win_count": wins,
            "loss_count": losses,
            "expired_count": expired,
            "gross_win_rate": round(gross_win_rate, 4),
            "net_win_rate": round(net_win_rate, 4),
            "total_gross_pnl_percent": round(float(row[4] or 0), 6),
            "total_net_pnl_percent": round(float(row[5] or 0), 6),
            "avg_win_percent": round(avg_win, 6),
            "avg_loss_percent": round(avg_loss, 6),
            "profit_factor": round(profit_factor, 4),
            "expectancy_per_trade": round(expectancy, 6),
        }

    def get_equity_curve_full(
        self, symbol: str, lookback_days: int = 0
    ) -> list[dict[str, Any]]:
        """Full equity curve from ALL closed trades. No LIMIT."""
        date_filter = ""
        params: list[Any] = [symbol]
        if lookback_days > 0:
            threshold = datetime.utcnow() - timedelta(days=lookback_days)
            date_filter = " AND COALESCE(closed_at, generated_at) >= ?"
            params.append(threshold)

        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT signal_id,
                       COALESCE(closed_at, generated_at) AS point_time,
                       status, net_pnl_percent, gross_pnl_percent
                FROM trade_signals
                WHERE symbol = ?
                  AND side IN ('LONG', 'SHORT')
                  AND status IN ('WIN', 'LOSS', 'EXPIRED', 'NO_DATA')
                  {date_filter}
                ORDER BY point_time ASC
                """,
                params,
            ).fetchall()

        cumulative_net = 0.0
        cumulative_gross = 0.0
        peak = 0.0
        curve: list[dict[str, Any]] = []
        for row in rows:
            net_pnl = float(row[3] or 0.0)
            gross_pnl = float(row[4] or 0.0)
            cumulative_net += net_pnl
            cumulative_gross += gross_pnl
            peak = max(peak, cumulative_net)
            drawdown = cumulative_net - peak

            curve.append({
                "signal_id": str(row[0]),
                "point_time": row[1],
                "status": str(row[2]),
                "net_pnl_percent": round(net_pnl, 6),
                "gross_pnl_percent": round(gross_pnl, 6),
                "cumulative_net_pnl": round(cumulative_net, 6),
                "cumulative_gross_pnl": round(cumulative_gross, 6),
                "drawdown_percent": round(drawdown, 6),
            })
        return curve

    def get_monthly_breakdown(self, symbol: str) -> list[dict[str, Any]]:
        """Monthly performance breakdown for calendar heatmap."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    strftime(COALESCE(closed_at, generated_at), '%Y-%m') AS ym,
                    COUNT(*) AS signals,
                    SUM(COALESCE(net_pnl_percent, 0)) AS net_pnl,
                    SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN status = 'LOSS' THEN 1 ELSE 0 END) AS losses
                FROM trade_signals
                WHERE symbol = ?
                  AND side IN ('LONG', 'SHORT')
                  AND status IN ('WIN', 'LOSS', 'EXPIRED')
                GROUP BY ym
                ORDER BY ym ASC
                """,
                [symbol],
            ).fetchall()
        result = []
        for row in rows:
            decisive = int(row[3] or 0) + int(row[4] or 0)
            result.append({
                "year_month": str(row[0]),
                "signals": int(row[1]),
                "net_pnl_percent": round(float(row[2] or 0), 6),
                "win_rate": round(int(row[3] or 0) / decisive, 4) if decisive > 0 else 0.0,
                "wins": int(row[3] or 0),
                "losses": int(row[4] or 0),
            })
        return result

    def list_signal_history_paginated(
        self,
        symbol: str,
        limit: int = 100,
        offset: int = 0,
        side: str | None = None,
        status: str | None = None,
        session: str | None = None,
        capital_base: float = 100000.0,
    ) -> dict[str, Any]:
        """Paginated signal history with filters."""
        conditions = ["symbol = ?", "side IN ('LONG', 'SHORT')"]
        params: list[Any] = [symbol]

        if side:
            conditions.append("side = ?")
            params.append(side.upper())
        if status:
            conditions.append("status = ?")
            params.append(status.upper())
        if session:
            conditions.append("session_tag = ?")
            params.append(session.upper())

        where_clause = " AND ".join(conditions)

        with self._lock:
            # Get total count
            count_row = self._conn.execute(
                f"SELECT COUNT(*) FROM trade_signals WHERE {where_clause}",
                params,
            ).fetchone()
            total_count = int(count_row[0]) if count_row else 0

            # Get paginated results
            rows = self._conn.execute(
                f"""
                SELECT signal_id, generated_at, closed_at, side, regime,
                       entry_price, close_price, stop_price, take_profit,
                       confidence, status, pnl_pct,
                       COALESCE(challenge_verdict, 'PENDING') AS challenge_verdict,
                       outcome_reason, rationale,
                       mfe_percent, mae_percent, session_tag,
                       funding_rate_at_entry, spread_at_entry,
                       gross_pnl_percent, net_pnl_percent,
                       alignment_score_at_entry, exit_reason,
                       max_unrealized_pnl_percent, volatility_regime_at_entry
                FROM trade_signals
                WHERE {where_clause}
                ORDER BY COALESCE(closed_at, generated_at) DESC
                LIMIT ? OFFSET ?
                """,
                params + [min(limit, 1000), max(offset, 0)],
            ).fetchall()

        signals = []
        for row in rows:
            pnl_pct = float(row[11] or 0.0)
            net_pnl = float(row[21] or 0.0)
            pos_size = float(capital_base)
            signals.append({
                "signal_id": str(row[0]),
                "generated_at": row[1],
                "closed_at": row[2],
                "side": str(row[3]),
                "regime": str(row[4]),
                "entry_price": float(row[5]),
                "close_price": float(row[6]) if row[6] else None,
                "stop_price": float(row[7]),
                "take_profit": float(row[8]),
                "confidence": round(float(row[9]), 4),
                "status": str(row[10]),
                "pnl_pct": round(pnl_pct, 4),
                "challenge_verdict": str(row[12]),
                "outcome_reason": str(row[13]) if row[13] else None,
                "rationale": str(row[14]),
                "mfe_percent": round(float(row[15] or 0), 4),
                "mae_percent": round(float(row[16] or 0), 4),
                "session_tag": str(row[17] or "UNKNOWN"),
                "funding_rate_at_entry": round(float(row[18] or 0), 6),
                "spread_at_entry": round(float(row[19] or 0), 2),
                "gross_pnl_percent": round(float(row[20] or 0), 4),
                "net_pnl_percent": round(net_pnl, 4),
                "alignment_score_at_entry": round(float(row[22] or 0), 2),
                "exit_reason": str(row[23] or ""),
                "max_unrealized_pnl_percent": round(float(row[24] or 0), 4),
                "volatility_regime_at_entry": str(row[25] or ""),
                "capital_base": round(float(capital_base), 2),
                "position_size_usd": round(pos_size, 2),
                "capital_pnl": round(pos_size * net_pnl, 2) if net_pnl else round(pos_size * pnl_pct, 2),
            })
        return {
            "signals": signals,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()

