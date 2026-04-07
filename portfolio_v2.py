"""
Three-Stage Portfolio Construction.

Integrates three signals:
  Signal 1 (GEM): Absolute momentum filter -- crash avoidance.
  Signal 2 (Momentum Ranking): Cross-asset 12-1 month momentum -- asset selection.
  Signal 3 (HY Regime): Credit spread regime -- risk budget sizing.

Pipeline:
  Stage 1: GEM absolute momentum check.
      IF winner return < BIL return -> 100% SHY. STOP.
      ELSE -> proceed to Stage 2.

  Stage 2: Momentum ranking allocates within risky budget.
      Top tercile: overweight.
      Mid tercile: baseline weight.
      Bottom tercile: zero.

  Stage 3: HY regime modifies total risk budget.
      TIGHT/NORMAL: 100% risky budget.
      STRESSED: 70% risky / 30% SHY.
      CRISIS: 50% risky / 30% SHY / 20% ANGL (structural, not ranked).
      WIDENING_FAST override: 100% SHY regardless of Stage 1-2 output.

References:
  Antonacci, G. (2012). "Risk Premia Harvesting Through Dual Momentum."
  Verdad Advisers. "Crisis Investing" (2020). -- ANGL crisis thesis.
  Blitz & van Vliet (2008). -- Momentum ranking methodology.
"""

import logging
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

# All portfolio tickers
ALL_TICKERS = ["SPY", "EFA", "EEM", "VNQ", "DBC", "GLD", "ANGL", "SHY", "BIL"]

# Defensive tickers (not momentum-ranked)
DEFENSIVE_TICKER = "SHY"

# Crisis structural allocation (Verdad fallen-angel thesis)
CRISIS_ANGL_WEIGHT = 0.20

# Regime risk budgets
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


# ──────────────────────────────────────────────
# Portfolio construction
# ──────────────────────────────────────────────

def construct_portfolio(
    gem_signal: dict,
    hy_regime: dict,
    momentum_signal,      # MomentumSignal from momentum_rank.py
    rebalance_threshold: float = 0.02,
    prior_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """
    Three-stage portfolio construction.

    Args:
        gem_signal: dict with keys per GEM signal spec.
        hy_regime: dict with keys per HY regime spec.
        momentum_signal: MomentumSignal instance from momentum_rank.py.
        rebalance_threshold: minimum weight delta to trigger a trade (default 2%).
        prior_weights: last month's portfolio weights (for threshold check).

    Returns:
        {ticker: weight} for all tickers, guaranteed to sum to 1.0.
        Includes a "_metadata" key with decision reasoning.
    """
    # Initialize all weights to zero
    weights = {t: 0.0 for t in ALL_TICKERS}
    metadata = {
        "stage_1_result": None,
        "stage_2_result": None,
        "stage_3_result": None,
        "override": None,
    }

    # ---- Stage 0: WIDENING_FAST override check ----
    fast_widen = hy_regime.get("fast_widen_override", False)
    if fast_widen:
        weights[DEFENSIVE_TICKER] = 1.0
        metadata["override"] = "WIDENING_FAST -> 100% SHY"
        metadata["stage_1_result"] = "OVERRIDDEN"
        metadata["stage_2_result"] = "OVERRIDDEN"
        metadata["stage_3_result"] = "OVERRIDDEN"
        return _finalize(weights, metadata, rebalance_threshold, prior_weights)

    # ---- Stage 1: GEM absolute momentum filter ----
    absolute_pass = gem_signal.get("absolute_pass", False)

    if not absolute_pass:
        # Risk-off: 100% defensive
        weights[DEFENSIVE_TICKER] = 1.0
        metadata["stage_1_result"] = (
            f"FAIL -- winner ({gem_signal.get('relative_winner', '?')}) "
            f"return < BIL return. 100% {DEFENSIVE_TICKER}."
        )
        metadata["stage_2_result"] = "SKIPPED (Stage 1 failed)"
        metadata["stage_3_result"] = "SKIPPED (Stage 1 failed)"
        return _finalize(weights, metadata, rebalance_threshold, prior_weights)

    metadata["stage_1_result"] = (
        f"PASS -- {gem_signal.get('relative_winner', '?')} "
        f"12M return > BIL. Proceeding to momentum ranking."
    )

    # ---- Stage 2: Momentum ranking -> risky asset weights ----
    risky_weights = momentum_signal.risky_weights  # sums to 1.0

    metadata["stage_2_result"] = {
        "terciles": dict(momentum_signal.terciles),
        "risky_weights": dict(risky_weights),
        "ranking_metric": momentum_signal.ranking_metric,
    }

    # ---- Stage 3: HY regime -> risk budget ----
    regime = hy_regime.get("regime", "NORMAL")
    risk_budget = REGIME_RISK_BUDGET.get(regime, 1.0)
    shy_alloc = REGIME_SHY_ALLOCATION.get(regime, 0.0)
    angl_alloc = CRISIS_ANGL_WEIGHT if regime == "CRISIS" else 0.0

    metadata["stage_3_result"] = {
        "regime": regime,
        "risk_budget": risk_budget,
        "shy_allocation": shy_alloc,
        "angl_crisis_allocation": angl_alloc,
    }

    # Apply risky weights scaled by risk budget
    for ticker, rw in risky_weights.items():
        if ticker in weights:
            weights[ticker] = rw * risk_budget

    # Add structural allocations
    weights[DEFENSIVE_TICKER] += shy_alloc
    if angl_alloc > 0:
        weights["ANGL"] += angl_alloc

    # ---- Validation ----
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-4:
        logger.error(
            f"Weights sum to {total:.6f}, not 1.0. "
            f"Regime={regime}, risk_budget={risk_budget}, "
            f"shy_alloc={shy_alloc}, angl_alloc={angl_alloc}. "
            f"Raw risky_weights sum={sum(risky_weights.values()):.6f}"
        )
        # Force normalization as fallback
        if total > 0:
            weights = {t: w / total for t, w in weights.items()}
        else:
            weights[DEFENSIVE_TICKER] = 1.0

    return _finalize(weights, metadata, rebalance_threshold, prior_weights)


def _finalize(
    weights: Dict[str, float],
    metadata: dict,
    rebalance_threshold: float,
    prior_weights: Optional[Dict[str, float]],
) -> Dict[str, float]:
    """
    Apply rebalance threshold and attach metadata.

    If all weight deltas vs. prior are below threshold, return prior weights
    (no-trade month). Otherwise return new weights.
    """
    result = dict(weights)

    # Rebalance threshold check
    if prior_weights is not None and rebalance_threshold > 0:
        max_delta = max(
            abs(weights.get(t, 0.0) - prior_weights.get(t, 0.0))
            for t in ALL_TICKERS
        )
        if max_delta < rebalance_threshold:
            metadata["rebalance_action"] = (
                f"NO TRADE -- max delta {max_delta:.3%} < threshold {rebalance_threshold:.1%}"
            )
            result = dict(prior_weights)
        else:
            metadata["rebalance_action"] = (
                f"REBALANCE -- max delta {max_delta:.3%} >= threshold {rebalance_threshold:.1%}"
            )
    else:
        metadata["rebalance_action"] = "REBALANCE (no prior weights)"

    result["_metadata"] = metadata
    return result


# ──────────────────────────────────────────────
# Formatting
# ──────────────────────────────────────────────

def format_portfolio(weights: Dict[str, float]) -> str:
    """Format portfolio weights for console output."""
    metadata = weights.get("_metadata", {})
    lines = [
        "=== Portfolio Allocation ===",
        "",
        f"Stage 1 (GEM):     {metadata.get('stage_1_result', 'N/A')}",
    ]

    s2 = metadata.get("stage_2_result", "N/A")
    if isinstance(s2, dict):
        lines.append(f"Stage 2 (Ranking): {s2.get('ranking_metric', '?')} metric")
        for t, terc in sorted(s2.get("terciles", {}).items()):
            rw = s2.get("risky_weights", {}).get(t, 0.0)
            lines.append(f"  {t:<6} {terc:<8} risky_wt={rw:.1%}")
    else:
        lines.append(f"Stage 2 (Ranking): {s2}")

    s3 = metadata.get("stage_3_result", "N/A")
    if isinstance(s3, dict):
        lines.append(
            f"Stage 3 (Regime):  {s3.get('regime', '?')} | "
            f"risk_budget={s3.get('risk_budget', 0):.0%} | "
            f"SHY={s3.get('shy_allocation', 0):.0%} | "
            f"ANGL_crisis={s3.get('angl_crisis_allocation', 0):.0%}"
        )
    else:
        lines.append(f"Stage 3 (Regime):  {s3}")

    if metadata.get("override"):
        lines.append(f"OVERRIDE:          {metadata['override']}")

    lines.append(f"Rebalance:         {metadata.get('rebalance_action', 'N/A')}")

    lines.append("")
    lines.append(f"{'Ticker':<8} {'Weight':>8}")
    lines.append("-" * 18)

    for ticker in ALL_TICKERS:
        w = weights.get(ticker, 0.0)
        if isinstance(w, (int, float)) and w > 0.001:
            lines.append(f"{ticker:<8} {w:>7.1%}")

    lines.append("=" * 30)
    return "\n".join(lines)
