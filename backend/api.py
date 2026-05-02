from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ── Ensure project root is importable ──────────────────────────────────
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine.database import FastTickDatabase
from engine.settings import load_settings
from backend.ws_manager import manager
import engine.config as C

LOGGER = logging.getLogger(__name__)

# ── Globals ────────────────────────────────────────────────────────────
settings = load_settings()
db: FastTickDatabase | None = None

app = FastAPI(title="Crypto Trading Signal API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Lifecycle ──────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    global db
    try:
        db = FastTickDatabase(db_path=settings.db_path, read_only=True)
        LOGGER.info("API connected to database: %s", settings.db_path)
    except Exception:
        LOGGER.warning("Could not open DB in read-only mode, trying read-write")
        db = FastTickDatabase(db_path=settings.db_path, read_only=False)
    # Start background broadcaster
    asyncio.create_task(_broadcast_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    global db
    if db:
        db.close()
        db = None


# ── Helpers ────────────────────────────────────────────────────────────

def _safe_str(dt: Any) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def _calc_risk_reward(side: str, entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0:
        return 0.0
    return round(reward / risk, 2)


def _position_size() -> float:
    """Calculate position size in USDT for each trade."""
    return settings.reporting_capital_base * settings.position_size_pct / 100.0


def _enrich_signal(sig: dict[str, Any], last_price: float | None = None) -> dict[str, Any]:
    """Add computed fields to a signal dict for the frontend."""
    side = str(sig.get("side", "FLAT")).upper()
    entry = float(sig.get("entry_price", 0))
    stop = float(sig.get("stop_price", 0))
    target = float(sig.get("take_profit", 0))
    sig["risk_reward"] = _calc_risk_reward(side, entry, stop, target)
    sig["generated_at"] = _safe_str(sig.get("generated_at"))
    sig["closed_at"] = _safe_str(sig.get("closed_at"))
    sig["evaluation_due_time"] = _safe_str(sig.get("evaluation_due_time"))

    # Build human-readable entry/exit logic from rationale + decision trace
    rationale = str(sig.get("rationale", ""))
    decision_trace = sig.get("decision_trace", {}) or {}
    analysis = sig.get("analysis", {}) or decision_trace.get("analysis", {}) or {}
    regime = str(sig.get("regime", ""))
    challenge_summary = sig.get("challenge_summary", "")

    # Entry logic
    entry_parts = []
    if regime:
        entry_parts.append(f"Market regime detected: {regime.replace('_', ' ')}")
    imbalance = analysis.get("imbalance")
    if imbalance is not None:
        direction = "buying" if float(imbalance) > 0 else "selling"
        entry_parts.append(f"Order flow imbalance: {direction} pressure ({float(imbalance):.4f})")
    vol_delta = analysis.get("volume_delta")
    if vol_delta is not None:
        entry_parts.append(f"Volume delta: {float(vol_delta):.4f} BTC")
    if rationale:
        entry_parts.append(rationale)
    sig["entry_logic"] = " → ".join(entry_parts) if entry_parts else rationale

    # Exit logic
    status = str(sig.get("status", "")).upper()
    outcome = sig.get("outcome_reason", "")
    if status == "WIN":
        sig["exit_logic"] = f"Target price hit. {outcome}" if outcome else "Take-profit target was reached."
    elif status == "LOSS":
        sig["exit_logic"] = f"Stop loss hit. {outcome}" if outcome else "Stop-loss was triggered."
    elif status == "EXPIRED":
        sig["exit_logic"] = "Signal expired without hitting target or stop."
    elif status == "OPEN":
        sig["exit_logic"] = "Trade is still active."
    else:
        sig["exit_logic"] = outcome or "N/A"

    # Success/failure explanation
    pnl = sig.get("pnl_pct")
    if status == "WIN":
        sig["success_failure_explanation"] = (
            f"This {side} trade succeeded with {float(pnl or 0):.2f}% profit. "
            f"The {regime.replace('_', ' ')} regime signal was accurate."
        )
    elif status == "LOSS":
        sig["success_failure_explanation"] = (
            f"This {side} trade lost {abs(float(pnl or 0)):.2f}%. "
            f"{outcome or 'Market moved against the predicted direction.'}"
        )
    elif status in ("EXPIRED", "NO_DATA"):
        sig["success_failure_explanation"] = (
            f"Signal expired with {float(pnl or 0):.2f}% move. "
            "Neither target nor stop was hit within the time horizon."
        )
    else:
        sig["success_failure_explanation"] = None

    # Validation fields from challenge
    verdict = str(sig.get("challenge_verdict", "PENDING")).upper()
    sig["validation_status"] = verdict
    sig["validation_reasoning"] = challenge_summary or None

    return sig


# ── API Endpoints ──────────────────────────────────────────────────────

@app.get("/api/dashboard")
async def get_dashboard() -> dict[str, Any]:
    if not db:
        return {"error": "Database not connected"}

    symbol = settings.symbol
    capital = settings.reporting_capital_base
    pos_size = _position_size()
    last_price = db.get_latest_price(symbol=symbol)
    health = db.get_recent_data_health(
        symbol=symbol,
        lookback_seconds=settings.lookback_seconds,
        max_gap_seconds=settings.max_allowed_tick_gap_seconds,
    )
    perf = db.get_signal_performance_summary(symbol=symbol)
    equity_curve = db.get_signal_equity_curve(
        symbol=symbol,
        capital_base=pos_size,
    )
    active = db.list_active_signals(
        symbol=symbol,
        capital_base=pos_size,
        lookback_seconds=settings.lookback_seconds,
        max_gap_seconds=settings.max_allowed_tick_gap_seconds,
        limit=20,
    )
    # Only count validated signals
    active = [s for s in active if str(s.get('status', '')).upper() == 'OPEN']

    # Compute metrics from FULL trade history (no windowing)
    total_signals = int(perf.get("total_trades", 0))
    win_count = int(perf.get("wins", 0))
    loss_count = int(perf.get("losses", 0))
    expired_count = int(perf.get("expired", 0))
    total_evaluated = win_count + loss_count
    win_rate = round(win_count / total_evaluated * 100, 1) if total_evaluated > 0 else 0.0
    current_equity = capital
    max_dd = 0.0
    total_pnl = 0.0
    if equity_curve:
        cumulative_pnl = float(equity_curve[-1].get("cumulative_pnl", 0))
        current_equity = capital + cumulative_pnl
        total_pnl = cumulative_pnl
        peak = capital
        running = capital
        for pt in equity_curve:
            trade_pnl = float(pt.get("trade_pnl", 0))
            running += trade_pnl
            peak = max(peak, running)
            dd = ((running - peak) / peak) * 100 if peak > 0 else 0
            max_dd = min(max_dd, dd)

    return {
        "symbol": symbol,
        "last_price": last_price,
        "health": health,
        "total_signals": total_signals,
        "active_signals": len(active),
        "win_count": win_count,
        "loss_count": loss_count,
        "expired_count": expired_count,
        "win_rate": win_rate,
        "total_pnl": round(total_pnl, 2),
        "capital_base": capital,
        "position_size": round(pos_size, 2),
        "position_size_pct": settings.position_size_pct,
        "currency": "USDT",
        "current_equity": round(current_equity, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "generated_at": datetime.utcnow().isoformat(),
    }


@app.get("/api/signals/active")
async def get_active_signals() -> list[dict[str, Any]]:
    if not db:
        return []
    symbol = settings.symbol
    last_price = db.get_latest_price(symbol=symbol)
    signals = db.list_active_signals(
        symbol=symbol,
        capital_base=_position_size(),
        lookback_seconds=settings.lookback_seconds,
        max_gap_seconds=settings.max_allowed_tick_gap_seconds,
        limit=50,
    )
    # Only show validated signals (OPEN with APPROVE verdict), hide PENDING_VALIDATION
    validated = [s for s in signals if str(s.get('status', '')).upper() == 'OPEN']
    for s in validated:
        s['position_size'] = round(_position_size(), 2)
    return [_enrich_signal(s, last_price) for s in validated]


@app.get("/api/signals/history")
async def get_signal_history(
    status: Optional[str] = Query(None, description="Filter: WIN, LOSS, EXPIRED, OPEN"),
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    if not db:
        return []
    symbol = settings.symbol
    last_price = db.get_latest_price(symbol=symbol)
    pos_size = _position_size()

    # Get closed signals (only validated ones that completed)
    closed = db.list_signal_history_with_capital(
        symbol=symbol,
        capital_base=pos_size,
        limit=limit,
    )
    for sig in closed:
        sig['position_size'] = round(pos_size, 2)

    # Also get active (OPEN) signals with unrealized P&L — only validated
    active = db.list_active_signals(
        symbol=symbol,
        capital_base=pos_size,
        lookback_seconds=settings.lookback_seconds,
        max_gap_seconds=settings.max_allowed_tick_gap_seconds,
        limit=50,
    )
    # Filter: only show validated OPEN signals, NOT pending validation
    active = [s for s in active if str(s.get('status', '')).upper() == 'OPEN']
    # Enrich active signals with unrealized P&L
    for sig in active:
        entry = float(sig.get("entry_price", 0))
        side = str(sig.get("side", "")).upper()
        if last_price and entry > 0:
            if side == "LONG":
                unrealized = ((last_price - entry) / entry) * 100
            elif side == "SHORT":
                unrealized = ((entry - last_price) / entry) * 100
            else:
                unrealized = 0.0
            sig["pnl_pct"] = round(unrealized, 4)
            sig["capital_pnl"] = round(pos_size * unrealized / 100.0, 2)
            sig["unrealized"] = True
        else:
            sig["pnl_pct"] = 0.0
            sig["capital_pnl"] = 0.0
            sig["unrealized"] = True
        sig['position_size'] = round(pos_size, 2)

    # Merge: active first, then closed
    all_signals = active + closed

    if status:
        status_upper = status.upper()
        if status_upper == "OPEN":
            all_signals = [s for s in all_signals if str(s.get("status", "")).upper() == "OPEN"]
        else:
            all_signals = [s for s in all_signals if str(s.get("status", "")).upper() == status_upper]

    return [_enrich_signal(s, last_price) for s in all_signals]


@app.get("/api/account")
async def get_account_summary() -> dict[str, Any]:
    """Overall account summary: realized + unrealized P&L with position sizing."""
    if not db:
        return {"error": "Database not connected"}
    symbol = settings.symbol
    capital = settings.reporting_capital_base
    pos_size = _position_size()
    last_price = db.get_latest_price(symbol=symbol)

    # Realized P&L from closed signals (position-sized)
    perf = db.get_signal_performance_summary(symbol=symbol)
    equity_curve = db.get_signal_equity_curve(symbol=symbol, capital_base=pos_size)
    realized_equity = capital
    if equity_curve:
        # Sum trade P&Ls to get cumulative realized P&L
        cumulative_trade_pnl = float(equity_curve[-1].get("cumulative_pnl", 0))
        realized_equity = capital + cumulative_trade_pnl
    realized_pnl = realized_equity - capital

    # Unrealized P&L from validated open signals only
    active = db.list_active_signals(
        symbol=symbol,
        capital_base=pos_size,
        lookback_seconds=settings.lookback_seconds,
        max_gap_seconds=settings.max_allowed_tick_gap_seconds,
        limit=50,
    )
    # Only count validated signals
    active = [s for s in active if str(s.get('status', '')).upper() == 'OPEN']
    total_unrealized = 0.0
    for sig in active:
        entry = float(sig.get("entry_price", 0))
        side = str(sig.get("side", "")).upper()
        if last_price and entry > 0:
            if side == "LONG":
                total_unrealized += pos_size * ((last_price - entry) / entry)
            elif side == "SHORT":
                total_unrealized += pos_size * ((entry - last_price) / entry)

    return {
        "capital_base": capital,
        "position_size": round(pos_size, 2),
        "position_size_pct": settings.position_size_pct,
        "currency": "USDT",
        "current_price": round(last_price, 2) if last_price else 0,
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": round(total_unrealized, 2),
        "total_pnl": round(realized_pnl + total_unrealized, 2),
        "current_equity": round(realized_equity + total_unrealized, 2),
        "active_trades": len(active),
        "total_closed": int(perf.get("total_signals", 0)),
        "win_count": int(perf.get("wins", 0)),
        "loss_count": int(perf.get("losses", 0)),
    }


# ── These /api/signals/ routes MUST be before the {signal_id} catch-all ──

@app.get("/api/signals/history-v2")
async def get_signal_history_v2(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    side: Optional[str] = Query(None, description="LONG or SHORT"),
    status: Optional[str] = Query(None, description="WIN, LOSS, EXPIRED, OPEN"),
    session: Optional[str] = Query(None, description="ASIAN, EUROPEAN, US, CLOSING"),
) -> dict[str, Any]:
    """Paginated signal history with filters."""
    if not db:
        return {"error": "Database not connected"}
    result = db.list_signal_history_paginated(
        symbol=settings.symbol,
        limit=limit,
        offset=offset,
        side=side,
        status=status,
        session=session,
        capital_base=_position_size(),
    )
    for sig in result.get("signals", []):
        sig["generated_at"] = _safe_str(sig.get("generated_at"))
        sig["closed_at"] = _safe_str(sig.get("closed_at"))
    return result


@app.get("/api/signals/missed-profit")
async def get_missed_profit_signals(
    limit: int = Query(20, ge=1, le=100),
) -> list[dict[str, Any]]:
    """EXPIRED signals where MFE exceeded 2x target distance."""
    if not db:
        return []
    data = db.get_missed_profit_signals(symbol=settings.symbol, limit=limit)
    for sig in data:
        sig["generated_at"] = _safe_str(sig.get("generated_at"))
    return data


@app.get("/api/timeframe/bias")
async def get_timeframe_bias() -> dict[str, Any]:
    """Return current multi-TF trend alignment data."""
    try:
        import engine.analysis.timeframe_analysis as TFA
        bias = TFA.get_trend_bias()
        return bias
    except Exception as e:
        return {"error": str(e), "daily_bias": "UNKNOWN", "alignment_score": 0.0}


@app.get("/api/performance/regime-breakdown")
async def get_regime_breakdown() -> list[dict[str, Any]]:
    """Win rate, avg P&L, avg duration grouped by volatility regime."""
    if not db:
        return []
    try:
        with db._lock:
            rows = db._conn.execute(
                """
                SELECT
                    volatility_regime_at_entry as regime,
                    COUNT(*) as total,
                    SUM(CASE WHEN net_pnl_percent > 0 THEN 1 ELSE 0 END) as wins,
                    ROUND(AVG(net_pnl_percent) * 100, 4) as avg_net_pnl_pct,
                    ROUND(AVG(gross_pnl_percent) * 100, 4) as avg_gross_pnl_pct,
                    ROUND(AVG(DATEDIFF('second', generated_at, closed_at)), 0) as avg_duration_s,
                    ROUND(AVG(mfe_percent), 4) as avg_mfe,
                    ROUND(AVG(mae_percent), 4) as avg_mae
                FROM trade_signals
                WHERE status IN ('WIN', 'LOSS', 'TREND_REVERSAL')
                  AND volatility_regime_at_entry IS NOT NULL
                  AND volatility_regime_at_entry != 'UNKNOWN'
                GROUP BY volatility_regime_at_entry
                ORDER BY total DESC
                """
            ).fetchall()
        cols = ["regime", "total", "wins", "avg_net_pnl_pct", "avg_gross_pnl_pct",
                "avg_duration_s", "avg_mfe", "avg_mae"]
        result = []
        for row in rows:
            d = dict(zip(cols, row))
            d["win_rate"] = round(d["wins"] / d["total"], 4) if d["total"] > 0 else 0
            result.append(d)
        return result
    except Exception as e:
        return [{"error": str(e)}]


@app.get("/api/signals/trend-reversals")
async def get_trend_reversal_signals(
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Signals closed by TREND_REVERSAL with entry/exit context."""
    if not db:
        return []
    try:
        with db._lock:
            rows = db._conn.execute(
                """
                SELECT signal_id, generated_at, closed_at, side, entry_price,
                       close_price, gross_pnl_percent, net_pnl_percent,
                       alignment_score_at_entry, daily_bias_at_entry,
                       four_h_structure_at_entry, mfe_percent, mae_percent,
                       max_unrealized_pnl_percent, drawdown_from_peak_percent,
                       DATEDIFF('second', generated_at, closed_at) as duration_s
                FROM trade_signals
                WHERE status = 'TREND_REVERSAL'
                ORDER BY closed_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        cols = ["signal_id", "generated_at", "closed_at", "side", "entry_price",
                "close_price", "gross_pnl_percent", "net_pnl_percent",
                "alignment_score_at_entry", "daily_bias_at_entry",
                "four_h_structure_at_entry", "mfe_percent", "mae_percent",
                "max_unrealized_pnl_percent", "drawdown_from_peak_percent",
                "duration_s"]
        result = []
        for row in rows:
            d = dict(zip(cols, row))
            d["generated_at"] = _safe_str(d.get("generated_at"))
            d["closed_at"] = _safe_str(d.get("closed_at"))
            d["was_profitable"] = (d.get("net_pnl_percent") or 0) > 0
            d["post_note"] = "GOOD_EARLY_EXIT" if d["was_profitable"] else "LATE_EXIT"
            result.append(d)
        return result
    except Exception as e:
        return [{"error": str(e)}]


@app.get("/api/signals/{signal_id}")
async def get_signal_detail(signal_id: str) -> dict[str, Any]:
    if not db:
        return {"error": "Database not connected"}

    debug = db.get_signal_debug(signal_id=signal_id)
    if not debug:
        return {"error": "Signal not found"}

    last_price = db.get_latest_price(symbol=settings.symbol)
    enriched = _enrich_signal(debug, last_price)

    # Find associated crew events for this signal
    events = db.list_crew_events(symbol=settings.symbol, limit=500)
    signal_events = []
    for ev in events:
        payload = ev.get("payload", {})
        if isinstance(payload, dict) and payload.get("signal_id") == signal_id:
            ev["event_time"] = _safe_str(ev.get("event_time"))
            signal_events.append(ev)

    # If signal has a run_id from any matching event, gather all events for that run
    run_ids = {ev.get("run_id") for ev in signal_events if ev.get("run_id")}
    for run_id in run_ids:
        if run_id:
            run_events = db.list_crew_events(symbol=settings.symbol, run_id=run_id, limit=100)
            for rev in run_events:
                rev["event_time"] = _safe_str(rev.get("event_time"))
                if rev not in signal_events:
                    signal_events.append(rev)

    # Sort by time
    signal_events.sort(key=lambda e: e.get("event_time") or "", reverse=False)

    return {
        "signal": enriched,
        "decision_trace": enriched.get("decision_trace", {}),
        "challenge": enriched.get("challenge"),
        "agent_events": signal_events,
    }


@app.get("/api/performance")
async def get_performance() -> dict[str, Any]:
    if not db:
        return {"error": "Database not connected"}

    symbol = settings.symbol
    capital = settings.reporting_capital_base
    pos_size = _position_size()
    perf = db.get_signal_performance_summary(symbol=symbol)
    equity_curve_raw = db.get_signal_equity_curve(symbol=symbol, capital_base=pos_size)
    history = db.list_signal_history_with_capital(symbol=symbol, capital_base=pos_size, limit=5000)

    win_count = int(perf.get("wins", 0))
    loss_count = int(perf.get("losses", 0))
    total = win_count + loss_count
    win_rate = round(win_count / total * 100, 1) if total > 0 else 0.0

    current_equity = capital
    max_dd = 0.0
    best_trade = 0.0
    worst_trade = 0.0
    win_pnls = []
    loss_pnls = []

    curve_points = []
    running = capital
    peak = capital
    for pt in equity_curve_raw:
        trade_pnl = float(pt.get("trade_pnl", 0))
        running += trade_pnl
        peak = max(peak, running)
        dd = ((running - peak) / peak) * 100 if peak > 0 else 0
        max_dd = min(max_dd, dd)
        current_equity = running
        pnl_pct = float(pt.get("pnl_pct", 0))
        best_trade = max(best_trade, pnl_pct)
        worst_trade = min(worst_trade, pnl_pct)
        if pnl_pct > 0:
            win_pnls.append(pnl_pct)
        elif pnl_pct < 0:
            loss_pnls.append(pnl_pct)
        curve_points.append({
            "signal_id": str(pt.get("signal_id", "")),
            "point_time": _safe_str(pt.get("point_time")),
            "status": str(pt.get("status", "")),
            "pnl_pct": round(pnl_pct, 4),
            "trade_pnl": round(trade_pnl, 2),
            "cumulative_pnl": round(float(pt.get("cumulative_pnl", 0)), 2),
            "equity_value": round(running, 2),
        })

    trade_bars = []
    for h in history:
        pnl = float(h.get("pnl_pct", 0) or 0)
        trade_bars.append({
            "signal_id": str(h.get("signal_id", ""))[:8],
            "side": str(h.get("side", "")),
            "status": str(h.get("status", "")),
            "pnl_pct": round(pnl, 4),
            "capital_pnl": round(float(h.get("capital_pnl", 0) or 0), 2),
            "generated_at": _safe_str(h.get("generated_at")),
        })

    return {
        "capital_base": capital,
        "position_size": round(pos_size, 2),
        "position_size_pct": settings.position_size_pct,
        "currency": "USDT",
        "current_equity": round(current_equity, 2),
        "total_pnl": round(current_equity - capital, 2),
        "total_pnl_pct": round((current_equity - capital) / capital * 100, 2) if capital > 0 else 0,
        "win_count": win_count,
        "loss_count": loss_count,
        "total_trades": total,
        "win_rate": win_rate,
        "max_drawdown_pct": round(max_dd, 2),
        "best_trade_pnl_pct": round(best_trade, 4),
        "worst_trade_pnl_pct": round(worst_trade, 4),
        "avg_win_pnl_pct": round(sum(win_pnls) / len(win_pnls), 4) if win_pnls else 0.0,
        "avg_loss_pnl_pct": round(sum(loss_pnls) / len(loss_pnls), 4) if loss_pnls else 0.0,
        "equity_curve": curve_points,
        "trade_pnl_bars": trade_bars,
    }

@app.post("/api/signals/close/{close_type}")
async def close_trades(close_type: str, signal_id: Optional[str] = None):
    """
    Close active trades. 
    close_type can be: 'all', 'win', 'loss', 'single'
    If 'single', signal_id must be provided.
    """
    if not db:
        return {"status": "error", "message": "Database not connected"}
    
    if close_type not in ["all", "win", "loss", "single"]:
        return {"status": "error", "message": "Invalid close type"}
        
    if close_type == "single" and not signal_id:
        return {"status": "error", "message": "signal_id required for single close"}

    closed_count = db.manual_close_trades(
        symbol=settings.symbol, 
        close_type=close_type, 
        specific_signal_id=signal_id if close_type == "single" else None
    )
    return {"status": "success", "closed_count": closed_count}


@app.get("/api/agents/events")
async def get_agent_events(
    limit: int = Query(500, ge=1, le=10000),
) -> list[dict[str, Any]]:
    if not db:
        return []
    events = db.list_crew_events(symbol=settings.symbol, limit=limit)
    for ev in events:
        ev["event_time"] = _safe_str(ev.get("event_time"))
    return events


@app.get("/api/agents/events/{run_id}")
async def get_agent_events_by_run(run_id: str) -> list[dict[str, Any]]:
    if not db:
        return []
    events = db.list_crew_events(symbol=settings.symbol, run_id=run_id, limit=1000)
    for ev in events:
        ev["event_time"] = _safe_str(ev.get("event_time"))
    return events


@app.get("/api/orderflow")
async def get_orderflow() -> dict[str, Any]:
    if not db:
        return {"error": "Database not connected"}
    snapshot = db.get_latest_orderflow_snapshot(symbol=settings.symbol) or {}
    analysis = db.get_latest_analysis(symbol=settings.symbol) or {}
    return {
        **snapshot,
        "regime": analysis.get("regime"),
        "confidence": analysis.get("confidence"),
        "summary": analysis.get("summary"),
    }


# ══════════════════════════════════════════════════════════════════════
# PROFITABILITY OVERHAUL: New Endpoints
# ══════════════════════════════════════════════════════════════════════

def _lttb_downsample(data: list[dict], target_points: int) -> list[dict]:
    """
    Largest Triangle Three Buckets (LTTB) downsampling.
    Preserves visual shape of the curve while reducing points.
    """
    if len(data) <= target_points or target_points < 3:
        return data

    sampled = [data[0]]  # Always keep first point
    bucket_size = (len(data) - 2) / (target_points - 2)

    a_idx = 0
    for i in range(1, target_points - 1):
        bucket_start = int((i - 1) * bucket_size) + 1
        bucket_end = int(i * bucket_size) + 1
        bucket_end = min(bucket_end, len(data) - 1)

        # Next bucket average
        next_start = int(i * bucket_size) + 1
        next_end = min(int((i + 1) * bucket_size) + 1, len(data) - 1)
        avg_y = sum(
            float(data[j].get("cumulative_net_pnl", 0))
            for j in range(next_start, next_end + 1)
        ) / max(next_end - next_start + 1, 1)
        avg_x = (next_start + next_end) / 2

        # Find point in current bucket with max triangle area
        max_area = -1
        best_idx = bucket_start
        a_y = float(data[a_idx].get("cumulative_net_pnl", 0))

        for j in range(bucket_start, bucket_end + 1):
            area = abs(
                (a_idx - avg_x) * (float(data[j].get("cumulative_net_pnl", 0)) - a_y)
                - (a_idx - j) * (avg_y - a_y)
            ) * 0.5
            if area > max_area:
                max_area = area
                best_idx = j

        sampled.append(data[best_idx])
        a_idx = best_idx

    sampled.append(data[-1])  # Always keep last point
    return sampled


@app.get("/api/system/config")
async def get_system_config() -> dict[str, Any]:
    """Return active config constants for frontend display."""
    return {
        "maker_fee": C.MAKER_FEE,
        "taker_fee": C.TAKER_FEE,
        "round_trip_fee": C.ROUND_TRIP_FEE,
        "round_trip_fee_pct": round(C.ROUND_TRIP_FEE * 100, 4),
        "min_gross_edge": C.MINIMUM_GROSS_EDGE_PERCENT,
        "min_gross_edge_pct": round(C.MINIMUM_GROSS_EDGE_PERCENT * 100, 4),
        "exit_mode": "structural",
        "trailing_be_atr": C.TRAILING_BE_ATR,
        "trailing_lock_atr": C.TRAILING_LOCK_ATR,
        "trailing_follow_atr": C.TRAILING_FOLLOW_ATR,
        "min_alignment_score": C.MIN_ALIGNMENT_SCORE,
        "min_adx": C.MIN_ADX_THRESHOLD,
        "adx_extreme": C.ADX_EXTREME_THRESHOLD,
        "min_atr_pct": C.MIN_ATR_PERCENT_FOR_TRADE,
        "max_spread_ticks": C.MAX_SPREAD_TICKS,
        "nofi_threshold": C.NOFI_COMPOSITE_THRESHOLD,
        "kelly_cap": C.KELLY_FRACTION_CAP,
        "max_active_trades": C.MAX_ACTIVE_TRADES,
        "circuit_breaker_losses": C.CIRCUIT_BREAKER_LOSSES,
        "daily_loss_stop_pct": C.DAILY_LOSS_STOP_PCT,
        "trend_reversal_hysteresis": C.TREND_REVERSAL_HYSTERESIS,
    }


@app.get("/api/performance/summary")
async def get_performance_summary(
    lookback_days: int = Query(0, ge=0, description="0 = all time"),
) -> dict[str, Any]:
    """Extended performance summary with optional lookback."""
    if not db:
        return {"error": "Database not connected"}
    return db.get_performance_summary_with_lookback(
        symbol=settings.symbol, lookback_days=lookback_days
    )


@app.get("/api/equity-curve")
async def get_equity_curve(
    lookback_days: int = Query(0, ge=0, description="0 = all time"),
    max_points: int = Query(500, ge=50, le=2000, description="LTTB target points"),
) -> dict[str, Any]:
    """Full equity curve with LTTB downsampling for large datasets."""
    if not db:
        return {"error": "Database not connected"}
    curve = db.get_equity_curve_full(
        symbol=settings.symbol, lookback_days=lookback_days
    )
    # Serialize datetime objects
    for pt in curve:
        pt["point_time"] = _safe_str(pt.get("point_time"))

    total_points = len(curve)
    if total_points > max_points:
        curve = _lttb_downsample(curve, max_points)
        downsampled = True
    else:
        downsampled = False

    return {
        "total_trades": total_points,
        "displayed_points": len(curve),
        "downsampled": downsampled,
        "curve": curve,
    }


@app.get("/api/performance/by-session")
async def get_performance_by_session() -> list[dict[str, Any]]:
    """Hourly performance table for session/regime analysis."""
    if not db:
        return []
    data = db.get_hourly_performance_table()
    for row in data:
        row["updated_at"] = _safe_str(row.get("updated_at"))
    return data


@app.get("/api/performance/monthly-breakdown")
async def get_monthly_breakdown() -> list[dict[str, Any]]:
    """Monthly performance grouped by year-month."""
    if not db:
        return []
    return db.get_monthly_breakdown(symbol=settings.symbol)


# ── WebSocket ──────────────────────────────────────────────────────────

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive; client can send pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)


async def _broadcast_loop() -> None:
    """Periodically broadcasts price + signal snapshot to all WS clients."""
    while True:
        await asyncio.sleep(3)
        if manager.client_count == 0 or db is None:
            continue
        try:
            symbol = settings.symbol
            last_price = db.get_latest_price(symbol=symbol)
            active = db.list_active_signals(
                symbol=symbol,
                capital_base=_position_size(),
                lookback_seconds=settings.lookback_seconds,
                max_gap_seconds=settings.max_allowed_tick_gap_seconds,
                limit=20,
            )
            # Only broadcast validated signals
            active = [s for s in active if str(s.get('status', '')).upper() == 'OPEN']
            enriched = [_enrich_signal(s, last_price) for s in active]

            # Latest events
            events = db.list_crew_events(symbol=symbol, limit=5)
            for ev in events:
                ev["event_time"] = _safe_str(ev.get("event_time"))

            # Profitability overhaul: enrich broadcast with L2 + session + regime
            try:
                from engine.data.binance_ingestor import l2_cache, funding_cache
                from engine.analysis.technical_math import fetch_macro_indicators
                l2_data = {
                    "spread_ticks": l2_cache.get("spread_ticks", 0),
                    "obi": l2_cache.get("obi_0_1pct", 0),
                    "funding_rate": funding_cache.get("current", 0),
                    "best_bid": l2_cache.get("best_bid", 0),
                    "best_ask": l2_cache.get("best_ask", 0),
                }
                session_tag = db.get_session_tag()
            except Exception:
                l2_data = {}
                session_tag = "UNKNOWN"

            await manager.broadcast({
                "type": "update",
                "timestamp": datetime.utcnow().isoformat(),
                "last_price": last_price,
                "active_signals": enriched,
                "recent_events": events,
                "current_l2": l2_data,
                "session_tag": session_tag,
                "config": {
                    "round_trip_fee_pct": round(C.ROUND_TRIP_FEE * 100, 4),
                    "min_edge_pct": round(C.MINIMUM_GROSS_EDGE_PERCENT * 100, 4),
                },
            })
        except Exception:
            LOGGER.debug("Broadcast loop error", exc_info=True)
