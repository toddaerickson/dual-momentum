"""
Signal Computation: GEM Dual Momentum + HY Spread Regime

Two independent signals:
  1. GEM: 12-month relative + absolute momentum → SPY, EFA, or SHY
  2. HY Regime: Spread level + rate of change → TIGHT/NORMAL/STRESSED/CRISIS

No future data leakage: all computations use only data available as of as_of_date.
"""

from datetime import datetime

import numpy as np
import pandas as pd

import config
import data as data_mod


def compute_gem_signal(
    prices: pd.DataFrame,
    as_of_date: pd.Timestamp,
    lookback_months: int = None,
) -> dict:
    """
    Compute Global Equity Momentum (GEM) signal.

    Logic:
      1. Compare 12-month total return of SPY vs EFA
      2. Winner = max(SPY, EFA) → relative momentum
      3. If winner return > BIL return → hold winner (absolute momentum pass)
      4. Else → hold SHY (absolute momentum fail, risk-off)

    Args:
        prices: DataFrame of adjusted close prices
        as_of_date: Signal date (uses only data through this date)
        lookback_months: Override for lookback period (default: config)

    Returns:
        dict with signal details
    """
    if lookback_months is None:
        lookback_months = config.GEM_LOOKBACK_MONTHS

    as_of = pd.Timestamp(as_of_date)

    # Compute trailing returns
    spy_ret = data_mod.get_trailing_total_return(prices, "SPY", as_of, lookback_months)

    # EFA may not be available before 2001 — run absolute-only if missing
    efa_available = (
        "EFA" in prices.columns
        and as_of >= pd.Timestamp(config.EFA_AVAILABLE_FROM)
    )
    if efa_available:
        efa_ret = data_mod.get_trailing_total_return(prices, "EFA", as_of, lookback_months)
        if np.isnan(efa_ret):
            efa_available = False
            efa_ret = np.nan
    else:
        efa_ret = np.nan

    # T-bill return: use BIL if available, else FRED DTB3
    bil_inception = pd.Timestamp(config.ETF_INCEPTION["BIL"])
    if as_of >= bil_inception and "BIL" in prices.columns:
        bil_ret = data_mod.get_trailing_total_return(prices, "BIL", as_of, lookback_months)
    else:
        bil_ret = data_mod.get_tbill_total_return(as_of, lookback_months)

    # Handle NaN — SPY and BIL are required; EFA is optional pre-2001
    if np.isnan(spy_ret) or np.isnan(bil_ret):
        return {
            "date": as_of.strftime("%Y-%m-%d"),
            "spy_12m_return": spy_ret,
            "efa_12m_return": efa_ret,
            "bil_12m_return": bil_ret,
            "relative_winner": None,
            "absolute_pass": None,
            "gem_signal": None,
            "error": "Insufficient data for lookback period",
        }

    # Step 1-2: Relative momentum
    if efa_available:
        # Normal GEM: compare SPY vs EFA
        if spy_ret >= efa_ret:
            relative_winner = "SPY"
            winner_ret = spy_ret
        else:
            relative_winner = "EFA"
            winner_ret = efa_ret
    else:
        # Pre-EFA: SPY is the only equity candidate
        relative_winner = "SPY"
        winner_ret = spy_ret

    # Step 3-4: Absolute momentum
    absolute_pass = winner_ret > bil_ret

    if absolute_pass:
        gem_signal = relative_winner
    else:
        gem_signal = "SHY"

    return {
        "date": as_of.strftime("%Y-%m-%d"),
        "spy_12m_return": round(spy_ret, 6),
        "efa_12m_return": round(efa_ret, 6) if not np.isnan(efa_ret) else None,
        "bil_12m_return": round(bil_ret, 6),
        "relative_winner": relative_winner,
        "winner_return": round(winner_ret, 6),
        "absolute_pass": absolute_pass,
        "gem_signal": gem_signal,
    }


def compute_hy_regime(
    hy_spread: pd.Series,
    as_of_date: pd.Timestamp,
) -> dict:
    """
    Classify HY spread regime and rate of change.

    Regime levels (OAS in basis points):
      TIGHT:    < 350 bps
      NORMAL:   350 - 500 bps
      STRESSED: 500 - 700 bps
      CRISIS:   >= 700 bps

    Rate of change (3-month delta):
      WIDENING_FAST: > +100 bps  (override trigger)
      WIDENING:      > +50 bps
      STABLE:        -50 to +50 bps
      TIGHTENING:    < -50 bps

    Args:
        hy_spread: Series of HY OAS spread values (index = date)
        as_of_date: Signal date

    Returns:
        dict with regime classification
    """
    as_of = pd.Timestamp(as_of_date)
    roc_months = config.HY_ROC_LOOKBACK_MONTHS
    roc_start = as_of - pd.DateOffset(months=roc_months)

    # Get current spread (most recent observation on or before as_of_date)
    available = hy_spread[hy_spread.index <= as_of]
    if len(available) == 0:
        return {
            "date": as_of.strftime("%Y-%m-%d"),
            "hy_spread_current": None,
            "hy_spread_3m_ago": None,
            "hy_spread_change_3m": None,
            "regime": None,
            "rate_of_change": None,
            "fast_widen_override": None,
            "error": "No HY spread data available",
        }

    current_spread = available.iloc[-1]

    # Get spread N months ago
    available_past = hy_spread[hy_spread.index <= roc_start]
    if len(available_past) > 0:
        spread_3m_ago = available_past.iloc[-1]
        spread_change = current_spread - spread_3m_ago
    else:
        spread_3m_ago = np.nan
        spread_change = np.nan

    # Classify regime
    thresholds = config.HY_REGIME_THRESHOLDS
    if current_spread < thresholds["TIGHT"]:
        regime = "TIGHT"
    elif current_spread < thresholds["NORMAL"]:
        regime = "NORMAL"
    elif current_spread < thresholds["STRESSED"]:
        regime = "STRESSED"
    else:
        regime = "CRISIS"

    # Classify rate of change
    roc_thresholds = config.HY_ROC_THRESHOLDS
    if np.isnan(spread_change):
        roc_class = "UNKNOWN"
        fast_widen = False
    elif spread_change > roc_thresholds["WIDENING_FAST"]:
        roc_class = "WIDENING_FAST"
        fast_widen = True
    elif spread_change > roc_thresholds["WIDENING"]:
        roc_class = "WIDENING"
        fast_widen = False
    elif spread_change < roc_thresholds["TIGHTENING"]:
        roc_class = "TIGHTENING"
        fast_widen = False
    else:
        roc_class = "STABLE"
        fast_widen = False

    return {
        "date": as_of.strftime("%Y-%m-%d"),
        "hy_spread_current": round(float(current_spread), 1),
        "hy_spread_3m_ago": round(float(spread_3m_ago), 1) if not np.isnan(spread_3m_ago) else None,
        "hy_spread_change_3m": round(float(spread_change), 1) if not np.isnan(spread_change) else None,
        "regime": regime,
        "rate_of_change": roc_class,
        "fast_widen_override": fast_widen,
    }


def compute_yield_curve_status(
    as_of_date: pd.Timestamp,
) -> dict:
    """
    Compute yield curve status (monitoring/logging only, not a trading signal).

    Returns:
        dict with yield curve data for memo logging
    """
    as_of = pd.Timestamp(as_of_date)
    try:
        yc = data_mod.get_yield_curve_spread()
        available = yc[yc.index <= as_of]
        if len(available) == 0:
            return {"t10y2y": None, "status": "NO_DATA"}

        current = float(available.iloc[-1])

        if current <= 0:
            status = "INVERTED"
        elif current < 0.50:
            status = "FLAT"
        elif current < 1.50:
            status = "NORMAL"
        else:
            status = "STEEP"

        return {
            "date": as_of.strftime("%Y-%m-%d"),
            "t10y2y": round(current, 2),
            "status": status,
        }
    except Exception as e:
        return {"t10y2y": None, "status": "ERROR", "error": str(e)}


def compute_all_signals(
    prices: pd.DataFrame,
    hy_spread: pd.Series,
    as_of_date: pd.Timestamp,
) -> dict:
    """
    Compute all signals for a given date.

    Returns:
        dict with keys: 'gem', 'hy_regime', 'yield_curve'
    """
    gem = compute_gem_signal(prices, as_of_date)
    hy = compute_hy_regime(hy_spread, as_of_date)
    yc = compute_yield_curve_status(as_of_date)

    return {
        "gem": gem,
        "hy_regime": hy,
        "yield_curve": yc,
    }


if __name__ == "__main__":
    # Quick test with current data
    print("Fetching data...")
    all_data = data_mod.fetch_all()

    as_of = all_data["prices"].index[-1]
    print(f"\nComputing signals as of {as_of.strftime('%Y-%m-%d')}...")

    signals = compute_all_signals(
        all_data["prices"],
        all_data["hy_spread"],
        as_of,
    )

    print(f"\nGEM Signal: {signals['gem']['gem_signal']}")
    print(f"  SPY 12M: {signals['gem']['spy_12m_return']:+.1%}")
    print(f"  EFA 12M: {signals['gem']['efa_12m_return']:+.1%}")
    print(f"  BIL 12M: {signals['gem']['bil_12m_return']:+.1%}")
    print(f"  Abs Pass: {signals['gem']['absolute_pass']}")

    print(f"\nHY Regime: {signals['hy_regime']['regime']} ({signals['hy_regime']['hy_spread_current']:.0f} bps)")
    print(f"  3M Change: {signals['hy_regime']['hy_spread_change_3m']:+.0f} bps ({signals['hy_regime']['rate_of_change']})")
    print(f"  Override: {signals['hy_regime']['fast_widen_override']}")

    print(f"\nYield Curve: {signals['yield_curve']['t10y2y']}% ({signals['yield_curve']['status']})")
