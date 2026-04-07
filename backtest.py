"""
Backtest Engine: Historical simulation of the model portfolio.

Strategies:
  - momentum_hy: Three-stage: abs momentum gate + momentum ranking + HY overlay
  - sixty_forty: 60% SPY / 40% SHY, monthly rebalance (control)
  - buy_hold:    100% SPY buy-and-hold (control)

Critical constraint: NO FUTURE DATA LEAKAGE.
All signals computed using only data available as of the decision date.
"""

import json
from datetime import datetime

import numpy as np
import pandas as pd

import config
import data as data_mod
import signals as signals_mod
import portfolio_v2


def _get_month_end_dates(
    prices: pd.DataFrame,
    start_date: str,
    end_date: str = None,
) -> list[pd.Timestamp]:
    """Get last trading day of each month within the date range."""
    if end_date is None:
        end_date = prices.index[-1].strftime("%Y-%m-%d")

    mask = (prices.index >= pd.Timestamp(start_date)) & (prices.index <= pd.Timestamp(end_date))
    filtered = prices[mask]

    # Group by year-month, take last date in each group
    month_ends = filtered.groupby(filtered.index.to_period("M")).apply(
        lambda x: x.index[-1]
    )
    return list(month_ends)


def _compute_daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Compute daily returns from price series."""
    return prices.pct_change().fillna(0)


def run_backtest(
    start_date: str = None,
    end_date: str = None,
    strategy: str = "momentum_hy",
) -> pd.DataFrame:
    """
    Run historical backtest for the specified strategy.

    Args:
        start_date: Backtest start (default: config.BACKTEST_START)
        end_date: Backtest end (default: latest data)
        strategy: One of 'momentum_hy', 'sixty_forty', 'buy_hold'

    Returns:
        DataFrame with daily portfolio values and metadata
    """
    if start_date is None:
        start_date = config.BACKTEST_START

    # Fetch data (need extra lookback for 12-month signal)
    fetch_start = (pd.Timestamp(start_date) - pd.DateOffset(months=15)).strftime("%Y-%m-%d")
    prices = data_mod.get_etf_prices(start_date=fetch_start, end_date=end_date)
    hy_spread = data_mod.get_hy_spread(start_date=fetch_start)
    ccc_bb_spread = data_mod.get_ccc_bb_spread(start_date=fetch_start)
    hy_b_spread = data_mod.get_hy_b_spread(start_date=fetch_start)
    daily_returns = _compute_daily_returns(prices)

    if end_date is None:
        end_date = prices.index[-1].strftime("%Y-%m-%d")

    # Get rebalance dates (month-ends)
    rebalance_dates = _get_month_end_dates(prices, start_date, end_date)

    if strategy == "buy_hold":
        return _run_buy_hold(prices, daily_returns, start_date, end_date)
    elif strategy == "sixty_forty":
        return _run_sixty_forty(prices, daily_returns, rebalance_dates, start_date, end_date)
    elif strategy == "momentum_hy":
        return _run_momentum_hy(prices, daily_returns, hy_spread, ccc_bb_spread,
                                hy_b_spread, rebalance_dates,
                                start_date, end_date)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def _run_buy_hold(
    prices: pd.DataFrame,
    daily_returns: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """100% SPY buy-and-hold."""
    mask = (prices.index >= pd.Timestamp(start_date)) & (prices.index <= pd.Timestamp(end_date))
    spy_returns = daily_returns.loc[mask, "SPY"]

    result = pd.DataFrame(index=spy_returns.index)
    result["daily_return"] = spy_returns
    result["cumulative"] = (1 + spy_returns).cumprod()
    result["strategy"] = "buy_hold"
    result["weights"] = json.dumps({"SPY": 1.0})
    result["absolute_pass"] = ""
    result["hy_regime"] = ""
    return result


def _run_sixty_forty(
    prices: pd.DataFrame,
    daily_returns: pd.DataFrame,
    rebalance_dates: list,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """60% SPY / 40% SHY, rebalanced monthly."""
    mask = (prices.index >= pd.Timestamp(start_date)) & (prices.index <= pd.Timestamp(end_date))
    filtered_returns = daily_returns[mask]

    weights = {"SPY": 0.6, "SHY": 0.4}
    portfolio_returns = []

    for date in filtered_returns.index:
        day_ret = sum(
            weights.get(t, 0) * filtered_returns.loc[date].get(t, 0)
            for t in config.ALL_TICKERS
        )
        portfolio_returns.append(day_ret)

    result = pd.DataFrame(index=filtered_returns.index)
    result["daily_return"] = portfolio_returns
    result["cumulative"] = (1 + result["daily_return"]).cumprod()
    result["strategy"] = "sixty_forty"
    result["weights"] = json.dumps(weights)
    result["absolute_pass"] = ""
    result["hy_regime"] = ""
    return result


def _run_momentum_hy(
    prices: pd.DataFrame,
    daily_returns: pd.DataFrame,
    hy_spread: pd.Series,
    ccc_bb_spread: pd.Series,
    hy_b_spread: pd.Series,
    rebalance_dates: list,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """
    Three-stage strategy: GEM absolute momentum + momentum ranking + HY regime.

    Stage 1: GEM absolute momentum filter (crash avoidance).
    Stage 2: Cross-asset momentum ranking (asset selection).
    Stage 3: HY regime (risk budget sizing).
    """
    mask = (prices.index >= pd.Timestamp(start_date)) & (prices.index <= pd.Timestamp(end_date))
    filtered_dates = daily_returns[mask].index

    # Initialize — all tickers from portfolio_v2
    current_weights = {t: 0.0 for t in portfolio_v2.ALL_TICKERS}
    current_weights["SHY"] = 1.0  # Start defensive
    results = []
    rebalance_set = set(rebalance_dates)
    tc_bps = config.TRANSACTION_COST_BPS / 10000

    current_abs_pass = False
    current_hy_regime = "NORMAL"
    prior_momentum_signal = None

    for date in filtered_dates:
        if date in rebalance_set:
            # Compute all three signals
            gem_sig = signals_mod.compute_absolute_momentum(prices, date)

            hy_data_available = (
                len(ccc_bb_spread) > 0
                and len(hy_b_spread) > 0
                and date >= pd.Timestamp(config.HY_OAS_AVAILABLE_FROM)
            )
            if hy_data_available:
                hy_reg = signals_mod.compute_hy_regime(
                    ccc_bb_spread, hy_b_spread, date, hy_spread=hy_spread,
                )
            else:
                hy_reg = {"regime": "TIGHT", "fast_widen_override": False}

            mom_sig = signals_mod.compute_momentum_ranking(
                prices, date, prior_signal=prior_momentum_signal,
            )

            current_abs_pass = gem_sig.get("absolute_pass", False)
            current_hy_regime = hy_reg.get("regime", "NORMAL") or "NORMAL"

            # Three-stage portfolio construction
            target_result = portfolio_v2.construct_portfolio(
                gem_sig, hy_reg, mom_sig,
                prior_weights=current_weights,
            )

            # Extract weights (filter out _metadata)
            target_weights = {
                k: v for k, v in target_result.items()
                if k != "_metadata" and isinstance(v, (int, float))
            }

            # Transaction costs
            tc = 0.0
            for ticker in portfolio_v2.ALL_TICKERS:
                old_w = current_weights.get(ticker, 0.0)
                new_w = target_weights.get(ticker, 0.0)
                if abs(new_w - old_w) > 1e-6:
                    tc += tc_bps

            current_weights = target_weights
            prior_momentum_signal = mom_sig

        # Daily portfolio return
        day_ret = sum(
            current_weights.get(t, 0) * daily_returns.loc[date].get(t, 0)
            for t in portfolio_v2.ALL_TICKERS
            if t in daily_returns.columns
        )

        if date in rebalance_set:
            day_ret -= tc

        results.append({
            "date": date,
            "daily_return": day_ret,
            "absolute_pass": current_abs_pass,
            "hy_regime": current_hy_regime,
            "weights": json.dumps({k: round(v, 4) for k, v in current_weights.items() if v > 0}),
        })

    result = pd.DataFrame(results).set_index("date")
    result["cumulative"] = (1 + result["daily_return"]).cumprod()
    result["strategy"] = "momentum_hy"
    return result


def run_all_strategies(
    start_date: str = None,
    end_date: str = None,
) -> dict[str, pd.DataFrame]:
    """
    Run all strategies and return results dict.

    Returns:
        {"momentum_hy": df, "sixty_forty": df, "buy_hold": df}
    """
    strategies = ["momentum_hy", "sixty_forty", "buy_hold"]
    results = {}

    for s in strategies:
        print(f"Running backtest: {s}...")
        results[s] = run_backtest(start_date, end_date, strategy=s)
        print(f"  Done. {len(results[s])} trading days.")

    return results


if __name__ == "__main__":
    print("Running full backtest suite...\n")
    results = run_all_strategies()

    print("\n=== Strategy Summary ===")
    print(f"  {'Strategy':<12s}  {'Return':>8s}  {'CAGR':>8s}  {'Max DD':>8s}  {'Ann Vol':>8s}")
    print(f"  {'─' * 48}")
    for name, df in results.items():
        final = df["cumulative"].iloc[-1]
        years = len(df) / 252
        cagr = (final ** (1 / years)) - 1
        ann_vol = df["daily_return"].std() * np.sqrt(252)
        rolling_max = df["cumulative"].cummax()
        max_dd = ((df["cumulative"] - rolling_max) / rolling_max).min()
        print(f"  {name:<12s}  {final:>7.2f}x  {cagr:>+7.1%}  {max_dd:>+7.1%}  {ann_vol:>7.1%}")
