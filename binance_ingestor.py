from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import websockets

from database import FastTickDatabase


LOGGER = logging.getLogger(__name__)


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
