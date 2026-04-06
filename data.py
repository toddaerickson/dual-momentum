"""
Data Layer: FRED + yfinance data fetching with caching.

All external data access goes through this module.
No other module should import fredapi or yfinance directly.
"""

import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from fredapi import Fred

import config

warnings.filterwarnings("ignore", category=FutureWarning)


def _get_fred_client() -> Fred:
    """Initialize FRED API client."""
    if not config.FRED_API_KEY:
        raise ValueError(
            "FRED_API_KEY not set. Export it: export FRED_API_KEY='your_key'"
        )
    return Fred(api_key=config.FRED_API_KEY)


# ──────────────────────────────────────────────
# Caching
# ──────────────────────────────────────────────

def cache_data(df: pd.DataFrame, filename: str) -> None:
    """Save DataFrame to CSV cache."""
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    filepath = config.CACHE_DIR / filename
    df.to_csv(filepath)


def load_cache(filename: str, max_age_hours: float = None) -> pd.DataFrame | None:
    """Load cached data if exists and fresh enough."""
    if max_age_hours is None:
        max_age_hours = config.CACHE_EXPIRY_HOURS
    filepath = config.CACHE_DIR / filename
    if not filepath.exists():
        return None
    age_hours = (time.time() - filepath.stat().st_mtime) / 3600
    if age_hours > max_age_hours:
        return None
    try:
        df = pd.read_csv(filepath, index_col=0, parse_dates=True)
        return df
    except Exception:
        return None


# ──────────────────────────────────────────────
# FRED Data
# ──────────────────────────────────────────────

def get_fred_series(
    series_id: str,
    start_date: str = "1978-01-01",
    end_date: str = None,
) -> pd.Series:
    """
    Fetch a FRED data series.

    Args:
        series_id: FRED series ID (e.g., 'BAMLH0A0HYM2')
        start_date: Start date string 'YYYY-MM-DD'
        end_date: End date string or None for latest

    Returns:
        pd.Series with datetime index, named by series_id
    """
    cache_name = f"fred_{series_id}.csv"
    cached = load_cache(cache_name)
    if cached is not None and len(cached) > 0:
        series = cached.iloc[:, 0]
        series.name = series_id
        return series

    fred = _get_fred_client()
    data = fred.get_series(
        series_id,
        observation_start=start_date,
        observation_end=end_date,
    )
    data.name = series_id
    data = data.dropna()

    # Cache it
    cache_data(data.to_frame(), cache_name)
    print(f"FRED fetched: {series_id}, {len(data)} observations")
    return data


def get_hy_spread(start_date: str = "1996-01-01") -> pd.Series:
    """
    Fetch ICE BofA HY OAS spread (available from 1997).

    FRED reports OAS in percentage points (e.g., 3.50 = 3.50%).
    We convert to basis points (e.g., 350 bps) to match config thresholds.
    """
    raw = get_fred_series(config.FRED_SERIES["HY_OAS"], start_date)
    return raw * 100  # percentage points → basis points


def get_hy_ccc_spread(start_date: str = "1996-01-01") -> pd.Series:
    """Fetch ICE BofA CCC & Lower OAS spread in basis points."""
    raw = get_fred_series(config.FRED_SERIES["HY_CCC"], start_date)
    return raw * 100


def get_hy_bb_spread(start_date: str = "1996-01-01") -> pd.Series:
    """Fetch ICE BofA BB OAS spread in basis points."""
    raw = get_fred_series(config.FRED_SERIES["HY_BB"], start_date)
    return raw * 100


def get_hy_b_spread(start_date: str = "1996-01-01") -> pd.Series:
    """Fetch ICE BofA Single-B OAS spread in basis points."""
    raw = get_fred_series(config.FRED_SERIES["HY_B"], start_date)
    return raw * 100


def get_ccc_bb_spread(start_date: str = "1996-01-01") -> pd.Series:
    """
    Compute CCC-BB spread (CCC OAS minus BB OAS) in basis points.

    This is the primary risk-appetite signal for the dual-signal classifier.
    A wider CCC-BB spread indicates investors demanding more compensation
    for the lowest-quality credits relative to BB, i.e. declining risk appetite.
    """
    ccc = get_hy_ccc_spread(start_date)
    bb = get_hy_bb_spread(start_date)
    # Align on common dates
    combined = pd.concat([ccc, bb], axis=1, keys=["CCC", "BB"]).dropna()
    spread = combined["CCC"] - combined["BB"]
    spread.name = "CCC_BB_SPREAD"
    return spread


def get_tbill_rate(start_date: str = "1978-01-01") -> pd.Series:
    """Fetch 3-month T-bill rate from FRED (available from 1954)."""
    return get_fred_series(config.FRED_SERIES["DTB3"], start_date)


def get_yield_curve_spread(start_date: str = "1976-01-01") -> pd.Series:
    """Fetch 10Y-2Y Treasury spread (available from 1976)."""
    return get_fred_series(config.FRED_SERIES["T10Y2Y"], start_date)


# ──────────────────────────────────────────────
# ETF Price Data
# ──────────────────────────────────────────────

def get_etf_prices(
    tickers: list[str] = None,
    start_date: str = "1978-01-01",
    end_date: str = None,
) -> pd.DataFrame:
    """
    Fetch adjusted close prices for ETFs via yfinance.

    For pre-inception periods, fetches index proxies (e.g., ^GSPC for SPY)
    and splices them into the ETF columns.

    Args:
        tickers: List of ticker symbols. Defaults to ALL_TICKERS.
        start_date: Start date string.
        end_date: End date string or None for latest.

    Returns:
        DataFrame with date index, ticker columns, adjusted close prices.
    """
    if tickers is None:
        tickers = config.ALL_TICKERS

    cache_name = f"etf_prices_{'_'.join(sorted(tickers))}_{start_date}.csv"
    cached = load_cache(cache_name)
    if cached is not None and len(cached) > 0:
        return cached

    # Determine all yfinance tickers we need (ETFs + index proxies)
    all_yf_tickers = list(tickers)
    proxy_map = {}  # {etf_ticker: proxy_ticker}
    for ticker in tickers:
        sub = config.BACKTEST_SUBSTITUTIONS.get(ticker, {})
        proxy = sub.get("substitute_ticker")
        if proxy and proxy not in all_yf_tickers:
            all_yf_tickers.append(proxy)
            proxy_map[ticker] = proxy
        # Also fetch proxies-of-proxies (e.g., JNK→SHY chain for ANGL)
        if proxy:
            sub2 = config.BACKTEST_SUBSTITUTIONS.get(proxy, {})
            proxy2 = sub2.get("substitute_ticker")
            if proxy2 and proxy2 not in all_yf_tickers:
                all_yf_tickers.append(proxy2)

    for attempt in range(config.YFINANCE_RETRY_COUNT):
        try:
            data = yf.download(
                all_yf_tickers,
                start=start_date,
                end=end_date,
                auto_adjust=True,   # Use adjusted prices (divs/splits)
                progress=False,
            )

            # yf.download returns MultiIndex columns when multiple tickers
            if isinstance(data.columns, pd.MultiIndex):
                prices = data["Close"]
            else:
                # Single ticker case
                prices = data[["Close"]]
                prices.columns = all_yf_tickers

            prices = prices.dropna(how="all")

            # Splice proxy data into ETF columns for pre-inception periods
            for ticker in tickers:
                sub = config.BACKTEST_SUBSTITUTIONS.get(ticker, {})
                proxy = sub.get("substitute_ticker")
                before = sub.get("before")
                if proxy and proxy in prices.columns and before:
                    before_date = pd.Timestamp(before)
                    if ticker in prices.columns:
                        # Scale proxy to match ETF price at splice point
                        # Find first overlapping date
                        etf_valid = prices[ticker].dropna()
                        proxy_valid = prices[proxy].dropna()
                        if len(etf_valid) > 0 and len(proxy_valid) > 0:
                            splice_date = etf_valid.index[0]
                            if splice_date in proxy_valid.index:
                                scale = etf_valid.iloc[0] / proxy_valid.loc[splice_date]
                            else:
                                # Find nearest proxy date
                                nearest = proxy_valid.index[proxy_valid.index <= splice_date]
                                if len(nearest) > 0:
                                    scale = etf_valid.iloc[0] / proxy_valid.loc[nearest[-1]]
                                else:
                                    scale = 1.0
                            # Fill pre-inception with scaled proxy
                            pre_inception = proxy_valid[proxy_valid.index < splice_date] * scale
                            prices.loc[pre_inception.index, ticker] = pre_inception
                    else:
                        # ETF column doesn't exist at all, create from proxy
                        if proxy in prices.columns:
                            prices[ticker] = prices[proxy]

            # Drop proxy columns that aren't in the original ticker list
            cols_to_keep = [c for c in prices.columns if c in tickers]
            prices = prices[cols_to_keep]

            prices = prices.dropna(how="all")
            cache_data(prices, cache_name)
            print(f"Prices fetched: {len(prices)} rows for {tickers}")
            return prices

        except Exception as e:
            if attempt < config.YFINANCE_RETRY_COUNT - 1:
                print(f"yfinance attempt {attempt + 1} failed: {e}. Retrying...")
                time.sleep(config.YFINANCE_RETRY_DELAY_SEC)
            else:
                raise RuntimeError(
                    f"Failed to fetch prices after {config.YFINANCE_RETRY_COUNT} attempts: {e}"
                )


def get_trailing_total_return(
    prices: pd.DataFrame,
    ticker: str,
    as_of_date: pd.Timestamp,
    months: int = 12,
) -> float:
    """
    Compute trailing total return for a ticker.

    Uses adjusted close prices (which incorporate dividends and splits).

    Args:
        prices: DataFrame of adjusted close prices (from get_etf_prices)
        ticker: Ticker symbol
        as_of_date: Compute return as of this date
        months: Lookback period in months

    Returns:
        Total return as a decimal (e.g., 0.094 = +9.4%)
    """
    if ticker not in prices.columns:
        raise ValueError(f"Ticker {ticker} not in price data")

    # Find the start date (N months ago)
    start_date = as_of_date - pd.DateOffset(months=months)

    # Get prices on or before the target dates
    ticker_prices = prices[ticker].dropna()

    # Find nearest available dates
    end_mask = ticker_prices.index <= as_of_date
    start_mask = ticker_prices.index <= start_date

    if not end_mask.any() or not start_mask.any():
        return np.nan

    end_price = ticker_prices[end_mask].iloc[-1]
    start_price = ticker_prices[start_mask].iloc[-1]

    if start_price == 0 or np.isnan(start_price):
        return np.nan

    return (end_price / start_price) - 1.0


def get_tbill_total_return(
    as_of_date: pd.Timestamp,
    months: int = 12,
) -> float:
    """
    Compute approximate T-bill total return from FRED DTB3 rates.

    Used for pre-BIL backtest periods. Approximates by compounding
    the 3-month rate over the lookback period.

    Args:
        as_of_date: End date
        months: Lookback period

    Returns:
        Approximate total return as decimal
    """
    dtb3 = get_tbill_rate()
    start_date = as_of_date - pd.DateOffset(months=months)

    # Get rates in the window
    mask = (dtb3.index >= start_date) & (dtb3.index <= as_of_date)
    rates = dtb3[mask].dropna()

    if len(rates) == 0:
        return np.nan

    # Approximate: compound daily rates (annual rate / 360)
    daily_returns = rates / 100 / 360
    cumulative = (1 + daily_returns).prod() - 1
    return cumulative


# ──────────────────────────────────────────────
# Convenience: fetch all required data
# ──────────────────────────────────────────────

def fetch_all(start_date: str = "1978-01-01") -> dict:
    """
    Fetch all data needed by the model.

    Returns dict with keys: 'prices', 'hy_spread', 'ccc_bb_spread',
    'hy_b_spread', 'yield_curve'
    """
    prices = get_etf_prices(start_date=start_date)
    hy_spread = get_hy_spread(start_date=start_date)
    ccc_bb_spread = get_ccc_bb_spread(start_date=start_date)
    hy_b_spread = get_hy_b_spread(start_date=start_date)
    yield_curve = get_yield_curve_spread(start_date=start_date)

    return {
        "prices": prices,
        "hy_spread": hy_spread,
        "ccc_bb_spread": ccc_bb_spread,
        "hy_b_spread": hy_b_spread,
        "yield_curve": yield_curve,
    }


if __name__ == "__main__":
    # Quick test
    print("Fetching all data...")
    data = fetch_all()
    print(f"\nPrices shape: {data['prices'].shape}")
    print(f"HY spread: {len(data['hy_spread'])} obs, latest: {data['hy_spread'].iloc[-1]:.0f} bps")
    print(f"CCC-BB spread: {len(data['ccc_bb_spread'])} obs, latest: {data['ccc_bb_spread'].iloc[-1]:.0f} bps")
    print(f"Single-B OAS: {len(data['hy_b_spread'])} obs, latest: {data['hy_b_spread'].iloc[-1]:.0f} bps")
    print(f"Yield curve: {len(data['yield_curve'])} obs, latest: {data['yield_curve'].iloc[-1]:.2f}%")
