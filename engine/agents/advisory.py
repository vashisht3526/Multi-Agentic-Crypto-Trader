"""
LLM Advisory Agents — Trend-Capture Architecture.

Two agents: TrendStrategist + RiskEngine.
Both are ADVISORY ONLY. They NEVER block or veto trades.
All gating is done by Python math in backbone.py.
"""

import json
import logging
from typing import Any, Dict

from litellm import completion
from engine.agents.llm_fallback import get_llm_chain

LOGGER = logging.getLogger(__name__)


def _execute_llm(
    agent_role: str, system_prompt: str, user_prompt: str, is_json: bool = True
) -> Dict[str, Any]:
    chain = get_llm_chain()
    for attempt in range(3):
        slot = chain.get_slot_for_agent(agent_role)
        try:
            response = completion(
                model=slot.model,
                api_key=slot.api_key,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"}
                if is_json and "groq" in slot.model
                else None,
                timeout=15,
            )
            content = response.choices[0].message.content
            if is_json:
                if content.startswith("```json"):
                    content = content[7:-3]
                elif content.startswith("```"):
                    content = content[3:-3]
                return json.loads(content)
            return {"content": content}
        except Exception as e:
            error_msg = str(e).lower()
            if "rate limit" in error_msg or "429" in error_msg:
                chain.mark_rate_limited(slot.model)
            if attempt == 2:
                LOGGER.error(f"[{agent_role}] Failed: {e}")
                raise
    return {}


class TrendStrategist:
    """
    Agent 1: Confirms the setup is a high-probability trend continuation entry.

    Receives multi-TF alignment data, microstructure, and funding context.
    ADVISORY ONLY — logged to crew_events, never vetos.
    """

    @staticmethod
    def evaluate(data: Dict[str, Any]) -> Dict[str, Any]:
        prompt = f"""
        You are TrendStrategist. You receive multi-timeframe alignment data
        and microstructure context. Your job: confirm the setup is a
        high-probability trend continuation entry.

        ECONOMIC REALITY:
        - Round-trip fees: 0.09% (maker 0.02% + taker 0.05% + 0.02% slippage)
        - Minimum gross edge: 0.18%, target 0.5%-2.0% gross per trade
        - Hold times: 15 minutes to 2 hours
        - Win rate target: 35-45% with 3:1 to 5:1 R:R

        Current Data:
        - Alignment Score: {data.get("alignment_score", 0):.2f}
        - Daily Bias: {data.get("daily_bias", "UNKNOWN")}
        - 4H Structure Direction: {data.get("four_h_direction", "UNKNOWN")}
        - 1H RSI: {data.get("one_h_rsi", 50):.1f}
        - 15m NOFI Composite: {data.get("nofi_composite", 0):.4f}
        - OBI: {data.get("obi", 0):.4f}
        - Current Funding Rate: {data.get("funding_rate", 0)*100:.4f}%
        - Volatility Regime: {data.get("volatility_regime", "UNKNOWN")}
        - Pullback Complete: {data.get("pullback_complete", False)}
        - Candidate Side: {data.get("candidate_side", "NONE")}

        Rules:
        - If alignment_score < 0.7: confidence = 0.0
        - If funding is against the trade: confidence -= 0.2
        - If NOFI < 0.30: confidence -= 0.3
        - If pullback is not complete: confidence -= 0.2

        Output JSON ONLY:
        {{"verdict": "ALIGN|NO_ALIGN", "confidence": 0.0-1.0,
          "suggested_tp_mult": 2.0-5.0, "reasoning": "max 100 chars"}}
        """
        result = _execute_llm(
            "TrendStrategist",
            prompt,
            "Analyze trend alignment and output JSON.",
            is_json=True,
        )
        LOGGER.info(f"TrendStrategist: {result}")
        return result


class RiskEngine:
    """
    Agent 2: Verifies the trade is economically viable.

    Receives TrendStrategist verdict, edge metrics, portfolio state.
    ADVISORY ONLY — logged to crew_events, never vetos.
    """

    @staticmethod
    def evaluate(data: Dict[str, Any]) -> Dict[str, Any]:
        prompt = f"""
        You are RiskEngine. You verify the trade is economically viable.

        ECONOMIC REALITY:
        - Round-trip fees: 0.09%
        - Minimum net edge: 0.06%
        - Max daily drawdown: 3%
        - Max active trades: 2

        Inputs:
        - TrendStrategist Verdict: {data.get("trend_verdict", "UNKNOWN")}
        - TrendStrategist Confidence: {data.get("trend_confidence", 0):.2f}
        - Gross Edge: {data.get("gross_edge_pct", 0):.3f}%
        - Net Edge: {data.get("net_edge_pct", 0):.3f}%
        - Position Size: {data.get("position_size_pct", 0):.2f}% of portfolio
        - Portfolio Heat (current risk): {data.get("portfolio_heat", 0):.2f}%
        - Kelly Fraction: {data.get("kelly_fraction", 0):.4f}
        - Active Trades: {data.get("active_trades", 0)}

        Rules:
        - If net edge < 0.06%: REJECT
        - If current drawdown > 3%: REJECT
        - If 2 active trades already: REJECT
        - If Kelly fraction < 0.1: REJECT (negative expectancy)

        Output JSON ONLY:
        {{"verdict": "PASS|REJECT", "risk_note": "max 100 chars"}}
        """
        result = _execute_llm(
            "RiskEngine",
            prompt,
            "Verify economic viability and output JSON.",
            is_json=True,
        )
        LOGGER.info(f"RiskEngine: {result}")
        return result
