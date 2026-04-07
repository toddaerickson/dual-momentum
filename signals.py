"""
Signal Computation: Three-Stage TAA Signals.

Three independent signals:
  1. GEM: 12-month absolute momentum filter → crash avoidance gate
  2. Momentum Ranking: Cross-asset 12-1 month momentum → asset selection
  3. HY Regime: Dual-signal credit spread classifier → risk budget sizing

No future data leakage: all computations use only data available as of as_of_date.
"""

from datetime import datetime

import numpy as np
import pandas as pd

import config
import data as data_mod
import momentum_rank


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


def _expanding_percentile_rank(series: pd.Series, as_of: pd.Timestamp) -> float:
    """
    Compute the percentile rank of the most recent value within the expanding
    window of all observations up to as_of.

    Returns percentile as 0-100 float.
    """
    available = series[series.index <= as_of].dropna()
    if len(available) < 2:
        return 50.0  # Default to median if insufficient history
    current = available.iloc[-1]
    rank = (available < current).sum() / len(available) * 100
    return float(rank)


_REGIME_ESCALATION = {
    "TIGHT": "NORMAL",
    "NORMAL": "STRESSED",
    "STRESSED": "CRISIS",
    "CRISIS": "CRISIS",
}


def compute_hy_regime(
    ccc_bb_spread: pd.Series,
    hy_b_spread: pd.Series,
    as_of_date: pd.Timestamp,
    hy_spread: pd.Series = None,
) -> dict:
    """
    Dual-signal HY regime classifier.

    Primary signal: CCC-BB spread (CCC OAS minus BB OAS) percentile rank
      on an expanding window.  Avoids fixed-bps thresholds that drift
      with index composition over time.

    Secondary signal: Single-B OAS percentile rank — retains the
      "absolute stress level" interpretation.  Used to escalate (never
      de-escalate) the primary regime by one step when elevated.

    WIDENING_FAST override: keyed off Single-B OAS 3-month change
      (rather than composite HY OAS) for composition-stability reasons.

    Args:
        ccc_bb_spread: CCC-BB spread series in bps (from data.get_ccc_bb_spread)
        hy_b_spread: Single-B OAS series in bps (from data.get_hy_b_spread)
        as_of_date: Signal date
        hy_spread: Composite HY OAS series (optional, for logging only)

    Returns:
        dict with regime classification
    """
    as_of = pd.Timestamp(as_of_date)
    roc_months = config.HY_ROC_LOOKBACK_MONTHS
    roc_start = as_of - pd.DateOffset(months=roc_months)

    # ── Get current CCC-BB spread ──
    ccc_bb_avail = ccc_bb_spread[ccc_bb_spread.index <= as_of]
    b_avail = hy_b_spread[hy_b_spread.index <= as_of]

    if len(ccc_bb_avail) == 0 or len(b_avail) == 0:
        return {
            "date": as_of.strftime("%Y-%m-%d"),
            "ccc_bb_spread_current": None,
            "ccc_bb_percentile": None,
            "hy_b_current": None,
            "hy_b_percentile": None,
            "hy_b_3m_ago": None,
            "hy_b_change_3m": None,
            "hy_spread_current": None,
            "regime_primary": None,
            "regime": None,
            "rate_of_change": None,
            "fast_widen_override": None,
            "error": "No HY spread data available",
        }

    current_ccc_bb = float(ccc_bb_avail.iloc[-1])
    current_b = float(b_avail.iloc[-1])

    # ── Percentile ranks (expanding window) ──
    ccc_bb_pctl = _expanding_percentile_rank(ccc_bb_spread, as_of)
    b_pctl = _expanding_percentile_rank(hy_b_spread, as_of)

    # ── Primary regime from CCC-BB percentile ──
    pctl_thresholds = config.HY_CCC_BB_PERCENTILE_THRESHOLDS
    if ccc_bb_pctl < pctl_thresholds["TIGHT"]:
        regime_primary = "TIGHT"
    elif ccc_bb_pctl < pctl_thresholds["NORMAL"]:
        regime_primary = "NORMAL"
    elif ccc_bb_pctl < pctl_thresholds["STRESSED"]:
        regime_primary = "STRESSED"
    else:
        regime_primary = "CRISIS"

    # ── Secondary confirmation from Single-B OAS percentile ──
    # Escalate (never de-escalate) if Single-B confirms elevated stress
    b_thresholds = config.HY_B_PERCENTILE_THRESHOLDS
    regime = regime_primary
    if b_pctl >= b_thresholds["CRISIS"]:
        # Single-B at extreme levels → at least STRESSED
        if regime in ("TIGHT", "NORMAL"):
            regime = "STRESSED"
        elif regime == "STRESSED":
            regime = "CRISIS"
    elif b_pctl >= b_thresholds["ELEVATED"]:
        # Single-B elevated → bump primary by one step
        regime = _REGIME_ESCALATION[regime_primary]

    # ── Rate of change: keyed off Single-B OAS 3-month change ──
    b_past = hy_b_spread[hy_b_spread.index <= roc_start]
    if len(b_past) > 0:
        b_3m_ago = float(b_past.iloc[-1])
        b_change = current_b - b_3m_ago
    else:
        b_3m_ago = np.nan
        b_change = np.nan

    roc_thresholds = config.HY_ROC_THRESHOLDS
    if np.isnan(b_change):
        roc_class = "UNKNOWN"
        fast_widen = False
    elif b_change > roc_thresholds["WIDENING_FAST"]:
        roc_class = "WIDENING_FAST"
        fast_widen = True
    elif b_change > roc_thresholds["WIDENING"]:
        roc_class = "WIDENING"
        fast_widen = False
    elif b_change < roc_thresholds["TIGHTENING"]:
        roc_class = "TIGHTENING"
        fast_widen = False
    else:
        roc_class = "STABLE"
        fast_widen = False

    # ── Composite HY OAS for backward-compatible logging ──
    hy_spread_current = None
    if hy_spread is not None:
        hy_avail = hy_spread[hy_spread.index <= as_of]
        if len(hy_avail) > 0:
            hy_spread_current = round(float(hy_avail.iloc[-1]), 1)

    return {
        "date": as_of.strftime("%Y-%m-%d"),
        "ccc_bb_spread_current": round(current_ccc_bb, 1),
        "ccc_bb_percentile": round(ccc_bb_pctl, 1),
        "hy_b_current": round(current_b, 1),
        "hy_b_percentile": round(b_pctl, 1),
        "hy_b_3m_ago": round(b_3m_ago, 1) if not np.isnan(b_3m_ago) else None,
        "hy_b_change_3m": round(b_change, 1) if not np.isnan(b_change) else None,
        "hy_spread_current": hy_spread_current,
        "regime_primary": regime_primary,
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


def compute_momentum_ranking(
    prices: pd.DataFrame,
    as_of_date: pd.Timestamp,
    prior_signal: 'momentum_rank.MomentumSignal | None' = None,
    mom_config: 'momentum_rank.MomentumConfig | None' = None,
) -> 'momentum_rank.MomentumSignal':
    """
    Compute cross-asset momentum ranking (Stage 2).

    Wrapper around momentum_rank.compute_momentum_signal() for use
    alongside GEM and HY regime signals.

    Args:
        prices: ETF price DataFrame (must include risky tickers)
        as_of_date: Signal date
        prior_signal: Previous month's MomentumSignal (for hysteresis)
        mom_config: MomentumConfig instance (uses defaults if None)

    Returns:
        MomentumSignal with rankings, terciles, and risky weights
    """
    return momentum_rank.compute_momentum_signal(
        prices, as_of_date,
        prior_signal=prior_signal,
        config=mom_config,
    )


def compute_all_signals(
    prices: pd.DataFrame,
    hy_spread: pd.Series,
    as_of_date: pd.Timestamp,
    ccc_bb_spread: pd.Series = None,
    hy_b_spread: pd.Series = None,
    prior_momentum_signal: 'momentum_rank.MomentumSignal | None' = None,
) -> dict:
    """
    Compute all three signals for a given date.

    Args:
        prices: ETF price DataFrame
        hy_spread: Composite HY OAS (for backward-compat logging; also used as
                   fallback if dual-signal series are unavailable)
        as_of_date: Signal date
        ccc_bb_spread: CCC-BB spread series (primary regime signal)
        hy_b_spread: Single-B OAS series (secondary signal + ROC)
        prior_momentum_signal: Previous month's MomentumSignal (for hysteresis)

    Returns:
        dict with keys: 'gem', 'hy_regime', 'momentum', 'yield_curve'
    """
    gem = compute_gem_signal(prices, as_of_date)

    if ccc_bb_spread is not None and hy_b_spread is not None:
        hy = compute_hy_regime(ccc_bb_spread, hy_b_spread, as_of_date,
                               hy_spread=hy_spread)
    else:
        # Fallback: use composite HY OAS with legacy fixed-bps thresholds
        hy = _compute_hy_regime_legacy(hy_spread, as_of_date)

    mom = compute_momentum_ranking(prices, as_of_date,
                                   prior_signal=prior_momentum_signal)
    yc = compute_yield_curve_status(as_of_date)

    return {
        "gem": gem,
        "hy_regime": hy,
        "momentum": mom,
        "yield_curve": yc,
    }


def _compute_hy_regime_legacy(
    hy_spread: pd.Series,
    as_of_date: pd.Timestamp,
) -> dict:
    """
    Legacy regime classifier using composite HY OAS with fixed-bps thresholds.
    Used as fallback when CCC/BB/B series are unavailable.
    """
    as_of = pd.Timestamp(as_of_date)
    roc_months = config.HY_ROC_LOOKBACK_MONTHS
    roc_start = as_of - pd.DateOffset(months=roc_months)

    available = hy_spread[hy_spread.index <= as_of]
    if len(available) == 0:
        return {
            "date": as_of.strftime("%Y-%m-%d"),
            "ccc_bb_spread_current": None, "ccc_bb_percentile": None,
            "hy_b_current": None, "hy_b_percentile": None,
            "hy_b_3m_ago": None, "hy_b_change_3m": None,
            "hy_spread_current": None,
            "regime_primary": None, "regime": None,
            "rate_of_change": None, "fast_widen_override": None,
            "error": "No HY spread data available",
        }

    current_spread = available.iloc[-1]
    available_past = hy_spread[hy_spread.index <= roc_start]
    spread_3m_ago = float(available_past.iloc[-1]) if len(available_past) > 0 else np.nan
    spread_change = float(current_spread - spread_3m_ago) if not np.isnan(spread_3m_ago) else np.nan

    thresholds = config.HY_REGIME_THRESHOLDS
    if current_spread < thresholds["TIGHT"]:
        regime = "TIGHT"
    elif current_spread < thresholds["NORMAL"]:
        regime = "NORMAL"
    elif current_spread < thresholds["STRESSED"]:
        regime = "STRESSED"
    else:
        regime = "CRISIS"

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
        "ccc_bb_spread_current": None, "ccc_bb_percentile": None,
        "hy_b_current": None, "hy_b_percentile": None,
        "hy_b_3m_ago": None, "hy_b_change_3m": None,
        "hy_spread_current": round(float(current_spread), 1),
        "regime_primary": regime, "regime": regime,
        "rate_of_change": roc_class,
        "fast_widen_override": fast_widen,
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
        ccc_bb_spread=all_data["ccc_bb_spread"],
        hy_b_spread=all_data["hy_b_spread"],
    )

    gem = signals["gem"]
    hy = signals["hy_regime"]
    mom = signals["momentum"]

    print(f"\nStage 1 — GEM Signal: {gem['gem_signal']}")
    print(f"  SPY 12M: {gem['spy_12m_return']:+.1%}")
    print(f"  EFA 12M: {gem['efa_12m_return']:+.1%}")
    print(f"  BIL 12M: {gem['bil_12m_return']:+.1%}")
    print(f"  Abs Pass: {gem['absolute_pass']}")

    print(f"\nStage 2 — Momentum Ranking:")
    print(momentum_rank.format_momentum_signal(mom))

    print(f"\nStage 3 — HY Regime: {hy['regime']} (primary: {hy['regime_primary']})")
    print(f"  CCC-BB spread: {hy['ccc_bb_spread_current']:.0f} bps (pctl: {hy['ccc_bb_percentile']:.0f})")
    print(f"  Single-B OAS:  {hy['hy_b_current']:.0f} bps (pctl: {hy['hy_b_percentile']:.0f})")
    b_chg = hy['hy_b_change_3m']
    print(f"  Single-B 3M Δ: {b_chg:+.0f} bps ({hy['rate_of_change']})" if b_chg else "  Single-B 3M Δ: N/A")
    print(f"  Override: {hy['fast_widen_override']}")
    if hy.get("hy_spread_current"):
        print(f"  Composite HY OAS: {hy['hy_spread_current']:.0f} bps")

    print(f"\nYield Curve: {signals['yield_curve']['t10y2y']}% ({signals['yield_curve']['status']})")
