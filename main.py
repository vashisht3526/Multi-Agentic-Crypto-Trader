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
from binance_ingestor import BinanceTickIngestionAgent
from backbone import HierarchicalBackbone
from database import FastTickDatabase
from settings import Settings, load_settings


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
    horizon_seconds: int,
    check_interval_seconds: int = 2,
) -> None:
    """
    Background evaluator: checks all OPEN trades for TP/SL hits or time expirations.
    Runs every `check_interval_seconds`. NEVER fails silently.
    """
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
                    SELECT signal_id, generated_at, side, entry_price, stop_price, take_profit, decision_trace_json
                    FROM trade_signals
                    WHERE symbol = ? AND status = 'OPEN'
                    """,
                    [symbol],
                ).fetchall()

            closed_any = False
            for row in open_trades:
                signal_id = str(row[0])
                generated_at = row[1]
                side = str(row[2]).upper()
                entry_price = float(row[3])
                stop_price = float(row[4])
                take_profit = float(row[5])
                
                # Dynamic BE trailing logic
                try:
                    trace_data = json.loads(row[6]) if row[6] else {}
                except Exception:
                    trace_data = {}
                
                atr_1 = 0.0
                if "targets" in trace_data and "atr_dollar" in trace_data["targets"]:
                    atr_1 = float(trace_data["targets"]["atr_dollar"]) * (3 ** 0.5)
                
                if atr_1 <= 0:
                    sl_mult = float(trace_data.get("targets", {}).get("sl_multiplier", 1.2))
                    risk_dist = abs(entry_price - stop_price)
                    atr_1 = risk_dist / sl_mult if sl_mult > 0 else 0

                duration_s = (now - generated_at).total_seconds()
                is_expired = duration_s >= horizon_seconds

                trigger_reason = None
                end_status = "EXPIRED"

                # Check trailing break-even trigger
                be_moved = False
                if side == "LONG" and stop_price != entry_price and atr_1 > 0:
                    if close_price >= entry_price + atr_1:
                        stop_price = entry_price
                        be_moved = True
                elif side == "SHORT" and stop_price != entry_price and atr_1 > 0:
                    if close_price <= entry_price - atr_1:
                        stop_price = entry_price
                        be_moved = True

                if be_moved:
                    with db._lock:
                        db._conn.execute(
                            "UPDATE trade_signals SET stop_price = ? WHERE signal_id = ?",
                            [stop_price, signal_id]
                        )
                    LOGGER.info("MONITOR: Break-Even triggered for %s. Moving SL to %.2f", signal_id[:8], stop_price)

                if side == "LONG":
                    if close_price <= stop_price:
                        trigger_reason = "stop_loss_hit"
                        end_status = "LOSS"
                    elif close_price >= take_profit:
                        trigger_reason = "take_profit_hit"
                        end_status = "WIN"
                elif side == "SHORT":
                    if close_price >= stop_price:
                        trigger_reason = "stop_loss_hit"
                        end_status = "LOSS"
                    elif close_price <= take_profit:
                        trigger_reason = "take_profit_hit"
                        end_status = "WIN"

                if not trigger_reason and is_expired:
                    trigger_reason = "reaper_forced_close"
                    end_status = "EXPIRED"

                if not trigger_reason:
                    continue  # Trade remains OPEN

                # Calculate P&L
                if side == "LONG":
                    pnl_pct = ((close_price - entry_price) / entry_price) * 100
                elif side == "SHORT":
                    pnl_pct = ((entry_price - close_price) / entry_price) * 100
                else:
                    pnl_pct = 0.0

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
                            data_quality = 'ok'
                        WHERE signal_id = ?
                        """,
                        [end_status, now, close_price, round(pnl_pct, 4), pnl_pct > 0, trigger_reason, signal_id],
                    )
                    db._conn.execute(
                        """
                        INSERT INTO signal_evaluations (signal_id, symbol, status, close_price, pnl_pct, reason)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        [signal_id, symbol, end_status, close_price, round(pnl_pct, 4), trigger_reason],
                    )

                LOGGER.warning(
                    "MONITOR: Closed %s | %s %s | Reason=%s | "
                    "Entry=%.2f Close=%.2f | PnL=%.4f%%",
                    signal_id[:8], side, symbol, trigger_reason,
                    entry_price, close_price, pnl_pct,
                )
                _store_system_event(
                    db=db,
                    symbol=symbol,
                    event_type="trade_closed",
                    title=f"Trade Closed: {signal_id[:8]}",
                    message=(
                        f"{side} trade closed ({trigger_reason}). "
                        f"Close={close_price:.2f}, PnL={pnl_pct:.4f}%"
                    ),
                    status=("COMPLETED" if end_status == "WIN" else "WARN"),
                    payload={
                        "signal_id": signal_id,
                        "side": side,
                        "entry_price": entry_price,
                        "close_price": close_price,
                        "trigger_reason": trigger_reason,
                        "end_status": end_status,
                        "duration_seconds": round(duration_s),
                        "pnl_pct": round(pnl_pct, 4),
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
    from datetime import datetime, timedelta  # needed by reaper

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
        message="Started ingestion and analysis services.",
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
        horizon_seconds=settings.signal_horizon_seconds,
        capital_base=settings.reporting_capital_base,
    )

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
    reaper_task = asyncio.create_task(
        trade_reaper_loop(
            db=db,
            symbol=settings.symbol,
            horizon_seconds=settings.signal_horizon_seconds,
            check_interval_seconds=2,
        ),
        name="trade_reaper",
    )

    try:
        await asyncio.gather(ingestion_task, analysis_task, dashboard_snapshot_task, api_task, reaper_task)
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
        for task in (ingestion_task, analysis_task, dashboard_snapshot_task, api_task, reaper_task):
            task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await asyncio.gather(ingestion_task, analysis_task, dashboard_snapshot_task, api_task, reaper_task, return_exceptions=True)
        db.close()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
