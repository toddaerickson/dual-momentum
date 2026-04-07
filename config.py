"""
Dual Momentum + HY Spread Model Portfolio System
Configuration and Constants

All thresholds, tickers, and FRED series IDs are defined here.
No magic numbers elsewhere in the codebase.
"""

import os
from pathlib import Path

# ──────────────────────────────────────────────
# API Keys
# ──────────────────────────────────────────────
# Set via environment variable: export FRED_API_KEY="your_key_here"
# Get free key at: https://fred.stlouisfed.org/docs/api/fred/
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# ──────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = BASE_DIR / "reports"
LOGS_DIR = BASE_DIR / "logs"

SIGNALS_HISTORY_FILE = DATA_DIR / "signals_history.csv"
PORTFOLIO_HISTORY_FILE = DATA_DIR / "portfolio_history.csv"

# ──────────────────────────────────────────────
# ETF Universe
# ──────────────────────────────────────────────
TICKERS = {
    "US_EQUITY": "SPY",
    "INTL_EQUITY": "EFA",
    "EM_EQUITY": "EEM",         # Emerging markets equity
    "REITS": "VNQ",             # Real estate investment trusts
    "COMMODITIES": "DBC",       # Broad commodities
    "GOLD": "GLD",              # Gold
    "DEFENSIVE_BOND": "SHY",    # Short-duration Treasuries (1-3 yr)
    "TBILL_PROXY": "BIL",       # T-bill proxy for absolute momentum hurdle
    "CRISIS_CREDIT": "ANGL",    # Fallen angel HY bonds
}

ALL_TICKERS = ["SPY", "EFA", "EEM", "VNQ", "DBC", "GLD", "SHY", "BIL", "ANGL"]

# Risky tickers eligible for momentum ranking (ANGL excluded — structural only)
RISKY_TICKERS = ["SPY", "EFA", "EEM", "VNQ", "DBC", "GLD"]

# ETF inception dates (for backtest boundary handling)
ETF_INCEPTION = {
    "SPY":  "1993-01-29",
    "EFA":  "2001-08-14",
    "EEM":  "2003-04-11",
    "VNQ":  "2004-09-29",
    "GLD":  "2004-11-18",
    "DBC":  "2006-02-03",
    "SHY":  "2002-07-22",
    "BIL":  "2007-05-25",
    "ANGL": "2012-04-10",
}

# Pre-inception substitutions for backtesting
# Before ETF inception, use index proxies or FRED rates
BACKTEST_SUBSTITUTIONS = {
    "SPY": {"before": "1993-01-29", "substitute_ticker": "^GSPC"},  # S&P 500 index
    "BIL": {"before": "2007-05-25", "fred_series": "DTB3"},
    "ANGL": {"before": "2012-04-10", "substitute_ticker": "JNK"},
    "JNK": {"before": "2007-11-28", "substitute_ticker": "SHY"},   # No HY ETF before JNK
    "SHY": {"before": "2002-07-22", "fred_series": "GS1"},  # 1-yr Treasury rate
}

# EFA (MSCI EAFE) has no free proxy before inception.
# Pre-2001: GEM runs absolute momentum only (SPY vs BIL), no relative comparison.
EFA_AVAILABLE_FROM = "2001-08-14"

# EEM, VNQ, DBC, GLD have no pre-inception proxies.
# Momentum ranking gracefully excludes unavailable tickers per month.

# ──────────────────────────────────────────────
# FRED Series
# ──────────────────────────────────────────────
FRED_SERIES = {
    "HY_OAS": "BAMLH0A0HYM2",       # ICE BofA US HY Index OAS (legacy, kept for charting)
    "HY_CCC": "BAMLH0A3HYC",        # ICE BofA CCC & Lower OAS
    "HY_BB": "BAMLH0A1HYBB",        # ICE BofA BB OAS
    "HY_B": "BAMLH0A2HYB",          # ICE BofA Single-B OAS
    "GS10": "GS10",                   # 10-Year Treasury rate
    "T10Y2Y": "T10Y2Y",              # 10Y minus 2Y spread (monitoring only)
    "DFF": "DFF",                     # Fed Funds effective rate
    "DTB3": "DTB3",                   # 3-Month T-bill rate
    "GS1": "GS1",                     # 1-Year Treasury rate
}

# ──────────────────────────────────────────────
# GEM Signal Parameters
# ──────────────────────────────────────────────
GEM_LOOKBACK_MONTHS = 12             # Trailing return period for momentum

# ──────────────────────────────────────────────
# HY Spread Regime: Dual-Signal Classifier
# ──────────────────────────────────────────────
# Primary signal: CCC-BB spread (BAMLH0A3HYC minus BAMLH0A1HYBB)
#   Percentile ranks on an expanding window avoid fixed-bps drift.
# Secondary signal: Single-B OAS (BAMLH0A2HYB) for confirmation.

# CCC-BB spread percentile → regime (primary)
HY_CCC_BB_PERCENTILE_THRESHOLDS = {
    "TIGHT":    25,     # percentile < 25
    "NORMAL":   60,     # 25 <= percentile < 60
    "STRESSED": 85,     # 60 <= percentile < 85
    # CRISIS: percentile >= 85 (implicit)
}

# Single-B OAS percentile → confirmation regime (secondary)
# Used to escalate (never de-escalate) the primary regime by one step.
HY_B_PERCENTILE_THRESHOLDS = {
    "ELEVATED": 75,     # Single-B OAS >= 75th percentile → escalate primary by 1
    "CRISIS":   90,     # Single-B OAS >= 90th percentile → escalate to at least STRESSED
}

# Legacy fixed-bps thresholds (kept for dashboard charting of composite HY OAS)
HY_REGIME_THRESHOLDS = {
    "TIGHT":    350,    # spread < 350 bps
    "NORMAL":   500,    # 350 <= spread < 500 bps
    "STRESSED": 700,    # 500 <= spread < 700 bps
    # CRISIS: spread >= 700 bps (implicit)
}

# Rate of change thresholds — keyed off Single-B OAS (3-month change in bps)
HY_ROC_THRESHOLDS = {
    "WIDENING_FAST":  100,   # 3-month Single-B OAS change > +100 bps → override
    "WIDENING":        50,   # 3-month change > +50 bps
    # STABLE: between -50 and +50
    "TIGHTENING":     -50,   # 3-month change < -50 bps
}

HY_ROC_LOOKBACK_MONTHS = 3          # Period for rate of change calc

# ──────────────────────────────────────────────
# Portfolio Weights (from decision matrix)
# ──────────────────────────────────────────────
# Format: {(gem_signal, hy_regime): {ticker: weight}}
# WIDENING_FAST override handled separately in portfolio.py

ALLOCATION_MATRIX = {
    # TIGHT regime
    ("SPY", "TIGHT"):    {"SPY": 1.0, "EFA": 0.0, "SHY": 0.0, "ANGL": 0.0},
    ("EFA", "TIGHT"):    {"SPY": 0.0, "EFA": 1.0, "SHY": 0.0, "ANGL": 0.0},
    ("SHY", "TIGHT"):    {"SPY": 0.0, "EFA": 0.0, "SHY": 1.0, "ANGL": 0.0},

    # NORMAL regime
    ("SPY", "NORMAL"):   {"SPY": 1.0, "EFA": 0.0, "SHY": 0.0, "ANGL": 0.0},
    ("EFA", "NORMAL"):   {"SPY": 0.0, "EFA": 1.0, "SHY": 0.0, "ANGL": 0.0},
    ("SHY", "NORMAL"):   {"SPY": 0.0, "EFA": 0.0, "SHY": 1.0, "ANGL": 0.0},

    # STRESSED regime
    ("SPY", "STRESSED"): {"SPY": 0.7, "EFA": 0.0, "SHY": 0.3, "ANGL": 0.0},
    ("EFA", "STRESSED"): {"SPY": 0.0, "EFA": 0.7, "SHY": 0.3, "ANGL": 0.0},
    ("SHY", "STRESSED"): {"SPY": 0.0, "EFA": 0.0, "SHY": 1.0, "ANGL": 0.0},

    # CRISIS regime
    ("SPY", "CRISIS"):   {"SPY": 0.5, "EFA": 0.0, "SHY": 0.3, "ANGL": 0.2},
    ("EFA", "CRISIS"):   {"SPY": 0.0, "EFA": 0.5, "SHY": 0.3, "ANGL": 0.2},
    ("SHY", "CRISIS"):   {"SPY": 0.0, "EFA": 0.0, "SHY": 0.8, "ANGL": 0.2},
}

# WIDENING_FAST override allocation (applied when 3mo HY change > 100 bps)
WIDENING_FAST_OVERRIDE = {"SPY": 0.0, "EFA": 0.0, "SHY": 1.0, "ANGL": 0.0}

# ──────────────────────────────────────────────
# gem_floor Strategy: 70% Equity Floor Allocations
# ──────────────────────────────────────────────
# When absolute momentum PASSES: full equity in TIGHT/NORMAL, softer HY overlay
ALLOCATION_MATRIX_FLOOR_OFFENSIVE = {
    ("SPY", "TIGHT"):    {"SPY": 1.0, "EFA": 0.0, "SHY": 0.0, "ANGL": 0.0},
    ("EFA", "TIGHT"):    {"SPY": 0.0, "EFA": 1.0, "SHY": 0.0, "ANGL": 0.0},
    ("SPY", "NORMAL"):   {"SPY": 1.0, "EFA": 0.0, "SHY": 0.0, "ANGL": 0.0},
    ("EFA", "NORMAL"):   {"SPY": 0.0, "EFA": 1.0, "SHY": 0.0, "ANGL": 0.0},
    ("SPY", "STRESSED"): {"SPY": 0.8, "EFA": 0.0, "SHY": 0.2, "ANGL": 0.0},
    ("EFA", "STRESSED"): {"SPY": 0.0, "EFA": 0.8, "SHY": 0.2, "ANGL": 0.0},
    ("SPY", "CRISIS"):   {"SPY": 0.7, "EFA": 0.0, "SHY": 0.1, "ANGL": 0.2},
    ("EFA", "CRISIS"):   {"SPY": 0.0, "EFA": 0.7, "SHY": 0.1, "ANGL": 0.2},
}

# When absolute momentum FAILS: 70% equity floor + 30% defensive
ALLOCATION_MATRIX_FLOOR_DEFENSIVE = {
    ("SPY", "TIGHT"):    {"SPY": 0.7, "EFA": 0.0, "SHY": 0.3, "ANGL": 0.0},
    ("EFA", "TIGHT"):    {"SPY": 0.0, "EFA": 0.7, "SHY": 0.3, "ANGL": 0.0},
    ("SPY", "NORMAL"):   {"SPY": 0.7, "EFA": 0.0, "SHY": 0.3, "ANGL": 0.0},
    ("EFA", "NORMAL"):   {"SPY": 0.0, "EFA": 0.7, "SHY": 0.3, "ANGL": 0.0},
    ("SPY", "STRESSED"): {"SPY": 0.7, "EFA": 0.0, "SHY": 0.3, "ANGL": 0.0},
    ("EFA", "STRESSED"): {"SPY": 0.0, "EFA": 0.7, "SHY": 0.3, "ANGL": 0.0},
    ("SPY", "CRISIS"):   {"SPY": 0.7, "EFA": 0.0, "SHY": 0.1, "ANGL": 0.2},
    ("EFA", "CRISIS"):   {"SPY": 0.0, "EFA": 0.7, "SHY": 0.1, "ANGL": 0.2},
}

# ──────────────────────────────────────────────
# Three-Stage Risk Budget (from portfolio_v2)
# ──────────────────────────────────────────────
# Stage 3: HY regime determines what fraction of the portfolio goes to risky assets
REGIME_RISK_BUDGET = {
    "TIGHT":    1.00,
    "NORMAL":   1.00,
    "STRESSED": 0.70,
    "CRISIS":   0.50,
}

# SHY allocation per regime (what doesn't go to risky budget or ANGL)
REGIME_SHY_ALLOCATION = {
    "TIGHT":    0.00,
    "NORMAL":   0.00,
    "STRESSED": 0.30,
    "CRISIS":   0.30,   # remaining 20% goes to ANGL
}

# Crisis structural allocation (Verdad fallen-angel thesis)
CRISIS_ANGL_WEIGHT = 0.20

# ──────────────────────────────────────────────
# Backtest Parameters
# ──────────────────────────────────────────────
BACKTEST_START = "1980-01-01"        # Extended via index proxies (^GSPC, FRED rates)

# HY OAS spread (BAMLH0A0HYM2) starts 1997-01-01.
# Before that date, strategies using HY overlay fall back to GEM-only.
HY_OAS_AVAILABLE_FROM = "1997-01-01"
BACKTEST_END = None                  # None = latest available date
TRANSACTION_COST_BPS = 5            # Per position that changes, each way
REBALANCE_DAY = "last_business_day"  # Of each month
RISK_FREE_PROXY = "BIL"             # For Sharpe ratio calculation

# ──────────────────────────────────────────────
# Data Fetch Parameters
# ──────────────────────────────────────────────
YFINANCE_RETRY_COUNT = 3
YFINANCE_RETRY_DELAY_SEC = 5
CACHE_EXPIRY_HOURS = 12              # Re-fetch if cache older than this
