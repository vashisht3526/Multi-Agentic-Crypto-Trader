from __future__ import annotations

from datetime import datetime
from html import escape
import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    import altair as alt
except Exception:  # pragma: no cover - altair ships with Streamlit, but keep a fallback.
    alt = None

from settings import load_settings


CAPITAL_BASE = 100000.0
ACTIVE_STATUSES = {"OPEN", "PENDING"}
CLOSED_STATUSES = {"WIN", "LOSS", "EXPIRED", "NO_DATA", "SKIPPED"}
VALIDITY_LIVE = {"LIVE", "OVERDUE", "QUESTIONABLE"}
SNAPSHOT_KEY_SCROLL = "btc_orderflow_desk_scroll_y"


def inject_styles() -> None:
    st.markdown(
        """
        <style>
            :root {
                --paper: #f8f3ea;
                --paper-soft: #fffdf8;
                --ink: #14110d;
                --muted: #6b6357;
                --line: #d8ceb9;
                --accent: #b07a22;
                --bull: #0c8a59;
                --bear: #b84d36;
                --steel: #4a5c7a;
            }

            .stApp {
                background:
                    radial-gradient(circle at top left, rgba(176, 122, 34, 0.10), transparent 24%),
                    radial-gradient(circle at bottom right, rgba(12, 138, 89, 0.08), transparent 24%),
                    linear-gradient(180deg, #fbf7ef 0%, #f3ecdf 100%);
                color: var(--ink);
            }

            [data-testid="stHeader"] {
                background: rgba(251, 247, 239, 0.86);
            }

            .block-container {
                max-width: 1560px;
                padding-top: 1.2rem;
                padding-bottom: 2.2rem;
            }

            h1, h2, h3, h4 {
                font-family: Aptos, "Segoe UI", sans-serif;
                color: var(--ink);
                letter-spacing: -0.03em;
            }

            .desk-hero {
                display: flex;
                align-items: end;
                justify-content: space-between;
                gap: 1.25rem;
                padding: 0.2rem 0 0.9rem;
                border-bottom: 1px solid rgba(216, 206, 185, 0.65);
                margin-bottom: 1rem;
            }

            .desk-label {
                color: var(--muted);
                font-size: 0.72rem;
                letter-spacing: 0.22em;
                text-transform: uppercase;
                margin-bottom: 0.35rem;
            }

            .desk-headline {
                font-size: clamp(2.2rem, 3vw, 3.45rem);
                line-height: 0.94;
                font-weight: 750;
                margin: 0;
            }

            .desk-subline {
                color: var(--muted);
                font-size: 0.98rem;
                max-width: 56rem;
                margin-top: 0.6rem;
            }

            .surface {
                background: rgba(255, 253, 248, 0.88);
                border: 1px solid rgba(216, 206, 185, 0.88);
                border-radius: 18px;
                padding: 1rem 1.05rem 1.05rem;
                box-shadow: 0 18px 44px rgba(31, 24, 14, 0.04);
            }

            .surface.tight {
                padding: 0.85rem 0.95rem;
            }

            .surface-title {
                font-size: 1rem;
                font-weight: 700;
                margin-bottom: 0.2rem;
            }

            .surface-subtitle {
                color: var(--muted);
                font-size: 0.86rem;
                margin-bottom: 0.75rem;
            }

            .metric-band {
                background: rgba(255, 253, 248, 0.86);
                border: 1px solid var(--line);
                border-radius: 16px;
                padding: 0.92rem 1rem 0.95rem;
                min-height: 7rem;
            }

            .metric-grid {
                display: grid;
                grid-template-columns: repeat(6, minmax(0, 1fr));
                gap: 0.8rem;
                margin: 1rem 0 1.25rem;
            }

            .metric-band.positive { border-color: rgba(12, 138, 89, 0.34); }
            .metric-band.negative { border-color: rgba(184, 77, 54, 0.34); }
            .metric-band.alert { border-color: rgba(176, 122, 34, 0.34); }

            .metric-label {
                color: var(--muted);
                font-size: 0.72rem;
                text-transform: uppercase;
                letter-spacing: 0.14em;
                margin-bottom: 0.3rem;
            }

            .metric-value {
                font-size: 1.7rem;
                line-height: 1;
                font-weight: 750;
                margin-bottom: 0.25rem;
            }

            .metric-note {
                color: var(--muted);
                font-size: 0.88rem;
                line-height: 1.35;
            }

            .focus-banner {
                background:
                    linear-gradient(135deg, rgba(255, 253, 248, 0.97), rgba(247, 240, 224, 0.94));
                border: 1px solid rgba(176, 122, 34, 0.22);
                border-radius: 20px;
                padding: 1rem 1rem 1.05rem;
                box-shadow: 0 16px 40px rgba(25, 20, 12, 0.05);
            }

            .focus-grid {
                display: grid;
                grid-template-columns: 1.4fr 1fr 1fr;
                gap: 0.7rem 1rem;
                margin-top: 0.8rem;
            }

            .focus-label {
                color: var(--muted);
                font-size: 0.72rem;
                text-transform: uppercase;
                letter-spacing: 0.12em;
                margin-bottom: 0.2rem;
            }

            .focus-value {
                font-size: 1.04rem;
                font-weight: 650;
                line-height: 1.25;
            }

            .signal-board {
                display: grid;
                gap: 0.85rem;
            }

            .signal-card {
                background: rgba(255, 253, 248, 0.93);
                border-radius: 18px;
                padding: 0.95rem 1rem 1rem;
                border: 1px solid rgba(216, 206, 185, 0.88);
                min-height: 14rem;
                box-shadow: 0 12px 32px rgba(31, 24, 14, 0.04);
            }

            .signal-card.live { border-color: rgba(12, 138, 89, 0.35); }
            .signal-card.warn { border-color: rgba(176, 122, 34, 0.36); }
            .signal-card.negative { border-color: rgba(184, 77, 54, 0.36); }

            .signal-top {
                display: flex;
                justify-content: space-between;
                gap: 0.5rem;
                align-items: flex-start;
                margin-bottom: 0.65rem;
            }

            .signal-title {
                font-size: 1.02rem;
                font-weight: 750;
                line-height: 1.1;
            }

            .signal-subtitle {
                color: var(--muted);
                font-size: 0.82rem;
                margin-top: 0.12rem;
            }

            .signal-grid {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 0.55rem 0.85rem;
                margin-top: 0.8rem;
            }

            .signal-cell {
                padding: 0.55rem 0.65rem;
                background: rgba(247, 242, 232, 0.65);
                border-radius: 12px;
                border: 1px solid rgba(216, 206, 185, 0.55);
            }

            .signal-cell-label {
                color: var(--muted);
                font-size: 0.7rem;
                letter-spacing: 0.1em;
                text-transform: uppercase;
            }

            .signal-cell-value {
                font-size: 0.95rem;
                font-weight: 650;
                margin-top: 0.18rem;
                line-height: 1.18;
                overflow-wrap: anywhere;
            }

            .signal-rationale {
                color: var(--muted);
                font-size: 0.86rem;
                margin-top: 0.7rem;
                line-height: 1.45;
            }

            .chip {
                display: inline-block;
                padding: 0.16rem 0.48rem;
                border-radius: 999px;
                border: 1px solid var(--line);
                font-size: 0.7rem;
                letter-spacing: 0.09em;
                text-transform: uppercase;
                background: rgba(255, 253, 248, 0.8);
                white-space: nowrap;
            }

            .chip.live {
                color: var(--bull);
                border-color: rgba(12, 138, 89, 0.36);
            }

            .chip.warn {
                color: var(--accent);
                border-color: rgba(176, 122, 34, 0.36);
            }

            .chip.negative {
                color: var(--bear);
                border-color: rgba(184, 77, 54, 0.36);
            }

            .chip.neutral {
                color: var(--steel);
                border-color: rgba(74, 92, 122, 0.26);
            }

            .chip.compact {
                font-size: 0.64rem;
                padding: 0.12rem 0.38rem;
            }

            .mini-note {
                color: var(--muted);
                font-size: 0.82rem;
                margin-top: 0.45rem;
            }

            .table-hint {
                color: var(--muted);
                font-size: 0.84rem;
                margin-bottom: 0.45rem;
            }

            .scroll-anchor {
                height: 1px;
            }

            @media (max-width: 960px) {
                .metric-grid {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }

                .focus-grid {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }
            }

            @media (max-width: 640px) {
                .metric-grid,
                .focus-grid,
                .signal-grid {
                    grid-template-columns: 1fr;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def install_auto_refresh(enabled: bool, refresh_seconds: int) -> None:
    if not enabled:
        return

    delay_ms = int(max(refresh_seconds, 1) * 1000)
    components.html(
        f"""
        <script>
            (function() {{
                const key = "{SNAPSHOT_KEY_SCROLL}";
                const parentWindow = window.parent;

                function saveScroll() {{
                    try {{
                        const y = parentWindow.scrollY || parentWindow.pageYOffset || 0;
                        parentWindow.localStorage.setItem(key, String(y));
                    }} catch (e) {{}}
                }}

                function restoreScroll() {{
                    try {{
                        const raw = parentWindow.localStorage.getItem(key);
                        if (raw !== null) {{
                            const y = parseInt(raw, 10);
                            if (!Number.isNaN(y)) {{
                                parentWindow.scrollTo(0, y);
                            }}
                        }}
                    }} catch (e) {{}}
                }}

                restoreScroll();
                setTimeout(restoreScroll, 250);
                setTimeout(restoreScroll, 900);
                window.addEventListener("beforeunload", saveScroll);
                setTimeout(function() {{
                    saveScroll();
                    parentWindow.location.reload();
                }}, {delay_ms});
            }})();
        </script>
        """,
        height=0,
        width=0,
    )


def json_safe(value: object) -> object:
    return json.loads(json.dumps(value, default=str))


def load_dashboard_data(snapshot_path: str) -> dict[str, Any]:
    path = Path(snapshot_path)
    if not path.exists():
        return {
            "generated_at": None,
            "symbol": "BTCUSDT",
            "health": {"status": "waiting", "reason": f"Snapshot file not found: {path}"},
            "error": f"Snapshot file not found: {path}",
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "generated_at": None,
            "symbol": "BTCUSDT",
            "health": {"status": "error", "reason": str(exc)},
            "error": f"Could not read snapshot: {exc}",
        }


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def to_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    ts = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    if getattr(ts, "tzinfo", None) is not None:
        return ts.tz_convert(None).to_pydatetime()
    return ts.to_pydatetime()


def human_age(value: datetime | None, now: datetime | None = None) -> str:
    if value is None:
        return "unknown"
    now = now or datetime.utcnow()
    seconds = max(0, int((now - value).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def format_indian_number(value: float | int) -> str:
    negative = float(value) < 0
    n = str(abs(int(round(float(value)))))
    if len(n) <= 3:
        result = n
    else:
        last3 = n[-3:]
        rest = n[:-3]
        groups: list[str] = []
        while len(rest) > 2:
            groups.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.append(rest)
        result = ",".join(reversed(groups)) + "," + last3
    return f"-{result}" if negative else result


def format_money(value: float | int) -> str:
    sign = "+" if float(value) >= 0 else "-"
    return f"{sign}{format_indian_number(abs(float(value)))}"


def format_price(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):,.2f}"


def format_pct(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}%"


def shorten_signal_id(signal_id: Any) -> str:
    text = str(signal_id or "")
    return text[:8] if len(text) > 8 else text


def chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), max(size, 1))]


def signal_status(signal: dict[str, Any]) -> str:
    status = str(signal.get("status") or signal.get("signal_status") or "UNKNOWN").upper()
    if not status:
        return "UNKNOWN"
    return status


def signal_verdict(signal: dict[str, Any]) -> str:
    verdict = str(signal.get("challenge_verdict") or "PENDING").upper()
    return verdict


def signal_validity(signal: dict[str, Any], now: datetime) -> str:
    status = signal_status(signal)
    verdict = signal_verdict(signal)
    due_at = to_datetime(signal.get("evaluation_due_time"))

    if status in CLOSED_STATUSES:
        return "CLOSED"
    if status in ACTIVE_STATUSES:
        if due_at and due_at < now:
            return "OVERDUE"
        if verdict == "REJECT":
            return "QUESTIONABLE"
        return "LIVE"
    if verdict == "REJECT":
        return "QUESTIONABLE"
    return status


def signal_side(signal: dict[str, Any]) -> str:
    return str(signal.get("side") or signal.get("signal_side") or "FLAT").upper()


def signal_time(signal: dict[str, Any]) -> datetime | None:
    return to_datetime(
        signal.get("closed_at")
        or signal.get("generated_at")
        or signal.get("event_time")
        or signal.get("snapshot_time")
    )


def signal_entry(signal: dict[str, Any]) -> float:
    return to_float(signal.get("entry_price") or signal.get("entry") or signal.get("price"))


def signal_close_price(signal: dict[str, Any]) -> float | None:
    value = signal.get("close_price") or signal.get("exit_price") or signal.get("last_price")
    return None if value is None else to_float(value)


def signal_pct(signal: dict[str, Any], last_price: float | None) -> float | None:
    pct = signal.get("pnl_pct")
    if pct is not None:
        return to_float(pct)

    entry = signal_entry(signal)
    if entry <= 0:
        return None

    reference = signal_close_price(signal)
    if reference is None:
        reference = last_price
    if reference is None:
        return None

    side = signal_side(signal)
    if side == "LONG":
        return ((reference - entry) / entry) * 100.0
    if side == "SHORT":
        return ((entry - reference) / entry) * 100.0
    return 0.0


def capital_pnl_from_pct(pct: float | None, capital_base: float = CAPITAL_BASE) -> float:
    if pct is None:
        return 0.0
    return capital_base * float(pct) / 100.0


def normalize_signal_row(
    signal: dict[str, Any],
    last_price: float | None,
    now: datetime,
    capital_base: float = CAPITAL_BASE,
) -> dict[str, Any]:
    generated_at = signal_time(signal)
    status = signal_status(signal)
    verdict = signal_verdict(signal)
    validity = signal_validity(signal, now)
    pct = signal_pct(signal, last_price)
    signal_capital_base = to_float(signal.get("capital_base"), default=capital_base)
    capital_pnl = capital_pnl_from_pct(pct, capital_base=signal_capital_base)
    signal_id = str(signal.get("signal_id") or signal.get("id") or "")
    challenge_summary = str(signal.get("challenge_summary") or "")
    due_at = to_datetime(signal.get("evaluation_due_time"))
    unrealized = None
    if status in ACTIVE_STATUSES and last_price is not None and pct is None:
        unrealized = signal_pct(signal, last_price)
        capital_pnl = capital_pnl_from_pct(unrealized, capital_base=signal_capital_base)

    move_pct = unrealized if unrealized is not None else pct
    outcome_reason = str(signal.get("outcome_reason") or "")
    rationale = str(signal.get("rationale") or signal.get("summary") or "")
    live_reason = str(signal.get("live_reason") or "")
    decision_trace = signal.get("decision_trace") or signal.get("analysis") or signal.get("debug") or signal

    return {
        "signal_id": signal_id,
        "signal_id_short": shorten_signal_id(signal_id),
        "generated_at": generated_at,
        "generated_at_text": generated_at.isoformat(sep=" ", timespec="seconds") if generated_at else "unknown",
        "symbol": str(signal.get("symbol") or ""),
        "side": signal_side(signal),
        "status": status,
        "validity": validity,
        "challenge_verdict": verdict,
        "challenge_score": to_float(signal.get("challenge_score"), default=0.0) if signal.get("challenge_score") is not None else None,
        "challenge_summary": challenge_summary,
        "confidence": to_float(signal.get("confidence"), default=0.0),
        "regime": str(signal.get("regime") or ""),
        "entry_price": signal_entry(signal),
        "entry": signal_entry(signal),
        "stop_price": to_float(signal.get("stop_price") or signal.get("stop"), default=0.0),
        "stop": to_float(signal.get("stop_price") or signal.get("stop"), default=0.0),
        "take_profit": to_float(signal.get("take_profit") or signal.get("target"), default=0.0),
        "target": to_float(signal.get("take_profit") or signal.get("target"), default=0.0),
        "current_price": to_float(signal.get("current_price"), default=last_price if last_price is not None else 0.0)
        if signal.get("current_price") is not None or last_price is not None
        else None,
        "close_price": signal_close_price(signal),
        "pnl_pct": pct,
        "capital_pnl": capital_pnl,
        "capital_base": signal_capital_base,
        "capital_after": signal_capital_base + capital_pnl,
        "outcome_reason": outcome_reason,
        "reason": outcome_reason or live_reason or challenge_summary or rationale,
        "data_quality": str(signal.get("data_quality") or ""),
        "quality": str(signal.get("data_quality") or signal.get("live_state") or "unknown"),
        "rationale": rationale,
        "evaluation_due_time": due_at,
        "evaluation_due_text": due_at.isoformat(sep=" ", timespec="seconds") if due_at else "n/a",
        "age": human_age(generated_at, now),
        "is_active": status in ACTIVE_STATUSES,
        "unrealized_pnl_pct": unrealized,
        "move_pct": move_pct,
        "live_state": str(signal.get("live_state") or ""),
        "is_valid": bool(signal.get("is_valid")) if signal.get("is_valid") is not None else validity == "LIVE",
        "live_reason": live_reason,
        "decision_trace": decision_trace,
        "debug": signal,
    }


def flatten_signals(
    snapshot: dict[str, Any],
    last_price: float | None,
    now: datetime,
    capital_base: float = CAPITAL_BASE,
) -> list[dict[str, Any]]:
    combined: dict[str, dict[str, Any]] = {}

    def merge_rows(raw_rows: list[dict[str, Any]] | None) -> None:
        for raw in raw_rows or []:
            row = normalize_signal_row(raw, last_price=last_price, now=now, capital_base=capital_base)
            key = row["signal_id"] or f"{row['generated_at_text']}:{row['side']}"
            existing = combined.get(key, {})
            combined[key] = {**existing, **row}

    merge_rows(snapshot.get("recent_signals"))
    merge_rows(snapshot.get("signal_history"))
    merge_rows(snapshot.get("active_signals"))

    latest = snapshot.get("latest_signal")
    if latest:
        row = normalize_signal_row(latest, last_price=last_price, now=now, capital_base=capital_base)
        key = row["signal_id"] or f"{row['generated_at_text']}:{row['side']}"
        existing = combined.get(key, {})
        combined[key] = {**existing, **row}

    rows = list(combined.values())
    rows.sort(
        key=lambda row: signal_time(row) or datetime.min,
        reverse=True,
    )
    return rows


def split_active_history(signals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active = [row for row in signals if row["is_active"] or row["validity"] in VALIDITY_LIVE]
    history = [row for row in signals if row not in active]
    return active, history

def build_history_frame(
    history_rows: list[dict[str, Any]],
    equity_curve: list[dict[str, Any]] | None,
    capital_base: float = CAPITAL_BASE,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = [dict(row) for row in history_rows]
    history_df = pd.DataFrame(rows)
    if not history_df.empty:
        history_df["sort_time"] = pd.to_datetime(history_df["generated_at"], errors="coerce")
        history_df = history_df.sort_values("sort_time").reset_index(drop=True)
        history_df["capital_pnl"] = history_df["capital_pnl"].fillna(0.0)
        history_df["equity_after"] = capital_base + history_df["capital_pnl"].cumsum()
        history_df["drawdown_pct"] = (
            (history_df["equity_after"] / history_df["equity_after"].cummax()) - 1.0
        ) * 100.0
        history_df["signal_label"] = history_df["signal_id_short"]

    curve_df = pd.DataFrame(equity_curve or [])
    if not curve_df.empty:
        time_col = None
        for candidate in ("time", "timestamp", "generated_at", "snapshot_time", "closed_at"):
            if candidate in curve_df.columns:
                time_col = candidate
                break
        if time_col:
            curve_df["time"] = pd.to_datetime(curve_df[time_col], errors="coerce")
        elif "time" not in curve_df.columns:
            curve_df["time"] = pd.NaT

        value_col = None
        for candidate in ("equity", "equity_value", "capital", "balance", "value"):
            if candidate in curve_df.columns:
                value_col = candidate
                break
        if value_col and value_col != "equity":
            curve_df["equity"] = pd.to_numeric(curve_df[value_col], errors="coerce")
        elif "equity" in curve_df.columns:
            curve_df["equity"] = pd.to_numeric(curve_df["equity"], errors="coerce")
        else:
            curve_df["equity"] = pd.NA
        if "drawdown_pct" not in curve_df.columns:
            curve_df["drawdown_pct"] = (
                (curve_df["equity"] / curve_df["equity"].cummax()) - 1.0
            ) * 100.0

    if curve_df.empty and not history_df.empty:
        curve_df = history_df[["sort_time", "equity_after", "drawdown_pct"]].rename(
            columns={"sort_time": "time", "equity_after": "equity"}
        )
    return history_df, curve_df


def build_equity_chart(curve_df: pd.DataFrame, capital_base: float = CAPITAL_BASE):
    if curve_df.empty or alt is None:
        return None
    data = curve_df.copy()
    data = data.dropna(subset=["time", "equity"])
    if data.empty:
        return None
    line = (
        alt.Chart(data)
        .mark_line(point=True, strokeWidth=2)
        .encode(
            x=alt.X("time:T", title="Time"),
            y=alt.Y("equity:Q", title=f"Capital starting at {format_indian_number(capital_base)}"),
            tooltip=[
                alt.Tooltip("time:T", title="Time"),
                alt.Tooltip("equity:Q", title="Equity", format=",.2f"),
            ],
        )
        .properties(height=300)
    )
    area = (
        alt.Chart(data)
        .mark_area(opacity=0.08)
        .encode(x="time:T", y="equity:Q")
        .properties(height=300)
    )
    return (area + line).interactive()


def build_pnl_chart(history_df: pd.DataFrame, capital_base: float = CAPITAL_BASE):
    if history_df.empty or alt is None:
        return None
    data = history_df.copy()
    data = data.dropna(subset=["sort_time"])
    if data.empty:
        return None
    color = alt.condition(
        alt.datum.capital_pnl >= 0,
        alt.value("#0c8a59"),
        alt.value("#b84d36"),
    )
    bars = (
        alt.Chart(data)
        .mark_bar()
        .encode(
            x=alt.X("signal_label:N", sort=None, title="Signal"),
            y=alt.Y("capital_pnl:Q", title=f"PnL on {format_indian_number(capital_base)} capital"),
            color=color,
            tooltip=[
                alt.Tooltip("signal_id_short:N", title="Signal"),
                alt.Tooltip("side:N", title="Side"),
                alt.Tooltip("status:N", title="Status"),
                alt.Tooltip("validity:N", title="Validity"),
                alt.Tooltip("pnl_pct:Q", title="PnL %", format=",.2f"),
                alt.Tooltip("capital_pnl:Q", title="Capital PnL", format=",.2f"),
                alt.Tooltip("equity_after:Q", title="Equity After", format=",.2f"),
            ],
        )
        .properties(height=280)
    )
    return bars


def render_metric_band(items: list[tuple[str, str]]) -> None:
    cells = []
    for label, value in items:
        cells.append(
            f"""
            <div class="metric-band neutral">
                <div class="metric-label">{escape(label)}</div>
                <div class="metric-value">{escape(value)}</div>
            </div>
            """
        )
    st.markdown(
        f"<div class='metric-grid'>{''.join(cells)}</div>",
        unsafe_allow_html=True,
    )


def render_focus_banner(
    signal: dict[str, Any] | None,
    last_price: float | None = None,
    capital_base: float = CAPITAL_BASE,
) -> None:
    if not signal:
        st.markdown(
            """
            <div class="focus-banner">
                <div class="surface-title">No live signal selected</div>
                <div class="surface-subtitle">The latest signal summary will appear here once the snapshot contains a candidate.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    validity = signal["validity"]
    validity_class = "live" if validity in {"LIVE"} else "warn" if validity in {"OVERDUE", "QUESTIONABLE"} else "negative"
    side = signal["side"]
    capital_pnl = signal["capital_pnl"]
    pct = signal["pnl_pct"]
    st.markdown(
        f"""
        <div class="focus-banner">
            <div class="surface-title">Current Signal Spotlight</div>
            <div class="surface-subtitle">
                This is the active or latest signal. It shows whether the setup is still valid, what the challenger thought,
                and what it did to a {format_indian_number(capital_base)} capital baseline.
            </div>
            <div class="focus-grid">
                <div>
                    <div class="focus-label">Signal</div>
                    <div class="focus-value">{escape(signal['signal_id_short'])} / {escape(side)}</div>
                </div>
                <div>
                    <div class="focus-label">Current Validity</div>
                    <div class="focus-value"><span class="chip {validity_class}">{escape(validity)}</span></div>
                </div>
                <div>
                    <div class="focus-label">Challenge Verdict</div>
                    <div class="focus-value"><span class="chip {'live' if signal['challenge_verdict'] == 'APPROVE' else 'warn' if signal['challenge_verdict'] == 'WEAK_APPROVE' else 'negative' if signal['challenge_verdict'] == 'REJECT' else 'neutral'}">{escape(signal['challenge_verdict'])}</span></div>
                </div>
                <div>
                    <div class="focus-label">PnL %</div>
                    <div class="focus-value">{escape(format_pct(pct))}</div>
                </div>
                <div>
                    <div class="focus-label">Capital PnL</div>
                    <div class="focus-value">{escape(format_money(capital_pnl))}</div>
                </div>
                <div>
                    <div class="focus-label">Last Price</div>
                    <div class="focus-value">{escape(format_price(last_price if last_price is not None else signal.get('current_price')))}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_active_board(
    active_signals: list[dict[str, Any]],
    signal_debug_map: dict[str, Any] | None = None,
    last_price: float | None = None,
    capital_base: float = CAPITAL_BASE,
) -> None:
    if not active_signals:
        st.info("No active signals in the current snapshot.")
        return

    for group in chunked(active_signals, 3):
        cols = st.columns(len(group), gap="medium")
        for idx, signal in enumerate(group):
            with cols[idx]:
                validity = signal["validity"]
                validity_class = "live" if validity == "LIVE" else "warn" if validity in {"OVERDUE", "QUESTIONABLE"} else "negative"
                verdict = signal["challenge_verdict"]
                verdict_class = (
                    "live" if verdict == "APPROVE" else "warn" if verdict == "WEAK_APPROVE" else "negative" if verdict == "REJECT" else "neutral"
                )
                signal_class = "live" if validity == "LIVE" else "warn" if validity in {"OVERDUE", "QUESTIONABLE"} else "negative"
                capital_pnl = signal["capital_pnl"]
                current_price_note = signal["unrealized_pnl_pct"]
                due_text = signal["evaluation_due_text"]
                st.markdown(
                    f"""
                    <div class="signal-card {signal_class}">
                        <div class="signal-top">
                            <div>
                                <div class="signal-title">{escape(signal['signal_id_short'])} / {escape(signal['side'])}</div>
                                <div class="signal-subtitle">{escape(signal['generated_at_text'])} | {escape(signal['age'])} ago</div>
                            </div>
                            <div style="text-align:right">
                                <span class="chip {validity_class}">{escape(validity)}</span><br/>
                                <span class="chip compact {verdict_class}" style="margin-top:0.35rem">{escape(verdict)}</span>
                            </div>
                        </div>
                        <div class="signal-grid">
                            <div class="signal-cell">
                                <div class="signal-cell-label">Entry</div>
                                <div class="signal-cell-value">{signal['entry_price']:,.2f}</div>
                            </div>
                            <div class="signal-cell">
                                <div class="signal-cell-label">Stop</div>
                                <div class="signal-cell-value">{signal['stop_price']:,.2f}</div>
                            </div>
                            <div class="signal-cell">
                                <div class="signal-cell-label">Target</div>
                                <div class="signal-cell-value">{signal['take_profit']:,.2f}</div>
                            </div>
                            <div class="signal-cell">
                                <div class="signal-cell-label">Confidence</div>
                                <div class="signal-cell-value">{signal['confidence']:.2f}</div>
                            </div>
                            <div class="signal-cell">
                                <div class="signal-cell-label">PnL on {format_indian_number(capital_base)}</div>
                                <div class="signal-cell-value">{escape(format_money(capital_pnl))}</div>
                            </div>
                            <div class="signal-cell">
                                <div class="signal-cell-label">Review Due</div>
                                <div class="signal-cell-value">{escape(due_text)}</div>
                            </div>
                        </div>
                        <div class="signal-rationale">{escape(signal['rationale'][:220] + ('...' if len(signal['rationale']) > 220 else ''))}</div>
                        <div class="mini-note">
                            Status: {escape(signal['status'])} | Live state: {escape(signal.get('live_state') or 'n/a')} | Current move: {escape(format_pct(current_price_note))}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if signal.get("live_reason"):
                    st.caption(f"Validity logic: {signal['live_reason']}")
                debug_payload = (signal_debug_map or {}).get(signal["signal_id"])
                if debug_payload and signal["signal_id"]:
                    with st.expander("Trace", expanded=False):
                        st.json(json_safe(debug_payload))

def render_history_table(history_df: pd.DataFrame) -> None:
    st.markdown("### Signal History")
    st.caption("This table shows realized or current PnL on a 1,00,000 capital baseline and preserves the latest outcome details.")
    if history_df.empty:
        st.info("No closed signal history available yet.")
        return

    display_cols = [
        "generated_at_text",
        "signal_id_short",
        "side",
        "status",
        "validity",
        "challenge_verdict",
        "pnl_pct",
        "capital_pnl",
        "equity_after",
        "outcome_reason",
        "data_quality",
    ]
    table = history_df.copy()
    table = table[display_cols].rename(
        columns={
            "generated_at_text": "Time",
            "signal_id_short": "Signal",
            "side": "Side",
            "status": "Status",
            "validity": "Validity",
            "challenge_verdict": "Challenge",
            "pnl_pct": "PnL %",
            "capital_pnl": "Capital PnL",
            "equity_after": "Equity After",
            "outcome_reason": "Outcome",
            "data_quality": "Quality",
        }
    )
    table["PnL %"] = table["PnL %"].map(lambda v: None if pd.isna(v) else round(float(v), 2))
    table["Capital PnL"] = table["Capital PnL"].map(lambda v: format_money(v))
    table["Equity After"] = table["Equity After"].map(lambda v: format_indian_number(v))
    st.dataframe(table, use_container_width=True, hide_index=True)


def render_signal_inspector(
    signals: list[dict[str, Any]],
    signal_debug_map: dict[str, Any],
    last_price: float | None,
) -> None:
    st.markdown("### Signal Inspector")
    st.caption("Choose a signal to inspect its full row, debug trace, and linked outcome details.")
    if not signals:
        st.info("No signals available to inspect.")
        return

    options = [row["signal_id"] for row in signals if row["signal_id"]]
    default_signal = signals[0]["signal_id"]
    if "selected_signal_id" not in st.session_state:
        st.session_state["selected_signal_id"] = default_signal

    selected_signal_id = st.selectbox(
        "Signal",
        options,
        index=options.index(st.session_state["selected_signal_id"]) if st.session_state["selected_signal_id"] in options else 0,
        key="selected_signal_id",
    )

    selected_row = next((row for row in signals if row["signal_id"] == selected_signal_id), None)
    if selected_row is None:
        st.info("Selected signal not found in the current snapshot.")
        return

    selected_debug = signal_debug_map.get(selected_signal_id) or selected_row.get("debug") or {}
    left, right = st.columns([1.05, 1.0], gap="large")
    with left:
        st.markdown("#### Summary")
        st.markdown(
            f"""
            <div class="focus-banner">
                <div class="focus-grid">
                    <div>
                        <div class="focus-label">Signal</div>
                        <div class="focus-value">{escape(selected_row['signal_id_short'])}</div>
                    </div>
                    <div>
                        <div class="focus-label">Validity</div>
                        <div class="focus-value"><span class="chip {'live' if selected_row['validity']=='LIVE' else 'warn' if selected_row['validity'] in {'OVERDUE','QUESTIONABLE'} else 'negative'}">{escape(selected_row['validity'])}</span></div>
                    </div>
                    <div>
                        <div class="focus-label">Challenge</div>
                        <div class="focus-value"><span class="chip {'live' if selected_row['challenge_verdict']=='APPROVE' else 'warn' if selected_row['challenge_verdict']=='WEAK_APPROVE' else 'negative' if selected_row['challenge_verdict']=='REJECT' else 'neutral'}">{escape(selected_row['challenge_verdict'])}</span></div>
                    </div>
                    <div>
                        <div class="focus-label">PnL %</div>
                        <div class="focus-value">{escape(format_pct(selected_row['pnl_pct']))}</div>
                    </div>
                    <div>
                        <div class="focus-label">Capital PnL</div>
                        <div class="focus-value">{escape(format_money(selected_row['capital_pnl']))}</div>
                    </div>
                    <div>
                        <div class="focus-label">Last Price Context</div>
                        <div class="focus-value">{escape(f"{last_price:,.2f}" if last_price is not None else 'n/a')}</div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("#### Signal Row")
        st.json(json_safe(selected_row))

    with right:
        st.markdown("#### Decision Trace")
        st.json(json_safe(selected_debug))


def render_learning_section(data: dict[str, Any]) -> None:
    st.markdown("### Learning Memory")
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### Signal Learning")
        df = pd.DataFrame(data.get("learning_profiles") or [])
        if df.empty:
            st.info("No signal learning profiles yet.")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
    with right:
        st.markdown("#### Challenger Calibration")
        df = pd.DataFrame(data.get("challenger_learning") or [])
        if df.empty:
            st.info("No challenger calibration profiles yet.")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
    st.markdown("#### Challenger Verdict Breakdown")
    st.json(json_safe(data.get("challenger_performance") or {}))


def render_crew_events(events: list[dict[str, Any]]) -> None:
    st.markdown("### Crew Progress")
    st.caption("This is the agent-by-agent event trail published by the running pipeline.")
    if not events:
        st.info("No crew events available yet.")
        return

    for event in events[:12]:
        status = str(event.get("status") or "INFO").upper()
        tone = "live" if status in {"COMPLETED", "OK"} else "warn" if status in {"STARTED", "SKIPPED", "WARN"} else "negative"
        payload = event.get("payload") or {}
        st.markdown(
            f"""
            <div class="surface tight" style="margin-bottom:0.65rem">
                <div class="signal-top">
                    <div>
                        <div class="signal-title">{escape(str(event.get('title') or 'Event'))}</div>
                        <div class="signal-subtitle">{escape(str(event.get('event_time') or ''))} | {escape(str(event.get('component') or ''))} | {escape(str(event.get('run_id') or '-'))}</div>
                    </div>
                    <div><span class="chip {tone}">{escape(status)}</span></div>
                </div>
                <div class="signal-rationale">{escape(str(event.get('message') or ''))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if payload:
            with st.expander("Payload", expanded=False):
                st.json(json_safe(payload))


def render_performance_section(
    history_df: pd.DataFrame,
    curve_df: pd.DataFrame,
    capital_base: float = CAPITAL_BASE,
) -> None:
    st.markdown("### Performance")
    st.caption(
        f"Graph 1 is equity growth from the signal history. Graph 2 is per-signal PnL on a {format_indian_number(capital_base)} capital baseline."
    )
    left, right = st.columns(2, gap="large")
    with left:
        chart = build_equity_chart(curve_df, capital_base=capital_base)
        if chart is None:
            st.info("No equity curve data yet.")
        else:
            st.altair_chart(chart, use_container_width=True)
    with right:
        chart = build_pnl_chart(history_df, capital_base=capital_base)
        if chart is None:
            st.info("No PnL series data yet.")
        else:
            st.altair_chart(chart, use_container_width=True)

def render_history_table(history_df: pd.DataFrame, capital_base: float = CAPITAL_BASE) -> None:
    st.markdown("### Signal History")
    st.caption(
        f"Closed and open signals with outcome, validity, and capital PnL on a {format_indian_number(capital_base)} baseline."
    )
    if history_df.empty:
        st.info("No signal history available yet.")
        return

    display_cols = [
        "signal_time",
        "signal_id_short",
        "symbol",
        "side",
        "status",
        "validity",
        "challenge_verdict",
        "pnl_pct",
        "capital_pnl",
        "equity_after",
        "move_pct",
        "confidence",
        "reason",
        "quality",
    ]
    existing_cols = [col for col in display_cols if col in history_df.columns]
    table = history_df.sort_values("sort_time", ascending=False).loc[:, existing_cols].copy()
    st.dataframe(table, use_container_width=True, hide_index=True, height=420)


def render_signal_inspector(
    signals: list[dict[str, Any]],
    signal_debug_map: dict[str, Any],
    last_price: float | None,
    capital_base: float = CAPITAL_BASE,
) -> None:
    st.markdown("### Signal Inspector")
    if not signals:
        st.info("No signals available in the current snapshot.")
        return

    options = {
        f"{row['signal_time'] or 'unknown'} | {row['side']} | {row['signal_id_short']} | {row['status']} | {row['validity']}": row['signal_id']
        for row in signals
    }
    selected_label = st.selectbox("Choose signal to inspect", list(options.keys()), index=0)
    selected_signal_id = options[selected_label]
    selected_row = next((row for row in signals if row["signal_id"] == selected_signal_id), None)
    if selected_row is None:
        st.warning("Selected signal is no longer available in the current snapshot.")
        return

    selected_debug = signal_debug_map.get(selected_signal_id) or selected_row.get("debug") or {}
    left, right = st.columns([1.05, 1.0], gap="large")
    with left:
        st.markdown("#### Summary")
        validity_class = "live" if selected_row["validity"] == "LIVE" else "warn" if selected_row["validity"] in {"OVERDUE", "QUESTIONABLE"} else "negative"
        challenge_class = "live" if selected_row["challenge_verdict"] == "APPROVE" else "warn" if selected_row["challenge_verdict"] == "WEAK_APPROVE" else "negative" if selected_row["challenge_verdict"] == "REJECT" else "neutral"
        st.markdown(
            f"""
            <div class="focus-banner">
                <div class="focus-grid">
                    <div><div class="focus-label">Signal</div><div class="focus-value">{escape(selected_row['signal_id_short'])}</div></div>
                    <div><div class="focus-label">Side</div><div class="focus-value">{escape(selected_row['side'])}</div></div>
                    <div><div class="focus-label">Status</div><div class="focus-value"><span class="chip {validity_class}">{escape(selected_row['validity'])}</span></div></div>
                    <div><div class="focus-label">Challenge</div><div class="focus-value"><span class="chip {challenge_class}">{escape(selected_row['challenge_verdict'])}</span></div></div>
                    <div><div class="focus-label">PnL %</div><div class="focus-value">{escape(format_pct(selected_row['pnl_pct']))}</div></div>
                    <div><div class="focus-label">Capital PnL @ {escape(format_indian_number(capital_base))}</div><div class="focus-value">{escape(format_money(selected_row['capital_pnl']))}</div></div>
                    <div><div class="focus-label">Entry</div><div class="focus-value">{escape(format_price(selected_row['entry']))}</div></div>
                    <div><div class="focus-label">Stop</div><div class="focus-value">{escape(format_price(selected_row['stop']))}</div></div>
                    <div><div class="focus-label">Target</div><div class="focus-value">{escape(format_price(selected_row['target']))}</div></div>
                    <div><div class="focus-label">Last Price</div><div class="focus-value">{escape(format_price(last_price))}</div></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("#### Decision Trace")
        st.json(json_safe(selected_row.get("decision_trace") or selected_row))
    with right:
        st.markdown("#### Signal Debug Map")
        st.json(json_safe(selected_debug))
        if selected_row.get("challenge_summary"):
            st.markdown("#### Challenger Summary")
            st.write(selected_row.get("challenge_summary"))


def main() -> None:
    st.set_page_config(page_title="BTC Orderflow Desk", page_icon="\U0001F4CA", layout="wide")
    settings = load_settings()
    snapshot_path = Path(getattr(settings, "dashboard_snapshot_path", "./dashboard_snapshot.json"))
    data = load_dashboard_data(snapshot_path)

    st.markdown("<div class='page-shell'>", unsafe_allow_html=True)
    inject_styles()

    with st.sidebar:
        st.markdown("### Desk Controls")
        st.caption("Snapshot-first view of the live pipeline.")
        st.text_input("Snapshot path", value=str(snapshot_path), disabled=True)
        symbol = st.text_input("Symbol", value=str(data.get("symbol") or getattr(settings, 'symbol', 'BTCUSDT')))
        refresh_seconds = st.slider("Refresh every (s)", 2, 30, int(getattr(settings, "dashboard_snapshot_interval_seconds", 5)))
        auto_refresh = st.toggle("Auto refresh", value=True)
        st.toggle("Preserve scroll", value=True, disabled=True)
        st.caption("Scroll position is restored after each refresh.")

    install_auto_refresh(auto_refresh, refresh_seconds)

    snapshot_info = data.get("snapshot") or {}
    health = data.get("health") or {}
    capital_base = to_float(data.get("capital_base"), default=float(getattr(settings, "reporting_capital_base", CAPITAL_BASE)))
    last_price = to_float(data.get("last_price"), default=None)
    now = datetime.utcnow()
    recent_signals = flatten_signals(data, last_price=last_price, now=now, capital_base=capital_base)
    active_signals, history_signals = split_active_history(recent_signals)
    history_df, curve_df = build_history_frame(
        history_signals or recent_signals,
        data.get("equity_curve") or data.get("capital_curve") or data.get("performance_curve") or [],
        capital_base=capital_base,
    )
    signal_debug_map = data.get("signal_debug_map") or {}
    total_signals = len(recent_signals)
    active_count = len(active_signals)
    closed_count = len(history_signals)
    win_count = sum(1 for row in history_signals if str(row.get("status") or "").upper() == "WIN")
    loss_count = sum(1 for row in history_signals if str(row.get("status") or "").upper() == "LOSS")
    open_pnl = sum(to_float(row.get("capital_pnl")) for row in active_signals)
    realized_pnl = sum(to_float(row.get("capital_pnl")) for row in history_signals)

    st.markdown(
        f"""
        <div class="hero-card">
            <div class="eyebrow">BTC Orderflow Desk</div>
            <h1>Signal validity, performance, and learning in one view</h1>
            <p>Active signal board, historical outcomes, capital PnL on {format_indian_number(capital_base)}, crew debate, and learning memory built from the snapshot feed.</p>
            <div class="meta-line">
                <span class="chip live">Snapshot: {escape(str(data.get('generated_at') or 'n/a'))}</span>
                <span class="chip neutral">Source: {escape(str(snapshot_path))}</span>
                <span class="chip {'live' if health.get('status') == 'ok' else 'warn'}">Health: {escape(str(health.get('status') or 'unknown'))}</span>
                <span class="chip neutral">Rows: {total_signals}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if data.get("error"):
        st.warning(str(data["error"]))

    render_metric_band(
        [
            ("Active", f"{active_count}"),
            ("Closed", f"{closed_count}"),
            ("Wins", f"{win_count}"),
            ("Losses", f"{loss_count}"),
            ("Open PnL", format_money(open_pnl)),
            ("Realized PnL", format_money(realized_pnl)),
        ]
    )

    latest_signal = data.get("latest_signal") or (active_signals[0] if active_signals else (recent_signals[0] if recent_signals else None))
    render_focus_banner(latest_signal, last_price=last_price, capital_base=capital_base)

    st.markdown("### Active Signal Board")
    st.caption("Top-level live view of the current signal. Validity updates with the snapshot feed and historical signals stay below.")
    render_active_board(active_signals, signal_debug_map=signal_debug_map, last_price=last_price, capital_base=capital_base)

    left, right = st.columns([1.15, 0.85], gap="large")
    with left:
        render_performance_section(history_df, curve_df, capital_base=capital_base)
    with right:
        render_history_table(history_df, capital_base=capital_base)

    render_signal_inspector(recent_signals, signal_debug_map, last_price, capital_base=capital_base)
    render_learning_section(data)
    render_crew_events(list(data.get("crew_events") or []))

    st.markdown("### Snapshot Payload")
    with st.expander("View raw snapshot JSON", expanded=False):
        st.json(json_safe(data))

    st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
