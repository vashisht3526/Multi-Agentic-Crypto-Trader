from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
import shutil
import time

import duckdb
import uvicorn
from engine.data.binance_ingestor import BinanceTickIngestionAgent, run_l2_depth_stream, run_funding_rate_poller
from engine.backbone import HierarchicalBackbone
from engine.database import FastTickDatabase
from engine.settings import Settings, load_settings
import engine.config as C


LOGGER = logging.getLogger(__name__)


def _store_system_event(
    db: FastTickDatabase,
    symbol: str,
    event_type: str,
    title: str,
    message: str,
    status: str = "INFO",
    payload: dict | None = None,
) -> None:
    db.store_crew_event(
        symbol=symbol,
        component="System",
        event_type=event_type,
        title=title,
        message=message,
        status=status,
        payload=payload,
    )


def _is_db_lock_error(exc: Exception) -> bool:
    error_text = str(exc).lower()
    return "cannot open file" in error_text and "being used by another process" in error_text


def _is_db_wal_replay_error(exc: Exception) -> bool:
    error_text = str(exc).lower()
    return "failure while replaying wal file" in error_text and "no default database set" in error_text


def _archive_unreplayable_wal(db_path: str) -> dict[str, str] | None:
    db_file = Path(db_path)
    wal_file = Path(f"{db_path}.wal")
    if not wal_file.exists():
        return None

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    recovery_dir = db_file.parent / "wal_recovery"
    recovery_dir.mkdir(parents=True, exist_ok=True)

    archived_db = recovery_dir / f"{db_file.stem}_{timestamp}{db_file.suffix}"
    archived_wal = recovery_dir / f"{db_file.stem}_{timestamp}{db_file.suffix}.wal"

    if db_file.exists():
        shutil.copy2(db_file, archived_db)
    shutil.move(str(wal_file), str(archived_wal))

    return {
        "recovery_dir": str(recovery_dir),
        "archived_db_path": str(archived_db),
        "archived_wal_path": str(archived_wal),
    }


def _fallback_db_path(db_path: str) -> str:
    path = Path(db_path)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    if path.suffix:
        return str(path.with_name(f"{path.stem}_{timestamp}{path.suffix}"))
    return str(path.with_name(f"{path.name}_{timestamp}.duckdb"))


def _open_database_with_recovery(settings: Settings) -> tuple[FastTickDatabase, str, bool, dict[str, str] | None]:
    wait_seconds = max(settings.db_lock_wait_seconds, 0)
    deadline = time.monotonic() + wait_seconds
    last_exc: Exception | None = None
    wal_recovery_info: dict[str, str] | None = None
    wal_recovery_attempted = False

    while True:
        try:
            return FastTickDatabase(db_path=settings.db_path), settings.db_path, False, wal_recovery_info
        except duckdb.IOException as exc:
            if not _is_db_lock_error(exc):
                raise
            last_exc = exc
            if time.monotonic() >= deadline:
                break
            LOGGER.warning(
                "DB file is locked (%s). Waiting for release...",
                settings.db_path,
            )
            time.sleep(1)
        except duckdb.InternalException as exc:
            if not _is_db_wal_replay_error(exc) or wal_recovery_attempted:
                raise
            wal_recovery_attempted = True
            wal_recovery_info = _archive_unreplayable_wal(settings.db_path)
            if wal_recovery_info is None:
                raise
            LOGGER.error(
                "DuckDB failed replaying WAL for %s. Archived unreplayed WAL to %s and retrying startup.",
                settings.db_path,
                wal_recovery_info["archived_wal_path"],
            )

    if not settings.db_lock_fallback:
        raise RuntimeError(
            f"Database file is locked: {settings.db_path}. "
            "Stop the other process or change DB_PATH."
        ) from last_exc

    fallback_path = _fallback_db_path(settings.db_path)
    LOGGER.warning(
        "DB file remained locked after %ss. Falling back to: %s",
        wait_seconds,
        fallback_path,
    )
    return FastTickDatabase(db_path=fallback_path), fallback_path, True, wal_recovery_info


async def analysis_loop(
    db: FastTickDatabase,
    backbone: HierarchicalBackbone,
    symbol: str,
    min_ticks: int,
    lookback_seconds: int,
    analysis_interval_seconds: int,
    max_allowed_tick_gap_seconds: int,
) -> None:
    while True:
        try:
            health = db.get_recent_data_health(
                symbol=symbol,
                lookback_seconds=lookback_seconds,
                max_gap_seconds=max_allowed_tick_gap_seconds,
            )
            tick_count = int(health["tick_count"])
            if tick_count < min_ticks:
                LOGGER.info(
                    "Waiting for enough tick data (%s/%s in last %ss)...",
                    tick_count,
                    min_ticks,
                    lookback_seconds,
                )
                _store_system_event(
                    db=db,
                    symbol=symbol,
                    event_type="analysis_waiting",
                    title="Waiting for enough ticks",
                    message=f"Only {tick_count}/{min_ticks} ticks available in the last {lookback_seconds}s.",
                    status="INFO",
                    payload=health,
                )
            elif not health["usable"]:
                LOGGER.warning(
                    "Skipping analysis cycle due to market-data gap: %s",
                    health["reason"],
                )
                _store_system_event(
                    db=db,
                    symbol=symbol,
                    event_type="analysis_skipped",
                    title="Analysis skipped",
                    message=f"Skipped cycle because local market-data health is {health['reason']}.",
                    status="WARN",
                    payload=health,
                )
            else:
                result = await asyncio.to_thread(backbone.run_cycle)
                LOGGER.info("Backbone cycle completed: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Analysis loop error")
            _store_system_event(
                db=db,
                symbol=symbol,
                event_type="analysis_error",
                title="Analysis loop error",
                message="The analysis loop raised an exception. Check logs for the traceback.",
                status="FAILED",
            )
        await asyncio.sleep(analysis_interval_seconds)


async def dashboard_snapshot_loop(
    db: FastTickDatabase,
    symbol: str,
    snapshot_path: str,
    lookback_seconds: int,
    max_allowed_tick_gap_seconds: int,
    snapshot_rows: int,
    capital_base: float,
    snapshot_interval_seconds: int,
) -> None:
    while True:
        try:
            written_path = await asyncio.to_thread(
                db.write_dashboard_snapshot,
                snapshot_path=snapshot_path,
                symbol=symbol,
                limit=snapshot_rows,
                lookback_seconds=lookback_seconds,
                max_gap_seconds=max_allowed_tick_gap_seconds,
                capital_base=capital_base,
            )
            LOGGER.debug("Dashboard snapshot written: %s", written_path)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Dashboard snapshot loop error")
        await asyncio.sleep(max(snapshot_interval_seconds, 1))


async def trade_reaper_loop(
    db: FastTickDatabase,
    symbol: str,
    check_interval_seconds: float = 1.0,
) -> None:
    """
    Structural exit evaluator. No time-based expiry.
    Runs every 1s. 3-tier trailing stop. Trend reversal with hysteresis.
    """
    import engine.analysis.timeframe_analysis as TFA

    _last_trend_check: float = 0.0  # monotonic time of last trend check

    while True:
        try:
            now = datetime.utcnow()
            close_price = db.get_latest_price(symbol=symbol)
            if close_price is None:
                await asyncio.sleep(check_interval_seconds)
                continue

            with db._lock:
                open_trades = db._conn.execute(
                    """
                    SELECT signal_id, generated_at, side, entry_price, stop_price,
                           take_profit, decision_trace_json, mfe_percent, mae_percent,
                           regime, atr_used, trend_fail_count,
                           max_unrealized_pnl_percent
                    FROM trade_signals
                    WHERE symbol = ? AND status = 'OPEN'
                    """,
                    [symbol],
                ).fetchall()

            closed_any = False
            now_mono = time.monotonic()
            do_trend_check = (now_mono - _last_trend_check) >= C.TREND_REVERSAL_CHECK_INTERVAL

            if do_trend_check:
                _last_trend_check = now_mono

            for row in open_trades:
                signal_id = str(row[0])
                generated_at = row[1]
                side = str(row[2]).upper()
                entry_price = float(row[3])
                stop_price = float(row[4])
                take_profit = float(row[5])
                current_mfe = float(row[7] or 0)
                current_mae = float(row[8] or 0)
                regime = str(row[9] or "UNKNOWN")
                atr_used = float(row[10] or 0)
                trend_fail_count = int(row[11] or 0)
                max_unrealized = float(row[12] or 0)

                try:
                    trace_data = json.loads(row[6]) if row[6] else {}
                except Exception:
                    trace_data = {}

                # ── ATR recovery ──
                atr_dollar = atr_used
                if atr_dollar <= 0 and "targets" in trace_data:
                    atr_dollar = float(trace_data["targets"].get("atr_dollar", 0))
                if atr_dollar <= 0:
                    sl_mult = float(trace_data.get("targets", {}).get("sl_multiplier", 1.0))
                    risk_dist = abs(entry_price - stop_price)
                    atr_dollar = risk_dist / sl_mult if sl_mult > 0 else 0

                duration_s = (now - generated_at).total_seconds() if generated_at else 0

                # ── Continuous MFE/MAE tracking ──
                if side == "LONG":
                    current_excursion = ((close_price - entry_price) / entry_price) * 100
                elif side == "SHORT":
                    current_excursion = ((entry_price - close_price) / entry_price) * 100
                else:
                    current_excursion = 0.0

                new_mfe = max(current_mfe, current_excursion)
                new_mae = min(current_mae, current_excursion)

                # Track max unrealized and drawdown from peak
                new_max_unrealized = max(max_unrealized, current_excursion)
                drawdown_from_peak = new_max_unrealized - current_excursion

                # Update MFE/MAE + peak tracking on every tick
                if (new_mfe != current_mfe or new_mae != current_mae
                        or new_max_unrealized != max_unrealized):
                    with db._lock:
                        db._conn.execute(
                            """UPDATE trade_signals SET
                                mfe_percent = ?, mae_percent = ?,
                                max_unrealized_pnl_percent = ?,
                                drawdown_from_peak_percent = ?
                            WHERE signal_id = ?""",
                            [round(new_mfe, 6), round(new_mae, 6),
                             round(new_max_unrealized, 6), round(drawdown_from_peak, 6),
                             signal_id],
                        )

                # ══════════════════════════════════════════════════════
                # 3-TIER TRAILING STOP
                # ══════════════════════════════════════════════════════
                new_sl = stop_price
                trailing_tier = ""

                if atr_dollar > 0:
                    if side == "LONG":
                        unrealized_atr = (close_price - entry_price) / atr_dollar
                        if unrealized_atr >= C.TRAILING_FOLLOW_ATR:
                            # Tier 3: Trail 1.5 ATR below current price
                            candidate_sl = close_price - (1.5 * atr_dollar)
                            new_sl = max(new_sl, candidate_sl)
                            trailing_tier = "TRAILING"
                        elif unrealized_atr >= C.TRAILING_LOCK_ATR:
                            # Tier 2: Lock 1.0 ATR profit
                            candidate_sl = entry_price + (1.0 * atr_dollar)
                            new_sl = max(new_sl, candidate_sl)
                            trailing_tier = "PROFIT_LOCK"
                        elif unrealized_atr >= C.TRAILING_BE_ATR:
                            # Tier 1: Break-even + 1 tick
                            candidate_sl = entry_price + 0.01
                            new_sl = max(new_sl, candidate_sl)
                            trailing_tier = "BREAK_EVEN"

                    elif side == "SHORT":
                        unrealized_atr = (entry_price - close_price) / atr_dollar
                        if unrealized_atr >= C.TRAILING_FOLLOW_ATR:
                            candidate_sl = close_price + (1.5 * atr_dollar)
                            new_sl = min(new_sl, candidate_sl) if new_sl > 0 else candidate_sl
                            trailing_tier = "TRAILING"
                        elif unrealized_atr >= C.TRAILING_LOCK_ATR:
                            candidate_sl = entry_price - (1.0 * atr_dollar)
                            new_sl = min(new_sl, candidate_sl) if new_sl > 0 else candidate_sl
                            trailing_tier = "PROFIT_LOCK"
                        elif unrealized_atr >= C.TRAILING_BE_ATR:
                            candidate_sl = entry_price - 0.01
                            new_sl = min(new_sl, candidate_sl) if new_sl > 0 else candidate_sl
                            trailing_tier = "BREAK_EVEN"

                if new_sl != stop_price:
                    with db._lock:
                        db._conn.execute(
                            "UPDATE trade_signals SET stop_price = ? WHERE signal_id = ?",
                            [round(new_sl, 2), signal_id],
                        )
                    stop_price = round(new_sl, 2)
                    LOGGER.info("REAPER: %s trailing → SL=%.2f (%s)", signal_id[:8], stop_price, trailing_tier)

                # ══════════════════════════════════════════════════════
                # EXIT CHECKS (priority order)
                # ══════════════════════════════════════════════════════
                trigger_reason = None
                end_status = None
                exit_reason = ""

                # 0. Partial Take-Profit (scale-out 50% at TP1)
                if C.PARTIAL_TP_ENABLED and atr_dollar > 0:
                    partial_tp1_dist = C.PARTIAL_TP1_ATR_MULT * atr_dollar
                    # Check if partial TP already taken
                    with db._lock:
                        ptp_row = db._conn.execute(
                            "SELECT partial_tp_taken FROM trade_signals WHERE signal_id = ?",
                            [signal_id],
                        ).fetchone()
                    partial_already = bool(ptp_row and ptp_row[0])

                    if not partial_already:
                        tp1_hit = False
                        if side == "LONG" and close_price >= entry_price + partial_tp1_dist:
                            tp1_hit = True
                        elif side == "SHORT" and close_price <= entry_price - partial_tp1_dist:
                            tp1_hit = True

                        if tp1_hit:
                            # Record partial close: mark flag, move SL to BE + 1 tick
                            be_sl = entry_price + 0.01 if side == "LONG" else entry_price - 0.01
                            # Extend the full TP by 2× (let remaining ride)
                            new_full_tp = take_profit + (take_profit - entry_price) if side == "LONG" else take_profit - (entry_price - take_profit)
                            with db._lock:
                                db._conn.execute(
                                    """UPDATE trade_signals SET
                                        partial_tp_taken = TRUE,
                                        stop_price = ?,
                                        take_profit = ?
                                    WHERE signal_id = ?""",
                                    [round(be_sl, 2), round(new_full_tp, 2), signal_id],
                                )
                            stop_price = round(be_sl, 2)
                            take_profit = round(new_full_tp, 2)

                            # Log partial close as event
                            partial_pnl = C.PARTIAL_TP_FRACTION * current_excursion
                            _store_system_event(
                                db=db, symbol=symbol,
                                event_type="partial_tp_hit",
                                title=f"Partial TP: {signal_id[:8]} — 50% closed at {close_price:.2f}",
                                message=(
                                    f"{side} partial close ({C.PARTIAL_TP_FRACTION*100:.0f}%) at "
                                    f"{close_price:.2f}, SL→BE({be_sl:.2f}), "
                                    f"TP extended to {new_full_tp:.2f}. "
                                    f"Partial PnL: {partial_pnl:.2f}%"
                                ),
                                status="COMPLETED",
                                payload={
                                    "signal_id": signal_id, "side": side,
                                    "partial_close_price": close_price,
                                    "partial_fraction": C.PARTIAL_TP_FRACTION,
                                    "new_sl": be_sl, "new_tp": new_full_tp,
                                    "partial_pnl_pct": round(partial_pnl, 4),
                                },
                            )
                            LOGGER.info(
                                "REAPER: %s PARTIAL TP1 → 50%% closed at %.2f | SL→BE(%.2f) | TP→%.2f",
                                signal_id[:8], close_price, be_sl, new_full_tp,
                            )

                # 1. TP/SL hits
                if side == "LONG":
                    if close_price <= stop_price:
                        trigger_reason = "stop_loss_hit"
                        exit_reason = "SL_HIT"
                    elif close_price >= take_profit:
                        trigger_reason = "take_profit_hit"
                        exit_reason = "TP_HIT"
                elif side == "SHORT":
                    if close_price >= stop_price:
                        trigger_reason = "stop_loss_hit"
                        exit_reason = "SL_HIT"
                    elif close_price <= take_profit:
                        trigger_reason = "take_profit_hit"
                        exit_reason = "TP_HIT"

                # 2. Trend reversal check (every 30s, with hysteresis)
                if not trigger_reason and do_trend_check:
                    trend_holds = TFA.check_trend_holds_for_side(side)
                    if not trend_holds:
                        trend_fail_count += 1
                        if trend_fail_count >= C.TREND_REVERSAL_HYSTERESIS:
                            trigger_reason = "trend_reversal"
                            exit_reason = "TREND_REVERSAL"
                        else:
                            # Update fail count but don't close yet
                            with db._lock:
                                db._conn.execute(
                                    "UPDATE trade_signals SET trend_fail_count = ? WHERE signal_id = ?",
                                    [trend_fail_count, signal_id],
                                )
                    else:
                        if trend_fail_count > 0:
                            trend_fail_count = 0
                            with db._lock:
                                db._conn.execute(
                                    "UPDATE trade_signals SET trend_fail_count = 0 WHERE signal_id = ?",
                                    [signal_id],
                                )

                if not trigger_reason:
                    continue  # Trade remains OPEN

                # ── Fee-aware PnL calculation ──
                if side == "LONG":
                    gross_pnl_pct = (close_price - entry_price) / entry_price
                elif side == "SHORT":
                    gross_pnl_pct = (entry_price - close_price) / entry_price
                else:
                    gross_pnl_pct = 0.0

                net_pnl_pct = gross_pnl_pct - C.ROUND_TRIP_FEE
                pnl_pct = gross_pnl_pct * 100
                was_profitable = net_pnl_pct > 0

                # Status: WIN only if net profitable
                if exit_reason == "TP_HIT":
                    end_status = "WIN" if was_profitable else "LOSS"
                elif exit_reason == "TREND_REVERSAL":
                    end_status = "TREND_REVERSAL"
                else:
                    end_status = "WIN" if was_profitable else "LOSS"

                # Fee erosion override
                if exit_reason == "TP_HIT" and not was_profitable:
                    trigger_reason = "fee_erosion"

                with db._lock:
                    db._conn.execute(
                        """
                        UPDATE trade_signals
                        SET status = ?,
                            evaluated = TRUE,
                            closed_at = ?,
                            close_price = ?,
                            pnl_pct = ?,
                            was_profitable = ?,
                            outcome_reason = ?,
                            data_quality = 'ok',
                            gross_pnl_percent = ?,
                            net_pnl_percent = ?,
                            mfe_percent = ?,
                            mae_percent = ?,
                            exit_reason = ?,
                            max_unrealized_pnl_percent = ?,
                            drawdown_from_peak_percent = ?
                        WHERE signal_id = ?
                        """,
                        [
                            end_status, now, close_price, round(pnl_pct, 4),
                            was_profitable, trigger_reason,
                            round(gross_pnl_pct, 6), round(net_pnl_pct, 6),
                            round(new_mfe, 6), round(new_mae, 6),
                            exit_reason,
                            round(new_max_unrealized, 6), round(drawdown_from_peak, 6),
                            signal_id,
                        ],
                    )
                    db._conn.execute(
                        """
                        INSERT INTO signal_evaluations (signal_id, symbol, status, close_price, pnl_pct, reason)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        [signal_id, symbol, end_status, close_price, round(pnl_pct, 4), trigger_reason],
                    )

                # Update hourly_performance
                try:
                    db.update_hourly_performance({
                        "hour_utc": generated_at.hour if generated_at else now.hour,
                        "side": side,
                        "regime": regime,
                        "status": "WIN" if was_profitable else "LOSS",
                        "mfe_percent": new_mfe,
                        "mae_percent": new_mae,
                    })
                except Exception as hp_err:
                    LOGGER.debug("hourly_performance update failed: %s", hp_err)

                # Post-close analysis for trend reversals
                post_note = ""
                if exit_reason == "TREND_REVERSAL":
                    if was_profitable:
                        post_note = "GOOD_EARLY_EXIT"
                    else:
                        post_note = "LATE_EXIT"

                LOGGER.warning(
                    "REAPER: Closed %s | %s %s | Exit=%s | "
                    "Entry=%.2f Close=%.2f | Gross=%.4f%% Net=%.4f%% | "
                    "MFE=%.4f%% MAE=%.4f%% | Duration=%ds | %s",
                    signal_id[:8], side, symbol, exit_reason,
                    entry_price, close_price,
                    gross_pnl_pct * 100, net_pnl_pct * 100,
                    new_mfe, new_mae, int(duration_s), post_note,
                )
                _store_system_event(
                    db=db,
                    symbol=symbol,
                    event_type="trade_closed",
                    title=f"Trade Closed: {signal_id[:8]}",
                    message=(
                        f"{side} closed ({exit_reason}). "
                        f"Close={close_price:.2f}, Gross={gross_pnl_pct*100:.4f}%, "
                        f"Net={net_pnl_pct*100:.4f}%, Duration={int(duration_s)}s "
                        f"{post_note}"
                    ),
                    status=("COMPLETED" if was_profitable else "WARN"),
                    payload={
                        "signal_id": signal_id, "side": side,
                        "entry_price": entry_price, "close_price": close_price,
                        "trigger_reason": trigger_reason,
                        "exit_reason": exit_reason,
                        "end_status": end_status,
                        "duration_seconds": round(duration_s),
                        "gross_pnl_pct": round(gross_pnl_pct * 100, 4),
                        "net_pnl_pct": round(net_pnl_pct * 100, 4),
                        "mfe_percent": round(new_mfe, 4),
                        "mae_percent": round(new_mae, 4),
                        "max_unrealized": round(new_max_unrealized, 4),
                        "drawdown_from_peak": round(drawdown_from_peak, 4),
                        "trailing_tier": trailing_tier,
                        "post_note": post_note,
                    },
                )
                closed_any = True

            if closed_any:
                LOGGER.info("Trade monitor sweep finished.")

        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Trade reaper error (will retry next tick)")
        await asyncio.sleep(check_interval_seconds)


async def run_api_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the FastAPI server as an async task within the main event loop."""
    from backend.api import app as fastapi_app, db as api_db_ref
    import backend.api as api_module

    config = uvicorn.Config(
        fastapi_app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    LOGGER.info("FastAPI server starting on http://%s:%d", host, port)
    await server.serve()


async def async_main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    from datetime import datetime, timedelta
    import engine.analysis.timeframe_analysis as TFA

    LOGGER.info(
        "Starting pipeline | symbol=%s | db=%s | llm=%s (%s)",
        settings.symbol,
        settings.db_path,
        settings.llm_model,
        settings.llm_provider,
    )

    db, active_db_path, used_fallback_db, wal_recovery_info = _open_database_with_recovery(settings)
    if used_fallback_db:
        LOGGER.warning("Using fallback database file: %s", active_db_path)
    if wal_recovery_info:
        LOGGER.warning(
            "Archived unreplayed WAL to %s before opening base DB.",
            wal_recovery_info["archived_wal_path"],
        )
    _store_system_event(
        db=db,
        symbol=settings.symbol,
        event_type="pipeline_started",
        title="Pipeline started",
        message="Multi-TF trend-capture system starting.",
        status="COMPLETED",
        payload={
            "db_path": active_db_path,
            "used_fallback_db": used_fallback_db,
            "wal_recovery_info": wal_recovery_info,
            "dashboard_snapshot_path": settings.dashboard_snapshot_path,
            "llm_model": settings.llm_model,
            "llm_provider": settings.llm_provider,
        },
    )
    ingestion_agent = BinanceTickIngestionAgent(
        symbol=settings.symbol,
        db=db,
        reconnect_backoff_seconds=settings.reconnect_backoff_seconds,
    )
    backbone = HierarchicalBackbone(
        db=db,
        symbol=settings.symbol,
        lookback_seconds=settings.lookback_seconds,
        capital_base=settings.reporting_capital_base,
    )

    # Core tasks
    ingestion_task = asyncio.create_task(ingestion_agent.run_forever(), name="binance_ingestion")
    analysis_task = asyncio.create_task(
        analysis_loop(
            db=db,
            backbone=backbone,
            symbol=settings.symbol,
            min_ticks=settings.min_ticks_before_analysis,
            lookback_seconds=settings.lookback_seconds,
            analysis_interval_seconds=settings.analysis_interval_seconds,
            max_allowed_tick_gap_seconds=settings.max_allowed_tick_gap_seconds,
        ),
        name="analysis_loop",
    )
    dashboard_snapshot_task = asyncio.create_task(
        dashboard_snapshot_loop(
            db=db,
            symbol=settings.symbol,
            snapshot_path=settings.dashboard_snapshot_path,
            lookback_seconds=settings.lookback_seconds,
            max_allowed_tick_gap_seconds=settings.max_allowed_tick_gap_seconds,
            snapshot_rows=settings.dashboard_snapshot_rows,
            capital_base=settings.reporting_capital_base,
            snapshot_interval_seconds=settings.dashboard_snapshot_interval_seconds,
        ),
        name="dashboard_snapshot_loop",
    )
    api_task = asyncio.create_task(run_api_server(host="0.0.0.0", port=8000), name="fastapi_server")

    # Structural exit reaper (no horizon_seconds — structural exits only)
    reaper_task = asyncio.create_task(
        trade_reaper_loop(
            db=db,
            symbol=settings.symbol,
            check_interval_seconds=1.0,
        ),
        name="trade_reaper",
    )

    # Data streams
    l2_depth_task = asyncio.create_task(
        run_l2_depth_stream(db=db, symbol=settings.symbol),
        name="l2_depth_stream",
    )
    funding_task = asyncio.create_task(
        run_funding_rate_poller(symbol=settings.symbol),
        name="funding_rate_poller",
    )

    # Multi-TF trend engine
    tf_task = asyncio.create_task(
        TFA.refresh_all_timeframes(symbol=settings.symbol),
        name="timeframe_analysis",
    )

    # Auto-tune (weekly)
    autotune_task = asyncio.create_task(
        TFA.auto_tune_loop(db=db, symbol=settings.symbol),
        name="auto_tune",
    )

    LOGGER.info(
        "Trend-capture system active: ROUND_TRIP_FEE=%.4f%%, MIN_EDGE=%.4f%%, "
        "NO_TIME_EXPIRY, TRAILING_BE=%.1f/LOCK=%.1f/FOLLOW=%.1f ATR, "
        "ALIGNMENT_MIN=%.1f, TREND_REVERSAL_HYSTERESIS=%d",
        C.ROUND_TRIP_FEE * 100, C.MINIMUM_GROSS_EDGE_PERCENT * 100,
        C.TRAILING_BE_ATR, C.TRAILING_LOCK_ATR, C.TRAILING_FOLLOW_ATR,
        C.MIN_ALIGNMENT_SCORE, C.TREND_REVERSAL_HYSTERESIS,
    )

    all_tasks = [
        ingestion_task, analysis_task, dashboard_snapshot_task,
        api_task, reaper_task, l2_depth_task, funding_task,
        tf_task, autotune_task,
    ]

    try:
        await asyncio.gather(*all_tasks)
    finally:
        _store_system_event(
            db=db,
            symbol=settings.symbol,
            event_type="pipeline_stopped",
            title="Pipeline stopped",
            message="Stopping ingestion and analysis services.",
            status="WARN",
        )
        ingestion_agent.stop()
        for task in all_tasks:
            task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await asyncio.gather(*all_tasks, return_exceptions=True)
        db.close()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

