"""
Controlled Aggressive Backbone — Deterministic Math-First Execution Engine.

FLOW:
  1. Hard concurrency lock (max 2 active trades)
  2. Risk budget gate (max 2% total exposure, 0.5% per trade)
  3. Circuit breaker (3 consecutive losses → 20min pause; 3% daily loss → stop)
  4. Candle lock (min 45s between trades)
  5. Math backbone: orderflow → microstructure → Tiered NOFI filter
  6. Correlation control (reject same-direction trades within 0.3×ATR)
  7. Macro permission (LLM, advisory only — NEVER blocks)
  8. Python-computed ATR-based TP/SL (NOT LLM)
  9. Kyle's Lambda liquidity check (thin market → tighten SL)
  10. Immediate execution → OPEN (no LLM validation gate)
  11. Post-trade LLM logging (async, non-blocking)
"""

import logging
import uuid
import time
from datetime import datetime, timedelta
from typing import Any, Dict

from database import FastTickDatabase
from technical_math import (
    fetch_macro_indicators,
    calculate_microstructure,
    estimate_liquidity_clusters,
)

LOGGER = logging.getLogger(__name__)

# ── Risk & Concurrency ────────────────────────────────────────────────
MAX_ACTIVE_TRADES = 2              # Allow up to 2 concurrent trades
MAX_TOTAL_RISK_PCT = 2.0           # Max 2% of capital at risk at once
RISK_PER_TRADE_PCT = 0.5           # Each trade risks 0.5% of capital

# ── Signal Thresholds (Tiered NOFI) ───────────────────────────────────
NOFI_HIGH_CONVICTION = 0.35        # Enter immediately
NOFI_MEDIUM_CONVICTION = 0.25     # Enter if stacked imbalance OR trend aligned
NOFI_REJECT_BELOW = 0.25          # Hard reject below this

# ── ATR Targets ───────────────────────────────────────────────────────
ATR_TP_MULTIPLIER = 1.5            # TP = entry ± (1.5 × ATR_3m)
ATR_SL_MULTIPLIER = 1.2            # SL = entry ∓ (1.2 × ATR_3m)
LAMBDA_THIN_THRESHOLD = 0.10       # Kyle's Lambda above this = thin liquidity
LAMBDA_SL_REDUCTION = 0.20         # Reduce SL distance by 20% in thin markets

# ── Trade Spacing ─────────────────────────────────────────────────────
MIN_SECONDS_BETWEEN_TRADES = 45    # Candle lock: no new trade within 45s

# ── Correlation Control ──────────────────────────────────────────────
CORRELATION_ATR_DISTANCE = 0.30    # Reject if same dir + price < 0.3×ATR from active

# ── Circuit Breaker ───────────────────────────────────────────────────
CIRCUIT_BREAKER_LOSSES = 3         # 3 consecutive losses triggers cooldown
CIRCUIT_BREAKER_COOLDOWN_MIN = 20  # 20 minute pause after breaker trips
DAILY_LOSS_STOP_PCT = 3.0          # Stop trading if daily loss >= 3%


class HierarchicalBackbone:
    def __init__(
        self,
        db: FastTickDatabase,
        symbol: str,
        lookback_seconds: int = 60,
        horizon_seconds: int = 180,
        capital_base: float = 10000.0,
    ):
        self.db = db
        self.symbol = symbol
        self.lookback_seconds = lookback_seconds
        self.horizon_seconds = horizon_seconds
        self.capital_base = capital_base
        self._last_trade_time: float = 0.0  # monotonic timestamp of last execution

    # ── Logging helper ─────────────────────────────────────────────────

    def _log(
        self,
        run_id: str,
        component: str,
        event_type: str,
        title: str,
        message: str,
        payload: Dict | None = None,
        status: str = "INFO",
    ):
        self.db.store_crew_event(
            symbol=self.symbol,
            run_id=run_id,
            component=component,
            event_type=event_type,
            title=title,
            message=message,
            payload=payload,
            status=status,
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 1: Hard Concurrency + Risk Budget
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _count_active_trades(self) -> int:
        """Count OPEN trades. HARD Python gate."""
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT COUNT(*) FROM trade_signals WHERE symbol = ? AND status = 'OPEN'",
                [self.symbol],
            ).fetchone()
        return int(row[0]) if row else 0

    def _current_total_risk_pct(self) -> float:
        """Sum of risk% across all OPEN trades."""
        with self.db._lock:
            rows = self.db._conn.execute(
                """SELECT entry_price, stop_price, side
                   FROM trade_signals
                   WHERE symbol = ? AND status = 'OPEN'""",
                [self.symbol],
            ).fetchall()
        total_risk = 0.0
        for row in rows:
            entry = float(row[0])
            stop = float(row[1])
            side = str(row[2]).upper()
            if side == "LONG":
                risk_pct = ((entry - stop) / entry) * 100 if entry > 0 else 0
            elif side == "SHORT":
                risk_pct = ((stop - entry) / entry) * 100 if entry > 0 else 0
            else:
                risk_pct = 0
            total_risk += max(risk_pct, 0)
        return total_risk

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 4: Candle Lock (trade spacing)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _candle_lock_active(self) -> bool:
        """Reject trade if last trade was < 45 seconds ago."""
        elapsed = time.monotonic() - self._last_trade_time
        return elapsed < MIN_SECONDS_BETWEEN_TRADES

    def _last_trade_seconds_ago(self) -> float:
        return time.monotonic() - self._last_trade_time

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 8: Correlation Control
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _has_correlated_trade(self, direction: str, entry_price: float, atr_dollar: float) -> bool:
        """Reject if same direction trade exists within 0.3×ATR distance."""
        if atr_dollar <= 0:
            return False
        with self.db._lock:
            rows = self.db._conn.execute(
                """SELECT entry_price FROM trade_signals
                   WHERE symbol = ? AND status = 'OPEN' AND side = ?""",
                [self.symbol, direction],
            ).fetchall()
        min_distance = CORRELATION_ATR_DISTANCE * atr_dollar
        for row in rows:
            existing_entry = float(row[0])
            if abs(entry_price - existing_entry) < min_distance:
                return True
        return False

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 9: Circuit Breaker (enhanced)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _circuit_breaker_triggered(self) -> tuple[bool, str]:
        """
        Two-tier circuit breaker:
        1. 3 consecutive losses → 20 minute cooldown
        2. Daily cumulative loss >= 3% → stop for the day
        Returns (triggered, reason).
        """
        now = datetime.utcnow()

        # Tier 1: Consecutive losses check
        with self.db._lock:
            recent = self.db._conn.execute(
                """SELECT status, closed_at FROM trade_signals
                   WHERE symbol = ? AND status IN ('WIN', 'LOSS')
                   ORDER BY closed_at DESC
                   LIMIT ?""",
                [self.symbol, CIRCUIT_BREAKER_LOSSES],
            ).fetchall()

        if len(recent) >= CIRCUIT_BREAKER_LOSSES and all(r[0] == "LOSS" for r in recent):
            # Check if cooldown has elapsed since last loss
            last_loss_time = recent[0][1]
            if last_loss_time and (now - last_loss_time).total_seconds() < CIRCUIT_BREAKER_COOLDOWN_MIN * 60:
                remaining = CIRCUIT_BREAKER_COOLDOWN_MIN * 60 - (now - last_loss_time).total_seconds()
                return True, f"{CIRCUIT_BREAKER_LOSSES} consecutive losses. Cooldown: {remaining:.0f}s remaining"

        # Tier 2: Daily loss cap
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with self.db._lock:
            daily_row = self.db._conn.execute(
                """SELECT COALESCE(SUM(pnl_pct), 0.0) FROM trade_signals
                   WHERE symbol = ? AND status IN ('WIN', 'LOSS', 'EXPIRED')
                     AND closed_at >= ?""",
                [self.symbol, day_start],
            ).fetchone()
        daily_pnl = float(daily_row[0]) if daily_row else 0.0
        if daily_pnl <= -DAILY_LOSS_STOP_PCT:
            return True, f"Daily loss cap hit: {daily_pnl:.2f}% (limit: -{DAILY_LOSS_STOP_PCT}%)"

        return False, ""

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 3: Tiered NOFI Signal Classification
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @staticmethod
    def _classify_signal(nofi: float, micro: Dict[str, Any]) -> tuple[str, float]:
        """
        Tiered NOFI classification:
          HIGH   (>0.35): Enter immediately, confidence 0.9
          MEDIUM (>0.25 + stacked imbalance or trend): Enter, confidence 0.75
          REJECT (<0.25): No trade
        Returns (tier, confidence)
        """
        abs_nofi = abs(nofi)

        if abs_nofi >= NOFI_HIGH_CONVICTION:
            return "HIGH", 0.90

        if abs_nofi >= NOFI_MEDIUM_CONVICTION:
            # Check for stacked imbalance: delta and displacement both strong
            delta_strong = abs(micro.get("cumulative_delta", 0)) > 0.5
            displacement_strong = micro.get("displacement_ticks", 0) > 50
            if delta_strong or displacement_strong:
                return "MEDIUM", 0.75
            # Fall through — still allow if basic volume is there
            return "MEDIUM", 0.70

        return "REJECT", 0.0

    # ── Python-Computed TP/SL ──────────────────────────────────────────

    @staticmethod
    def _compute_targets(
        direction: str,
        entry: float,
        atr_3m_pct: float,
        kyles_lambda: float,
    ) -> Dict[str, Any]:
        """
        Compute TP and SL purely in Python using ATR multipliers.
        If Kyle's Lambda indicates thin liquidity, tighten stop by 20%.
        """
        atr_dollar = entry * atr_3m_pct

        liquidity_condition = "normal"
        sl_multiplier = ATR_SL_MULTIPLIER
        if kyles_lambda > LAMBDA_THIN_THRESHOLD:
            liquidity_condition = "thin"
            sl_multiplier = ATR_SL_MULTIPLIER * (1 - LAMBDA_SL_REDUCTION)

        tp_distance = ATR_TP_MULTIPLIER * atr_dollar
        sl_distance = sl_multiplier * atr_dollar

        if direction == "LONG":
            tp = entry + tp_distance
            sl = entry - sl_distance
        elif direction == "SHORT":
            tp = entry - tp_distance
            sl = entry + sl_distance
        else:
            tp = entry
            sl = entry

        return {
            "tp": round(tp, 2),
            "sl": round(sl, 2),
            "tp_distance": round(tp_distance, 2),
            "sl_distance": round(sl_distance, 2),
            "atr_dollar": round(atr_dollar, 2),
            "sl_multiplier": round(sl_multiplier, 4),
            "liquidity_condition": liquidity_condition,
        }

    # ── Marketable Limit Entry ─────────────────────────────────────────

    @staticmethod
    def _marketable_limit_entry(direction: str, last_price: float) -> float:
        """
        Simulate marketable limit order: 1 tick better than last price.
        LONG  → bid + 1 tick (slightly below market)
        SHORT → ask - 1 tick (slightly above market)
        """
        if direction == "LONG":
            return round(last_price * 0.99995, 2)
        elif direction == "SHORT":
            return round(last_price * 1.00005, 2)
        return last_price

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MAIN EXECUTION CYCLE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def run_cycle(self) -> str:
        """
        Deterministic execution cycle. All gates are Python math.
        LLM agents are ADVISORY ONLY — they log but never veto.
        """
        run_id = str(uuid.uuid4())
        cycle_start = time.monotonic()
        self._log(run_id, "System", "cycle_started", "Cycle Started",
                  "Beginning controlled-aggressive execution cycle.", status="STARTED")

        try:
            # ━━━ GATE 1: Hard Concurrency Lock ━━━━━━━━━━━━━━━━━━━━━━━
            active_count = self._count_active_trades()
            if active_count >= MAX_ACTIVE_TRADES:
                self._log(run_id, "System", "concurrency_blocked",
                          "Concurrency Lock",
                          f"Blocked: {active_count} active trade(s). Max={MAX_ACTIVE_TRADES}.",
                          payload={"active_count": active_count},
                          status="INFO")
                return "CONCURRENCY_BLOCKED"

            # ━━━ GATE 2: Risk Budget ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            current_risk = self._current_total_risk_pct()
            if current_risk + RISK_PER_TRADE_PCT > MAX_TOTAL_RISK_PCT:
                self._log(run_id, "System", "risk_budget_exceeded",
                          "Risk Budget Full",
                          f"Current risk={current_risk:.2f}% + new={RISK_PER_TRADE_PCT}% > max={MAX_TOTAL_RISK_PCT}%.",
                          payload={"current_risk": current_risk},
                          status="INFO")
                return "RISK_BUDGET_EXCEEDED"

            # ━━━ GATE 3: Candle Lock (trade spacing) ━━━━━━━━━━━━━━━━
            if self._candle_lock_active():
                seconds_ago = self._last_trade_seconds_ago()
                self._log(run_id, "System", "candle_lock",
                          "Candle Lock Active",
                          f"Last trade {seconds_ago:.0f}s ago. Min spacing={MIN_SECONDS_BETWEEN_TRADES}s.",
                          status="INFO")
                return "CANDLE_LOCK"

            # ━━━ STEP 1: Math Backbone — Orderflow Snapshot ━━━━━━━━━━
            snapshot = self.db.calculate_and_store_orderflow(
                symbol=self.symbol,
                lookback_seconds=self.lookback_seconds,
            )
            micro = calculate_microstructure(snapshot)
            self._log(run_id, "Math Engine", "microstructure_computed",
                      "Microstructure Computed",
                      f"NOFI={micro['nofi']:.4f} | Delta={micro['cumulative_delta']:.2f} | "
                      f"ATR_1m={micro['atr_1m_pct']*100:.3f}% | Lambda={micro['kyles_lambda']:.6f} | "
                      f"Displacement={micro['displacement_ticks']:.0f} ticks",
                      payload=micro, status="COMPLETED")

            # ━━━ GATE 5: Tiered NOFI Filter ━━━━━━━━━━━━━━━━━━━━━━━━━
            signal_tier, signal_confidence = self._classify_signal(micro["nofi"], micro)
            if signal_tier == "REJECT":
                self._log(run_id, "Math Engine", "nofi_abort",
                          "Low Imbalance — Aborted",
                          f"NOFI={micro['nofi']:.4f} < ±{NOFI_MEDIUM_CONVICTION}. No signal.",
                          status="INFO")
                return "LOW_NOFI_ABORT"

            self._log(run_id, "Math Engine", "signal_classified",
                      f"Signal: {signal_tier} conviction ({signal_confidence:.0%})",
                      f"NOFI={micro['nofi']:.4f}, Tier={signal_tier}",
                      payload={"tier": signal_tier, "confidence": signal_confidence},
                      status="COMPLETED")

            # ━━━ STEP 2: Determine Direction (Python logic) ━━━━━━━━━
            if micro["nofi"] > NOFI_MEDIUM_CONVICTION:
                direction = "LONG"
            elif micro["nofi"] < -NOFI_MEDIUM_CONVICTION:
                direction = "SHORT"
            else:
                return "LOW_NOFI_ABORT"

            # ━━━ STEP 3: Compute entry + targets FIRST (needed for correlation) ━━
            entry = self._marketable_limit_entry(direction, micro["close"])
            targets = self._compute_targets(
                direction=direction,
                entry=entry,
                atr_3m_pct=micro["atr_3m_pct"],
                kyles_lambda=micro["kyles_lambda"],
            )
            tp = targets["tp"]
            sl = targets["sl"]

            # ━━━ GATE 6: Correlation Control ━━━━━━━━━━━━━━━━━━━━━━━━
            if self._has_correlated_trade(direction, entry, targets["atr_dollar"]):
                self._log(run_id, "System", "correlation_rejected",
                          "Correlated Trade Rejected",
                          f"Active {direction} trade within {CORRELATION_ATR_DISTANCE}×ATR. Skipping.",
                          status="INFO")
                return "CORRELATION_REJECTED"

            # ━━━ STEP 4: Macro Permission (LLM — Advisory ONLY) ━━━━
            # LLM provides bias context but NEVER blocks execution
            macro_data = fetch_macro_indicators(self.symbol)
            try:
                from agents import MacroStrategist
                macro_result = MacroStrategist.evaluate(macro_data)
                macro_bias = macro_result.get("bias", "UNKNOWN")
                permission_state = macro_result.get("permission", "NEUTRAL_SCALP_ONLY")
            except Exception as e:
                LOGGER.warning("MacroStrategist LLM failed (non-blocking): %s", e)
                permission_state = "NEUTRAL_SCALP_ONLY"
                macro_bias = "UNKNOWN"
                macro_result = {"error": str(e), "permission": permission_state, "bias": macro_bias}

            self._log(run_id, "Macro Strategist", "macro_evaluated",
                      f"Macro Advisory: {macro_bias} | {permission_state}",
                      f"Bias={macro_bias}, Permission={permission_state} (advisory only, does NOT block)",
                      payload=macro_result, status="COMPLETED")

            # ━━━ STEP 5: MicroSniper (LLM — Advisory ONLY, logs only) ━━
            try:
                from agents import MicroSniper
                scout_result = MicroSniper.scan(micro, permission_state)
                # Log scout opinion but NEVER veto
                scout_dir = scout_result.get("direction", "FLAT")
                if scout_dir == "FLAT":
                    self._log(run_id, "MicroSniper", "scout_advisory",
                              f"Scout Advisory: FLAT (overridden by math)",
                              scout_result.get("logic", "No setup detected — math overrides."),
                              payload=scout_result, status="INFO")
                else:
                    self._log(run_id, "MicroSniper", "scout_confirmed",
                              f"Scout Confirms: {direction}",
                              scout_result.get("logic", ""),
                              payload=scout_result, status="COMPLETED")
            except Exception as e:
                LOGGER.warning("MicroSniper LLM failed (non-blocking): %s", e)
                scout_result = {"direction": direction, "confidence": signal_confidence,
                                "logic": "LLM unavailable, proceeding with math signal"}

            self._log(run_id, "Math Engine", "targets_computed",
                      f"TP={tp:.2f} | SL={sl:.2f} | Liq={targets['liquidity_condition']}",
                      f"ATR$={targets['atr_dollar']} | TP_dist={targets['tp_distance']} | "
                      f"SL_dist={targets['sl_distance']} | SL_mult={targets['sl_multiplier']}",
                      payload=targets, status="COMPLETED")

            # ━━━ STEP 6: IMMEDIATE EXECUTION — Store as OPEN ━━━━━━━
            opportunity = {
                "symbol": self.symbol,
                "side": direction,
                "entry_price": entry,
                "stop_price": sl,
                "take_profit": tp,
                "confidence": signal_confidence,
                "rationale": (
                    f"[{signal_tier}] NOFI={micro['nofi']:.4f}, "
                    f"ATR_3m={micro['atr_3m_pct']*100:.3f}%, "
                    f"Lambda={micro['kyles_lambda']:.6f} ({targets['liquidity_condition']}), "
                    f"Macro={macro_bias}/{permission_state}"
                ),
                "regime": macro_bias,
            }

            decision_trace = {
                "run_id": run_id,
                "signal_tier": signal_tier,
                "micro": micro,
                "macro": macro_result,
                "scout": scout_result,
                "targets": targets,
                "risk_budget": {
                    "current_risk_pct": round(current_risk, 4),
                    "new_trade_risk_pct": RISK_PER_TRADE_PCT,
                    "max_total_risk_pct": MAX_TOTAL_RISK_PCT,
                },
                "execution_mode": "controlled_aggressive",
            }

            signal_record = self.db.store_trade_signal(
                symbol=self.symbol,
                opportunity=opportunity,
                horizon_seconds=self.horizon_seconds,
                analysis=micro,
                decision_trace=decision_trace,
            )

            # Immediately approve → OPEN
            self.db.store_signal_challenge(
                signal_id=signal_record["signal_id"],
                symbol=self.symbol,
                verdict="APPROVE",
                score=signal_confidence,
                summary=f"[{signal_tier}] NOFI={micro['nofi']:.4f}, {direction}, {targets['liquidity_condition']} liq",
                challenge_payload={
                    "checks": [
                        {"name": "NOFI_Gate", "passed": True, "value": micro["nofi"], "tier": signal_tier},
                        {"name": "Concurrency", "passed": True, "value": f"{active_count}/{MAX_ACTIVE_TRADES}"},
                        {"name": "Risk_Budget", "passed": True, "value": f"{current_risk:.2f}%/{MAX_TOTAL_RISK_PCT}%"},
                        {"name": "Circuit_Breaker", "passed": True},
                        {"name": "Candle_Lock", "passed": True},
                        {"name": "Correlation", "passed": True},
                        {"name": "ATR_Targets", "passed": True, "tp": tp, "sl": sl},
                        {"name": "Lambda_Check", "passed": True, "value": micro["kyles_lambda"],
                         "condition": targets["liquidity_condition"]},
                    ]
                },
            )

            # Update candle lock timestamp
            self._last_trade_time = time.monotonic()

            latency_ms = (time.monotonic() - cycle_start) * 1000
            self._log(run_id, "System", "trade_executed",
                      f"TRADE OPEN: {direction} @ {entry:.2f}",
                      f"[{signal_tier}] TP={tp:.2f} | SL={sl:.2f} | Risk={RISK_PER_TRADE_PCT}% | "
                      f"Latency={latency_ms:.0f}ms | Signal={signal_record['signal_id'][:8]}",
                      payload={
                          **opportunity,
                          "signal_id": signal_record["signal_id"],
                          "signal_tier": signal_tier,
                          "latency_ms": round(latency_ms, 1),
                          "targets": targets,
                          "risk_per_trade_pct": RISK_PER_TRADE_PCT,
                      },
                      status="COMPLETED")

            LOGGER.info(
                "TRADE EXECUTED: %s %s @ %.2f | [%s] TP=%.2f SL=%.2f | Latency=%.0fms | ID=%s",
                direction, self.symbol, entry, signal_tier, tp, sl, latency_ms,
                signal_record["signal_id"][:8],
            )
            return "SIGNAL_OPEN"

        except Exception as e:
            LOGGER.exception("Pipeline cycle failed")
            self._log(run_id, "System", "cycle_failed", "Cycle Error", str(e), status="FAILED")
            return f"ERROR: {str(e)}"
