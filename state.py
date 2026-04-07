"""
State Management: Persistent signal and portfolio history.

Reads/writes CSV files for signal tracking and portfolio logging.
Provides change detection for alerts.
"""

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

import config


def _ensure_file(filepath: Path, columns: list[str]) -> None:
    """Create CSV file with headers if it doesn't exist."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    if not filepath.exists():
        pd.DataFrame(columns=columns).to_csv(filepath, index=False)


# ──────────────────────────────────────────────
# Signal History
# ──────────────────────────────────────────────

SIGNAL_COLUMNS = [
    "date", "absolute_pass", "spy_12m_return", "efa_12m_return", "bil_12m_return",
    "ccc_bb_spread_current", "ccc_bb_percentile",
    "hy_b_current", "hy_b_percentile", "hy_b_3m_ago", "hy_b_change_3m",
    "hy_spread_current",
    "hy_regime_primary", "hy_regime", "hy_roc_class", "fast_widen_override",
    "momentum_top_tickers", "momentum_mid_tickers", "momentum_ranking_metric",
    "momentum_available_assets",
    "t10y2y", "yc_status",
]


def log_daily(
    date: str,
    gem_signal: dict,
    hy_regime: dict,
    yield_curve: dict = None,
    momentum_signal=None,
) -> None:
    """Append one row to signals_history.csv."""
    _ensure_file(config.SIGNALS_HISTORY_FILE, SIGNAL_COLUMNS)

    # Extract momentum summary if available
    mom_top = ""
    mom_mid = ""
    mom_metric = ""
    mom_available = 0
    if momentum_signal is not None:
        terciles = getattr(momentum_signal, "terciles", {})
        mom_top = ",".join(t for t, v in sorted(terciles.items()) if v == "TOP")
        mom_mid = ",".join(t for t, v in sorted(terciles.items()) if v == "MID")
        mom_metric = getattr(momentum_signal, "ranking_metric", "")
        mom_available = getattr(momentum_signal, "available_assets", 0)

    row = {
        "date": date,
        "absolute_pass": gem_signal.get("absolute_pass"),
        "spy_12m_return": gem_signal.get("spy_12m_return"),
        "efa_12m_return": gem_signal.get("efa_12m_return"),
        "bil_12m_return": gem_signal.get("bil_12m_return"),
        "ccc_bb_spread_current": hy_regime.get("ccc_bb_spread_current"),
        "ccc_bb_percentile": hy_regime.get("ccc_bb_percentile"),
        "hy_b_current": hy_regime.get("hy_b_current"),
        "hy_b_percentile": hy_regime.get("hy_b_percentile"),
        "hy_b_3m_ago": hy_regime.get("hy_b_3m_ago"),
        "hy_b_change_3m": hy_regime.get("hy_b_change_3m"),
        "hy_spread_current": hy_regime.get("hy_spread_current"),
        "hy_regime_primary": hy_regime.get("regime_primary"),
        "hy_regime": hy_regime.get("regime"),
        "hy_roc_class": hy_regime.get("rate_of_change"),
        "fast_widen_override": hy_regime.get("fast_widen_override"),
        "momentum_top_tickers": mom_top,
        "momentum_mid_tickers": mom_mid,
        "momentum_ranking_metric": mom_metric,
        "momentum_available_assets": mom_available,
        "t10y2y": yield_curve.get("t10y2y") if yield_curve else None,
        "yc_status": yield_curve.get("status") if yield_curve else None,
    }

    new_row = pd.DataFrame([row])
    new_row.to_csv(config.SIGNALS_HISTORY_FILE, mode="a", header=False, index=False)


# ──────────────────────────────────────────────
# Portfolio History
# ──────────────────────────────────────────────

PORTFOLIO_COLUMNS = [
    "date", "absolute_pass", "hy_regime", "fast_widen_override",
    "target_weights", "prior_weights", "trades",
]


def log_monthly(
    date: str,
    target_weights: dict,
    gem_signal: str,
    hy_regime: str,
    prior_weights: dict = None,
    trades: list = None,
) -> None:
    """Append one row to portfolio_history.csv."""
    _ensure_file(config.PORTFOLIO_HISTORY_FILE, PORTFOLIO_COLUMNS)

    row = {
        "date": date,
        "absolute_pass": gem_signal,
        "hy_regime": hy_regime,
        "fast_widen_override": False,
        "target_weights": json.dumps(target_weights),
        "prior_weights": json.dumps(prior_weights) if prior_weights else "{}",
        "trades": json.dumps(trades) if trades else "[]",
    }

    new_row = pd.DataFrame([row])
    new_row.to_csv(config.PORTFOLIO_HISTORY_FILE, mode="a", header=False, index=False)


def get_current_portfolio() -> dict:
    """
    Read latest portfolio from history.

    Returns:
        dict of {ticker: weight} or default defensive portfolio
    """
    if not config.PORTFOLIO_HISTORY_FILE.exists():
        return {"SPY": 0.0, "EFA": 0.0, "SHY": 1.0, "ANGL": 0.0}

    try:
        df = pd.read_csv(config.PORTFOLIO_HISTORY_FILE)
        if len(df) == 0:
            return {"SPY": 0.0, "EFA": 0.0, "SHY": 1.0, "ANGL": 0.0}

        latest = df.iloc[-1]
        weights = json.loads(latest["target_weights"])
        return weights
    except Exception:
        return {"SPY": 0.0, "EFA": 0.0, "SHY": 1.0, "ANGL": 0.0}


def get_signal_history(lookback_days: int = 90) -> pd.DataFrame:
    """Return recent signal history."""
    if not config.SIGNALS_HISTORY_FILE.exists():
        return pd.DataFrame(columns=SIGNAL_COLUMNS)

    df = pd.read_csv(config.SIGNALS_HISTORY_FILE, parse_dates=["date"])
    if len(df) == 0:
        return df

    cutoff = pd.Timestamp.now() - pd.Timedelta(days=lookback_days)
    return df[df["date"] >= cutoff].reset_index(drop=True)


def detect_signal_change() -> dict:
    """
    Compare today's signals to the most recent logged entry.

    Returns:
        dict with change flags
    """
    if not config.SIGNALS_HISTORY_FILE.exists():
        return {"gem_changed": False, "regime_changed": False, "override_activated": False}

    try:
        df = pd.read_csv(config.SIGNALS_HISTORY_FILE)
        if len(df) < 2:
            return {"abs_mom_changed": False, "regime_changed": False, "override_activated": False}

        current = df.iloc[-1]
        previous = df.iloc[-2]

        return {
            "abs_mom_changed": bool(current.get("absolute_pass")) != bool(previous.get("absolute_pass")),
            "regime_changed": current["hy_regime"] != previous["hy_regime"],
            "override_activated": bool(current.get("fast_widen_override", False)),
            "current_abs_pass": bool(current.get("absolute_pass")),
            "previous_abs_pass": bool(previous.get("absolute_pass")),
            "current_regime": current["hy_regime"],
            "previous_regime": previous["hy_regime"],
        }
    except Exception:
        return {"abs_mom_changed": False, "regime_changed": False, "override_activated": False}
