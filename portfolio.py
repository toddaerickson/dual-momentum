"""
Portfolio Constructor: Signal → Target Weights

Legacy (two-signal) and v2 (three-stage) portfolio construction.

Legacy: GEM binary signal + HY regime → decision matrix lookup.
V2: GEM absolute momentum + momentum ranking + HY regime → three-stage pipeline.
"""

import config
import portfolio_v2


def construct_portfolio(gem_signal: dict, hy_regime: dict) -> dict:
    """
    Map GEM signal + HY regime to target portfolio weights.

    Implements the full decision matrix including WIDENING_FAST override.

    Args:
        gem_signal: dict from signals.compute_gem_signal()
        hy_regime: dict from signals.compute_hy_regime()

    Returns:
        dict of {ticker: weight} summing to 1.0
    """
    signal = gem_signal.get("gem_signal")
    regime = hy_regime.get("regime")
    fast_widen = hy_regime.get("fast_widen_override", False)

    # Validate inputs
    if signal is None or regime is None:
        # Fallback to fully defensive if signals are unavailable
        return dict(config.WIDENING_FAST_OVERRIDE)

    # WIDENING_FAST override: if spreads widening >100 bps in 3 months
    # and GEM says equities, override to defensive
    if fast_widen and signal in ("SPY", "EFA"):
        return dict(config.WIDENING_FAST_OVERRIDE)

    # Look up in allocation matrix
    key = (signal, regime)
    if key not in config.ALLOCATION_MATRIX:
        raise ValueError(f"Unknown signal/regime combination: {key}")

    weights = dict(config.ALLOCATION_MATRIX[key])

    # Validate weights sum to 1.0
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Weights sum to {total}, expected 1.0 for {key}")

    return weights


def construct_portfolio_floor(gem_signal: dict, hy_regime: dict) -> dict:
    """
    Map GEM signal + HY regime to target weights using gem_floor strategy.

    Maintains 70% minimum equity floor. When absolute momentum fails,
    holds 70% relative momentum winner + 30% SHY instead of 100% SHY.
    """
    signal = gem_signal.get("gem_signal")
    relative_winner = gem_signal.get("relative_winner")
    absolute_pass = gem_signal.get("absolute_pass", False)
    regime = hy_regime.get("regime")
    fast_widen = hy_regime.get("fast_widen_override", False)

    if signal is None or regime is None or relative_winner is None:
        return {"SPY": 0.0, "EFA": 0.0, "SHY": 1.0, "ANGL": 0.0}

    # WIDENING_FAST override: 70% equity winner + 30% SHY
    if fast_widen:
        weights = {"SPY": 0.0, "EFA": 0.0, "SHY": 0.3, "ANGL": 0.0}
        weights[relative_winner] = 0.7
        return weights

    key = (relative_winner, regime)
    if absolute_pass:
        matrix = config.ALLOCATION_MATRIX_FLOOR_OFFENSIVE
    else:
        matrix = config.ALLOCATION_MATRIX_FLOOR_DEFENSIVE

    if key not in matrix:
        raise ValueError(f"Unknown key {key} for gem_floor strategy")

    weights = dict(matrix[key])

    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Weights sum to {total}, expected 1.0 for {key}")

    return weights


def compute_trades(
    current_weights: dict,
    target_weights: dict,
) -> list[dict]:
    """
    Compute trade list from current to target portfolio.

    Args:
        current_weights: {ticker: weight} of current portfolio
        target_weights: {ticker: weight} of target portfolio

    Returns:
        List of dicts: [{"ticker": str, "from": float, "to": float, "change": float}]
    """
    all_tickers = set(list(current_weights.keys()) + list(target_weights.keys()))
    trades = []

    for ticker in sorted(all_tickers):
        current = current_weights.get(ticker, 0.0)
        target = target_weights.get(ticker, 0.0)
        change = target - current

        if abs(change) > 1e-6:
            trades.append({
                "ticker": ticker,
                "from": round(current, 4),
                "to": round(target, 4),
                "change": round(change, 4),
            })

    return trades


def portfolio_description(weights: dict) -> str:
    """Human-readable portfolio description."""
    parts = []
    for ticker in config.ALL_TICKERS:
        w = weights.get(ticker, 0.0)
        if w > 0:
            parts.append(f"{ticker}: {w:.0%}")
    return " / ".join(parts) if parts else "EMPTY"


def construct_portfolio_v2(
    gem_signal: dict,
    hy_regime: dict,
    momentum_signal,
    rebalance_threshold: float = 0.02,
    prior_weights: dict = None,
) -> dict:
    """
    Three-stage portfolio construction (v2).

    Delegates to portfolio_v2.construct_portfolio(). Returns weights dict
    with '_metadata' key for decision reasoning.

    Args:
        gem_signal: dict from signals.compute_gem_signal()
        hy_regime: dict from signals.compute_hy_regime()
        momentum_signal: MomentumSignal from momentum_rank.py
        rebalance_threshold: minimum weight delta to trigger trade (default 2%)
        prior_weights: last month's portfolio weights (for threshold check)

    Returns:
        {ticker: weight} for all tickers, with '_metadata' key.
    """
    return portfolio_v2.construct_portfolio(
        gem_signal, hy_regime, momentum_signal,
        rebalance_threshold=rebalance_threshold,
        prior_weights=prior_weights,
    )


def format_portfolio_v2(weights: dict) -> str:
    """Format three-stage portfolio weights for console output."""
    return portfolio_v2.format_portfolio(weights)


if __name__ == "__main__":
    # Test all matrix cells
    print("Testing all decision matrix cells:\n")
    for (signal, regime), weights in config.ALLOCATION_MATRIX.items():
        desc = portfolio_description(weights)
        print(f"  GEM={signal:3s}  HY={regime:8s}  → {desc}")

    print(f"\n  WIDENING_FAST override → {portfolio_description(config.WIDENING_FAST_OVERRIDE)}")

    # Test trade computation
    print("\n\nTrade example: SPY 100% → EFA 70% / SHY 30%")
    current = {"SPY": 1.0, "EFA": 0.0, "SHY": 0.0, "ANGL": 0.0}
    target = {"SPY": 0.0, "EFA": 0.7, "SHY": 0.3, "ANGL": 0.0}
    trades = compute_trades(current, target)
    for t in trades:
        print(f"  {t['ticker']}: {t['from']:.0%} → {t['to']:.0%} ({t['change']:+.0%})")
