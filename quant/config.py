"""CAT quant core — read-only configuration constants.

This module holds ONLY constants: paths, the universe definition, and the
Master Settings / strategy defaults from the design doc
(CAT_merged_plan_v2.0.md, rev3). It performs no I/O and imports nothing that
does — keep it that way so every layer can import it freely.

Provenance tags on each value:
  [A#]  a locked product decision (附录 B A-group; do NOT change without Jiacong)
  [C#]  a backtest-calibrated knob — the value here is only a STARTING point,
        the real value comes out of R0-9 walk-forward calibration
  [design §x]  spelled out in that section of the plan
"""

from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
# repo root = two levels up from this file (quant/config.py -> quant/ -> repo)
REPO_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_ROOT: Path = REPO_ROOT / "cat-data"          # gitignored; created on demand
BARS_DIR: Path = DATA_ROOT / "bars"
UNIVERSE_DIR: Path = DATA_ROOT / "universe"
META_DIR: Path = DATA_ROOT / "_meta"
MANIFEST_DB: Path = META_DIR / "manifest.db"       # SQLite (D2); migrates to PG in R1
SCHEMA_VERSION_FILE: Path = META_DIR / "schema_version.json"

# --------------------------------------------------------------------------
# Market data (Alpaca free tier) — see design §4.5, research B1
# --------------------------------------------------------------------------
# Historical/incremental bars MUST use the SIP feed with end >= 15 minutes old
# (free-tier-legal full-market SIP incl. pre/post). Never pull history via IEX.
DATA_FEED: str = "sip"
SIP_DELAY_MINUTES: int = 15
# Bar schema stored in Parquet (indicators are NOT persisted — computed in memory)
BAR_COLUMNS: tuple[str, ...] = (
    "ts", "open", "high", "low", "close", "volume", "vwap", "trade_count",
)
PARQUET_COMPRESSION: str = "zstd"

# Storage layout (design §4.2–4.3): daily per-symbol, 5m per-symbol-per-month.
DAILY_HISTORY_YEARS: int = 10
INTRADAY_HISTORY_YEARS: int = 2
INTRADAY_TIMEFRAME: str = "5Min"
DAILY_TIMEFRAME: str = "1Day"

# --------------------------------------------------------------------------
# Universe (design §7; A4, A8)
# --------------------------------------------------------------------------
STOCK_INDEX: str = "sp500"                  # [A4] point-in-time S&P500 constituents
ETF_WHITELIST: tuple[str, ...] = ("SPY", "QQQ")  # [A4] separate quota, <=1 slot
ETF_MAX_SLOTS: int = 1                       # [A4] ETFs don't share the stock funnel

# Funnel filter defaults [A8] — tuned in backtest
MIN_AVG_DOLLAR_VOLUME: float = 20_000_000.0  # [A8] $20M ADV
MIN_PRICE: float = 5.0                        # [design §7] no penny stocks
MAX_PER_SECTOR: int = 2                       # [A8] sector diversification cap
SHORTLIST_MAX: int = 10                       # [design §7] top-N into veto
MIN_CONFIDENCE: float = 0.0                   # [C1] entry threshold — 0 = pure
                                              # rank-based; the real value comes
                                              # out of R0-9 calibration

# --------------------------------------------------------------------------
# Master Settings (design §8.6) — tighten-only at runtime; these are the
# canonical DEFAULTS the R1 master_settings table seeds from.
# --------------------------------------------------------------------------
STARTING_CAPITAL_USD: float = 2_000.0         # [A2]
PER_TRADE_RISK_PCT: float = 0.03              # [A3] 3% of equity, hard cap
DAILY_LOSS_PAUSE_PCT: float = 0.02            # [C3] -2% starting value
PORTFOLIO_DRAWDOWN_HALT_PCT: float = 0.15     # [A5] 15%
MAX_PYRAMID_ADDS_PER_SYMBOL: int = 1          # [A7]

# Concurrent-position ladder by equity (design §8.1). Ordered thresholds:
# below the first threshold uses the first slot count, etc.
POSITION_LADDER: tuple[tuple[float, int], ...] = (
    (2_000.0, 3),
    (5_000.0, 4),
    (10_000.0, 5),
    (20_000.0, 10),
)

FRACTIONAL_SHARES: bool = True                # [A2] $2k/3 slots needs fractionals

# --------------------------------------------------------------------------
# Strategy defaults (design §6.3) — indicator params are [C2] starting points,
# walk-forward validated in R0-9. Roles: MA=direction gate, MACD=momentum,
# RSI=filter, ATR=volatility scale.
# --------------------------------------------------------------------------
MA_FAST: int = 5
MA_SLOW: int = 20
MACD_FAST: int = 12
MACD_SLOW: int = 26
MACD_SIGNAL: int = 9
RSI_PERIOD: int = 14
ATR_PERIOD: int = 14

# Exit stack (design §6.6) — long-only [A1]; trend-following, no max holding days [A3]
HARD_STOP_RISK_PCT: float = 0.03              # [A3] == PER_TRADE_RISK_PCT at entry
TRAILING_START_R: float = 1.5                 # [C8] start/tighten trailing at ~1.5-2R
# reversal-vs-pullback hysteresis [C5], stagnation rotation [C6], trailing distance
# [C8] are calibrated in R0-9 — no fixed defaults asserted here to avoid pretending
# they are decided.

# --------------------------------------------------------------------------
# Backtest cost model (design §4.6, §11) — costs MUST be deducted
# --------------------------------------------------------------------------
COMMISSION_PER_SHARE: float = 0.0             # US equities/ETF ~ $0 (tiny reg fees)
# slippage + spread assumptions are set in backtest/costs.py and calibrated
# against observed paper slippage later (成本校准闭环).
