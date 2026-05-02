"""
Technical Math Engine — ATR(15m), ADX(14), Microstructure Calculations.

All indicator calculations use standard Wilder's method.
Replaces the old 3-minute ATR proxy with proper 15-minute candle-based ATR.
"""

import logging
import time
from typing import Any, Dict

import requests

LOGGER = logging.getLogger(__name__)

# Cache for macro data (5 minutes) to prevent rate limits
_MACRO_CACHE: Dict[str, Any] = {}
_MACRO_CACHE_TTL = 300


def _calculate_ema(prices: list[float], period: int = 200) -> float:
    if len(prices) < period:
        return 0.0
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price * k) + (ema * (1 - k))
    return ema


def _calculate_rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) <= period:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        gains.append(change if change > 0 else 0.0)
        losses.append(abs(change) if change < 0 else 0.0)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(prices) - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calculate_atr_wilder(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> float:
    """Calculate ATR using Wilder's smoothing method."""
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        # Fallback: simple range-based estimate
        if highs and lows and closes:
            tr_list = []
            for i in range(1, len(highs)):
                tr = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
                tr_list.append(tr)
            return sum(tr_list) / len(tr_list) if tr_list else 0.0
        return 0.0

    # Calculate True Range series
    tr_list = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)

    # First ATR = simple average of first `period` TRs
    atr = sum(tr_list[:period]) / period

    # Wilder's smoothing for remaining
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period

    return atr


def _calculate_adx(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> float:
    """
    Calculate ADX(14) using Wilder's smoothing.

    Steps:
    1. Compute +DM and -DM from high/low
    2. Smooth over 14 periods using Wilder's method
    3. DI+ = 100 * smoothed +DM / ATR
    4. DI- = 100 * smoothed -DM / ATR
    5. DX = 100 * abs(DI+ - DI-) / (DI+ + DI-)
    6. ADX = smoothed DX over 14 periods
    """
    n = len(highs)
    if n < period + 2:
        return 0.0

    # Step 1: Calculate +DM, -DM, TR
    plus_dm_list = []
    minus_dm_list = []
    tr_list = []

    for i in range(1, n):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]

        plus_dm = high_diff if high_diff > low_diff and high_diff > 0 else 0.0
        minus_dm = low_diff if low_diff > high_diff and low_diff > 0 else 0.0

        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        tr_list.append(tr)

    if len(tr_list) < period:
        return 0.0

    # Step 2: Wilder's smoothing for +DM, -DM, TR
    smoothed_plus_dm = sum(plus_dm_list[:period])
    smoothed_minus_dm = sum(minus_dm_list[:period])
    smoothed_tr = sum(tr_list[:period])

    dx_list = []

    for i in range(period, len(tr_list)):
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_list[i]

        # Step 3: DI+ and DI-
        if smoothed_tr > 0:
            di_plus = 100 * smoothed_plus_dm / smoothed_tr
            di_minus = 100 * smoothed_minus_dm / smoothed_tr
        else:
            di_plus = 0.0
            di_minus = 0.0

        # Step 4: DX
        di_sum = di_plus + di_minus
        if di_sum > 0:
            dx = 100 * abs(di_plus - di_minus) / di_sum
        else:
            dx = 0.0

        dx_list.append(dx)

    if len(dx_list) < period:
        return sum(dx_list) / len(dx_list) if dx_list else 0.0

    # Step 5: Smooth DX into ADX using Wilder's method
    adx = sum(dx_list[:period]) / period
    for i in range(period, len(dx_list)):
        adx = (adx * (period - 1) + dx_list[i]) / period

    return adx


def fetch_macro_indicators(symbol: str) -> Dict[str, Any]:
    """
    Fetches:
    - 15m ATR(14) via Wilder's method
    - ADX(14) via Wilder's smoothing
    - 4H EMA 200
    - 1H RSI(14)
    - 24H VWAP
    - Current funding rate
    """
    now = time.time()
    if (
        symbol in _MACRO_CACHE
        and now - _MACRO_CACHE[symbol]["timestamp"] < _MACRO_CACHE_TTL
    ):
        return _MACRO_CACHE[symbol]["data"]

    try:
        # 1. Fetch 15m candles for ATR(14) and ADX(14) — need ~50 candles
        url_15m = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=50"
        resp_15m = requests.get(url_15m, timeout=10).json()
        highs_15m = [float(k[2]) for k in resp_15m]
        lows_15m = [float(k[3]) for k in resp_15m]
        closes_15m = [float(k[4]) for k in resp_15m]

        atr_15m = _calculate_atr_wilder(highs_15m, lows_15m, closes_15m, 14)
        adx = _calculate_adx(highs_15m, lows_15m, closes_15m, 14)

        current_price_15m = closes_15m[-1] if closes_15m else 0.0
        atr_15m_percent = atr_15m / current_price_15m if current_price_15m > 0 else 0.0

        # 2. Fetch 4H candles for EMA 200
        url_4h = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=4h&limit=250"
        resp_4h = requests.get(url_4h, timeout=10).json()
        closes_4h = [float(k[4]) for k in resp_4h]
        ema_200 = _calculate_ema(closes_4h, 200)

        # 3. Fetch 1H candles for RSI
        url_1h = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=30"
        resp_1h = requests.get(url_1h, timeout=10).json()
        closes_1h = [float(k[4]) for k in resp_1h]
        rsi_1h = _calculate_rsi(closes_1h, 14)

        # 4. 24H VWAP
        url_ticker = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
        resp_ticker = requests.get(url_ticker, timeout=10).json()
        vwap = float(resp_ticker["weightedAvgPrice"])
        current_price = float(resp_ticker["lastPrice"])

        # 5. Current funding rate from global cache
        from engine.data.binance_ingestor import funding_cache
        current_funding = float(funding_cache.get("current", 0.0))

        data = {
            "atr_15m": atr_15m,
            "atr_15m_percent": atr_15m_percent,
            "adx": adx,
            "ema_200_4h": ema_200,
            "rsi_1h": rsi_1h,
            "vwap_24h": vwap,
            "daily_vwap": vwap,  # backward compat alias
            "current_price": current_price,
            "current_funding": current_funding,
        }

        _MACRO_CACHE[symbol] = {"timestamp": now, "data": data}
        return data

    except Exception as e:
        LOGGER.error(f"Failed to fetch macro indicators: {e}")
        return {
            "atr_15m": 0.0,
            "atr_15m_percent": 0.0,
            "adx": 0.0,
            "ema_200_4h": 0.0,
            "rsi_1h": 50.0,
            "vwap_24h": 0.0,
            "daily_vwap": 0.0,
            "current_price": 0.0,
            "current_funding": 0.0,
        }


def calculate_microstructure(
    price: float,
    buffers: Dict[str, Any],
    l2_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compute advanced microstructure metrics from delta buffers and L2 data.

    Args:
        price: Current market price.
        buffers: Dict with '5s', '30s', '120s' deque buffers, each containing
                 dicts with 'delta', 'price', 'timestamp' keys.
        l2_data: Dict from l2_cache with obi_0_1pct, spread_ticks, best_bid, best_ask.
    """
    buf_5s = buffers.get("5s", [])
    buf_30s = buffers.get("30s", [])
    buf_120s = buffers.get("120s", [])

    # ── Delta calculations ──
    delta_5s = sum(t.get("delta", 0) for t in buf_5s)
    delta_30s = sum(t.get("delta", 0) for t in buf_30s)
    delta_120s = sum(t.get("delta", 0) for t in buf_120s)

    total_vol_5s = sum(abs(t.get("delta", 0)) for t in buf_5s)
    total_vol_30s = sum(abs(t.get("delta", 0)) for t in buf_30s)

    # ── NOFI calculations ──
    nofi_5s = delta_5s / total_vol_5s if total_vol_5s > 0 else 0.0
    nofi_30s = delta_30s / total_vol_30s if total_vol_30s > 0 else 0.0
    nofi_composite = (0.6 * nofi_30s) + (0.4 * nofi_5s)

    # ── Delta slope alignment ──
    delta_slope_aligned = False
    if delta_5s > 0 and delta_30s > 0 and delta_120s > 0:
        delta_slope_aligned = True
    elif delta_5s < 0 and delta_30s < 0 and delta_120s < 0:
        delta_slope_aligned = True

    # ── Displacement ──
    open_price = buf_30s[0].get("price", price) if buf_30s else price
    displacement_ticks = abs(price - open_price) / 0.01

    # ── Absorption detection ──
    absorption_detected = (abs(delta_30s) > 50) and (displacement_ticks < 5)
    absorption_resolved = False  # Managed by backbone state machine

    # ── Kyle's Lambda from 30s buffer ──
    kyles_lambda = 0.0
    if buf_30s and total_vol_30s > 0:
        prices_30s = [t.get("price", price) for t in buf_30s]
        high_30s = max(prices_30s) if prices_30s else price
        low_30s = min(prices_30s) if prices_30s else price
        kyles_lambda = (high_30s - low_30s) / total_vol_30s

    # ── L2 data ──
    obi = float(l2_data.get("obi_0_1pct", 0))
    spread_ticks = float(l2_data.get("spread_ticks", 0))
    best_bid = float(l2_data.get("best_bid", 0))
    best_ask = float(l2_data.get("best_ask", 0))

    return {
        "nofi_composite": round(nofi_composite, 4),
        "nofi_5s": round(nofi_5s, 4),
        "nofi_30s": round(nofi_30s, 4),
        "nofi": round(nofi_composite, 4),  # backward compat alias
        "delta_5s": round(delta_5s, 4),
        "delta_30s": round(delta_30s, 4),
        "delta_120s": round(delta_120s, 4),
        "total_vol_5s": round(total_vol_5s, 4),
        "total_vol_30s": round(total_vol_30s, 4),
        "delta_slope_aligned": delta_slope_aligned,
        "displacement_ticks": round(displacement_ticks, 1),
        "absorption_detected": absorption_detected,
        "absorption_resolved": absorption_resolved,
        "kyles_lambda": kyles_lambda,
        "obi": obi,
        "spread_ticks": spread_ticks,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "close": price,
    }


def estimate_liquidity_clusters(current_price: float, atr_pct: float) -> list[dict]:
    """
    Mock/Heuristic generation of "Liquidity Heatmap" magnets.
    In real life this comes from Hyblock or Coinglass.
    Here we generate synthetic clusters at whole numbers or 1.5x ATR away.
    """
    # Create fake liquidity clusters above and below price
    magnet_up = current_price * (1 + (atr_pct * 2.5))
    magnet_down = current_price * (1 - (atr_pct * 2.5))

    # Snap to nearest 50 for realistic "magnet" feel for BTC
    magnet_up = round(magnet_up / 50.0) * 50.0
    magnet_down = round(magnet_down / 50.0) * 50.0

    return [
        {
            "side": "SHORT_LIQS",
            "price": magnet_up,
            "distance_pct": ((magnet_up - current_price) / current_price) * 100,
        },
        {
            "side": "LONG_LIQS",
            "price": magnet_down,
            "distance_pct": ((current_price - magnet_down) / current_price) * 100,
        },
    ]
