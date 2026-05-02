"""
Global Configuration — Immutable Constants for the Trading System.

Multi-Timeframe Trend-Capture Architecture.
DO NOT modify these at runtime. Treat as law.
"""

# ══════════════════════════════════════════════════════════════════════
# ECONOMIC REALITY
# ══════════════════════════════════════════════════════════════════════
MAKER_FEE = 0.0002                      # 0.02% — limit order entry
TAKER_FEE = 0.0005                      # 0.05% — market order exit (stops)
ROUND_TRIP_FEE = MAKER_FEE + TAKER_FEE + 0.0002  # 0.0009 (0.09%)
MINIMUM_GROSS_EDGE_PERCENT = 0.0018     # 0.18% minimum gross target
MIN_NET_PROFIT_PERCENT = 0.0006         # 0.06% net profit floor after fees

# ══════════════════════════════════════════════════════════════════════
# MULTI-TIMEFRAME ALIGNMENT
# ══════════════════════════════════════════════════════════════════════
MIN_ALIGNMENT_SCORE = 0.5               # Hard gate: abort if < 0.5 (pullback is bonus, not required)
DAILY_EMA_PERIOD = 20                   # EMA period for daily bias

# ══════════════════════════════════════════════════════════════════════
# REGIME-ADAPTIVE TARGET MULTIPLIERS
# ══════════════════════════════════════════════════════════════════════
# Low Vol (ratio < 0.5)
TP_MULT_LOW_VOL = 4.0
SL_MULT_LOW_VOL = 1.0
# Normal (ratio 0.5–1.5)
TP_MULT_NORMAL = 3.0
SL_MULT_NORMAL = 1.0
# High Vol (ratio 1.5–2.5)
TP_MULT_HIGH_VOL = 2.5
SL_MULT_HIGH_VOL = 1.2
# Extreme Vol (ratio > 2.5)
TP_MULT_EXTREME = 2.0
SL_MULT_EXTREME = 1.5

# ══════════════════════════════════════════════════════════════════════
# STRUCTURAL EXITS — Trailing Stops (replace time-based expiry)
# ══════════════════════════════════════════════════════════════════════
TRAILING_BE_ATR = 1.5                   # Move SL to entry+1tick at 1.5×ATR profit
TRAILING_LOCK_ATR = 2.5                 # Lock 1.0×ATR profit at 2.5×ATR
TRAILING_FOLLOW_ATR = 4.0              # Trail 1.5×ATR below price at 4.0×ATR+
TREND_REVERSAL_CHECK_INTERVAL = 30      # Seconds between trend reversal checks
TREND_REVERSAL_HYSTERESIS = 2           # Consecutive fails before closing

# ── Partial Take-Profit (Scale-Out) ──
PARTIAL_TP_ENABLED = True               # Enable 50% scale-out at first target
PARTIAL_TP_FRACTION = 0.50              # Close 50% of position at TP1
PARTIAL_TP1_ATR_MULT = 2.0             # TP1 at 2×ATR (remaining trails via tier system)

# ══════════════════════════════════════════════════════════════════════
# ATR SETTINGS
# ══════════════════════════════════════════════════════════════════════
ATR_CALCULATION_MINUTES = 15            # 15-minute candle ATR
CANDLE_LOCK_SECONDS = 120               # Min spacing between trades

# ══════════════════════════════════════════════════════════════════════
# RISK MANAGEMENT
# ══════════════════════════════════════════════════════════════════════
BASE_RISK_PER_TRADE = 0.003             # 0.3% of portfolio per trade
MAX_DAILY_DRAWDOWN = 0.03               # 3% daily loss cap
KELLY_FRACTION_CAP = 0.5                # Half-Kelly maximum

# ── Conviction-Based Sizing ──
# Alignment score → position size multiplier
CONVICTION_TIERS = {
    0.80: 1.50,   # Strong alignment → 150% of base size
    0.60: 1.00,   # Good alignment  → 100% of base size (default)
    0.50: 0.70,   # Minimum viable  → 70% of base size
}

# ══════════════════════════════════════════════════════════════════════
# REGIME FILTERS
# ══════════════════════════════════════════════════════════════════════
MIN_ADX_THRESHOLD = 20.0                # Default: reject if ADX < 20
ADX_EXTREME_THRESHOLD = 50.0            # Reject if ADX > 50 (parabolic)
MIN_ATR_PERCENT_FOR_TRADE = 0.0005      # 0.05% minimum 15m ATR

# ── Dynamic ADX (regime-adaptive) ──
# In low-vol regimes BTC trends at lower ADX readings
ADX_THRESHOLD_LOW_VOL = 13.0            # Lowered from 15 — BTC trends at low ADX during quiet sessions
ADX_THRESHOLD_NORMAL = 20.0             # Standard threshold
ADX_THRESHOLD_HIGH_VOL = 22.0           # Require stronger trend in noisy markets
MAX_SPREAD_TICKS = 3                    # Reject if spread > 3 ticks

# ══════════════════════════════════════════════════════════════════════
# FUNDING RATE
# ══════════════════════════════════════════════════════════════════════
FUNDING_CROWDED_THRESHOLD = 0.0001      # 0.01% — abort if crowded side

# ══════════════════════════════════════════════════════════════════════
# DELTA WINDOWS
# ══════════════════════════════════════════════════════════════════════
DELTA_WINDOW_SHORT = 5                  # 5-second window
DELTA_WINDOW_MEDIUM = 30                # 30-second window
DELTA_WINDOW_LONG = 120                 # 120-second window

# ══════════════════════════════════════════════════════════════════════
# CONCURRENCY & CIRCUIT BREAKERS
# ══════════════════════════════════════════════════════════════════════
MAX_ACTIVE_TRADES = 3                   # Allow 3 concurrent (was 2, prevents missing high-align trades)
MAX_TOTAL_RISK_PCT = 3.0                # Increased from 2.0 for 3 concurrent trades
CIRCUIT_BREAKER_LOSSES = 3
CIRCUIT_BREAKER_COOLDOWN_MIN = 20
DAILY_LOSS_STOP_PCT = 3.0

# ══════════════════════════════════════════════════════════════════════
# MICROSTRUCTURE THRESHOLDS (execution-quality filters)
# ══════════════════════════════════════════════════════════════════════
NOFI_COMPOSITE_THRESHOLD = 0.15         # Loosened from 0.30 — orderly trends have lower NOFI
NOFI_HIGH_CONVICTION = 0.40             # High conviction threshold
LAMBDA_THIN_THRESHOLD = 0.10            # Thin liquidity above this
LAMBDA_SIZE_REDUCTION = 0.25            # Reduce size by 25% if Lambda > threshold
OBI_LONG_THRESHOLD = 0.05              # Loosened from 0.15 — normal BTC book imbalance
OBI_SHORT_THRESHOLD = -0.05            # Loosened from -0.15

# ══════════════════════════════════════════════════════════════════════
# ABSORPTION
# ══════════════════════════════════════════════════════════════════════
ABSORPTION_DELTA_THRESHOLD = 50
ABSORPTION_DISPLACEMENT_THRESHOLD = 5
ABSORPTION_RESOLUTION_TICKS = 10
ABSORPTION_TIMEOUT_SECONDS = 30

# ══════════════════════════════════════════════════════════════════════
# FILL MONITORING
# ══════════════════════════════════════════════════════════════════════
FILL_TIMEOUT_SECONDS = 10
FILL_CHECK_INTERVAL_MS = 500
NOFI_DECAY_THRESHOLD = 0.80

# ══════════════════════════════════════════════════════════════════════
# AUTO-TUNE (weekly)
# ══════════════════════════════════════════════════════════════════════
AUTO_TUNE_MISSED_PROFIT_THRESHOLD = 1.5   # MFE > 1.5× TP = missed profit
AUTO_TUNE_TP_INCREMENT = 0.5              # Increase TP mult by 0.5 if > 5 misses
AUTO_TUNE_TP_MAX = 5.0                    # Cap TP multiplier
AUTO_TUNE_SL_INCREMENT = 0.2              # Increase SL mult by 0.2 if > 5 quick stops
AUTO_TUNE_SL_MAX = 2.0                    # Cap SL multiplier
AUTO_TUNE_QUICK_STOP_SECONDS = 30         # SL hit under this = "quick stop"
AUTO_TUNE_TRIGGER_COUNT = 5               # N events to trigger adjustment
