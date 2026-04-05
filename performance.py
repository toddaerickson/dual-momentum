"""
Performance Metrics and Comparison Tables.

All metrics computed from daily return series.
"""

import numpy as np
import pandas as pd

import config


def compute_metrics(result_df: pd.DataFrame) -> dict:
    """
    Compute comprehensive performance metrics from backtest results.

    Args:
        result_df: DataFrame with 'daily_return' and 'cumulative' columns

    Returns:
        dict of performance metrics
    """
    returns = result_df["daily_return"]
    cumulative = result_df["cumulative"]
    n_days = len(returns)
    n_years = n_days / 252

    # CAGR
    final_value = cumulative.iloc[-1]
    cagr = (final_value ** (1 / n_years)) - 1 if n_years > 0 else 0

    # Total return
    total_return = final_value - 1

    # Annualized volatility
    ann_vol = returns.std() * np.sqrt(252)

    # Max drawdown
    rolling_max = cumulative.cummax()
    drawdown = (cumulative - rolling_max) / rolling_max
    max_drawdown = drawdown.min()

    # Max drawdown duration (trading days)
    in_drawdown = drawdown < 0
    if in_drawdown.any():
        dd_groups = (~in_drawdown).cumsum()
        dd_durations = in_drawdown.groupby(dd_groups).sum()
        max_dd_duration = int(dd_durations.max()) if len(dd_durations) > 0 else 0
    else:
        max_dd_duration = 0

    # Sharpe ratio (using mean BIL return as risk-free approx)
    rf_daily = returns.mean() * 0.1  # Rough approximation; refine with actual BIL
    excess = returns - rf_daily / 252
    sharpe = (excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else 0

    # Sortino ratio (downside deviation only)
    downside = returns[returns < 0]
    downside_std = downside.std() * np.sqrt(252) if len(downside) > 0 else 1e-10
    sortino = (cagr / downside_std) if downside_std > 0 else 0

    # Calmar ratio
    calmar = (cagr / abs(max_drawdown)) if max_drawdown != 0 else 0

    # Annual returns
    result_df_copy = result_df.copy()
    result_df_copy["year"] = result_df_copy.index.year
    annual = result_df_copy.groupby("year")["daily_return"].apply(
        lambda x: (1 + x).prod() - 1
    )
    best_year = annual.max()
    worst_year = annual.min()
    pct_positive_years = (annual > 0).mean()
    best_year_label = str(annual.idxmax())
    worst_year_label = str(annual.idxmin())

    # Turnover: count rebalance events (weight changes)
    if "weights" in result_df.columns:
        weight_changes = result_df["weights"].ne(result_df["weights"].shift())
        rebalance_count = weight_changes.sum()
        avg_annual_turnover = rebalance_count / n_years if n_years > 0 else 0
    else:
        avg_annual_turnover = 0

    return {
        "cagr": round(cagr, 4),
        "total_return": round(total_return, 4),
        "ann_volatility": round(ann_vol, 4),
        "max_drawdown": round(max_drawdown, 4),
        "max_dd_duration_days": max_dd_duration,
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
        "best_year": round(best_year, 4),
        "best_year_label": best_year_label,
        "worst_year": round(worst_year, 4),
        "worst_year_label": worst_year_label,
        "pct_positive_years": round(pct_positive_years, 4),
        "avg_annual_turnover": round(avg_annual_turnover, 1),
        "n_years": round(n_years, 1),
    }


def comparison_table(results: dict[str, pd.DataFrame]) -> str:
    """
    Generate formatted side-by-side comparison table.

    Args:
        results: {"Strategy Name": backtest_df, ...}

    Returns:
        Formatted string table
    """
    all_metrics = {}
    for name, df in results.items():
        all_metrics[name] = compute_metrics(df)

    # Build table
    metrics_order = [
        ("CAGR", "cagr", "{:+.1%}"),
        ("Total Return", "total_return", "{:.1%}"),
        ("Ann. Volatility", "ann_volatility", "{:.1%}"),
        ("Max Drawdown", "max_drawdown", "{:.1%}"),
        ("Max DD Duration", "max_dd_duration_days", "{:d} days"),
        ("Sharpe", "sharpe", "{:.2f}"),
        ("Sortino", "sortino", "{:.2f}"),
        ("Calmar", "calmar", "{:.2f}"),
        ("Best Year", "best_year", "{:+.1%}"),
        ("Worst Year", "worst_year", "{:+.1%}"),
        ("% Positive Years", "pct_positive_years", "{:.0%}"),
        ("Turnover/Year", "avg_annual_turnover", "{:.1f}"),
    ]

    names = list(results.keys())
    col_width = max(14, max(len(n) for n in names) + 2)

    # Header
    header = f"{'Metric':<20s}"
    for name in names:
        header += f"{name:>{col_width}s}"
    lines = [header, "─" * len(header)]

    # Rows
    for label, key, fmt in metrics_order:
        row = f"{label:<20s}"
        for name in names:
            val = all_metrics[name][key]
            formatted = fmt.format(val)
            row += f"{formatted:>{col_width}s}"
        lines.append(row)

    return "\n".join(lines)


def rolling_returns(
    result_df: pd.DataFrame,
    window_years: int = 3,
) -> pd.Series:
    """
    Compute annualized rolling N-year returns.

    Args:
        result_df: Backtest results with 'daily_return' column
        window_years: Rolling window in years

    Returns:
        Series of annualized rolling returns
    """
    window_days = window_years * 252
    rolling_cum = (1 + result_df["daily_return"]).rolling(window_days).apply(
        lambda x: x.prod(), raw=True
    )
    rolling_ann = rolling_cum ** (1 / window_years) - 1
    rolling_ann.name = f"rolling_{window_years}y_return"
    return rolling_ann


def drawdown_series(result_df: pd.DataFrame) -> pd.Series:
    """Compute drawdown series from cumulative returns."""
    cumulative = result_df["cumulative"]
    rolling_max = cumulative.cummax()
    dd = (cumulative - rolling_max) / rolling_max
    dd.name = "drawdown"
    return dd


if __name__ == "__main__":
    # Test with synthetic data
    np.random.seed(42)
    n = 252 * 5  # 5 years
    daily_rets = np.random.normal(0.0003, 0.01, n)
    dates = pd.bdate_range("2019-01-01", periods=n)
    df = pd.DataFrame({
        "daily_return": daily_rets,
        "cumulative": (1 + pd.Series(daily_rets)).cumprod(),
        "weights": "test",
    }, index=dates)

    metrics = compute_metrics(df)
    print("Test Metrics:")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
