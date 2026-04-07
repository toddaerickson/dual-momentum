"""
Test harness for momentum_rank.py and portfolio_v2.py.

Uses synthetic price data to validate:
1. Momentum computation correctness
2. Vol adjustment scaling
3. Ranking and tercile assignment
4. Hysteresis behavior
5. Weight normalization
6. Portfolio construction across all regime/GEM branches
7. WIDENING_FAST override
8. Edge cases (missing data, degenerate universes)
"""

import numpy as np
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from momentum_rank import (
    MomentumConfig,
    compute_12_1_momentum,
    compute_trailing_vol,
    vol_adjusted_momentum,
    rank_assets,
    assign_terciles,
    apply_hysteresis,
    compute_momentum_signal,
    format_momentum_signal,
    _tercile_sizes,
)
from portfolio_v2 import (
    construct_portfolio,
    format_portfolio,
    ALL_TICKERS,
)


def make_synthetic_prices(
    tickers, start="2015-01-02", end="2026-03-31",
    annual_returns=None, annual_vols=None, seed=42
):
    """
    Generate synthetic daily price series for testing.

    Args:
        tickers: list of ticker strings.
        annual_returns: dict {ticker: annualized return}. Default varies.
        annual_vols: dict {ticker: annualized vol}. Default varies.
        seed: random seed for reproducibility.
    """
    np.random.seed(seed)

    default_vols = {
        "SPY": 0.16, "EFA": 0.18, "EEM": 0.22,
        "VNQ": 0.19, "DBC": 0.20, "GLD": 0.16,
        "ANGL": 0.08, "SHY": 0.02, "BIL": 0.005,
    }
    default_rets = {
        "SPY": 0.10, "EFA": 0.06, "EEM": 0.08,
        "VNQ": 0.07, "DBC": 0.03, "GLD": 0.08,
        "ANGL": 0.05, "SHY": 0.02, "BIL": 0.04,
    }

    dates = pd.bdate_range(start, end)
    prices = {}

    for ticker in tickers:
        mu = (annual_returns or default_rets).get(ticker, 0.08)
        sigma = (annual_vols or default_vols).get(ticker, 0.15)

        daily_mu = mu / 252
        daily_sigma = sigma / np.sqrt(252)

        log_returns = np.random.normal(daily_mu, daily_sigma, len(dates))
        price_series = 100.0 * np.exp(np.cumsum(log_returns))
        prices[ticker] = price_series

    return pd.DataFrame(prices, index=dates)


# ──────────────────────────────────────────────
# Test functions
# ──────────────────────────────────────────────

def test_tercile_sizes():
    """Validate tercile sizing for expected universe sizes."""
    assert _tercile_sizes(5) == (2, 1, 2), f"n=5: {_tercile_sizes(5)}"
    assert _tercile_sizes(6) == (2, 2, 2), f"n=6: {_tercile_sizes(6)}"
    assert _tercile_sizes(4) == (2, 1, 1), f"n=4: {_tercile_sizes(4)}"
    assert _tercile_sizes(3) == (1, 1, 1), f"n=3: {_tercile_sizes(3)}"
    print("  PASS: tercile_sizes")


def test_momentum_computation():
    """Validate 12-1 month momentum with known prices."""
    tickers = ["SPY", "EFA", "EEM", "VNQ", "DBC", "GLD"]
    prices = make_synthetic_prices(tickers)
    as_of = pd.Timestamp("2025-06-30")

    mom = compute_12_1_momentum(prices, as_of, lookback_months=12, skip_months=1)

    for t in tickers:
        assert not np.isnan(mom[t]), f"{t} momentum is NaN"

    # Verify SPY momentum manually
    end_target = as_of - pd.DateOffset(months=1)
    start_target = as_of - pd.DateOffset(months=12)

    spy = prices["SPY"].dropna()
    spy = spy[spy.index <= as_of]
    end_p = spy[spy.index <= end_target].iloc[-1]
    start_p = spy[spy.index <= start_target].iloc[-1]
    expected = (end_p / start_p) - 1.0

    assert abs(mom["SPY"] - expected) < 1e-10, (
        f"SPY momentum mismatch: {mom['SPY']:.6f} vs {expected:.6f}"
    )
    print("  PASS: momentum_computation")


def test_vol_computation():
    """Validate trailing vol is reasonable."""
    tickers = ["SPY", "DBC"]
    prices = make_synthetic_prices(tickers)
    as_of = pd.Timestamp("2025-06-30")

    vols = compute_trailing_vol(prices, as_of, lookback_months=60, min_months=12)

    # SPY synthetic vol should be near 0.16 (+/-0.04)
    assert 0.10 < vols["SPY"] < 0.25, f"SPY vol out of range: {vols['SPY']:.4f}"
    # DBC synthetic vol should be near 0.20 (+/-0.05)
    assert 0.12 < vols["DBC"] < 0.30, f"DBC vol out of range: {vols['DBC']:.4f}"
    print("  PASS: vol_computation")


def test_vol_adjustment():
    """Vol adjustment should compress high-vol momentum and expand low-vol."""
    mom = {"SPY": 0.10, "DBC": 0.10}  # same raw momentum
    vol = {"SPY": 0.16, "DBC": 0.24}  # DBC higher vol

    adj = vol_adjusted_momentum(mom, vol, target_vol=0.10)

    # SPY scaled up: 0.10 * (0.10/0.16) = 0.0625
    # DBC scaled down: 0.10 * (0.10/0.24) = 0.0417
    assert adj["SPY"] > adj["DBC"], (
        f"Vol adjustment failed: SPY={adj['SPY']:.4f}, DBC={adj['DBC']:.4f}"
    )
    assert abs(adj["SPY"] - 0.0625) < 1e-6
    assert abs(adj["DBC"] - 0.10 * 0.10 / 0.24) < 1e-6
    print("  PASS: vol_adjustment")


def test_ranking():
    """Validate ranking is descending by score."""
    scores = {"SPY": 0.15, "EFA": 0.08, "EEM": 0.20, "VNQ": 0.05, "DBC": 0.12, "GLD": 0.10}
    ranks = rank_assets(scores)

    assert ranks["EEM"] == 1  # highest
    assert ranks["SPY"] == 2
    assert ranks["DBC"] == 3
    assert ranks["GLD"] == 4
    assert ranks["EFA"] == 5
    assert ranks["VNQ"] == 6  # lowest
    print("  PASS: ranking")


def test_tercile_assignment():
    """Validate tercile assignment for 6 assets."""
    ranks = {"EEM": 1, "SPY": 2, "DBC": 3, "GLD": 4, "EFA": 5, "VNQ": 6}
    tickers = ["SPY", "EFA", "EEM", "VNQ", "DBC", "GLD"]

    terciles = assign_terciles(ranks, tickers)

    assert terciles["EEM"] == "TOP"
    assert terciles["SPY"] == "TOP"
    assert terciles["DBC"] == "MID"
    assert terciles["GLD"] == "MID"
    assert terciles["EFA"] == "BOTTOM"
    assert terciles["VNQ"] == "BOTTOM"
    print("  PASS: tercile_assignment")


def test_hysteresis_prevents_small_demotion():
    """Asset dropping 1 rank should not be demoted."""
    current_terciles = {"SPY": "MID", "EFA": "TOP", "EEM": "TOP", "VNQ": "BOTTOM", "DBC": "BOTTOM", "GLD": "MID"}
    prior_terciles = {"SPY": "TOP", "EFA": "TOP", "EEM": "MID", "VNQ": "BOTTOM", "DBC": "BOTTOM", "GLD": "MID"}
    current_ranks = {"SPY": 3, "EFA": 1, "EEM": 2, "VNQ": 5, "DBC": 6, "GLD": 4}
    prior_ranks = {"SPY": 2, "EFA": 1, "EEM": 3, "VNQ": 5, "DBC": 6, "GLD": 4}

    adjusted = apply_hysteresis(
        current_terciles, prior_terciles,
        current_ranks, prior_ranks,
        hysteresis=1
    )

    # SPY dropped from rank 2 -> 3 (delta=1, <= hysteresis): keep TOP
    assert adjusted["SPY"] == "TOP", f"SPY should stay TOP, got {adjusted['SPY']}"
    # EEM promoted from MID -> TOP: always apply
    assert adjusted["EEM"] == "TOP", f"EEM should be TOP, got {adjusted['EEM']}"
    print("  PASS: hysteresis_prevents_small_demotion")


def test_hysteresis_allows_large_demotion():
    """Asset dropping 2+ ranks should be demoted."""
    current_terciles = {"SPY": "BOTTOM", "EFA": "TOP", "EEM": "TOP", "VNQ": "MID", "DBC": "BOTTOM", "GLD": "MID"}
    prior_terciles = {"SPY": "TOP", "EFA": "MID", "EEM": "TOP", "VNQ": "BOTTOM", "DBC": "BOTTOM", "GLD": "MID"}
    current_ranks = {"SPY": 6, "EFA": 1, "EEM": 2, "VNQ": 3, "DBC": 5, "GLD": 4}
    prior_ranks = {"SPY": 2, "EFA": 3, "EEM": 1, "VNQ": 5, "DBC": 6, "GLD": 4}

    adjusted = apply_hysteresis(
        current_terciles, prior_terciles,
        current_ranks, prior_ranks,
        hysteresis=1
    )

    # SPY dropped from rank 2 -> 6 (delta=4, > hysteresis): demote
    assert adjusted["SPY"] == "BOTTOM", f"SPY should be BOTTOM, got {adjusted['SPY']}"
    # EFA promoted from MID -> TOP: always apply
    assert adjusted["EFA"] == "TOP", f"EFA should be TOP, got {adjusted['EFA']}"
    print("  PASS: hysteresis_allows_large_demotion")


def test_weight_normalization():
    """Weights within risky budget must sum to 1.0."""
    config = MomentumConfig()

    # For 6 assets: top=2 @ 0.30, mid=2 @ 0.20, bot=2 @ 0.00
    # Sum = 2*0.30 + 2*0.20 + 2*0.00 = 1.00
    config.validate()  # should not raise
    print("  PASS: weight_normalization (config validates)")


def test_full_pipeline():
    """End-to-end momentum signal computation."""
    tickers = ["SPY", "EFA", "EEM", "VNQ", "DBC", "GLD"]
    prices = make_synthetic_prices(tickers + ["SHY", "BIL", "ANGL"])
    config = MomentumConfig()
    as_of = pd.Timestamp("2025-06-30")

    signal = compute_momentum_signal(prices, as_of, prior_signal=None, config=config)

    assert signal.available_assets == 6
    assert sum(signal.risky_weights.values()) - 1.0 < 1e-6
    assert all(t in signal.terciles for t in tickers)

    # Print for visual inspection
    print(format_momentum_signal(signal))
    print("  PASS: full_pipeline")


def test_portfolio_gem_fail():
    """When GEM fails, portfolio should be 100% SHY regardless of momentum."""
    tickers = ["SPY", "EFA", "EEM", "VNQ", "DBC", "GLD"]
    prices = make_synthetic_prices(tickers + ["SHY", "BIL", "ANGL"])
    config = MomentumConfig()

    mom_signal = compute_momentum_signal(
        prices, pd.Timestamp("2025-06-30"), config=config
    )

    gem = {
        "gem_signal": "SHY",
        "absolute_pass": False,
        "spy_12m_return": 0.02,
        "efa_12m_return": 0.01,
        "bil_12m_return": 0.04,
        "relative_winner": "SPY",
    }
    hy = {"regime": "NORMAL", "fast_widen_override": False}

    weights = construct_portfolio(gem, hy, mom_signal)
    assert weights["SHY"] == 1.0, f"SHY should be 1.0, got {weights['SHY']}"
    assert all(weights.get(t, 0.0) == 0.0 for t in tickers), "Risky assets should be 0"
    print("  PASS: portfolio_gem_fail -> 100% SHY")


def test_portfolio_normal_regime():
    """GEM pass + NORMAL regime -> full risky budget to momentum ranking."""
    tickers = ["SPY", "EFA", "EEM", "VNQ", "DBC", "GLD"]
    prices = make_synthetic_prices(tickers + ["SHY", "BIL", "ANGL"])
    config = MomentumConfig()

    mom_signal = compute_momentum_signal(
        prices, pd.Timestamp("2025-06-30"), config=config
    )

    gem = {
        "gem_signal": "SPY",
        "absolute_pass": True,
        "spy_12m_return": 0.12,
        "efa_12m_return": 0.08,
        "bil_12m_return": 0.04,
        "relative_winner": "SPY",
    }
    hy = {"regime": "NORMAL", "fast_widen_override": False}

    weights = construct_portfolio(gem, hy, mom_signal)

    # Remove metadata for sum check
    numeric_weights = {k: v for k, v in weights.items() if k != "_metadata"}
    total = sum(numeric_weights.values())
    assert abs(total - 1.0) < 1e-6, f"Weights sum to {total}"

    # SHY should be 0 in NORMAL regime (GEM passed)
    assert weights["SHY"] == 0.0, f"SHY should be 0, got {weights['SHY']}"
    # ANGL should be 0 (not crisis)
    assert weights["ANGL"] == 0.0, f"ANGL should be 0, got {weights['ANGL']}"

    print(format_portfolio(weights))
    print("  PASS: portfolio_normal_regime")


def test_portfolio_stressed_regime():
    """STRESSED regime -> 70% risky / 30% SHY."""
    tickers = ["SPY", "EFA", "EEM", "VNQ", "DBC", "GLD"]
    prices = make_synthetic_prices(tickers + ["SHY", "BIL", "ANGL"])
    config = MomentumConfig()

    mom_signal = compute_momentum_signal(
        prices, pd.Timestamp("2025-06-30"), config=config
    )

    gem = {
        "gem_signal": "SPY",
        "absolute_pass": True,
        "spy_12m_return": 0.10,
        "efa_12m_return": 0.06,
        "bil_12m_return": 0.04,
        "relative_winner": "SPY",
    }
    hy = {"regime": "STRESSED", "fast_widen_override": False}

    weights = construct_portfolio(gem, hy, mom_signal)

    numeric_weights = {k: v for k, v in weights.items() if k != "_metadata"}
    total = sum(numeric_weights.values())
    assert abs(total - 1.0) < 1e-6, f"Weights sum to {total}"
    assert abs(weights["SHY"] - 0.30) < 1e-6, f"SHY should be 30%, got {weights['SHY']:.1%}"

    risky_total = sum(weights[t] for t in tickers)
    assert abs(risky_total - 0.70) < 1e-6, f"Risky total should be 70%, got {risky_total:.1%}"

    print(format_portfolio(weights))
    print("  PASS: portfolio_stressed_regime")


def test_portfolio_crisis_regime():
    """CRISIS regime -> 50% risky / 30% SHY / 20% ANGL."""
    tickers = ["SPY", "EFA", "EEM", "VNQ", "DBC", "GLD"]
    prices = make_synthetic_prices(tickers + ["SHY", "BIL", "ANGL"])
    config = MomentumConfig()

    mom_signal = compute_momentum_signal(
        prices, pd.Timestamp("2025-06-30"), config=config
    )

    gem = {
        "gem_signal": "SPY",
        "absolute_pass": True,
        "spy_12m_return": 0.10,
        "efa_12m_return": 0.06,
        "bil_12m_return": 0.04,
        "relative_winner": "SPY",
    }
    hy = {"regime": "CRISIS", "fast_widen_override": False}

    weights = construct_portfolio(gem, hy, mom_signal)

    numeric_weights = {k: v for k, v in weights.items() if k != "_metadata"}
    total = sum(numeric_weights.values())
    assert abs(total - 1.0) < 1e-6, f"Weights sum to {total}"
    assert abs(weights["SHY"] - 0.30) < 1e-6, f"SHY should be 30%, got {weights['SHY']:.1%}"
    assert abs(weights["ANGL"] - 0.20) < 1e-6, f"ANGL should be 20%, got {weights['ANGL']:.1%}"

    risky_total = sum(weights[t] for t in tickers)
    assert abs(risky_total - 0.50) < 1e-6, f"Risky total should be 50%, got {risky_total:.1%}"

    print(format_portfolio(weights))
    print("  PASS: portfolio_crisis_regime")


def test_portfolio_widening_fast():
    """WIDENING_FAST override -> 100% SHY regardless of everything else."""
    tickers = ["SPY", "EFA", "EEM", "VNQ", "DBC", "GLD"]
    prices = make_synthetic_prices(tickers + ["SHY", "BIL", "ANGL"])
    config = MomentumConfig()

    mom_signal = compute_momentum_signal(
        prices, pd.Timestamp("2025-06-30"), config=config
    )

    gem = {
        "gem_signal": "SPY",
        "absolute_pass": True,
        "spy_12m_return": 0.12,
        "efa_12m_return": 0.08,
        "bil_12m_return": 0.04,
        "relative_winner": "SPY",
    }
    hy = {"regime": "STRESSED", "fast_widen_override": True}

    weights = construct_portfolio(gem, hy, mom_signal)
    assert weights["SHY"] == 1.0, f"SHY should be 1.0, got {weights['SHY']}"
    print("  PASS: portfolio_widening_fast -> 100% SHY")


def test_missing_ticker():
    """Gracefully handle missing ticker in price data."""
    # Only provide 3 of 6 risky tickers
    tickers = ["SPY", "EFA", "EEM"]
    prices = make_synthetic_prices(tickers)
    config = MomentumConfig()  # expects 6 tickers

    signal = compute_momentum_signal(
        prices, pd.Timestamp("2025-06-30"), config=config
    )

    assert signal.available_assets == 3
    # VNQ, DBC, GLD should be EXCLUDED
    assert signal.terciles.get("VNQ") == "EXCLUDED"
    assert signal.terciles.get("DBC") == "EXCLUDED"
    assert signal.terciles.get("GLD") == "EXCLUDED"
    # Weights should still sum to 1.0 among available assets
    total = sum(signal.risky_weights.values())
    assert abs(total - 1.0) < 1e-6 or total == 0.0
    print("  PASS: missing_ticker handled gracefully")


# ──────────────────────────────────────────────
# Run all tests
# ──────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Running momentum_rank + portfolio_v2 test suite")
    print("=" * 60)

    tests = [
        test_tercile_sizes,
        test_momentum_computation,
        test_vol_computation,
        test_vol_adjustment,
        test_ranking,
        test_tercile_assignment,
        test_hysteresis_prevents_small_demotion,
        test_hysteresis_allows_large_demotion,
        test_weight_normalization,
        test_full_pipeline,
        test_portfolio_gem_fail,
        test_portfolio_normal_regime,
        test_portfolio_stressed_regime,
        test_portfolio_crisis_regime,
        test_portfolio_widening_fast,
        test_missing_ticker,
    ]

    passed = 0
    failed = 0

    for test_fn in tests:
        try:
            print(f"\n--- {test_fn.__name__} ---")
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 60)
