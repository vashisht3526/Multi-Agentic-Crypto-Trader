import json
import logging
from typing import Any, Dict

from litellm import completion
from llm_fallback import get_llm_chain

LOGGER = logging.getLogger(__name__)


def _execute_llm(
    agent_role: str, system_prompt: str, user_prompt: str, is_json: bool = True
) -> Dict[str, Any]:
    chain = get_llm_chain()
    # Try up to 3 times to get valid JSON
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
                # Sometimes markdown ```json wrap is returned even with response_format
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
                LOGGER.error(f"[{agent_role}] Failed to generate response: {e}")
                raise
    return {}


class MacroStrategist:
    """Agent 1: Determines Market Regime and sets Permission State."""

    @staticmethod
    def evaluate(macro_data: Dict[str, Any]) -> Dict[str, Any]:
        prompt = f"""
        You are the Chief Macro Strategist for a high-frequency Bitcoin trading desk. 
        Your goal is to identify the current "Market Regime" to prevent the Scout from trading against institutional momentum.

        Current Data:
        - 4H Trend (EMA 200): {macro_data["ema_200_4h"]}
        - 1H RSI: {macro_data["rsi_1h"]}
        - Daily VWAP: {macro_data["daily_vwap"]}
        - Current Price: {macro_data["current_price"]}

        Task Logic:
        Determine the Directional Bias:
        - BULLISH: Price > Daily VWAP AND Price > 4H EMA 200.
        - BEARISH: Price < Daily VWAP AND Price < 4H EMA 200.
        - RANGING: Price is oscillating within 0.2% of VWAP.
        
        Set the Permission State:
        - If BULLISH: Output "ONLY_LONG".
        - If BEARISH: Output "ONLY_SHORT".
        - If RANGING: Output "NEUTRAL_SCALP_ONLY" with tighter targets.
        
        Identify No-Trade Zones: If 1H RSI is > 75 or < 25, signal "EXHAUSTION_WARNING" to the Sentinel.
        
        Output Requirement: You must provide a concise JSON object: 
        {{"bias": "STRING", "permission": "STRING", "reasoning": "STRING"}}
        """
        return _execute_llm(
            "Macro Strategist",
            prompt,
            "Analyze the market regime and output JSON.",
            is_json=True,
        )


class MicroSniper:
    """Agent 2: Identifies microstructure anomalies using DuckDB ticks."""

    @staticmethod
    def scan(micro_data: Dict[str, Any], permission_state: str) -> Dict[str, Any]:
        LOGGER.info(
            f"MicroSniper inputs: NOFI={micro_data['nofi']}, Cumulative Delta={micro_data['cumulative_delta']}, Displacement={micro_data['displacement_ticks']} ticks, Permission={permission_state}"
        )
        prompt = f"""
        You are a Micro-Microstructure Analyst. You ignore broad "momentum" and focus exclusively on Order Flow Imbalance (OFI) and Trade Flow Imbalance (TFI).

        Inputs:
        - NOFI (Normalized OFI): {micro_data["nofi"]}
        - Cumulative Delta: {micro_data["cumulative_delta"]}
        - Tick Price Displacement: {micro_data["displacement_ticks"]} ticks
        - Macro Permission State: {permission_state}

        Strict Operational Rules:
        - Propose a signal ONLY if NOFI > ±0.30 AND the Permission State matches your direction.
        - Absorption Check: If Cumulative Delta (aggressive volume) is high (abs(delta) > 5) but price displacement is < 2 ticks, you are hitting a "Passive Wall." FLAG this as "ABSORPTION_DETECTED" and direction="FLAT".
        - Exhaustion Check: If volume is declining while price moves further, signal "MOMENTUM_DIVERGENCE".

        Logic Enhancement: Your goal is NOT to take many trades. It is to find the one setup where aggressive market orders are overwhelming the limit order book.

        Output: Provide a structured JSON proposal:
        {{"direction": "LONG/SHORT/FLAT", "confidence": 0.0-1.0, "anomaly_type": "STRING", "logic": "STRING"}}
        """
        result = _execute_llm(
            "BTC Opportunity Scout",
            prompt,
            "Analyze microstructure and output JSON proposal.",
            is_json=True,
        )
        LOGGER.info(f"MicroSniper output: {result}")
        return result


class LiquidityHeatmapAnalyst:
    """Agent 3: Identifies required fuel (Liquidation Magnets)."""

    @staticmethod
    def analyze(
        scout_direction: str, current_price: float, magnets: list[dict]
    ) -> Dict[str, Any]:
        prompt = f"""
        You are a Liquidity Map Expert. You identify "Liquidity Magnets" where leveraged stop-losses are clustered.

        Inputs:
        - Scout Entry Direction: {scout_direction}
        - Current Price: {current_price}
        - Liquidation Clusters (Heuristic): {json.dumps(magnets)}

        Task:
        - Cross-reference the Scout's entry with the nearest liquidation cluster.
        - If the Scout wants to go LONG, but the nearest Short Liquidation cluster (above price) is > 1.5% away, reject the signal as "LACK_OF_FUEL".
        - If a cluster is within 0.3% - 0.6% of entry, label it a "MAGNET_MOVE" with high conviction.
        - Stop-Loss Safety: Never allow an entry if a massive cluster of the SAME direction is just below/above the entry (Market Makers will "sweep" it).

        Output: Provide a JSON object:
        {{"liquidity_fuel": "YES/NO", "magnet_price": NUMBER, "risk_zone_proximity": "PERCENT"}}
        """
        return _execute_llm(
            "Orderflow Analyst",
            prompt,
            "Analyze liquidity magnets and output JSON.",
            is_json=True,
        )


class RiskSentinel:
    """Agent 4: Gatekeeper using negative selection and ATR targets."""

    @staticmethod
    def challenge(
        scout_proposal: Dict, macro_bias: str, liquidity: Dict, micro_data: Dict
    ) -> Dict[str, Any]:
        prompt = f"""
        You are the Risk Sentinel. Your default state is to REJECT the Scout's signal. You only approve if Macro and Liquidity agents provide unanimous support.

        Inputs:
        - Scout Direction: {scout_proposal["direction"]}
        - Target Entry: {micro_data["close"]}
        - Macro Permission: {macro_bias}
        - Liquidity Fuel: {liquidity["liquidity_fuel"]}
        - 3Min ATR (Proxy): {micro_data["atr_3m_pct"] * 100}%
        - Kyle's Lambda (Price Impact): {micro_data["kyles_lambda"]}

        Decision Logic:
        1. Dynamic Targets: Calculate Take Profit (TP) and Stop Loss (SL) using ATR (Average True Range) multipliers.
           - TP = Entry +/- (1.5 * ATR)
           - SL = Entry -/+ (1.2 * ATR)
           - NEVER use a static 1% target.
        2. Negative Selection: Identify reasons why this trade will FAIL. E.g., low NOFI, trading against Macro Bias. If Lambda > 0.1, tighten SL by 20%.
        
        Final Verdict: You have VETO power. If any metric is "CRITICAL" (e.g., LACK_OF_FUEL, ABSORPTION_DETECTED, macro mismatch), output verdict="REJECT".
        
        Output: Provide JSON:
        {{"verdict": "APPROVE/REJECT", "tp": NUMBER, "sl": NUMBER, "reason": "STRING"}}
        """
        return _execute_llm(
            "Signal Challenger",
            prompt,
            "Challenge the signal and output JSON target/stop.",
            is_json=True,
        )
