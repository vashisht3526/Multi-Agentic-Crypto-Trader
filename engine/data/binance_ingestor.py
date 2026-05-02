"""
Binance Tick Ingestion Agent — Trade stream, L2 Depth, Funding Rate, Delta Buffers.

Manages three parallel data streams:
1. @trade stream — Individual trades → DuckDB + delta buffers
2. @depth@100ms — L2 order book → in-memory l2_cache + periodic DB snapshots
3. Funding rate polling — REST API every 300s → in-memory funding_cache
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
from typing import Any

import aiohttp
import websockets

from engine.database import FastTickDatabase

LOGGER = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
# GLOBAL IN-MEMORY CACHES (accessed by backbone and math engine)
# ══════════════════════════════════════════════════════════════════════

l2_cache: dict[str, Any] = {
    "best_bid": 0.0,
    "best_ask": 0.0,
    "mid_price": 0.0,
    "bid_vol_0_1pct": 0.0,
    "ask_vol_0_1pct": 0.0,
    "obi_0_1pct": 0.0,
    "spread_ticks": 0.0,
    "last_update": 0.0,
}

funding_cache: dict[str, Any] = {
    "current": 0.0,
    "last_update": 0.0,
}

# Delta buffers: each entry is {"delta": float, "price": float, "timestamp": float}
delta_buffer_5s: collections.deque = collections.deque(maxlen=500)
delta_buffer_30s: collections.deque = collections.deque(maxlen=3000)
delta_buffer_120s: collections.deque = collections.deque(maxlen=12000)

# L2 snapshot batch buffer for periodic DB writes
_l2_snapshot_buffer: list[dict[str, Any]] = []
_l2_snapshot_lock = asyncio.Lock() if asyncio.get_event_loop().is_running() else None

# Internal book state for depth stream
_order_book: dict[str, Any] = {"bids": {}, "asks": {}, "initialized": False}


class BinanceTickIngestionAgent:
    def __init__(
        self,
        symbol: str,
        db: FastTickDatabase,
        reconnect_backoff_seconds: int = 5,
    ) -> None:
        self.symbol = symbol.upper()
        self.db = db
        self.reconnect_backoff_seconds = reconnect_backoff_seconds
        self.stream_url = f"wss://stream.binance.com:9443/ws/{self.symbol.lower()}@trade"
        self._running = True
        self._heartbeat_event_seconds = 5
        self._heartbeat_batch_ticks = 250
        self._ticks_since_event = 0
        self._last_event_monotonic = time.monotonic()
        self._last_trade_payload: dict[str, Any] = {}

    def stop(self) -> None:
        self._running = False

    async def run_forever(self) -> None:
        backoff = self.reconnect_backoff_seconds
        while self._running:
            try:
                async with websockets.connect(
                    self.stream_url,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=8192,
                ) as socket:
                    LOGGER.info("Connected to Binance stream: %s", self.stream_url)
                    self.db.store_crew_event(
                        symbol=self.symbol,
                        component="Binance Ingestor",
                        event_type="stream_connected",
                        title="Binance stream connected",
                        message=f"Connected to {self.stream_url}.",
                        payload={"stream_url": self.stream_url},
                        status="COMPLETED",
                    )
                    backoff = self.reconnect_backoff_seconds
                    async for raw_message in socket:
                        if not self._running:
                            self._emit_heartbeat(force=True)
                            break
                        payload = json.loads(raw_message)
                        trade = self._parse_trade_message(payload)
                        if trade is None:
                            continue
                        self.db.insert_tick(trade)

                        # ── Populate delta buffers ──
                        is_taker_buy = not trade["is_buyer_maker"]
                        delta = trade["qty"] if is_taker_buy else -trade["qty"]
                        tick_entry = {
                            "delta": delta,
                            "price": trade["price"],
                            "timestamp": trade["trade_time_ms"] / 1000.0,
                        }
                        delta_buffer_5s.append(tick_entry)
                        delta_buffer_30s.append(tick_entry)
                        delta_buffer_120s.append(tick_entry)

                        self._ticks_since_event += 1
                        self._last_trade_payload = {
                            "trade_id": trade["trade_id"],
                            "price": trade["price"],
                            "qty": trade["qty"],
                            "trade_time_ms": trade["trade_time_ms"],
                        }
                        self._emit_heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning("Binance stream disconnected: %s", exc)
                self.db.store_crew_event(
                    symbol=self.symbol,
                    component="Binance Ingestor",
                    event_type="stream_disconnected",
                    title="Binance stream disconnected",
                    message=str(exc),
                    payload={"stream_url": self.stream_url, "backoff_seconds": backoff},
                    status="WARN",
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _emit_heartbeat(self, force: bool = False) -> None:
        now = time.monotonic()
        if self._ticks_since_event <= 0:
            return
        if not force:
            if self._ticks_since_event < self._heartbeat_batch_ticks and (
                now - self._last_event_monotonic
            ) < self._heartbeat_event_seconds:
                return

        payload = {
            "ticks_ingested": self._ticks_since_event,
            **self._last_trade_payload,
        }
        self.db.store_crew_event(
            symbol=self.symbol,
            component="Binance Ingestor",
            event_type="ingestion_heartbeat",
            title="Binance ingestion heartbeat",
            message=f"Ingested {self._ticks_since_event} ticks since the last heartbeat.",
            payload=payload,
            status="INFO",
        )
        self._ticks_since_event = 0
        self._last_event_monotonic = now

    def _parse_trade_message(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if payload.get("e") != "trade":
            return None
        try:
            price = float(payload["p"])
            qty = float(payload["q"])
            return {
                "symbol": payload["s"],
                "trade_id": int(payload["t"]),
                "price": price,
                "qty": qty,
                "quote_qty": price * qty,
                "trade_time_ms": int(payload["T"]),
                "event_time_ms": int(payload["E"]),
                "is_buyer_maker": bool(payload["m"]),
            }
        except (KeyError, TypeError, ValueError):
            LOGGER.debug("Skipping malformed trade payload: %s", payload)
            return None


# ══════════════════════════════════════════════════════════════════════
# L2 DEPTH STREAM
# ══════════════════════════════════════════════════════════════════════

async def run_l2_depth_stream(db: FastTickDatabase, symbol: str = "BTCUSDT") -> None:
    """
    Parallel WebSocket connection to Binance L2 depth stream.
    URI: wss://stream.binance.com:9443/ws/btcusdt@depth@100ms

    Parses top 5 bid/ask levels. Updates global l2_cache every message.
    Batches l2_snapshots to DuckDB every 1 second.
    """
    global _order_book
    depth_url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@depth@100ms"
    backoff = 5

    while True:
        try:
            # First, get a snapshot to initialize the order book
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit=10",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        snapshot = await resp.json()
                        _order_book["bids"] = {
                            float(b[0]): float(b[1]) for b in snapshot.get("bids", [])
                        }
                        _order_book["asks"] = {
                            float(a[0]): float(a[1]) for a in snapshot.get("asks", [])
                        }
                        _order_book["initialized"] = True
                        LOGGER.info("L2 order book initialized with %d bids, %d asks",
                                    len(_order_book["bids"]), len(_order_book["asks"]))
            except Exception as e:
                LOGGER.warning("Failed to fetch initial depth snapshot: %s", e)
                _order_book["initialized"] = False

            async with websockets.connect(
                depth_url,
                ping_interval=20,
                ping_timeout=20,
                max_queue=4096,
            ) as socket:
                LOGGER.info("Connected to L2 depth stream: %s", depth_url)
                backoff = 5

                # Start batch writer task
                writer_task = asyncio.create_task(
                    _l2_batch_writer(db), name="l2_batch_writer"
                )

                try:
                    async for raw_message in socket:
                        payload = json.loads(raw_message)
                        _process_depth_update(payload)
                finally:
                    writer_task.cancel()
                    try:
                        await writer_task
                    except asyncio.CancelledError:
                        pass

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.warning("L2 depth stream disconnected: %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def _process_depth_update(payload: dict[str, Any]) -> None:
    """Process a depth update message and update global l2_cache."""
    global l2_cache

    # Update order book with incremental data
    for bid in payload.get("b", []):
        price = float(bid[0])
        qty = float(bid[1])
        if qty == 0:
            _order_book["bids"].pop(price, None)
        else:
            _order_book["bids"][price] = qty

    for ask in payload.get("a", []):
        price = float(ask[0])
        qty = float(ask[1])
        if qty == 0:
            _order_book["asks"].pop(price, None)
        else:
            _order_book["asks"][price] = qty

    # Extract top 5 levels
    bids = sorted(_order_book["bids"].items(), key=lambda x: x[0], reverse=True)[:5]
    asks = sorted(_order_book["asks"].items(), key=lambda x: x[0])[:5]

    if not bids or not asks:
        return

    best_bid = bids[0][0]
    best_ask = asks[0][0]
    mid_price = (best_bid + best_ask) / 2

    # Calculate volumes within 0.1% of mid price
    bid_vol_0_1pct = sum(
        qty for px, qty in _order_book["bids"].items()
        if (mid_price - px) <= (mid_price * 0.001)
    )
    ask_vol_0_1pct = sum(
        qty for px, qty in _order_book["asks"].items()
        if (px - mid_price) <= (mid_price * 0.001)
    )

    # OBI
    denom = bid_vol_0_1pct + ask_vol_0_1pct
    obi = (bid_vol_0_1pct - ask_vol_0_1pct) / denom if denom > 0 else 0.0

    # Spread in ticks ($0.01 per tick for BTC)
    spread_ticks = (best_ask - best_bid) / 0.01

    # Update global cache
    l2_cache = {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid_price": mid_price,
        "bid_vol_0_1pct": round(bid_vol_0_1pct, 4),
        "ask_vol_0_1pct": round(ask_vol_0_1pct, 4),
        "obi_0_1pct": round(obi, 4),
        "spread_ticks": round(spread_ticks, 1),
        "last_update": time.time(),
    }

    # Buffer for batch DB write
    _l2_snapshot_buffer.append(dict(l2_cache))


async def _l2_batch_writer(db: FastTickDatabase) -> None:
    """Write buffered L2 snapshots to DB every 1 second."""
    global _l2_snapshot_buffer
    while True:
        await asyncio.sleep(1.0)
        if not _l2_snapshot_buffer:
            continue
        # Take the latest snapshot only (don't flood DB)
        batch = [_l2_snapshot_buffer[-1]]
        _l2_snapshot_buffer.clear()
        try:
            await asyncio.to_thread(db.batch_insert_l2_snapshots, batch)
        except Exception as e:
            LOGGER.debug("L2 batch write error: %s", e)


# ══════════════════════════════════════════════════════════════════════
# FUNDING RATE POLLING
# ══════════════════════════════════════════════════════════════════════

async def run_funding_rate_poller(symbol: str = "BTCUSDT") -> None:
    """
    Every 300 seconds, fetch current funding rate from Binance Futures API.
    Store in global funding_cache['current'].
    """
    global funding_cache
    url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=1"

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    data = await resp.json()
                    if data and isinstance(data, list) and len(data) > 0:
                        rate = float(data[0].get("fundingRate", 0))
                        funding_cache["current"] = rate
                        funding_cache["last_update"] = time.time()
                        LOGGER.info("Funding rate updated: %.6f%%", rate * 100)
                    else:
                        LOGGER.debug("No funding rate data returned")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            LOGGER.warning("Funding rate fetch failed: %s", e)

        await asyncio.sleep(300)
