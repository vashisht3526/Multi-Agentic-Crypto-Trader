from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Dashboard ──────────────────────────────────────────────────────────

class HealthStatus(BaseModel):
    usable: bool = False
    reason: str = "unknown"
    tick_count: int = 0
    max_gap_seconds: Optional[float] = None
    symbol: str = ""
    lookback_seconds: int = 60


class DashboardSummary(BaseModel):
    symbol: str
    last_price: Optional[float] = None
    health: HealthStatus
    total_signals: int = 0
    active_signals: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    capital_base: float = 100000.0
    current_equity: float = 100000.0
    max_drawdown_pct: float = 0.0
    generated_at: Optional[str] = None


# ── Signals ────────────────────────────────────────────────────────────

class SignalCard(BaseModel):
    signal_id: str
    generated_at: Optional[str] = None
    closed_at: Optional[str] = None
    symbol: str = ""
    side: str = "FLAT"
    regime: str = ""
    entry_price: float = 0.0
    stop_price: float = 0.0
    take_profit: float = 0.0
    confidence: float = 0.0
    risk_reward: float = 0.0
    status: str = "OPEN"
    challenge_verdict: str = "PENDING"
    challenge_summary: Optional[str] = None
    rationale: str = ""
    current_price: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    pnl_pct: Optional[float] = None
    capital_pnl: Optional[float] = None
    capital_base: float = 100000.0
    time_remaining_seconds: Optional[int] = None
    live_state: str = ""
    is_valid: bool = False
    live_reason: str = ""
    outcome_reason: Optional[str] = None
    validation_status: Optional[str] = None
    validation_reasoning: Optional[str] = None
    entry_logic: Optional[str] = None
    exit_logic: Optional[str] = None
    success_failure_explanation: Optional[str] = None


class SignalDeepDive(BaseModel):
    signal: SignalCard
    decision_trace: dict[str, Any] = Field(default_factory=dict)
    challenge: Optional[dict[str, Any]] = None
    agent_events: list[dict[str, Any]] = Field(default_factory=list)


# ── Performance ────────────────────────────────────────────────────────

class EquityCurvePoint(BaseModel):
    signal_id: str = ""
    point_time: Optional[str] = None
    status: str = ""
    pnl_pct: float = 0.0
    trade_pnl: float = 0.0
    cumulative_pnl: float = 0.0
    equity_value: float = 100000.0


class PerformanceMetrics(BaseModel):
    capital_base: float = 100000.0
    current_equity: float = 100000.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    total_trades: int = 0
    win_rate: float = 0.0
    max_drawdown_pct: float = 0.0
    best_trade_pnl_pct: float = 0.0
    worst_trade_pnl_pct: float = 0.0
    avg_win_pnl_pct: float = 0.0
    avg_loss_pnl_pct: float = 0.0
    equity_curve: list[EquityCurvePoint] = Field(default_factory=list)
    trade_pnl_bars: list[dict[str, Any]] = Field(default_factory=list)


# ── Agent Events ───────────────────────────────────────────────────────

class AgentEvent(BaseModel):
    event_time: Optional[str] = None
    run_id: Optional[str] = None
    component: str = ""
    event_type: str = ""
    status: str = ""
    title: str = ""
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


# ── Orderflow ──────────────────────────────────────────────────────────

class OrderflowSnapshot(BaseModel):
    symbol: str = ""
    lookback_seconds: int = 60
    trade_count: int = 0
    taker_buy_volume: float = 0.0
    taker_sell_volume: float = 0.0
    volume_delta: float = 0.0
    imbalance: float = 0.0
    avg_price: float = 0.0
    last_price: float = 0.0
    regime: Optional[str] = None
    confidence: Optional[float] = None
    summary: Optional[str] = None
