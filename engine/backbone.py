"""
Trend-Capture Backbone — 7-Gate Deterministic Execution Engine.

FLOW (Multi-Timeframe Trend-Capture):
  Pre:    Concurrency (≤2), circuit breaker, candle lock, risk budget, MAX_PYRAMID
  Gate 1: Multi-TF Alignment (≥ 0.7) — determines direction
  Gate 2: Regime Filter — 25 ≤ ADX ≤ 50, ATR 15m ≥ 0.05%
  Gate 3: Funding Contrarian — abort if crowded side
  Gate 4: Minimum Gross Edge ≥ 0.18%
  Gate 5: Microstructure (NOFI ≥ 0.30, delta aligned, OBI confirms, spread ≤ 3)
  Gate 6: Pullback Timing — entry only at completed pullback
  Gate 7: Kelly Sizing — regime/session-aware position sizing

All constants imported from config.py.
LLM agents (TrendStrategist + RiskEngine) are ADVISORY ONLY.
"""

import logging
import uuid
import time
from datetime import datetime, timedelta
from typing import Any, Dict

from engine.database import FastTickDatabase
from engine.analysis.technical_math import (
    fetch_macro_indicators,
    calculate_microstructure,
    estimate_liquidity_clusters,
)
import engine.analysis.timeframe_analysis as TFA
import engine.config as C

LOGGER = logging.getLogger(__name__)


class HierarchicalBackbone:
    def __init__(
        self,
        db: FastTickDatabase,
        symbol: str,
        lookback_seconds: int = 60,
        capital_base: float = 10000.0,
    ):
        self.db = db
        self.symbol = symbol
        self.lookback_seconds = lookback_seconds
        self.capital_base = capital_base
        self._last_trade_time: float = 0.0
        self._absorption_detected_at: float | None = None

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
    # HELPER METHODS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _count_active_trades(self) -> int:
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT COUNT(*) FROM trade_signals WHERE symbol = ? AND status = 'OPEN'",
                [self.symbol],
            ).fetchone()
        return int(row[0]) if row else 0

    def _current_total_risk_pct(self) -> float:
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

    def _candle_lock_active(self) -> bool:
        elapsed = time.monotonic() - self._last_trade_time
        return elapsed < C.CANDLE_LOCK_SECONDS

    def _last_trade_seconds_ago(self) -> float:
        return time.monotonic() - self._last_trade_time

    def _max_pyramid_reached(self, direction: str) -> bool:
        """
        Reject if 2 active trades exist in the same direction and both are profitable.
        Prevents over-concentration without blocking legitimate pyramiding.
        """
        with self.db._lock:
            rows = self.db._conn.execute(
                """SELECT entry_price, side
                   FROM trade_signals
                   WHERE symbol = ? AND status = 'OPEN' AND side = ?""",
                [self.symbol, direction],
            ).fetchall()

        if len(rows) < 2:
            return False

        # Check if all same-direction trades are profitable
        current_price = self.db.get_latest_price(symbol=self.symbol)
        if not current_price:
            return True  # Defensive: block if no price data

        all_profitable = True
        for row in rows:
            entry = float(row[0])
            side = str(row[1]).upper()
            if side == "LONG" and current_price <= entry:
                all_profitable = False
            elif side == "SHORT" and current_price >= entry:
                all_profitable = False

        return all_profitable  # Block only if all are in profit

    def _has_correlated_trade(self, direction: str, entry_price: float, atr_dollar: float) -> bool:
        if atr_dollar <= 0:
            return False
        with self.db._lock:
            rows = self.db._conn.execute(
                """SELECT entry_price FROM trade_signals
                   WHERE symbol = ? AND status = 'OPEN' AND side = ?""",
                [self.symbol, direction],
            ).fetchall()
        min_distance = 0.30 * atr_dollar
        for row in rows:
            existing_entry = float(row[0])
            if abs(entry_price - existing_entry) < min_distance:
                return True
        return False

    def _circuit_breaker_triggered(self) -> tuple[bool, str]:
        now = datetime.utcnow()
        with self.db._lock:
            recent = self.db._conn.execute(
                """SELECT status, closed_at FROM trade_signals
                   WHERE symbol = ? AND status IN ('WIN', 'LOSS')
                   ORDER BY closed_at DESC
                   LIMIT ?""",
                [self.symbol, C.CIRCUIT_BREAKER_LOSSES],
            ).fetchall()

        if len(recent) >= C.CIRCUIT_BREAKER_LOSSES and all(r[0] == "LOSS" for r in recent):
            last_loss_time = recent[0][1]
            if last_loss_time and (now - last_loss_time).total_seconds() < C.CIRCUIT_BREAKER_COOLDOWN_MIN * 60:
                remaining = C.CIRCUIT_BREAKER_COOLDOWN_MIN * 60 - (now - last_loss_time).total_seconds()
                return True, f"{C.CIRCUIT_BREAKER_LOSSES} consecutive losses. Cooldown: {remaining:.0f}s"

        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with self.db._lock:
            daily_row = self.db._conn.execute(
                """SELECT COALESCE(SUM(pnl_pct), 0.0) FROM trade_signals
                   WHERE symbol = ? AND status IN ('WIN', 'LOSS', 'TREND_REVERSAL')
                     AND closed_at >= ?""",
                [self.symbol, day_start],
            ).fetchone()
        daily_pnl = float(daily_row[0]) if daily_row else 0.0
        if daily_pnl <= -C.DAILY_LOSS_STOP_PCT:
            return True, f"Daily loss cap hit: {daily_pnl:.2f}% (limit: -{C.DAILY_LOSS_STOP_PCT}%)"

        return False, ""

    def _compute_kelly_fraction(self, side: str, adx: float) -> float:
        session_tag = self.db.get_session_tag()
        perf = self.db.get_regime_performance(session_tag, side, adx)
        win_rate = perf.get("win_rate", 0.5)
        total = perf.get("total_signals", 0)
        if total < 10:
            return C.BASE_RISK_PER_TRADE
        avg_mfe = abs(perf.get("avg_mfe", 0.001))
        avg_mae = abs(perf.get("avg_mae", 0.001))
        payoff_ratio = avg_mfe / avg_mae if avg_mae > 0 else 1.0
        kelly = win_rate - ((1 - win_rate) / payoff_ratio) if payoff_ratio > 0 else 0.0
        half_kelly = kelly / 2.0
        return max(0.001, min(half_kelly, C.KELLY_FRACTION_CAP))

    # ── Entry Price ────────────────────────────────────────────────────

    @staticmethod
    def _marketable_limit_entry(direction: str, l2_data: Dict[str, Any]) -> float:
        best_bid = float(l2_data.get("best_bid", 0))
        best_ask = float(l2_data.get("best_ask", 0))
        if direction == "LONG" and best_bid > 0:
            return round(best_bid + 0.01, 2)
        elif direction == "SHORT" and best_ask > 0:
            return round(best_ask - 0.01, 2)
        mid = float(l2_data.get("mid_price", 0))
        if mid > 0:
            return round(mid * (0.99995 if direction == "LONG" else 1.00005), 2)
        return 0.0

    # ── Target Computation (regime-adaptive) ──────────────────────────

    @staticmethod
    def _compute_targets(
        direction: str,
        entry: float,
        atr_15m: float,
        kyles_lambda: float,
        atr_pct: float,
    ) -> Dict[str, Any]:
        """
        Compute TP/SL using regime-adaptive multipliers.
        Lambda > threshold → reduce position SIZE (not SL).
        """
        atr_dollar = atr_15m
        tp_mult, sl_mult, vol_regime = TFA.get_regime_multipliers(atr_pct)

        liquidity_condition = "normal"
        if kyles_lambda > C.LAMBDA_THIN_THRESHOLD:
            liquidity_condition = "thin"

        tp_distance = tp_mult * atr_dollar
        sl_distance = sl_mult * atr_dollar

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
            "atr_15m": round(atr_15m, 4),
            "tp_multiplier": tp_mult,
            "sl_multiplier": sl_mult,
            "volatility_regime": vol_regime,
            "liquidity_condition": liquidity_condition,
        }

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MAIN EXECUTION CYCLE — 7-Gate Trend-Capture System
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def run_cycle(self) -> str:
        """
        Deterministic 7-gate execution cycle.
        Direction is determined by multi-TF alignment (not NOFI).
        NOFI is the execution-quality filter (Gate 5).
        LLM agents are ADVISORY ONLY.
        """
        run_id = str(uuid.uuid4())
        cycle_start = time.monotonic()
        self._log(run_id, "System", "cycle_started", "Cycle Started",
                  "Beginning 7-gate trend-capture cycle.", status="STARTED")

        try:
            # ━━━ PRE-GATES: Concurrency, Circuit Breaker, Lock ━━━━━━━
            active_count = self._count_active_trades()
            if active_count >= C.MAX_ACTIVE_TRADES:
                self._log(run_id, "Pre", "concurrency_blocked", "Concurrency Lock",
                          f"Blocked: {active_count}/{C.MAX_ACTIVE_TRADES}.",
                          payload={"active_count": active_count}, status="INFO")
                return "CONCURRENCY_BLOCKED"

            current_risk = self._current_total_risk_pct()
            if current_risk + C.BASE_RISK_PER_TRADE * 100 > C.MAX_TOTAL_RISK_PCT:
                self._log(run_id, "Pre", "risk_budget_exceeded", "Risk Budget Full",
                          f"Current={current_risk:.2f}% + new={C.BASE_RISK_PER_TRADE*100:.2f}%.",
                          status="INFO")
                return "RISK_BUDGET_EXCEEDED"

            breaker_triggered, breaker_reason = self._circuit_breaker_triggered()
            if breaker_triggered:
                self._log(run_id, "Pre", "circuit_breaker", "Circuit Breaker", breaker_reason, status="WARN")
                return "CIRCUIT_BREAKER"

            if self._candle_lock_active():
                self._log(run_id, "Pre", "candle_lock", "Candle Lock",
                          f"Last trade {self._last_trade_seconds_ago():.0f}s ago.",
                          status="INFO")
                return "CANDLE_LOCK"

            # ━━━ DATA: Fetch orderflow, L2, macro, microstructure ━━━━━
            snapshot = self.db.calculate_and_store_orderflow(
                symbol=self.symbol,
                lookback_seconds=self.lookback_seconds,
            )

            from engine.data.binance_ingestor import l2_cache, funding_cache
            from engine.data.binance_ingestor import delta_buffer_5s, delta_buffer_30s, delta_buffer_120s

            l2_data = dict(l2_cache)
            buffers = {
                "5s": list(delta_buffer_5s),
                "30s": list(delta_buffer_30s),
                "120s": list(delta_buffer_120s),
            }

            macro_data = fetch_macro_indicators(self.symbol)
            current_price = float(l2_data.get("mid_price", 0)) or float(
                snapshot.get("last_price", 0)
            )
            micro = calculate_microstructure(current_price, buffers, l2_data)

            self._log(run_id, "Data", "data_computed",
                      "Orderflow + Microstructure Computed",
                      f"NOFI={micro['nofi_composite']:.4f} | ADX={macro_data['adx']:.1f} | "
                      f"ATR_15m%={macro_data['atr_15m_percent']*100:.3f}% | "
                      f"Spread={micro['spread_ticks']:.1f} ticks",
                      payload={**micro, "macro": macro_data}, status="COMPLETED")

            # ━━━ GATE 1: Multi-TF Alignment (≥ 0.7) ━━━━━━━━━━━━━━━━━
            trend_bias = TFA.get_trend_bias()
            alignment_score = trend_bias["alignment_score"]
            candidate_side = trend_bias["candidate_side"]
            daily_bias = trend_bias["daily_bias"]
            four_h_dir = trend_bias["four_h_direction"]
            one_h_rsi = trend_bias["one_h_rsi"]
            pullback_complete = trend_bias["pullback_complete"]

            if candidate_side == "NONE":
                self._log(run_id, "Gate 1", "no_alignment",
                          f"No Directional Alignment: daily={daily_bias}, 4H={four_h_dir}",
                          "Daily and 4H don't agree. No trade.",
                          payload=trend_bias, status="INFO")
                return "NO_ALIGNMENT"

            if alignment_score < C.MIN_ALIGNMENT_SCORE:
                self._log(run_id, "Gate 1", "low_alignment",
                          f"Alignment Score {alignment_score:.2f} < {C.MIN_ALIGNMENT_SCORE}",
                          f"Side={candidate_side} daily={daily_bias} 4H={four_h_dir} "
                          f"RSI={one_h_rsi:.1f} pullback={pullback_complete}",
                          payload=trend_bias, status="INFO")
                return "LOW_ALIGNMENT"

            direction = candidate_side  # Direction from higher TF, NOT from NOFI

            # MAX_PYRAMID check: 2 same-dir profitable trades = reject
            if self._max_pyramid_reached(direction):
                self._log(run_id, "Pre", "max_pyramid",
                          f"MAX_PYRAMID: 2 profitable {direction} trades open",
                          "Over-concentration blocked.", status="INFO")
                return "MAX_PYRAMID"

            self._log(run_id, "Gate 1", "alignment_passed",
                      f"Alignment PASS: {direction} @ {alignment_score:.2f}",
                      f"daily={daily_bias} 4H={four_h_dir} RSI={one_h_rsi:.1f}",
                      payload=trend_bias, status="COMPLETED")

            # ━━━ GATE 2: Regime — Dynamic ADX ≤ 50, ATR% ≥ 0.05% ━━━
            adx = float(macro_data.get("adx", 0))
            atr_pct = float(macro_data.get("atr_15m_percent", 0))

            # Dynamic ADX: regime-adaptive threshold
            # Compute vol_regime early for ADX decision
            _vol_regime_for_adx = "NORMAL"
            if atr_pct > 0:
                _atr_ratio = atr_pct / C.MIN_ATR_PERCENT_FOR_TRADE
                if _atr_ratio < 2.0:            # Was 1.5 — widened to catch borderline quiet markets
                    _vol_regime_for_adx = "LOW_VOL"
                elif _atr_ratio > 3.0:
                    _vol_regime_for_adx = "HIGH_VOL"

            adx_threshold = {
                "LOW_VOL": C.ADX_THRESHOLD_LOW_VOL,
                "NORMAL": C.ADX_THRESHOLD_NORMAL,
                "HIGH_VOL": C.ADX_THRESHOLD_HIGH_VOL,
            }.get(_vol_regime_for_adx, C.MIN_ADX_THRESHOLD)

            if adx < adx_threshold:
                self._log(run_id, "Gate 2", "low_adx",
                          f"ADX Too Low: {adx:.1f} < {adx_threshold} ({_vol_regime_for_adx})",
                          f"Regime={_vol_regime_for_adx}, dynamic threshold={adx_threshold}",
                          status="INFO")
                return "LOW_ADX"

            if adx > C.ADX_EXTREME_THRESHOLD:
                self._log(run_id, "Gate 2", "extreme_adx",
                          f"ADX Extreme: {adx:.1f} > {C.ADX_EXTREME_THRESHOLD}",
                          "Parabolic/likely reversal. Reject.", status="INFO")
                return "EXTREME_ADX"

            if atr_pct < C.MIN_ATR_PERCENT_FOR_TRADE:
                self._log(run_id, "Gate 2", "compression",
                          f"ATR Compression: {atr_pct*100:.3f}% < {C.MIN_ATR_PERCENT_FOR_TRADE*100:.3f}%",
                          "Volatility too low.", status="INFO")
                return "COMPRESSION_PHASE"

            # ━━━ GATE 3: Funding Contrarian — abort if crowded ━━━━━━━
            funding_rate = float(funding_cache.get("current", 0))
            if direction == "LONG" and funding_rate > C.FUNDING_CROWDED_THRESHOLD:
                self._log(run_id, "Gate 3", "crowded_long",
                          f"Funding Crowded LONG: {funding_rate*100:.4f}% > {C.FUNDING_CROWDED_THRESHOLD*100:.4f}%",
                          "Longs overleveraged. Reject.", status="INFO")
                return "CROWDED_LONG"

            if direction == "SHORT" and funding_rate < -C.FUNDING_CROWDED_THRESHOLD:
                self._log(run_id, "Gate 3", "crowded_short",
                          f"Funding Crowded SHORT: {funding_rate*100:.4f}%",
                          "Shorts overleveraged. Reject.", status="INFO")
                return "CROWDED_SHORT"

            # ━━━ Compute entry + targets ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            entry = self._marketable_limit_entry(direction, l2_data)
            if entry <= 0:
                entry = round(current_price * (0.99995 if direction == "LONG" else 1.00005), 2)

            atr_15m_dollar = float(macro_data.get("atr_15m", 0))
            targets = self._compute_targets(
                direction=direction,
                entry=entry,
                atr_15m=atr_15m_dollar,
                kyles_lambda=micro["kyles_lambda"],
                atr_pct=atr_pct,
            )
            tp = targets["tp"]
            sl = targets["sl"]
            vol_regime = targets["volatility_regime"]

            # ━━━ GATE 4: Minimum Gross Edge ≥ 0.18% ━━━━━━━━━━━━━━━━
            if direction == "LONG":
                gross_edge = (tp - entry) / entry if entry > 0 else 0
            else:
                gross_edge = (entry - tp) / entry if entry > 0 else 0

            if gross_edge < C.MINIMUM_GROSS_EDGE_PERCENT:
                self._log(run_id, "Gate 4", "insufficient_edge",
                          f"Gross Edge {gross_edge*100:.3f}% < {C.MINIMUM_GROSS_EDGE_PERCENT*100:.3f}%",
                          f"TP={tp:.2f} Entry={entry:.2f} SL={sl:.2f}",
                          status="INFO")
                return "INSUFFICIENT_EDGE"

            # ━━━ GATE 5: Microstructure (execution-quality) ━━━━━━━━━━
            # NOFI — execution quality, NOT direction
            nofi = micro["nofi_composite"]
            abs_nofi = abs(nofi)
            if abs_nofi < C.NOFI_COMPOSITE_THRESHOLD:
                self._log(run_id, "Gate 5", "low_nofi",
                          f"NOFI Too Low: {nofi:.4f} < ±{C.NOFI_COMPOSITE_THRESHOLD}",
                          "No aggressive flow to confirm execution timing.", status="INFO")
                return "LOW_NOFI"

            # NOFI must agree with direction
            if direction == "LONG" and nofi < 0:
                self._log(run_id, "Gate 5", "nofi_direction_mismatch",
                          f"NOFI Bearish ({nofi:.4f}) vs LONG direction",
                          "Execution flow opposes trend direction.", status="INFO")
                return "NOFI_DIRECTION_MISMATCH"
            if direction == "SHORT" and nofi > 0:
                self._log(run_id, "Gate 5", "nofi_direction_mismatch",
                          f"NOFI Bullish ({nofi:.4f}) vs SHORT direction",
                          "Execution flow opposes trend direction.", status="INFO")
                return "NOFI_DIRECTION_MISMATCH"

            # Delta slope alignment
            if not micro.get("delta_slope_aligned", False):
                self._log(run_id, "Gate 5", "delta_divergence",
                          "Delta Slope Divergence",
                          f"5s/30s/120s deltas not aligned for {direction}.", status="INFO")
                return "DELTA_DIVERGENCE"

            # OBI confirmation (tightened thresholds)
            obi = micro.get("obi", 0)
            if direction == "LONG" and obi < C.OBI_LONG_THRESHOLD:
                self._log(run_id, "Gate 5", "l2_no_support",
                          f"OBI={obi:.4f} < {C.OBI_LONG_THRESHOLD}",
                          "L2 doesn't support LONG.", status="INFO")
                return "L2_NO_SUPPORT"

            if direction == "SHORT" and obi > C.OBI_SHORT_THRESHOLD:
                self._log(run_id, "Gate 5", "l2_no_support",
                          f"OBI={obi:.4f} > {C.OBI_SHORT_THRESHOLD}",
                          "L2 doesn't support SHORT.", status="INFO")
                return "L2_NO_SUPPORT"

            # Spread
            spread_ticks = micro.get("spread_ticks", 0)
            if spread_ticks > C.MAX_SPREAD_TICKS:
                self._log(run_id, "Gate 5", "wide_spread",
                          f"Spread {spread_ticks:.1f} > {C.MAX_SPREAD_TICKS}",
                          "Market too wide.", status="INFO")
                return "WIDE_SPREAD"

            # Absorption
            if micro.get("absorption_detected", False):
                if micro.get("displacement_ticks", 0) > C.ABSORPTION_RESOLUTION_TICKS:
                    self._absorption_detected_at = None
                else:
                    if self._absorption_detected_at is None:
                        self._absorption_detected_at = time.monotonic()
                    elapsed = time.monotonic() - self._absorption_detected_at
                    if elapsed >= C.ABSORPTION_TIMEOUT_SECONDS:
                        self._log(run_id, "Gate 5", "absorption_timeout",
                                  f"Absorption Unresolved ({elapsed:.0f}s)",
                                  "Reject.", status="INFO")
                        return "UNRESOLVED_ABSORPTION"
                    else:
                        self._log(run_id, "Gate 5", "absorption_active",
                                  f"Absorption Active ({elapsed:.0f}s)",
                                  "Waiting for resolution.", status="INFO")
                        return "UNRESOLVED_ABSORPTION"
            else:
                self._absorption_detected_at = None

            # ━━━ GATE 6: Pullback Timing (SOFT GATE — reduces size) ━━━━
            pullback_penalty = 1.0
            if not pullback_complete:
                pullback_penalty = 0.85  # 15% size reduction for chasing entry
                self._log(run_id, "Gate 6", "pullback_not_complete",
                          f"Pullback Not Complete for {direction} (soft gate: -15% size)",
                          "Entering without pullback confirmation — reduced conviction.",
                          status="INFO")

            # ━━━ GATE 7: Kelly Sizing ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            kelly_fraction = self._compute_kelly_fraction(direction, adx)

            # Apply pullback penalty (soft gate 6)
            kelly_fraction *= pullback_penalty

            if targets["liquidity_condition"] == "thin":
                kelly_fraction *= (1 - C.LAMBDA_SIZE_REDUCTION)
                self._log(run_id, "Gate 7", "lambda_size_reduction",
                          f"Lambda → Size Reduced to {kelly_fraction*100:.2f}%",
                          f"Lambda={micro['kyles_lambda']:.6f}", status="INFO")

            # Conviction-based sizing: scale by alignment score
            conviction_mult = C.CONVICTION_TIERS.get(0.50, 0.70)  # default to lowest
            for threshold in sorted(C.CONVICTION_TIERS.keys(), reverse=True):
                if alignment_score >= threshold:
                    conviction_mult = C.CONVICTION_TIERS[threshold]
                    break
            kelly_fraction *= conviction_mult

            self._log(run_id, "Gate 7", "conviction_sizing",
                      f"Conviction Sizing: {conviction_mult:.2f}× (align={alignment_score:.2f})",
                      f"Kelly={kelly_fraction:.4f} | Pullback={pullback_penalty} | Conviction={conviction_mult}",
                      status="INFO")

            position_size_pct = kelly_fraction * 100

            # Correlation check
            if self._has_correlated_trade(direction, entry, targets["atr_dollar"]):
                self._log(run_id, "Pre", "correlation_rejected",
                          "Correlated Trade Rejected",
                          f"Active {direction} trade within 0.30×ATR.", status="INFO")
                return "CORRELATION_REJECTED"

            # ━━━ ADVISORY: TrendStrategist + RiskEngine (never block) ━━
            net_edge = gross_edge - C.ROUND_TRIP_FEE
            try:
                from engine.agents.advisory import TrendStrategist
                trend_result = TrendStrategist.evaluate({
                    "alignment_score": alignment_score,
                    "daily_bias": daily_bias,
                    "four_h_direction": four_h_dir,
                    "one_h_rsi": one_h_rsi,
                    "nofi_composite": nofi,
                    "obi": obi,
                    "funding_rate": funding_rate,
                    "volatility_regime": vol_regime,
                    "pullback_complete": pullback_complete,
                    "candidate_side": direction,
                })
            except Exception as e:
                LOGGER.warning("TrendStrategist failed (non-blocking): %s", e)
                trend_result = {"verdict": "ALIGN", "confidence": 0.7,
                                "reasoning": "LLM unavailable"}

            self._log(run_id, "TrendStrategist", "trend_advisory",
                      f"Trend Advisory: {trend_result.get('verdict', 'UNKNOWN')}",
                      trend_result.get("reasoning", ""),
                      payload=trend_result, status="COMPLETED")

            try:
                from engine.agents.advisory import RiskEngine
                risk_result = RiskEngine.evaluate({
                    "trend_verdict": trend_result.get("verdict", "ALIGN"),
                    "trend_confidence": trend_result.get("confidence", 0.7),
                    "gross_edge_pct": gross_edge * 100,
                    "net_edge_pct": net_edge * 100,
                    "position_size_pct": position_size_pct,
                    "portfolio_heat": current_risk,
                    "kelly_fraction": kelly_fraction,
                    "active_trades": active_count,
                })
            except Exception as e:
                LOGGER.warning("RiskEngine failed (non-blocking): %s", e)
                risk_result = {"verdict": "PASS", "risk_note": "LLM unavailable"}

            self._log(run_id, "RiskEngine", "risk_advisory",
                      f"Risk Advisory: {risk_result.get('verdict', 'UNKNOWN')}",
                      risk_result.get("risk_note", ""),
                      payload=risk_result, status="COMPLETED")

            # ━━━ ALL GATES PASSED — EXECUTE ━━━━━━━━━━━━━━━━━━━━━━━━━
            signal_confidence = min(alignment_score, abs_nofi / 0.50)
            four_h_structure = trend_bias.get("four_h_structure", {})

            opportunity = {
                "symbol": self.symbol,
                "side": direction,
                "entry_price": entry,
                "stop_price": sl,
                "take_profit": tp,
                "confidence": signal_confidence,
                "rationale": (
                    f"[TREND] Alignment={alignment_score:.2f}, "
                    f"NOFI={nofi:.4f}, ADX={adx:.1f}, "
                    f"ATR%={atr_pct*100:.3f}%, Regime={vol_regime}, "
                    f"TP_mult={targets['tp_multiplier']}, SL_mult={targets['sl_multiplier']}, "
                    f"GrossEdge={gross_edge*100:.3f}%, NetEdge={net_edge*100:.3f}%, "
                    f"Kelly={kelly_fraction:.4f}, Funding={funding_rate*100:.4f}%"
                ),
                "regime": vol_regime,
            }

            decision_trace = {
                "run_id": run_id,
                "micro": micro,
                "trend_bias": trend_bias,
                "trend_advisory": trend_result,
                "risk_advisory": risk_result,
                "targets": targets,
                "gates_passed": [
                    "PRE_CHECKS", "ALIGNMENT", "REGIME_FILTER",
                    "FUNDING_CONTRARIAN", "EDGE_CHECK",
                    "MICROSTRUCTURE", "PULLBACK_TIMING", "KELLY_SIZING",
                ],
                "economic_reality": {
                    "gross_edge": round(gross_edge, 6),
                    "round_trip_fee": C.ROUND_TRIP_FEE,
                    "net_edge": round(net_edge, 6),
                },
                "execution_mode": "7_gate_trend_capture",
            }

            signal_record = self.db.store_trade_signal(
                symbol=self.symbol,
                opportunity=opportunity,
                horizon_seconds=7200,  # 2hr max — structural exits handle actual close
                analysis=micro,
                decision_trace=decision_trace,
            )

            signal_id = signal_record["signal_id"]
            session_tag = self.db.get_session_tag()

            # Update all new + existing columns on signal
            with self.db._lock:
                self.db._conn.execute(
                    """UPDATE trade_signals SET
                        session_tag = ?,
                        funding_rate_at_entry = ?,
                        obi_at_entry = ?,
                        spread_at_entry = ?,
                        kelly_fraction_used = ?,
                        nofi_at_entry = ?,
                        atr_used = ?,
                        alignment_score_at_entry = ?,
                        daily_bias_at_entry = ?,
                        four_h_structure_at_entry = ?,
                        pullback_complete_at_entry = ?,
                        volatility_regime_at_entry = ?,
                        tp_multiplier_used = ?,
                        sl_multiplier_used = ?,
                        trend_fail_count = 0
                    WHERE signal_id = ?""",
                    [
                        session_tag,
                        funding_rate,
                        obi,
                        spread_ticks,
                        kelly_fraction,
                        nofi,
                        atr_15m_dollar,
                        alignment_score,
                        daily_bias,
                        four_h_dir,
                        pullback_complete,
                        vol_regime,
                        targets["tp_multiplier"],
                        targets["sl_multiplier"],
                        signal_id,
                    ],
                )

            # Approve → OPEN
            self.db.store_signal_challenge(
                signal_id=signal_id,
                symbol=self.symbol,
                verdict="APPROVE",
                score=signal_confidence,
                summary=(
                    f"[TREND] {direction} | Alignment={alignment_score:.2f} "
                    f"Regime={vol_regime} | Edge={gross_edge*100:.3f}%-{net_edge*100:.3f}%net"
                ),
                challenge_payload={
                    "checks": [
                        {"name": "Alignment", "score": alignment_score, "min": C.MIN_ALIGNMENT_SCORE},
                        {"name": "ADX", "value": adx, "range": f"{C.MIN_ADX_THRESHOLD}-{C.ADX_EXTREME_THRESHOLD}"},
                        {"name": "Funding", "rate": funding_rate, "crowded": False},
                        {"name": "Edge", "gross": gross_edge, "net": net_edge},
                        {"name": "NOFI", "value": nofi, "threshold": C.NOFI_COMPOSITE_THRESHOLD},
                        {"name": "Pullback", "complete": pullback_complete},
                        {"name": "Regime", "value": vol_regime},
                        {"name": "Kelly", "fraction": kelly_fraction},
                    ]
                },
            )

            self._last_trade_time = time.monotonic()
            latency_ms = (time.monotonic() - cycle_start) * 1000

            with self.db._lock:
                self.db._conn.execute(
                    "UPDATE trade_signals SET latency_ms = ? WHERE signal_id = ?",
                    [int(latency_ms), signal_id],
                )

            self._log(run_id, "System", "trade_executed",
                      f"TRADE OPEN: {direction} @ {entry:.2f}",
                      f"Alignment={alignment_score:.2f} | Regime={vol_regime} | "
                      f"TP={tp:.2f}({targets['tp_multiplier']}x) | SL={sl:.2f}({targets['sl_multiplier']}x) | "
                      f"Edge={gross_edge*100:.3f}%/{net_edge*100:.3f}%net | "
                      f"Kelly={kelly_fraction:.4f} | Latency={latency_ms:.0f}ms",
                      payload={
                          **opportunity, "signal_id": signal_id,
                          "latency_ms": round(latency_ms, 1),
                          "targets": targets, "alignment_score": alignment_score,
                          "volatility_regime": vol_regime,
                      },
                      status="COMPLETED")

            LOGGER.info(
                "TRADE EXECUTED: %s %s @ %.2f | Align=%.2f Regime=%s | "
                "TP=%.2f(%.1fx) SL=%.2f(%.1fx) | Edge=%.3f%%/%.3f%%net | Kelly=%.4f | %dms | %s",
                direction, self.symbol, entry, alignment_score, vol_regime,
                tp, targets["tp_multiplier"], sl, targets["sl_multiplier"],
                gross_edge * 100, net_edge * 100, kelly_fraction,
                int(latency_ms), signal_id[:8],
            )
            return "SIGNAL_OPEN"

        except Exception as e:
            LOGGER.exception("Pipeline cycle failed")
            self._log(run_id, "System", "cycle_failed", "Cycle Error", str(e), status="FAILED")
            return f"ERROR: {str(e)}"
