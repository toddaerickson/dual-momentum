"""
Portfolio Constructor: Signal → Target Weights

Three-stage pipeline:
  Stage 1: Absolute momentum gate (pass/fail)
  Stage 2: Momentum ranking → risky asset weights
  Stage 3: HY regime → risk budget sizing
"""

import portfolio_v2


def construct_portfolio(
    gem_signal: dict,
    hy_regime: dict,
    momentum_signal,
    rebalance_threshold: float = 0.02,
    prior_weights: dict = None,
) -> dict:
    """
    Three-stage portfolio construction.

    Delegates to portfolio_v2.construct_portfolio(). Returns weights dict
    with '_metadata' key for decision reasoning.

    Args:
        gem_signal: dict from signals.compute_absolute_momentum()
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


def format_portfolio(weights: dict) -> str:
    """Format portfolio weights for console output."""
    return portfolio_v2.format_portfolio(weights)


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
    for ticker in portfolio_v2.ALL_TICKERS:
        w = weights.get(ticker, 0.0)
        if w > 0.001:
            parts.append(f"{ticker}: {w:.0%}")
    return " / ".join(parts) if parts else "EMPTY"


if __name__ == "__main__":
    print("Portfolio module ready. Use construct_portfolio() with three-stage signals.")
    print(f"Ticker universe: {portfolio_v2.ALL_TICKERS}")
