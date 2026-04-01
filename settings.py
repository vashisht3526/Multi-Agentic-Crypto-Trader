from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    symbol: str
    db_path: str
    dashboard_snapshot_path: str
    dashboard_snapshot_interval_seconds: int
    dashboard_snapshot_rows: int
    reporting_capital_base: float
    position_size_pct: float
    db_lock_wait_seconds: int
    db_lock_fallback: bool
    max_allowed_tick_gap_seconds: int
    signal_horizon_seconds: int
    signal_eval_retry_seconds: int
    max_signal_eval_deferrals: int
    learning_min_samples: int
    lookback_seconds: int
    analysis_interval_seconds: int
    min_ticks_before_analysis: int
    reconnect_backoff_seconds: int
    crew_verbose: bool
    llm_model: str
    llm_provider: str
    openai_model: str
    groq_model: str


def load_settings() -> Settings:
    load_dotenv()
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    use_groq = bool(os.getenv("GROQ_API_KEY"))
    return Settings(
        symbol=os.getenv("SYMBOL", "BTCUSDT").upper(),
        db_path=os.getenv("DB_PATH", "./binance_ticks.duckdb"),
        dashboard_snapshot_path=os.getenv("DASHBOARD_SNAPSHOT_PATH", "./dashboard_snapshot.json"),
        dashboard_snapshot_interval_seconds=int(os.getenv("DASHBOARD_SNAPSHOT_INTERVAL_SECONDS", "5")),
        dashboard_snapshot_rows=int(os.getenv("DASHBOARD_SNAPSHOT_ROWS", "50")),
        reporting_capital_base=float(os.getenv("REPORTING_CAPITAL_BASE", "10000")),
        position_size_pct=float(os.getenv("POSITION_SIZE_PCT", "20")),
        db_lock_wait_seconds=int(os.getenv("DB_LOCK_WAIT_SECONDS", "10")),
        db_lock_fallback=_to_bool(os.getenv("DB_LOCK_FALLBACK", "0")),
        max_allowed_tick_gap_seconds=int(os.getenv("MAX_ALLOWED_TICK_GAP_SECONDS", "10")),
        signal_horizon_seconds=int(os.getenv("SIGNAL_HORIZON_SECONDS", "180")),
        signal_eval_retry_seconds=int(os.getenv("SIGNAL_EVAL_RETRY_SECONDS", "30")),
        max_signal_eval_deferrals=int(os.getenv("MAX_SIGNAL_EVAL_DEFERRALS", "3")),
        learning_min_samples=int(os.getenv("LEARNING_MIN_SAMPLES", "12")),
        lookback_seconds=int(os.getenv("LOOKBACK_SECONDS", "60")),
        analysis_interval_seconds=int(os.getenv("ANALYSIS_INTERVAL_SECONDS", "15")),
        min_ticks_before_analysis=int(os.getenv("MIN_TICKS_BEFORE_ANALYSIS", "50")),
        reconnect_backoff_seconds=int(os.getenv("RECONNECT_BACKOFF_SECONDS", "5")),
        crew_verbose=_to_bool(os.getenv("CREW_VERBOSE", "0")),
        llm_model=f"groq/{groq_model}" if use_groq else openai_model,
        llm_provider="groq" if use_groq else "openai",
        openai_model=openai_model,
        groq_model=groq_model,
    )
