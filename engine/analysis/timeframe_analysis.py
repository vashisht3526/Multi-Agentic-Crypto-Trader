"""
Multi-Timeframe Trend Engine — Top-Down Trend Hierarchy.

Polls Binance REST klines on 4 timeframes (1d, 4h, 1h, 15m).
Maintains in-memory buffers. Provides alignment scoring, swing detection,
pullback timing, and regime-adaptive multiplier selection.

NO DuckDB writes per poll — only last-fetch timestamp logged.
"""

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests as sync_requests

import engine.config as C

LOGGER = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
# IN-MEMORY CANDLE BUFFERS
# ══════════════════════════════════════════════════════════════════════
_daily_candles: deque = deque(maxlen=50)
_4h_candles: deque = deque(maxlen=100)
_1h_candles: deque = deque(maxlen=100)
_15m_candles: deque = deque(maxlen=200)

# Last fetch timestamps (monotonic) for polling intervals
_last_fetch: Dict[str, float] = {
    "1d": 0.0,
    "4h": 0.0,
    "1h": 0.0,
    "15m": 0.0,
}

# Auto-tune state (loaded from DB on startup, persisted weekly)
_active_tp_adjustment: float = 0.0  # Added to regime TP mult
_active_sl_adjustment: float = 0.0  # Added to regime SL mult
_last_auto_tune_week: int = 0


# ══════════════════════════════════════════════════════════════════════
# KLINE FETCHING
# ══════════════════════════════════════════════════════════════════════

def _parse_kline(raw: list) -> Dict[str, float]:
    """Parse a Binance kline array into a dict."""
    return {
        "open_time": int(raw[0]),
        "open": float(raw[1]),
        "high": float(raw[2]),
        "low": float(raw[3]),
        "close": float(raw[4]),
        "volume": float(raw[5]),
        "close_time": int(raw[6]),
    }


def _fetch_klines(symbol: str, interval: str, limit: int = 50) -> List[Dict[str, float]]:
    """Fetch klines from Binance REST API."""
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        resp = sync_requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return [_parse_kline(k) for k in resp.json()]
    except Exception as e:
        LOGGER.error("Failed to fetch %s klines for %s: %s", interval, symbol, e)
        return []


def _refresh_buffer(
    buffer: deque, symbol: str, interval: str, limit: int
) -> bool:
    """Fetch and replace buffer contents. Returns True if successful."""
    candles = _fetch_klines(symbol, interval, limit)
    if candles:
        buffer.clear()
        buffer.extend(candles)
        _last_fetch[interval] = time.monotonic()
        return True
    return False


async def refresh_all_timeframes(symbol: str = "BTCUSDT") -> None:
    """
    Continuous polling coroutine. Runs forever.
    - 1d + 4h: every 15 minutes
    - 1h: every 5 minutes
    - 15m: every 2 minutes
    """
    LOGGER.info("Timeframe analysis poller starting for %s", symbol)

    # Initial fetch — all timeframes
    loop = asyncio.get_event_loop()
    for interval, buf, limit in [
        ("1d", _daily_candles, 50),
        ("4h", _4h_candles, 100),
        ("1h", _1h_candles, 100),
        ("15m", _15m_candles, 200),
    ]:
        await loop.run_in_executor(None, _refresh_buffer, buf, symbol, interval, limit)
        await asyncio.sleep(0.5)  # Stagger to avoid burst

    LOGGER.info(
        "Initial TF fetch complete: 1d=%d, 4h=%d, 1h=%d, 15m=%d candles",
        len(_daily_candles), len(_4h_candles), len(_1h_candles), len(_15m_candles),
    )

    while True:
        try:
            now = time.monotonic()

            # 15m: every 2 minutes
            if now - _last_fetch["15m"] >= 120:
                await loop.run_in_executor(
                    None, _refresh_buffer, _15m_candles, symbol, "15m", 200
                )

            # 1h: every 5 minutes
            if now - _last_fetch["1h"] >= 300:
                await loop.run_in_executor(
                    None, _refresh_buffer, _1h_candles, symbol, "1h", 100
                )

            # 4h + 1d: every 15 minutes
            if now - _last_fetch["4h"] >= 900:
                await loop.run_in_executor(
                    None, _refresh_buffer, _4h_candles, symbol, "4h", 100
                )
            if now - _last_fetch["1d"] >= 900:
                await loop.run_in_executor(
                    None, _refresh_buffer, _daily_candles, symbol, "1d", 50
                )

            await asyncio.sleep(30)  # Check every 30s if any interval is due

        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Timeframe refresh error (will retry)")
            await asyncio.sleep(60)


# ══════════════════════════════════════════════════════════════════════
# INDICATOR CALCULATIONS
# ══════════════════════════════════════════════════════════════════════

def _ema(prices: List[float], period: int) -> float:
    """Exponential moving average."""
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0.0
    k = 2.0 / (period + 1)
    ema_val = sum(prices[:period]) / period
    for p in prices[period:]:
        ema_val = (p * k) + (ema_val * (1 - k))
    return ema_val


def _rsi(prices: List[float], period: int = 14) -> float:
    """RSI using Wilder's smoothing."""
    if len(prices) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        gains.append(change if change > 0 else 0.0)
        losses.append(abs(change) if change < 0 else 0.0)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ══════════════════════════════════════════════════════════════════════
# DAILY BIAS
# ══════════════════════════════════════════════════════════════════════

def _daily_ema_bias() -> str:
    """BULLISH if current price > 20 EMA, BEARISH otherwise."""
    if len(_daily_candles) < C.DAILY_EMA_PERIOD:
        return "NEUTRAL"
    closes = [c["close"] for c in _daily_candles]
    ema_val = _ema(closes, C.DAILY_EMA_PERIOD)
    current = closes[-1]
    if current > ema_val:
        return "BULLISH"
    elif current < ema_val:
        return "BEARISH"
    return "NEUTRAL"


# ══════════════════════════════════════════════════════════════════════
# 4H SWING STRUCTURE DETECTION
# ══════════════════════════════════════════════════════════════════════

def _detect_swing_structure() -> Dict[str, Any]:
    """
    Scan last 20 four-hour candles for swing highs/lows.
    Swing high: local max with 2 lower highs on each side.
    Swing low: local min with 2 higher lows on each side.
    """
    candles = list(_4h_candles)
    result: Dict[str, Any] = {
        "direction": "RANGING",
        "strength": "WEAK",
        "swing_high": None,
        "swing_low": None,
        "swing_high_idx": None,
        "swing_low_idx": None,
        "higher_highs": False,
        "higher_lows": False,
        "lower_highs": False,
        "lower_lows": False,
    }

    if len(candles) < 10:
        return result

    # Use last 20 candles
    recent = candles[-20:] if len(candles) >= 20 else candles

    highs = [c["high"] for c in recent]
    lows = [c["low"] for c in recent]

    # Find swing points (need 2 on each side)
    swing_highs: List[Tuple[int, float]] = []
    swing_lows: List[Tuple[int, float]] = []

    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and \
           highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append((i, highs[i]))

        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and \
           lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            swing_lows.append((i, lows[i]))

    if swing_highs:
        result["swing_high"] = swing_highs[-1][1]
        result["swing_high_idx"] = swing_highs[-1][0]
    if swing_lows:
        result["swing_low"] = swing_lows[-1][1]
        result["swing_low_idx"] = swing_lows[-1][0]

    # Determine HH/HL/LH/LL
    if len(swing_highs) >= 2:
        result["higher_highs"] = swing_highs[-1][1] > swing_highs[-2][1]
        result["lower_highs"] = swing_highs[-1][1] < swing_highs[-2][1]
    if len(swing_lows) >= 2:
        result["higher_lows"] = swing_lows[-1][1] > swing_lows[-2][1]
        result["lower_lows"] = swing_lows[-1][1] < swing_lows[-2][1]

    # Direction
    if result["higher_highs"] and result["higher_lows"]:
        result["direction"] = "BULLISH"
    elif result["lower_highs"] and result["lower_lows"]:
        result["direction"] = "BEARISH"
    else:
        result["direction"] = "RANGING"

    # Strength: STRONG if clear pattern with both swings confirming
    if result["direction"] != "RANGING":
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            result["strength"] = "STRONG"
        else:
            result["strength"] = "WEAK"

    return result


# ══════════════════════════════════════════════════════════════════════
# PULLBACK DETECTION
# ══════════════════════════════════════════════════════════════════════

def is_pullback_complete(side: str) -> bool:
    """
    Check if price is in the Fibonacci retracement zone and has printed
    a reversal candle on 15m.

    For BULLISH (LONG): price retraced to 0.382-0.618 zone of last swing,
    then 15m candle closed back in trend direction (above Fib midpoint).

    For BEARISH (SHORT): inverse.
    """
    structure = _detect_swing_structure()
    swing_high = structure.get("swing_high")
    swing_low = structure.get("swing_low")

    if swing_high is None or swing_low is None:
        return False

    swing_range = swing_high - swing_low
    if swing_range <= 0:
        return False

    # Fibonacci levels
    fib_382 = swing_high - (0.382 * swing_range)  # Shallow retracement
    fib_618 = swing_high - (0.618 * swing_range)  # Deep retracement
    fib_mid = swing_high - (0.50 * swing_range)

    # Get recent 15m candles
    if len(_15m_candles) < 3:
        return False

    recent_15m = list(_15m_candles)[-5:]  # Last 5 candles
    current_close = recent_15m[-1]["close"]
    prev_close = recent_15m[-2]["close"]

    if side == "LONG":
        # Price should have been in the retracement zone
        in_fib_zone = any(
            fib_618 <= c["low"] <= fib_382 or fib_618 <= c["close"] <= fib_382
            for c in recent_15m[:-1]
        )
        # Current 15m candle closed above the Fib midpoint (bounce confirmed)
        reversal_confirmed = current_close > fib_mid and current_close > prev_close
        return in_fib_zone and reversal_confirmed

    elif side == "SHORT":
        # For SHORT: inverse — price bounced up into zone then fell back
        inv_fib_382 = swing_low + (0.382 * swing_range)
        inv_fib_618 = swing_low + (0.618 * swing_range)
        inv_fib_mid = swing_low + (0.50 * swing_range)

        in_fib_zone = any(
            inv_fib_382 <= c["high"] <= inv_fib_618 or inv_fib_382 <= c["close"] <= inv_fib_618
            for c in recent_15m[:-1]
        )
        reversal_confirmed = current_close < inv_fib_mid and current_close < prev_close
        return in_fib_zone and reversal_confirmed

    return False


# ══════════════════════════════════════════════════════════════════════
# ALIGNMENT SCORING
# ══════════════════════════════════════════════════════════════════════

def _score_alignment(
    daily_bias: str,
    four_h: Dict[str, Any],
    one_h_rsi: float,
    side: str,
) -> float:
    """
    Score multi-timeframe alignment from 0.0 to 1.0.
    Restructured so pullback is a BONUS, not required for minimum threshold.
    - 0.25: daily bias matches direction
    - 0.20: 4H structure confirms (BULLISH/BEARISH, not RANGING)
    - 0.10: 4H structure strength is STRONG
    - 0.15: RSI in trend-continuation zone
    - 0.10: RSI not extreme
    - 0.10: 15m momentum aligned
    - 0.10: pullback complete (BONUS)
    """
    score = 0.0
    four_h_dir = four_h.get("direction", "RANGING")

    # 0.25 — Daily matches direction
    if side == "LONG" and daily_bias == "BULLISH":
        score += 0.25
    elif side == "SHORT" and daily_bias == "BEARISH":
        score += 0.25

    # 0.20 — 4H structure confirms direction (not RANGING)
    if side == "LONG" and four_h_dir == "BULLISH":
        score += 0.20
    elif side == "SHORT" and four_h_dir == "BEARISH":
        score += 0.20
    elif four_h_dir == "RANGING":
        # Partial credit if RANGING but has favorable structure
        if side == "LONG" and four_h.get("higher_lows"):
            score += 0.10
        elif side == "SHORT" and four_h.get("lower_highs"):
            score += 0.10

    # 0.10 — 4H strength
    if four_h.get("strength") == "STRONG":
        score += 0.10

    # 0.15 — RSI in sweet zone
    if side == "LONG" and 40 <= one_h_rsi <= 68:
        score += 0.15
    elif side == "SHORT" and 32 <= one_h_rsi <= 60:
        score += 0.15

    # 0.10 — RSI not extreme
    if 25 <= one_h_rsi <= 75:
        score += 0.10

    # 0.10 — 15m momentum
    if len(_15m_candles) >= 3:
        last_3 = list(_15m_candles)[-3:]
        if side == "LONG" and last_3[-1]["close"] > last_3[0]["close"]:
            score += 0.10
        elif side == "SHORT" and last_3[-1]["close"] < last_3[0]["close"]:
            score += 0.10

    # 0.10 — Pullback complete (BONUS)
    if is_pullback_complete(side):
        score += 0.10

    return round(score, 2)


# ══════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════

def get_trend_bias() -> Dict[str, Any]:
    """
    Return complete trend analysis. Called by backbone (gate 1)
    and trade reaper (trend reversal checks).
    """
    daily_bias = _daily_ema_bias()
    four_h_structure = _detect_swing_structure()

    # 1H RSI
    if len(_1h_candles) >= 15:
        one_h_closes = [c["close"] for c in _1h_candles]
        one_h_rsi = _rsi(one_h_closes, 14)
    else:
        one_h_rsi = 50.0

    # 15m momentum check
    momentum_ok = False
    if len(_15m_candles) >= 3:
        last_3 = list(_15m_candles)[-3:]
        if last_3[-1]["close"] > last_3[0]["close"]:
            momentum_ok = True  # 15m trending up
        elif last_3[-1]["close"] < last_3[0]["close"]:
            momentum_ok = True  # 15m trending down (valid for shorts)

    four_h_dir = four_h_structure.get("direction", "RANGING")

    # Determine candidate side from higher-TF bias
    # Allow daily-dominant trades when 4H has partial structure
    if daily_bias == "BULLISH" and four_h_dir == "BULLISH":
        candidate_side = "LONG"
    elif daily_bias == "BEARISH" and four_h_dir == "BEARISH":
        candidate_side = "SHORT"
    elif daily_bias == "BULLISH" and four_h_dir == "RANGING":
        # Daily bullish + 4H ranging but has higher lows = cautious LONG
        if four_h_structure.get("higher_lows", False):
            candidate_side = "LONG"
        else:
            candidate_side = "NONE"
    elif daily_bias == "BEARISH" and four_h_dir == "RANGING":
        # Daily bearish + 4H ranging but has lower highs = cautious SHORT
        if four_h_structure.get("lower_highs", False):
            candidate_side = "SHORT"
        else:
            candidate_side = "NONE"
    else:
        candidate_side = "NONE"

    # Score for candidate side
    if candidate_side != "NONE":
        alignment_score = _score_alignment(daily_bias, four_h_structure, one_h_rsi, candidate_side)
        pullback = is_pullback_complete(candidate_side)
    else:
        alignment_score = 0.0
        pullback = False

    return {
        "daily_bias": daily_bias,
        "four_h_structure": four_h_structure,
        "four_h_direction": four_h_dir,
        "one_h_rsi": round(one_h_rsi, 2),
        "momentum_ok": momentum_ok,
        "candidate_side": candidate_side,
        "alignment_score": alignment_score,
        "pullback_complete": pullback,
        "buffers_loaded": {
            "daily": len(_daily_candles),
            "4h": len(_4h_candles),
            "1h": len(_1h_candles),
            "15m": len(_15m_candles),
        },
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def check_trend_holds_for_side(side: str) -> bool:
    """
    Check if the trend still supports an existing position.
    Used by trade reaper for trend reversal exits.
    Returns False if the trend has broken.
    """
    daily_bias = _daily_ema_bias()
    four_h = _detect_swing_structure()
    four_h_dir = four_h.get("direction", "RANGING")

    if side == "LONG":
        # LONG breaks if: daily flips BEARISH OR 4H makes a lower low
        if daily_bias == "BEARISH":
            return False
        if four_h.get("lower_lows", False):
            return False
    elif side == "SHORT":
        # SHORT breaks if: daily flips BULLISH OR 4H makes a higher high
        if daily_bias == "BULLISH":
            return False
        if four_h.get("higher_highs", False):
            return False

    return True


# ══════════════════════════════════════════════════════════════════════
# REGIME-ADAPTIVE MULTIPLIERS
# ══════════════════════════════════════════════════════════════════════

def get_regime_multipliers(current_atr_pct: float) -> Tuple[float, float, str]:
    """
    Returns (tp_mult, sl_mult, regime_name) based on current vs 30-day avg ATR.
    """
    # Compute 30-day average ATR% from daily candles
    if len(_daily_candles) >= 30:
        daily_ranges = []
        candles = list(_daily_candles)[-30:]
        for c in candles:
            daily_range_pct = (c["high"] - c["low"]) / c["close"] if c["close"] > 0 else 0
            daily_ranges.append(daily_range_pct)
        avg_atr_pct = sum(daily_ranges) / len(daily_ranges)
    elif len(_daily_candles) > 0:
        daily_ranges = [(c["high"] - c["low"]) / c["close"] for c in _daily_candles if c["close"] > 0]
        avg_atr_pct = sum(daily_ranges) / len(daily_ranges) if daily_ranges else current_atr_pct
    else:
        avg_atr_pct = current_atr_pct  # No data, assume normal

    ratio = current_atr_pct / avg_atr_pct if avg_atr_pct > 0 else 1.0

    if ratio < 0.5:
        tp = C.TP_MULT_LOW_VOL + _active_tp_adjustment
        sl = C.SL_MULT_LOW_VOL + _active_sl_adjustment
        regime = "LOW_VOL"
    elif ratio <= 1.5:
        tp = C.TP_MULT_NORMAL + _active_tp_adjustment
        sl = C.SL_MULT_NORMAL + _active_sl_adjustment
        regime = "NORMAL"
    elif ratio <= 2.5:
        tp = C.TP_MULT_HIGH_VOL + _active_tp_adjustment
        sl = C.SL_MULT_HIGH_VOL + _active_sl_adjustment
        regime = "HIGH_VOL"
    else:
        tp = C.TP_MULT_EXTREME + _active_tp_adjustment
        sl = C.SL_MULT_EXTREME + _active_sl_adjustment
        regime = "EXTREME"

    # Clamp
    tp = max(1.5, min(tp, C.AUTO_TUNE_TP_MAX))
    sl = max(0.5, min(sl, C.AUTO_TUNE_SL_MAX))

    return round(tp, 1), round(sl, 1), regime


# ══════════════════════════════════════════════════════════════════════
# AUTO-TUNE (weekly background)
# ══════════════════════════════════════════════════════════════════════

async def auto_tune_loop(db, symbol: str = "BTCUSDT") -> None:
    """
    Background task: every 60s, check if it's Sunday 00:00 UTC.
    If so, analyze last 7 days and adjust TP/SL multipliers.
    """
    global _active_tp_adjustment, _active_sl_adjustment, _last_auto_tune_week

    while True:
        try:
            now = datetime.now(timezone.utc)
            iso_week = now.isocalendar()[1]

            if now.weekday() == 6 and now.hour == 0 and iso_week != _last_auto_tune_week:
                _last_auto_tune_week = iso_week
                LOGGER.info("AUTO-TUNE: Running weekly target adjustment...")

                # Query signals from last 7 days
                with db._lock:
                    rows = db._conn.execute(
                        """
                        SELECT signal_id, status, mfe_percent, mae_percent,
                               take_profit, entry_price, side,
                               DATEDIFF('second', generated_at, closed_at) as duration_s
                        FROM trade_signals
                        WHERE symbol = ? AND status IN ('WIN', 'LOSS', 'TREND_REVERSAL')
                          AND closed_at >= CURRENT_TIMESTAMP - INTERVAL 7 DAY
                        """,
                        [symbol],
                    ).fetchall()

                if not rows:
                    LOGGER.info("AUTO-TUNE: No trades in last 7 days. Skipping.")
                    await asyncio.sleep(60)
                    continue

                missed_profits = 0
                quick_stops = 0

                for row in rows:
                    mfe = float(row[2] or 0) / 100.0  # Convert from percent
                    entry = float(row[5] or 0)
                    tp = float(row[4] or 0)
                    side = str(row[6]).upper()
                    duration_s = int(row[7] or 0)

                    if entry > 0 and tp > 0:
                        if side == "LONG":
                            tp_dist = (tp - entry) / entry
                        else:
                            tp_dist = (entry - tp) / entry

                        if tp_dist > 0 and mfe > C.AUTO_TUNE_MISSED_PROFIT_THRESHOLD * tp_dist:
                            missed_profits += 1

                    if str(row[1]) == "LOSS" and duration_s < C.AUTO_TUNE_QUICK_STOP_SECONDS:
                        quick_stops += 1

                tp_adj = _active_tp_adjustment
                sl_adj = _active_sl_adjustment

                if missed_profits >= C.AUTO_TUNE_TRIGGER_COUNT:
                    tp_adj = min(tp_adj + C.AUTO_TUNE_TP_INCREMENT, C.AUTO_TUNE_TP_MAX - C.TP_MULT_NORMAL)
                    LOGGER.warning("AUTO-TUNE: %d missed profits → TP adjustment +%.1f", missed_profits, tp_adj)

                if quick_stops >= C.AUTO_TUNE_TRIGGER_COUNT:
                    sl_adj = min(sl_adj + C.AUTO_TUNE_SL_INCREMENT, C.AUTO_TUNE_SL_MAX - C.SL_MULT_NORMAL)
                    LOGGER.warning("AUTO-TUNE: %d quick stops → SL adjustment +%.1f", quick_stops, sl_adj)

                _active_tp_adjustment = tp_adj
                _active_sl_adjustment = sl_adj

                # Log to crew_events
                db.store_crew_event(
                    symbol=symbol,
                    component="AutoTune",
                    event_type="auto_tune_weekly",
                    title="Weekly Auto-Tune Complete",
                    message=(
                        f"Analyzed {len(rows)} trades. "
                        f"Missed profits: {missed_profits}, Quick stops: {quick_stops}. "
                        f"TP adj: {tp_adj:+.1f}, SL adj: {sl_adj:+.1f}"
                    ),
                    payload={
                        "total_trades": len(rows),
                        "missed_profits": missed_profits,
                        "quick_stops": quick_stops,
                        "tp_adjustment": tp_adj,
                        "sl_adjustment": sl_adj,
                    },
                    status="COMPLETED",
                )

                # Store in regime_log
                try:
                    with db._lock:
                        db._conn.execute(
                            """
                            INSERT INTO regime_log (week_starting, regime, signals_count,
                                missed_profits_count, quick_stops_count, tp_multiplier, sl_multiplier)
                            VALUES (CURRENT_DATE, 'AUTO_TUNE', ?, ?, ?, ?, ?)
                            """,
                            [
                                len(rows), missed_profits, quick_stops,
                                C.TP_MULT_NORMAL + tp_adj, C.SL_MULT_NORMAL + sl_adj,
                            ],
                        )
                except Exception as e:
                    LOGGER.debug("regime_log insert failed: %s", e)

        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Auto-tune error (will retry)")

        await asyncio.sleep(60)
