from __future__ import annotations

import json
import logging
from typing import Any
import uuid

from crewai import Agent, Crew, Process, Task
from crewai.tools import tool

from database import FastTickDatabase
from llm_fallback import get_llm_chain
from orderflow_logic import (
    build_orderflow_analysis,
    challenge_trading_signal,
    derive_trading_opportunity,
)

LOGGER = logging.getLogger(__name__)


def _log_event(
    db: FastTickDatabase,
    symbol: str,
    run_id: str,
    component: str,
    event_type: str,
    title: str,
    message: str,
    payload: dict[str, Any] | None = None,
    status: str = "INFO",
) -> None:
    db.store_crew_event(
        symbol=symbol,
        run_id=run_id,
        component=component,
        event_type=event_type,
        title=title,
        message=message,
        payload=payload,
        status=status,
    )


def build_calculate_orderflow_tool(db: FastTickDatabase, symbol: str, run_id: str):
    @tool("calculate_orderflow")
    def calculate_orderflow(lookback_seconds: int = 60) -> str:
        """Calculate orderflow metrics from tick-level Binance trades and persist them."""
        _log_event(
            db=db,
            symbol=symbol,
            run_id=run_id,
            component="Orderflow Calculator",
            event_type="tool_started",
            title="Calculating orderflow",
            message=f"Computing orderflow over the last {lookback_seconds}s.",
            payload={"lookback_seconds": lookback_seconds},
            status="STARTED",
        )
        try:
            snapshot = db.calculate_and_store_orderflow(symbol=symbol, lookback_seconds=lookback_seconds)
        except Exception as exc:
            _log_event(
                db=db,
                symbol=symbol,
                run_id=run_id,
                component="Orderflow Calculator",
                event_type="tool_failed",
                title="Orderflow calculation failed",
                message=str(exc),
                payload={"lookback_seconds": lookback_seconds},
                status="FAILED",
            )
            raise
        _log_event(
            db=db,
            symbol=symbol,
            run_id=run_id,
            component="Orderflow Calculator",
            event_type="tool_completed",
            title="Orderflow snapshot stored",
            message="Stored latest microstructure snapshot.",
            payload=snapshot,
            status="COMPLETED",
        )
        return json.dumps(snapshot, sort_keys=True)

    return calculate_orderflow


def build_analyze_orderflow_tool(db: FastTickDatabase, symbol: str, run_id: str):
    @tool("analyze_orderflow")
    def analyze_orderflow(target_symbol: str = symbol) -> str:
        """Analyze latest orderflow snapshot and persist the interpreted market regime."""
        _log_event(
            db=db,
            symbol=target_symbol,
            run_id=run_id,
            component="Orderflow Analyst",
            event_type="tool_started",
            title="Analyzing orderflow",
            message="Interpreting the latest stored orderflow snapshot.",
            status="STARTED",
        )
        snapshot = db.get_latest_orderflow_snapshot(symbol=target_symbol)
        if snapshot is None:
            _log_event(
                db=db,
                symbol=target_symbol,
                run_id=run_id,
                component="Orderflow Analyst",
                event_type="tool_skipped",
                title="No snapshot available",
                message="Analysis skipped because no orderflow snapshot exists yet.",
                status="SKIPPED",
            )
            return "No orderflow snapshot exists yet."
        analysis = build_orderflow_analysis(snapshot)
        db.store_orderflow_analysis(symbol=target_symbol, analysis=analysis)
        _log_event(
            db=db,
            symbol=target_symbol,
            run_id=run_id,
            component="Orderflow Analyst",
            event_type="tool_completed",
            title="Orderflow regime stored",
            message="Stored regime, confidence, and summary for the latest snapshot.",
            payload=analysis,
            status="COMPLETED",
        )
        return json.dumps(analysis, sort_keys=True)

    return analyze_orderflow


def _apply_learning_adjustment(
    opportunity: dict[str, Any],
    learning: dict[str, Any],
    min_samples: int,
) -> dict[str, Any]:
    adjusted = dict(opportunity)
    side = str(adjusted["side"]).upper()
    if side not in {"LONG", "SHORT"}:
        return adjusted

    recommendation = str(learning.get("recommendation", "insufficient_data"))
    sample_size = int(learning.get("sample_size", 0))
    win_rate = float(learning.get("win_rate", 0.0))

    if sample_size < min_samples or recommendation == "insufficient_data":
        adjusted["rationale"] += " Learning memory: insufficient data for this setup."
        return adjusted

    if recommendation == "avoid":
        adjusted["side"] = "FLAT"
        adjusted["confidence"] = round(max(0.10, float(adjusted["confidence"]) - 0.12), 4)
        adjusted["stop_price"] = adjusted["entry_price"]
        adjusted["take_profit"] = adjusted["entry_price"]
        adjusted["rationale"] += (
            f" Learning memory: avoided this setup due to low historical win-rate={win_rate:.2f} "
            f"across {sample_size} similar trades."
        )
        return adjusted

    if recommendation == "promote":
        adjusted["confidence"] = round(min(0.99, float(adjusted["confidence"]) + 0.05), 4)
        adjusted["rationale"] += (
            f" Learning memory: promoted this setup due to win-rate={win_rate:.2f} "
            f"across {sample_size} similar trades."
        )
        return adjusted

    adjusted["rationale"] += (
        f" Learning memory: neutral stance (win-rate={win_rate:.2f}, samples={sample_size})."
    )
    return adjusted


def build_opportunity_tool(
    db: FastTickDatabase,
    symbol: str,
    signal_horizon_seconds: int,
    learning_min_samples: int,
    run_id: str,
):
    @tool("scan_btc_opportunity")
    def scan_btc_opportunity(target_symbol: str = symbol) -> str:
        """Create and persist a BTC trading opportunity using latest orderflow analysis."""
        _log_event(
            db=db,
            symbol=target_symbol,
            run_id=run_id,
            component="BTC Opportunity Scout",
            event_type="tool_started",
            title="Scanning BTC opportunity",
            message="Building a trade idea from the latest orderflow analysis.",
            status="STARTED",
        )

        # ── Cooldown: skip if signal generated in last 90 seconds ──
        from datetime import datetime, timedelta
        recent_cutoff = datetime.utcnow() - timedelta(seconds=90)
        try:
            with db._lock:
                recent_count = db._conn.execute(
                    "SELECT COUNT(*) FROM trade_signals WHERE symbol = ? AND generated_at > ?",
                    [target_symbol, recent_cutoff],
                ).fetchone()[0]
            if recent_count > 0:
                _log_event(
                    db=db, symbol=target_symbol, run_id=run_id,
                    component="BTC Opportunity Scout", event_type="tool_skipped",
                    title="Signal cooldown active",
                    message=f"Skipped: {recent_count} signal(s) generated in last 90s. Waiting for cooldown.",
                    status="SKIPPED",
                )
                return "Signal cooldown: a signal was generated recently. Waiting before generating another."
        except Exception:
            pass  # Don't block if check fails

        # ── Max open signals: skip if 3+ active ──
        try:
            with db._lock:
                open_count = db._conn.execute(
                    "SELECT COUNT(*) FROM trade_signals WHERE symbol = ? AND status IN ('OPEN', 'PENDING_VALIDATION')",
                    [target_symbol],
                ).fetchone()[0]
            if open_count >= 3:
                _log_event(
                    db=db, symbol=target_symbol, run_id=run_id,
                    component="BTC Opportunity Scout", event_type="tool_skipped",
                    title="Max open signals reached",
                    message=f"Skipped: {open_count} signals already open (max 3).",
                    status="SKIPPED",
                )
                return f"Max open signals: {open_count} signals already active. Wait for some to close."
        except Exception:
            pass

        latest_analysis = db.get_latest_analysis(symbol=target_symbol)
        if latest_analysis is None:
            _log_event(
                db=db,
                symbol=target_symbol,
                run_id=run_id,
                component="BTC Opportunity Scout",
                event_type="tool_skipped",
                title="No analysis available",
                message="Opportunity scan skipped because analysis is missing.",
                status="SKIPPED",
            )
            return "No analysis exists yet."

        last_price = db.get_latest_price(symbol=target_symbol) or float(latest_analysis.get("last_price", 0))
        if last_price <= 0:
            _log_event(
                db=db,
                symbol=target_symbol,
                run_id=run_id,
                component="BTC Opportunity Scout",
                event_type="tool_skipped",
                title="No valid BTC price",
                message="Opportunity scan skipped because no valid BTC price was available.",
                status="SKIPPED",
            )
            return "No valid BTC price found."

        raw_opportunity = derive_trading_opportunity(
            symbol=target_symbol,
            analysis=latest_analysis,
            last_price=last_price,
        )

        learning = db.get_learning_recommendation(
            symbol=target_symbol,
            side=raw_opportunity["side"],
            regime=raw_opportunity["regime"],
        )
        opportunity = _apply_learning_adjustment(
            opportunity=raw_opportunity,
            learning=learning,
            min_samples=learning_min_samples,
        )
        decision_trace = {
            "logic_version": "orderflow_rulebook_v2",
            "analysis": latest_analysis,
            "raw_opportunity": raw_opportunity,
            "learning_recommendation": learning,
            "final_opportunity": opportunity,
        }

        signal_record = db.store_trade_signal(
            symbol=target_symbol,
            opportunity=opportunity,
            horizon_seconds=signal_horizon_seconds,
            analysis=latest_analysis,
            decision_trace=decision_trace,
        )
        db.store_trading_opportunity(
            symbol=target_symbol,
            opportunity=opportunity,
            signal_id=signal_record["signal_id"],
        )

        payload = {
            **opportunity,
            "signal_id": signal_record["signal_id"],
            "signal_status": signal_record["status"],
            "evaluation_due_time": signal_record["evaluation_due_time"],
            "challenge_verdict": signal_record["challenge_verdict"],
            "learning_recommendation": learning,
        }
        _log_event(
            db=db,
            symbol=target_symbol,
            run_id=run_id,
            component="BTC Opportunity Scout",
            event_type="tool_completed",
            title="Signal proposed",
            message=f"Generated {opportunity['side']} signal and stored decision trace.",
            payload={**payload, "decision_trace": decision_trace},
            status="COMPLETED",
        )
        return json.dumps(payload, sort_keys=True)

    return scan_btc_opportunity


def build_signal_challenge_tool(db: FastTickDatabase, symbol: str, run_id: str):
    @tool("challenge_signal_logic")
    def challenge_signal_logic(
        target_symbol: str = symbol,
        target_signal_id: str = "",
        signal_id: str = "",
        side: str = "",
        entry: float = 0.0,
        stop: float = 0.0,
        target: float = 0.0,
        learning_impact: float = 0.0,
        challenge_verdict: str = "",
        confidence: float = 0.0,
        rationale: str = "",
        regime: str = "",
        signal_status: str = "",
        evaluation_due_time: str = "",
        learning_recommendation: str = "",
        score: float = 0.0,
        failed_checks: str = "",
    ) -> str:
        """Challenge the latest pending BTC signal and persist verdicts about its logic quality."""
        _log_event(
            db=db,
            symbol=target_symbol,
            run_id=run_id,
            component="Signal Challenger",
            event_type="tool_started",
            title="Challenging latest signal",
            message="Auditing signal direction, geometry, and learning consistency.",
            status="STARTED",
        )
        signal_record = None
        requested_signal_id = target_signal_id.strip() or signal_id.strip()
        if requested_signal_id:
            signal_record = db.get_trade_signal(signal_id=requested_signal_id)
        if signal_record is None:
            signal_record = db.get_latest_signal_for_challenge(symbol=target_symbol)
        if signal_record is None:
            _log_event(
                db=db,
                symbol=target_symbol,
                run_id=run_id,
                component="Signal Challenger",
                event_type="tool_skipped",
                title="No signal pending challenge",
                message="Challenge step skipped because no pending signal exists.",
                status="SKIPPED",
            )
            return "No signal is pending challenge."

        decision_trace = signal_record.get("decision_trace", {})
        analysis = signal_record.get("analysis", {})
        challenge = challenge_trading_signal(
            signal=signal_record,
            analysis=analysis,
            decision_trace=decision_trace,
        )
        challenge["historical_calibration"] = db.get_challenger_learning_profile(
            symbol=signal_record["symbol"],
            verdict=str(challenge["verdict"]),
        )
        db.store_signal_challenge(
            signal_id=signal_record["signal_id"],
            symbol=signal_record["symbol"],
            verdict=str(challenge["verdict"]),
            score=float(challenge["score"]),
            summary=str(challenge["summary"]),
            challenge_payload=challenge,
        )
        payload = {
            "signal_id": signal_record["signal_id"],
            "side": signal_record["side"],
            "status": signal_record["status"],
            "regime": signal_record["regime"],
            "challenge": challenge,
        }
        _log_event(
            db=db,
            symbol=target_symbol,
            run_id=run_id,
            component="Signal Challenger",
            event_type="tool_completed",
            title="Signal challenged",
            message=challenge["summary"],
            payload=payload,
            status="COMPLETED",
        )
        return json.dumps(payload, sort_keys=True)

    return challenge_signal_logic


def build_signal_review_tool(
    db: FastTickDatabase,
    symbol: str,
    learning_min_samples: int,
    max_allowed_tick_gap_seconds: int,
    signal_eval_retry_seconds: int,
    max_signal_eval_deferrals: int,
    run_id: str,
):
    @tool("review_signal_performance")
    def review_signal_performance(
        target_symbol: str = symbol,
        signal_id: str = "",
        side: str = "",
        entry: float = 0.0,
        stop: float = 0.0,
        target: float = 0.0,
        learning_impact: float = 0.0,
        challenge_verdict: str = "",
        confidence: float = 0.0,
        rationale: str = "",
        regime: str = "",
        signal_status: str = "",
        evaluation_due_time: str = "",
        learning_recommendation: str = "",
        score: float = 0.0,
        failed_checks: str = "",
    ) -> str:
        """Evaluate mature signals and update learning memory from good/bad trade outcomes."""
        _log_event(
            db=db,
            symbol=target_symbol,
            run_id=run_id,
            component="Signal Performance Reviewer",
            event_type="tool_started",
            title="Reviewing signal outcomes",
            message="Scoring mature signals and refreshing learning memory.",
            status="STARTED",
        )
        evaluation = db.evaluate_due_signals(
            symbol=target_symbol,
            max_signals=300,
            max_gap_seconds=max_allowed_tick_gap_seconds,
            retry_seconds=signal_eval_retry_seconds,
            max_deferrals=max_signal_eval_deferrals,
        )
        learning = db.refresh_learning_memory(symbol=target_symbol, min_samples=learning_min_samples)
        challenger_learning = db.refresh_challenger_learning_memory(
            symbol=target_symbol,
            min_samples=learning_min_samples,
        )
        performance = db.get_signal_performance_summary(symbol=target_symbol)
        challenger = db.get_challenger_performance_summary(symbol=target_symbol)
        payload = {
            "evaluation": evaluation,
            "learning": learning,
            "challenger_learning": challenger_learning,
            "performance": performance,
            "challenger": challenger,
        }
        _log_event(
            db=db,
            symbol=target_symbol,
            run_id=run_id,
            component="Signal Performance Reviewer",
            event_type="tool_completed",
            title="Signal review completed",
            message="Updated signal outcomes, learning memory, and challenger calibration.",
            payload=payload,
            status="COMPLETED",
        )
        return json.dumps(payload, sort_keys=True)

    return review_signal_performance


class BTCOpportunityCrew:
    MAX_RETRIES = 3  # retries on rate-limit with different models

    def __init__(
        self,
        db: FastTickDatabase,
        symbol: str,
        lookback_seconds: int,
        signal_horizon_seconds: int,
        max_allowed_tick_gap_seconds: int,
        signal_eval_retry_seconds: int,
        max_signal_eval_deferrals: int,
        learning_min_samples: int,
        llm_model: str,
        verbose: bool = False,
    ) -> None:
        self.db = db
        self.symbol = symbol
        self.lookback_seconds = lookback_seconds
        self.signal_horizon_seconds = signal_horizon_seconds
        self.max_allowed_tick_gap_seconds = max_allowed_tick_gap_seconds
        self.signal_eval_retry_seconds = signal_eval_retry_seconds
        self.max_signal_eval_deferrals = max_signal_eval_deferrals
        self.learning_min_samples = learning_min_samples
        self.llm_model = llm_model
        self.verbose = verbose
        self._llm_chain = get_llm_chain()

    def _build_crew(self, run_id: str) -> Crew:
        chain = self._llm_chain

        # Each agent gets its own primary model → spreads rate limits
        calc_model = chain.get_model_for_agent("Orderflow Calculator")
        analyst_model = chain.get_model_for_agent("Orderflow Analyst")
        scout_model = chain.get_model_for_agent("BTC Opportunity Scout")
        challenger_model = chain.get_model_for_agent("Signal Challenger")
        reviewer_model = chain.get_model_for_agent("Signal Performance Reviewer")

        LOGGER.info(
            "Agent models: calc=%s analyst=%s scout=%s challenger=%s reviewer=%s",
            calc_model, analyst_model, scout_model, challenger_model, reviewer_model,
        )

        calculate_orderflow = build_calculate_orderflow_tool(self.db, self.symbol, run_id=run_id)
        analyze_orderflow = build_analyze_orderflow_tool(self.db, self.symbol, run_id=run_id)
        scan_btc_opportunity = build_opportunity_tool(
            self.db,
            self.symbol,
            signal_horizon_seconds=self.signal_horizon_seconds,
            learning_min_samples=self.learning_min_samples,
            run_id=run_id,
        )
        challenge_signal_logic = build_signal_challenge_tool(self.db, self.symbol, run_id=run_id)
        review_signal_performance = build_signal_review_tool(
            self.db,
            self.symbol,
            learning_min_samples=self.learning_min_samples,
            max_allowed_tick_gap_seconds=self.max_allowed_tick_gap_seconds,
            signal_eval_retry_seconds=self.signal_eval_retry_seconds,
            max_signal_eval_deferrals=self.max_signal_eval_deferrals,
            run_id=run_id,
        )

        orderflow_calculator_agent = Agent(
            role="Orderflow Calculator",
            goal="Convert tick-by-tick BTC trade flow into reliable orderflow metrics.",
            backstory=(
                "You are a crypto microstructure specialist. "
                "You always compute objective market pressure from raw trades."
            ),
            tools=[calculate_orderflow],
            allow_delegation=False,
            llm=calc_model,
            verbose=self.verbose,
        )

        orderflow_analyst_agent = Agent(
            role="Orderflow Analyst",
            goal="Interpret orderflow metrics into a clear market regime and confidence score.",
            backstory=(
                "You are a short-horizon orderflow analyst focused on BTC execution pressure. "
                "You only make claims supported by current metrics. Do not ask for human approval."
            ),
            tools=[analyze_orderflow],
            allow_delegation=False,
            llm=analyst_model,
            verbose=self.verbose,
        )

        opportunity_scout_agent = Agent(
            role="BTC Opportunity Scout",
            goal="Translate analyzed orderflow into practical BTC trade opportunities.",
            backstory=(
                "You produce actionable but risk-aware BTC opportunities with entry, stop, and target. "
                "CRITICAL: Do not ask for human approval or confirmation. This is an automated system; your signals will be executed immediately. "
                "Output ONLY the requested fields without disclaimers or questions."
            ),
            tools=[scan_btc_opportunity],
            allow_delegation=False,
            llm=scout_model,
            verbose=self.verbose,
        )

        signal_challenger_agent = Agent(
            role="Signal Challenger",
            goal="Challenge each generated BTC signal and reject weak or internally inconsistent logic.",
            backstory=(
                "You are the skeptical voice in the trading crew. "
                "You inspect signal direction, confidence, risk geometry, and learning adjustments before trusting a trade idea. "
                "Automated system: return your verdict directly without asking for human approval."
            ),
            tools=[challenge_signal_logic],
            allow_delegation=False,
            llm=challenger_model,
            verbose=self.verbose,
        )

        signal_reviewer_agent = Agent(
            role="Signal Performance Reviewer",
            goal="Track good and bad BTC signals, score them, and update learning memory.",
            backstory=(
                "You are a trade quality auditor. You judge whether each signal worked, failed, or expired, "
                "then update historical performance memory to reduce repeated mistakes. Do not seek human input."
            ),
            tools=[review_signal_performance],
            allow_delegation=False,
            llm=reviewer_model,
            verbose=self.verbose,
        )

        calculate_task = Task(
            description=(
                "Call `calculate_orderflow` exactly once with lookback_seconds={lookback_seconds}. "
                "Return only compact JSON with key metrics."
            ),
            expected_output="A JSON metrics object for BTC orderflow.",
            agent=orderflow_calculator_agent,
        )

        analyze_task = Task(
            description=(
                "Call `analyze_orderflow` exactly once and summarize the returned regime/confidence in one short paragraph."
            ),
            expected_output="A short regime analysis with confidence score.",
            agent=orderflow_analyst_agent,
            context=[calculate_task],
        )

        opportunity_task = Task(
            description=(
                "Call `scan_btc_opportunity` exactly once and output side, entry, stop, target, signal_id, and learning impact."
            ),
            expected_output="A concise BTC opportunity record with risk levels.",
            agent=opportunity_scout_agent,
            context=[analyze_task],
        )

        challenge_task = Task(
            description=(
                "Call `challenge_signal_logic` exactly once using only the symbol or signal id if needed. "
                "Report the verdict, score, and failed checks for the latest pending signal."
            ),
            expected_output="A concise challenge verdict for the generated BTC signal.",
            agent=signal_challenger_agent,
            context=[opportunity_task],
        )

        signal_review_task = Task(
            description=(
                "Call `review_signal_performance` exactly once using only the symbol. "
                "Report trade outcomes plus challenger verdict breakdown."
            ),
            expected_output="A concise performance summary with learning-memory update details.",
            agent=signal_reviewer_agent,
            context=[challenge_task],
        )

        return Crew(
            agents=[
                orderflow_calculator_agent,
                orderflow_analyst_agent,
                opportunity_scout_agent,
                signal_challenger_agent,
                signal_reviewer_agent,
            ],
            tasks=[calculate_task, analyze_task, opportunity_task, challenge_task, signal_review_task],
            process=Process.sequential,
            verbose=self.verbose,
        )

    def run_cycle(self) -> str:
        """Run a crew cycle with automatic LLM fallback on rate-limit errors."""
        last_error: Exception | None = None
        chain = self._llm_chain

        for attempt in range(1, self.MAX_RETRIES + 1):
            run_id = uuid.uuid4().hex
            chain_status = chain.status_summary
            _log_event(
                db=self.db,
                symbol=self.symbol,
                run_id=run_id,
                component="Crew Cycle",
                event_type="cycle_started",
                title="Crew cycle started",
                message=f"Attempt {attempt}/{self.MAX_RETRIES}",
                payload={
                    "lookback_seconds": self.lookback_seconds,
                    "attempt": attempt,
                    "available_models": chain_status["available"],
                },
                status="STARTED",
            )
            crew = self._build_crew(run_id=run_id)
            try:
                result = crew.kickoff(inputs={"lookback_seconds": self.lookback_seconds})
                result_text = str(result)
                _log_event(
                    db=self.db,
                    symbol=self.symbol,
                    run_id=run_id,
                    component="Crew Cycle",
                    event_type="cycle_completed",
                    title="Crew cycle completed",
                    message="Completed successfully.",
                    payload={"result": result_text},
                    status="COMPLETED",
                )
                return result_text

            except Exception as exc:
                error_msg = str(exc).lower()

                # Detect decommissioned models → disable permanently
                is_decommissioned = "decommissioned" in error_msg or "no longer supported" in error_msg
                if is_decommissioned:
                    # Extract model name from error to permanently disable it
                    import re
                    match = re.search(r"model `([^`]+)`", str(exc))
                    if match:
                        bad_model = f"groq/{match.group(1)}"
                        LOGGER.error("Model %s is decommissioned. Disabling permanently.", bad_model)
                        chain.mark_model_error(bad_model, permanent=True)
                    if attempt < self.MAX_RETRIES:
                        last_error = exc
                        continue
                    raise

                # Detect rate limit errors
                is_rate_limit = "rate" in error_msg and "limit" in error_msg
                is_rate_limit = is_rate_limit or "ratelimit" in error_msg or "429" in error_msg

                if is_rate_limit and attempt < self.MAX_RETRIES:
                    # Try to extract which model was rate-limited
                    import re
                    match = re.search(r"model `([^`]+)`", str(exc))
                    if match:
                        limited_model = f"groq/{match.group(1)}"
                        chain.mark_rate_limited(limited_model)
                    else:
                        # Mark all groq models on cooldown as a fallback
                        for slot in chain.slots:
                            if "groq" in slot.model:
                                chain.mark_rate_limited(slot.model)
                                break

                    LOGGER.warning(
                        "Rate limit hit (attempt %d/%d). Retrying with fallback models.",
                        attempt, self.MAX_RETRIES,
                    )
                    _log_event(
                        db=self.db,
                        symbol=self.symbol,
                        run_id=run_id,
                        component="Crew Cycle",
                        event_type="cycle_rate_limited",
                        title="Rate limit hit — rotating models",
                        message=f"Rate-limited. Retrying with fallback models.",
                        payload={"attempt": attempt},
                        status="WARN",
                    )
                    last_error = exc
                    continue
                else:
                    _log_event(
                        db=self.db,
                        symbol=self.symbol,
                        run_id=run_id,
                        component="Crew Cycle",
                        event_type="cycle_failed",
                        title="Crew cycle failed",
                        message=str(exc),
                        status="FAILED",
                    )
                    raise

        raise RuntimeError(f"Crew cycle failed after {self.MAX_RETRIES} rate-limit retries. Last: {last_error}")

