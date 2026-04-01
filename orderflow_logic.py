from __future__ import annotations

from typing import Any


BULLISH_REGIMES = {"strong_buy_pressure", "mild_buy_pressure"}
BEARISH_REGIMES = {"strong_sell_pressure", "mild_sell_pressure"}
PASSIVE_REGIMES = {"balanced", "thin_market"}


def build_orderflow_analysis(snapshot: dict[str, Any]) -> dict[str, Any]:
    trade_count = int(snapshot["trade_count"])
    taker_buy_volume = float(snapshot["taker_buy_volume"])
    taker_sell_volume = float(snapshot["taker_sell_volume"])
    volume_delta = float(snapshot["volume_delta"])
    imbalance = float(snapshot["imbalance"])
    last_price = float(snapshot["last_price"])

    total_volume = taker_buy_volume + taker_sell_volume
    if trade_count < 50 or total_volume <= 0:
        regime = "thin_market"
    elif imbalance >= 0.25:
        regime = "strong_buy_pressure"
    elif imbalance >= 0.12:
        regime = "mild_buy_pressure"
    elif imbalance <= -0.25:
        regime = "strong_sell_pressure"
    elif imbalance <= -0.12:
        regime = "mild_sell_pressure"
    else:
        regime = "balanced"

    confidence = min(0.95, max(0.10, abs(imbalance) * 2.2 + min(trade_count / 1500.0, 0.25)))
    summary = (
        f"Regime={regime}, trades={trade_count}, buy_vol={taker_buy_volume:.4f}, "
        f"sell_vol={taker_sell_volume:.4f}, delta={volume_delta:.4f}, imbalance={imbalance:.4f}, "
        f"last_price={last_price:.2f}"
    )

    return {
        "regime": regime,
        "confidence": round(confidence, 4),
        "summary": summary,
        "trade_count": trade_count,
        "taker_buy_volume": taker_buy_volume,
        "taker_sell_volume": taker_sell_volume,
        "volume_delta": volume_delta,
        "imbalance": imbalance,
        "last_price": last_price,
    }


def derive_trading_opportunity(symbol: str, analysis: dict[str, Any], last_price: float) -> dict[str, Any]:
    regime = analysis["regime"]
    confidence = float(analysis["confidence"])

    if regime in BULLISH_REGIMES and confidence >= 0.72:
        side = "LONG"
    elif regime in BEARISH_REGIMES and confidence >= 0.72:
        side = "SHORT"
    else:
        side = "FLAT"

    # Require higher confidence for mild regimes
    if side != "FLAT" and regime in {"mild_buy_pressure", "mild_sell_pressure"} and confidence < 0.80:
        side = "FLAT"

    stop_pct = 0.0050
    take_profit_pct = 0.0100

    if side == "LONG":
        stop_price = last_price * (1 - stop_pct)
        take_profit = last_price * (1 + take_profit_pct)
    elif side == "SHORT":
        stop_price = last_price * (1 + stop_pct)
        take_profit = last_price * (1 - take_profit_pct)
    else:
        stop_price = last_price
        take_profit = last_price

    rationale = (
        f"{symbol} signal={side} from regime={regime} with confidence={confidence:.2f}. "
        f"Orderflow imbalance={analysis['imbalance']:.4f}, delta={analysis['volume_delta']:.4f}."
    )

    return {
        "symbol": symbol,
        "side": side,
        "entry_price": round(last_price, 2),
        "stop_price": round(stop_price, 2),
        "take_profit": round(take_profit, 2),
        "confidence": round(confidence, 4),
        "rationale": rationale,
        "regime": regime,
    }


def _build_check(
    name: str,
    passed: bool,
    details: str,
    weight: float,
    severity: str = "soft",
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "details": details,
        "weight": weight,
        "severity": severity,
    }


def challenge_trading_signal(
    signal: dict[str, Any],
    analysis: dict[str, Any] | None = None,
    decision_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    analysis = analysis or {}
    decision_trace = decision_trace or {}

    side = str(signal.get("side", "FLAT")).upper()
    regime = str(signal.get("regime") or analysis.get("regime", "unknown"))
    confidence = float(signal.get("confidence", analysis.get("confidence", 0.0)))
    entry_price = float(signal.get("entry_price", 0.0))
    stop_price = float(signal.get("stop_price", entry_price))
    take_profit = float(signal.get("take_profit", entry_price))
    rationale = str(signal.get("rationale", ""))

    raw_opportunity = decision_trace.get("raw_opportunity", {})
    learning_recommendation = decision_trace.get("learning_recommendation", {})
    recommendation = str(learning_recommendation.get("recommendation", "insufficient_data"))
    raw_side = str(raw_opportunity.get("side", side)).upper()
    tolerance = max(abs(entry_price) * 0.000001, 0.05)

    checks: list[dict[str, Any]] = []

    expected_direction = "PASSIVE"
    if regime in BULLISH_REGIMES:
        expected_direction = "LONG"
    elif regime in BEARISH_REGIMES:
        expected_direction = "SHORT"

    if side == "LONG":
        direction_ok = expected_direction == "LONG" and confidence >= 0.55
    elif side == "SHORT":
        direction_ok = expected_direction == "SHORT" and confidence >= 0.55
    else:
        direction_ok = (
            expected_direction == "PASSIVE"
            or confidence < 0.55
            or recommendation == "avoid"
            or raw_side == "FLAT"
        )
    checks.append(
        _build_check(
            name="direction_matches_regime",
            passed=direction_ok,
            details=(
                f"side={side}, regime={regime}, confidence={confidence:.4f}, "
                f"expected_direction={expected_direction}, learning={recommendation}"
            ),
            weight=3.0,
            severity="hard",
        )
    )

    if side == "LONG":
        geometry_ok = stop_price < entry_price < take_profit
        geometry_details = (
            f"LONG geometry requires stop < entry < target; "
            f"stop={stop_price:.2f}, entry={entry_price:.2f}, target={take_profit:.2f}"
        )
    elif side == "SHORT":
        geometry_ok = take_profit < entry_price < stop_price
        geometry_details = (
            f"SHORT geometry requires target < entry < stop; "
            f"target={take_profit:.2f}, entry={entry_price:.2f}, stop={stop_price:.2f}"
        )
    else:
        geometry_ok = (
            abs(stop_price - entry_price) <= tolerance and abs(take_profit - entry_price) <= tolerance
        )
        geometry_details = (
            f"FLAT geometry requires entry ~= stop ~= target within {tolerance:.4f}; "
            f"entry={entry_price:.2f}, stop={stop_price:.2f}, target={take_profit:.2f}"
        )
    checks.append(
        _build_check(
            name="risk_geometry",
            passed=geometry_ok,
            details=geometry_details,
            weight=2.0,
            severity="hard",
        )
    )

    if side in {"LONG", "SHORT"}:
        risk = abs(entry_price - stop_price)
        reward = abs(take_profit - entry_price)
        reward_to_risk = (reward / risk) if risk > 0 else 0.0
        reward_ok = 1.5 <= reward_to_risk <= 5.0
        reward_details = (
            f"reward_to_risk={reward_to_risk:.3f}, reward={reward:.2f}, risk={risk:.2f}"
        )
    else:
        reward_ok = True
        reward_details = "No directional risk for FLAT signal."
    checks.append(
        _build_check(
            name="reward_to_risk",
            passed=reward_ok,
            details=reward_details,
            weight=1.0,
        )
    )

    if recommendation == "avoid":
        learning_ok = side == "FLAT"
        learning_details = f"Learning said avoid, final side={side}."
    elif raw_opportunity:
        if raw_side == side:
            learning_ok = True
        elif raw_side in {"LONG", "SHORT"} and side == "FLAT":
            learning_ok = recommendation == "avoid"
        else:
            learning_ok = False
        learning_details = (
            f"raw_side={raw_side}, final_side={side}, learning={recommendation}, "
            f"sample_size={learning_recommendation.get('sample_size', 0)}"
        )
    else:
        learning_ok = True
        learning_details = "No raw opportunity trace stored."
    checks.append(
        _build_check(
            name="learning_adjustment_consistency",
            passed=learning_ok,
            details=learning_details,
            weight=2.0,
        )
    )

    rationale_ok = f"signal={side}" in rationale and f"regime={regime}" in rationale
    checks.append(
        _build_check(
            name="rationale_traceability",
            passed=rationale_ok,
            details=f"rationale='{rationale}'",
            weight=1.0,
        )
    )

    total_weight = sum(float(check["weight"]) for check in checks) or 1.0
    passed_weight = sum(float(check["weight"]) for check in checks if check["passed"])
    hard_fail = any(check["severity"] == "hard" and not check["passed"] for check in checks)
    score = round(passed_weight / total_weight, 4)

    failed_checks = [check["name"] for check in checks if not check["passed"]]
    if hard_fail or score < 0.65:
        verdict = "REJECT"
    elif score < 0.85:
        verdict = "WEAK_APPROVE"
    else:
        verdict = "APPROVE"

    if failed_checks:
        summary = f"{verdict}: failed checks={', '.join(failed_checks)}"
    else:
        summary = f"{verdict}: signal passed challenger rulebook checks."

    return {
        "logic_version": "orderflow_rulebook_v2",
        "verdict": verdict,
        "score": score,
        "summary": summary,
        "checks": checks,
    }
