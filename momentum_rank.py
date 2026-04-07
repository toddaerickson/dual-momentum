"""
Cross-Asset 12-1 Month Momentum Ranking.

Ranks risky ETFs by trailing 12-month return (skipping the most recent month)
to capture intermediate-term momentum while avoiding short-term reversal.
Optionally adjusts for trailing volatility to prevent high-vol assets from
dominating the ranking.

References:
Blitz, D. & van Vliet, P. (2008). "Global Tactical Cross-Asset Allocation:
Applying Value and Momentum Across Asset Classes." Journal of Portfolio
Management, 35(1), 23-38.
Asness, C., Moskowitz, T. & Pedersen, L. (2013). "Value and Momentum
Everywhere." Journal of Finance, 68(3), 929-985.
Faber, M. (2007). "A Quantitative Approach to Tactical Asset Allocation."
Journal of Wealth Management, 9(4), 9-79.

Design decisions:
- 12-1 month lookback: academic standard. Skipping the most recent month
  avoids short-term reversal documented in Jegadeesh & Titman (1993).
- Vol-adjustment: normalizes all assets to 10% target vol before ranking,
  per Blitz & van Vliet robustness test (Exhibit 10). Prevents commodities
  (~20% vol) and gold (~16% vol) from dominating over REITs (~14% vol).
- Tercile assignment: with 6 risky assets, terciles = top 2, mid 2, bottom 2.
  This concentrates in winners per the Q1-Q4 spread evidence (IR 1.19).
  Blitz & van Vliet used quartiles on 12 assets; terciles on 6 is the
  natural analog.
- Hysteresis: tolerates 1-rank slippage without triggering rebalance,
  per Blitz & van Vliet footnote xiii. Reduces turnover ~30-40%.
- No in-sample weight optimization: top/mid split is 30%/20% per asset
  from structural reasoning, not fitted.
- GLD added for its near-zero long-run correlation to equities and
  documented momentum effect (Asness-Moskowitz-Pedersen, commodities
  universe). Gold also provides crisis-hedge exposure distinct from
  SHY's duration hedge and ANGL's credit recovery thesis.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Configuration dataclass
# ──────────────────────────────────────────────

@dataclass
class MomentumConfig:
    """All tunable parameters for the momentum ranking module."""

    # Universe: risky assets that participate in momentum ranking.
    # ANGL excluded — structural crisis-only allocation, not momentum-ranked.
    # GLD included — near-zero correlation to equities, documented momentum
    # in commodities (Asness-Moskowitz-Pedersen), distinct crisis-hedge factor.
    risky_tickers: List[str] = field(
        default_factory=lambda: ["SPY", "EFA", "EEM", "VNQ", "DBC", "GLD"]
    )

    # Lookback
    lookback_months: int = 12       # total lookback window
    skip_months: int = 1            # skip most recent N months (reversal avoidance)

    # Volatility adjustment
    use_vol_adjustment: bool = True
    vol_lookback_months: int = 60   # trailing window for vol estimate
    vol_min_months: int = 12        # absolute minimum for vol computation
    vol_warn_months: int = 36       # warn if fewer than this
    vol_target: float = 0.10        # annualized target vol for normalization

    # Allocation weights within risky budget
    # For 6 assets: terciles = (2, 2, 2)
    #   Top 2 get 30% each (60%), Mid 2 get 20% each (40%), Bottom 2 get 0%
    # Weights within risky budget must sum to 1.0:
    #   2*0.30 + 2*0.20 + 2*0.00 = 1.00
    top_weight: float = 0.30        # per asset in top tercile
    mid_weight: float = 0.20        # per asset in mid tercile
    bot_weight: float = 0.00        # per asset in bottom tercile

    # Hysteresis: tolerate this many rank positions of slippage
    # before demoting an asset to a lower tercile.
    rank_hysteresis: int = 1

    def validate(self):
        """Validate configuration consistency."""
        n = len(self.risky_tickers)
        top_n, mid_n, bot_n = _tercile_sizes(n)
        total = top_n * self.top_weight + mid_n * self.mid_weight + bot_n * self.bot_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Risky weights do not sum to 1.0: "
                f"{top_n}x{self.top_weight} + {mid_n}x{self.mid_weight} + "
                f"{bot_n}x{self.bot_weight} = {total:.4f}. "
                f"Adjust top_weight/mid_weight/bot_weight for {n} assets."
            )
        if self.skip_months >= self.lookback_months:
            raise ValueError(
                f"skip_months ({self.skip_months}) must be < "
                f"lookback_months ({self.lookback_months})"
            )


# ──────────────────────────────────────────────
# Tercile sizing
# ──────────────────────────────────────────────

def _tercile_sizes(n: int) -> Tuple[int, int, int]:
    """
    Compute (top_n, mid_n, bot_n) for a universe of n assets.

    Design:
        n=5: top=2, mid=1, bot=2
        n=6: top=2, mid=2, bot=2
        n=4: top=2, mid=1, bot=1
        n=3: top=1, mid=1, bot=1

    Final decision: hardcode for expected sizes, formula for others.
    """
    if n == 5:
        return (2, 1, 2)
    elif n == 6:
        return (2, 2, 2)
    elif n == 4:
        return (2, 1, 1)
    elif n == 3:
        return (1, 1, 1)
    else:
        # General: top = n//3, bottom = n//3, mid = n - 2*(n//3)
        t = n // 3
        b = n // 3
        m = n - t - b
        if t == 0:
            t = 1
            m = max(0, n - t - b)
        return (t, m, b)


# ──────────────────────────────────────────────
# Core computations
# ──────────────────────────────────────────────

def compute_12_1_momentum(
    prices_df: pd.DataFrame,
    as_of_date: pd.Timestamp,
    lookback_months: int = 12,
    skip_months: int = 1,
) -> Dict[str, float]:
    """
    Compute 12-1 month momentum for each ticker.

    Momentum = price[t - skip_months] / price[t - lookback_months] - 1.0

    Uses adjusted close prices (dividends reinvested). Finds the nearest
    available trading day for each target date.

    Args:
        prices_df: DateTimeIndex, columns = tickers, values = adjusted close.
        as_of_date: computation date (must be in prices_df index or earlier).
        lookback_months: total lookback (default 12).
        skip_months: months to skip at the recent end (default 1).

    Returns:
        {ticker: momentum_value} -- NaN if insufficient data.
    """
    result = {}

    # Target dates
    end_target = as_of_date - pd.DateOffset(months=skip_months)
    start_target = as_of_date - pd.DateOffset(months=lookback_months)

    for ticker in prices_df.columns:
        series = prices_df[ticker].dropna()

        if series.empty:
            result[ticker] = np.nan
            continue

        # Filter to data available as of as_of_date (no look-ahead)
        series = series[series.index <= as_of_date]

        # Find nearest trading day on or before each target
        end_price = _get_price_on_or_before(series, end_target)
        start_price = _get_price_on_or_before(series, start_target)

        if end_price is None or start_price is None or start_price <= 0:
            result[ticker] = np.nan
            logger.warning(
                f"{ticker}: insufficient data for momentum as of {as_of_date.date()}. "
                f"start_target={start_target.date()}, end_target={end_target.date()}"
            )
        else:
            result[ticker] = (end_price / start_price) - 1.0

    return result


def compute_trailing_vol(
    prices_df: pd.DataFrame,
    as_of_date: pd.Timestamp,
    lookback_months: int = 60,
    min_months: int = 12,
    warn_months: int = 36,
) -> Dict[str, float]:
    """
    Annualized volatility of monthly returns over trailing window.

    Args:
        prices_df: DateTimeIndex, columns = tickers, adjusted close.
        as_of_date: computation date.
        lookback_months: ideal lookback (default 60).
        min_months: absolute minimum months required (default 12).
        warn_months: log warning if fewer than this (default 36).

    Returns:
        {ticker: annualized_vol} -- NaN if < min_months of data.
    """
    result = {}
    start_target = as_of_date - pd.DateOffset(months=lookback_months)

    for ticker in prices_df.columns:
        series = prices_df[ticker].dropna()
        series = series[series.index <= as_of_date]

        if series.empty:
            result[ticker] = np.nan
            continue

        # Clip to lookback window
        series_window = series[series.index >= start_target]

        # Resample to month-end, take last price
        monthly = series_window.resample("ME").last().dropna()

        n_months = len(monthly)
        if n_months < min_months + 1:  # need min_months returns = min_months+1 prices
            result[ticker] = np.nan
            logger.warning(
                f"{ticker}: only {n_months - 1} monthly returns available "
                f"(need {min_months}). Vol set to NaN."
            )
            continue

        if n_months < warn_months + 1:
            logger.info(
                f"{ticker}: {n_months - 1} monthly returns for vol "
                f"(< {warn_months} ideal). Using available data."
            )

        # Monthly log returns -> annualized vol
        monthly_returns = np.log(monthly / monthly.shift(1)).dropna()
        annualized = monthly_returns.std() * np.sqrt(12)

        result[ticker] = annualized

    return result


def vol_adjusted_momentum(
    momentum_dict: Dict[str, float],
    vol_dict: Dict[str, float],
    target_vol: float = 0.10,
) -> Dict[str, float]:
    """
    Scale each asset's momentum by (target_vol / trailing_vol).

    If vol is NaN or zero, the vol-adjusted momentum is NaN (asset excluded
    from ranking for that month).

    Returns:
        {ticker: vol_adjusted_momentum}
    """
    result = {}
    for ticker, mom in momentum_dict.items():
        vol = vol_dict.get(ticker, np.nan)
        if np.isnan(mom) or np.isnan(vol) or vol <= 0:
            result[ticker] = np.nan
        else:
            result[ticker] = mom * (target_vol / vol)
    return result


# ──────────────────────────────────────────────
# Ranking and tercile assignment
# ──────────────────────────────────────────────

def rank_assets(score_dict: Dict[str, float]) -> Dict[str, int]:
    """
    Rank assets by score descending (highest momentum = rank 1).
    NaN scores are excluded and assigned rank = NaN.
    Ties are broken by alphabetical ticker order (arbitrary but deterministic).

    Returns:
        {ticker: rank} -- 1-indexed. NaN if score was NaN.
    """
    # Separate valid and invalid
    valid = {k: v for k, v in score_dict.items() if not np.isnan(v)}
    invalid = {k: v for k, v in score_dict.items() if np.isnan(v)}

    # Sort descending by score, then ascending by ticker for ties
    sorted_tickers = sorted(valid.keys(), key=lambda t: (-valid[t], t))

    result = {}
    for i, ticker in enumerate(sorted_tickers):
        result[ticker] = i + 1

    for ticker in invalid:
        result[ticker] = np.nan

    return result


def assign_terciles(
    rank_dict: Dict[str, int],
    risky_tickers: List[str],
) -> Dict[str, str]:
    """
    Assign TOP / MID / BOTTOM based on rank among available (non-NaN) assets.

    Assets with NaN rank are assigned "EXCLUDED".

    Returns:
        {ticker: "TOP" | "MID" | "BOTTOM" | "EXCLUDED"}
    """
    # Count available assets
    available = [t for t in risky_tickers if not np.isnan(rank_dict.get(t, np.nan))]
    n = len(available)

    if n == 0:
        return {t: "EXCLUDED" for t in risky_tickers}

    top_n, mid_n, bot_n = _tercile_sizes(n)

    result = {}
    for ticker in risky_tickers:
        r = rank_dict.get(ticker, np.nan)
        if np.isnan(r):
            result[ticker] = "EXCLUDED"
        elif r <= top_n:
            result[ticker] = "TOP"
        elif r <= top_n + mid_n:
            result[ticker] = "MID"
        else:
            result[ticker] = "BOTTOM"

    return result


def apply_hysteresis(
    current_terciles: Dict[str, str],
    prior_terciles: Optional[Dict[str, str]],
    current_ranks: Dict[str, int],
    prior_ranks: Optional[Dict[str, int]],
    hysteresis: int = 1,
) -> Dict[str, str]:
    """
    Prevent rebalancing when rank changes by <= hysteresis.

    Rules:
        - If asset was TOP and is now MID, but rank changed by only
          `hysteresis` positions: keep as TOP.
        - If asset was MID and is now BOTTOM, but rank changed by only
          `hysteresis` positions: keep as MID.
        - Promotions (BOTTOM->MID, MID->TOP) always apply immediately.
        - Demotions of more than `hysteresis` ranks always apply.
        - EXCLUDED assets always apply current assignment.

    Args:
        current_terciles: this month's raw tercile assignments.
        prior_terciles: last month's (possibly hysteresis-adjusted) terciles.
            If None (first month), return current_terciles unchanged.
        current_ranks: this month's ranks.
        prior_ranks: last month's ranks. If None, return current unchanged.
        hysteresis: max rank slippage to tolerate.

    Returns:
        {ticker: adjusted_tercile}
    """
    if prior_terciles is None or prior_ranks is None:
        return dict(current_terciles)

    TERCILE_ORDER = {"TOP": 0, "MID": 1, "BOTTOM": 2, "EXCLUDED": 3}

    adjusted = {}
    for ticker, cur_terc in current_terciles.items():
        prior_terc = prior_terciles.get(ticker, "EXCLUDED")
        cur_rank = current_ranks.get(ticker, np.nan)
        prior_rank = prior_ranks.get(ticker, np.nan)

        # If either rank is NaN, no hysteresis possible
        if np.isnan(cur_rank) or np.isnan(prior_rank):
            adjusted[ticker] = cur_terc
            continue

        cur_order = TERCILE_ORDER.get(cur_terc, 3)
        prior_order = TERCILE_ORDER.get(prior_terc, 3)

        # Promotion (lower order number = higher tercile): always apply
        if cur_order < prior_order:
            adjusted[ticker] = cur_terc
        # Same tercile: no change needed
        elif cur_order == prior_order:
            adjusted[ticker] = cur_terc
        # Demotion: check rank change magnitude
        else:
            rank_change = cur_rank - prior_rank  # positive = worsening
            if rank_change <= hysteresis:
                # Small slippage -- keep prior tercile
                adjusted[ticker] = prior_terc
                logger.debug(
                    f"{ticker}: rank {prior_rank}->{cur_rank} "
                    f"(delta={rank_change} <= hysteresis={hysteresis}), "
                    f"keeping {prior_terc} instead of {cur_terc}"
                )
            else:
                adjusted[ticker] = cur_terc

    return adjusted


# ──────────────────────────────────────────────
# Weight computation
# ──────────────────────────────────────────────

def compute_risky_weights(
    terciles: Dict[str, str],
    config: MomentumConfig,
) -> Dict[str, float]:
    """
    Convert tercile assignments to portfolio weights within the risky budget.

    Weights are normalized to sum to 1.0 across all ranked assets (excluding
    EXCLUDED assets). If normalization changes weights significantly from
    the configured defaults, log a warning.

    Returns:
        {ticker: weight_within_risky_budget}
        Weights for EXCLUDED and BOTTOM assets = 0.0.
    """
    raw_weights = {}
    for ticker, terc in terciles.items():
        if terc == "TOP":
            raw_weights[ticker] = config.top_weight
        elif terc == "MID":
            raw_weights[ticker] = config.mid_weight
        elif terc == "BOTTOM":
            raw_weights[ticker] = config.bot_weight
        else:  # EXCLUDED
            raw_weights[ticker] = 0.0

    total = sum(raw_weights.values())

    if total <= 0:
        # All assets excluded or zero-weighted -- degenerate case
        logger.warning("All risky assets have zero weight. Returning zeros.")
        return {t: 0.0 for t in terciles}

    # Normalize
    normalized = {t: w / total for t, w in raw_weights.items()}

    # Check if normalization distorted weights significantly
    if abs(total - 1.0) > 0.05:
        logger.info(
            f"Risky weights required normalization: raw sum={total:.3f}. "
            f"This may indicate fewer assets than expected are available."
        )

    return normalized


# ──────────────────────────────────────────────
# Main pipeline
# ──────────────────────────────────────────────

@dataclass
class MomentumSignal:
    """Output of the momentum ranking pipeline for one date."""
    date: pd.Timestamp
    raw_momentum: Dict[str, float]
    vol_adjusted_momentum: Dict[str, float]
    trailing_vol: Dict[str, float]
    ranks: Dict[str, int]
    raw_terciles: Dict[str, str]
    terciles: Dict[str, str]           # after hysteresis
    risky_weights: Dict[str, float]    # within risky budget, sums to 1.0
    available_assets: int
    ranking_metric: str                # "vol_adjusted" or "raw"


def compute_momentum_signal(
    prices_df: pd.DataFrame,
    as_of_date: pd.Timestamp,
    prior_signal: Optional[MomentumSignal] = None,
    config: Optional[MomentumConfig] = None,
) -> MomentumSignal:
    """
    Full pipeline: momentum -> vol-adjust -> rank -> tercile -> hysteresis -> weights.

    Args:
        prices_df: adjusted close prices, DateTimeIndex, columns = tickers.
            Must contain at least the tickers in config.risky_tickers.
        as_of_date: computation date.
        prior_signal: previous month's MomentumSignal (for hysteresis).
            None for the first month.
        config: MomentumConfig instance. Uses defaults if None.

    Returns:
        MomentumSignal with all intermediate computations for diagnostics.
    """
    if config is None:
        config = MomentumConfig()

    # Validate config on first call
    config.validate()

    # Ensure as_of_date is Timestamp
    as_of_date = pd.Timestamp(as_of_date)

    # Filter prices to risky universe only
    available_tickers = [t for t in config.risky_tickers if t in prices_df.columns]
    missing = set(config.risky_tickers) - set(available_tickers)
    if missing:
        logger.warning(f"Tickers not in price data: {missing}")

    prices_subset = prices_df[available_tickers]

    # Step 1: Raw 12-1 momentum
    raw_mom = compute_12_1_momentum(
        prices_subset, as_of_date,
        lookback_months=config.lookback_months,
        skip_months=config.skip_months,
    )

    # Step 2: Trailing volatility
    trail_vol = compute_trailing_vol(
        prices_subset, as_of_date,
        lookback_months=config.vol_lookback_months,
        min_months=config.vol_min_months,
        warn_months=config.vol_warn_months,
    )

    # Step 3: Vol-adjusted momentum
    vol_adj_mom = vol_adjusted_momentum(raw_mom, trail_vol, config.vol_target)

    # Step 4: Choose ranking metric
    if config.use_vol_adjustment:
        ranking_scores = vol_adj_mom
        ranking_metric = "vol_adjusted"
    else:
        ranking_scores = raw_mom
        ranking_metric = "raw"

    # Step 5: Rank
    ranks = rank_assets(ranking_scores)

    # Step 6: Assign terciles
    raw_terciles = assign_terciles(ranks, config.risky_tickers)

    # Step 7: Hysteresis
    prior_terciles = prior_signal.terciles if prior_signal else None
    prior_ranks = prior_signal.ranks if prior_signal else None

    final_terciles = apply_hysteresis(
        raw_terciles, prior_terciles,
        ranks, prior_ranks,
        hysteresis=config.rank_hysteresis,
    )

    # Step 8: Compute weights within risky budget
    risky_weights = compute_risky_weights(final_terciles, config)

    # Count available (non-NaN) assets
    n_available = sum(1 for t in config.risky_tickers if not np.isnan(ranks.get(t, np.nan)))

    return MomentumSignal(
        date=as_of_date,
        raw_momentum=raw_mom,
        vol_adjusted_momentum=vol_adj_mom,
        trailing_vol=trail_vol,
        ranks=ranks,
        raw_terciles=raw_terciles,
        terciles=final_terciles,
        risky_weights=risky_weights,
        available_assets=n_available,
        ranking_metric=ranking_metric,
    )


# ──────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────

def _get_price_on_or_before(
    series: pd.Series,
    target_date: pd.Timestamp,
    max_lookback_days: int = 10,
) -> Optional[float]:
    """
    Get the price on or before target_date.
    Searches up to max_lookback_days before target for a valid price.

    Returns None if no price found in window.
    """
    target_date = pd.Timestamp(target_date)
    mask = series.index <= target_date
    candidates = series[mask]

    if candidates.empty:
        return None

    last_date = candidates.index[-1]
    if (target_date - last_date).days > max_lookback_days:
        return None

    return float(candidates.iloc[-1])


# ──────────────────────────────────────────────
# Formatting for console / logging
# ──────────────────────────────────────────────

def format_momentum_signal(signal: MomentumSignal) -> str:
    """Format momentum signal for console output."""
    lines = [
        f"=== Cross-Asset Momentum Ranking: {signal.date.date()} ===",
        f"Metric: {signal.ranking_metric} | Available assets: {signal.available_assets}",
        "",
        f"{'Ticker':<8} {'Raw Mom':>10} {'Vol':>10} {'VolAdj Mom':>12} {'Rank':>6} {'Tercile':>10} {'Weight':>8}",
        "-" * 70,
    ]

    for ticker in sorted(signal.ranks.keys(), key=lambda t: signal.ranks.get(t, 99)):
        raw = signal.raw_momentum.get(ticker, np.nan)
        vol = signal.trailing_vol.get(ticker, np.nan)
        vadj = signal.vol_adjusted_momentum.get(ticker, np.nan)
        rank = signal.ranks.get(ticker, np.nan)
        terc = signal.terciles.get(ticker, "N/A")
        wt = signal.risky_weights.get(ticker, 0.0)

        raw_s = f"{raw:+.2%}" if not np.isnan(raw) else "N/A"
        vol_s = f"{vol:.2%}" if not np.isnan(vol) else "N/A"
        vadj_s = f"{vadj:+.4f}" if not np.isnan(vadj) else "N/A"
        rank_s = f"{int(rank)}" if not np.isnan(rank) else "N/A"
        wt_s = f"{wt:.1%}"

        lines.append(
            f"{ticker:<8} {raw_s:>10} {vol_s:>10} {vadj_s:>12} {rank_s:>6} {terc:>10} {wt_s:>8}"
        )

    lines.append("=" * 70)
    return "\n".join(lines)
