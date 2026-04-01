import logging
import time
from typing import Any, Dict
import requests

LOGGER = logging.getLogger(__name__)

# Cache for macro data (e.g. 5 minutes) to prevent rate limits
_MACRO_CACHE = {}
_MACRO_CACHE_TTL = 300


def _calculate_ema(prices: list[float], period: int = 200) -> float:
    if len(prices) < period:
        return 0.0
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period  # type: ignore
    for price in prices[period:]:  # type: ignore
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

    avg_gain = sum(gains[:period]) / period  # type: ignore
    avg_loss = sum(losses[:period]) / period  # type: ignore

    for i in range(period, len(prices) - 1):
        avg_gain = (avg_gain * 13 + gains[i]) / 14
        avg_loss = (avg_loss * 13 + losses[i]) / 14

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def fetch_macro_indicators(symbol: str) -> Dict[str, Any]:
    """
    Fetches 4H EMA 200, 1H RSI, and 24H VWAP.
    Caches the results to prevent spamming Binance REST API.
    """
    now = time.time()
    if (
        symbol in _MACRO_CACHE
        and now - _MACRO_CACHE[symbol]["timestamp"] < _MACRO_CACHE_TTL
    ):
        return _MACRO_CACHE[symbol]["data"]

    try:
        # 1. Fetch 4H candles for EMA 200
        # Need at least ~250 for stabilized EMA 200
        url_4h = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=4h&limit=250"
        resp_4h = requests.get(url_4h, timeout=10).json()
        closes_4h = [float(k[4]) for k in resp_4h]
        ema_200 = _calculate_ema(closes_4h, 200)

        # 2. Fetch 1H candles for RSI
        url_1h = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=30"
        resp_1h = requests.get(url_1h, timeout=10).json()
        closes_1h = [float(k[4]) for k in resp_1h]
        rsi_1h = _calculate_rsi(closes_1h, 14)

        # 3. 24H VWAP (Rolling 24hr Volume Weighted Average Price)
        # We can use the 24hr ticker endpoint because VWAP = quoteVolume / volume
        url_ticker = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
        resp_ticker = requests.get(url_ticker, timeout=10).json()
        vwap = float(resp_ticker["weightedAvgPrice"])

        current_price = float(resp_ticker["lastPrice"])

        data = {
            "ema_200_4h": ema_200,
            "rsi_1h": rsi_1h,
            "daily_vwap": vwap,
            "current_price": current_price,
        }

        _MACRO_CACHE[symbol] = {"timestamp": now, "data": data}
        return data

    except Exception as e:
        LOGGER.error(f"Failed to fetch macro indicators: {e}")
        # Return safe defaults if API fails
        return {
            "ema_200_4h": 0.0,
            "rsi_1h": 50.0,
            "daily_vwap": 0.0,
            "current_price": 0.0,
        }


def calculate_microstructure(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """
    Computes advanced tick-derived math requested in the prompt:
    - NOFI (Normalized Order Flow Imbalance)
    - Cumulative Delta (already volume_delta)
    - Kyle's Lambda (Price impact indicator)
    - Displacement (Price moves relative to tick count)
    - ATR (Estimated from High-Low of the lookback window)
    """
    taker_buy = float(snapshot.get("taker_buy_volume", 0.0))
    taker_sell = float(snapshot.get("taker_sell_volume", 0.0))
    high = float(snapshot.get("highest_price", 0.0))
    low = float(snapshot.get("lowest_price", 0.0))
    open_p = float(snapshot.get("oldest_price", 0.0))
    close_p = float(snapshot.get("last_price", 0.0))

    total_volume = taker_buy + taker_sell
    delta = taker_buy - taker_sell

    # 1. NOFI: Scaled from -1 to 1 (This is the same as the old imbalance, but explicit)
    nofi = (delta / total_volume) if total_volume > 0 else 0.0

    # 2. Kyle's Lambda: (P_high - P_low) / total_volume -> measures illiquidity/price impact
    lambda_kyle = ((high - low) / total_volume) if total_volume > 0 else 0.0

    # 3. ATR proxy (3-minute equivalent based on 60s window extrapolated)
    # Using true range of the current window.
    tr = high - low
    atr_1m = (tr / close_p) if close_p > 0 else 0.001
    atr_3m = (
        atr_1m * 1.732
    )  # approx scaling by sqrt(3) since volatility scales with sqrt(time)

    # 4. Price displacement in ticks
    # Binance BTC ticks are $0.01.
    displacement = abs(close_p - open_p)
    displacement_ticks = displacement / 0.01  # 1 tick = $0.01

    return {
        "nofi": float(f"{nofi:.4f}"),
        "cumulative_delta": float(f"{delta:.4f}"),
        "kyles_lambda": lambda_kyle,
        "displacement_ticks": displacement_ticks,
        "atr_1m_pct": float(f"{atr_1m:.6f}"),
        "atr_3m_pct": float(f"{atr_3m:.6f}"),
        "high": high,
        "low": low,
        "close": close_p,
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
