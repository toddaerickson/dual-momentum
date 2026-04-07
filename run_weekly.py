"""
Weekly Summary Email: Current signals + plain English interpretation.

Run via Task Scheduler or cron:
  Every Sunday at 6pm:
    python run_weekly.py

Or manually:
    python run_weekly.py
"""

import sys
from datetime import datetime

import config
import data as data_mod
import signals as signals_mod
import portfolio as portfolio_mod
import portfolio_v2
import momentum_rank
import notifications


def build_summary(gem: dict, hy: dict, yc: dict, target: dict, as_of_str: str,
                   momentum_signal=None) -> str:
    """Build the plain English summary (mirrors dashboard logic)."""
    abs_pass = gem.get("absolute_pass", False)
    regime = hy.get("regime", "N/A")
    regime_primary = hy.get("regime_primary", "N/A")
    ccc_bb = hy.get("ccc_bb_spread_current", 0) or 0
    ccc_bb_pctl = hy.get("ccc_bb_percentile", 0) or 0
    b_oas = hy.get("hy_b_current", 0) or 0
    b_pctl = hy.get("hy_b_percentile", 0) or 0
    b_change = hy.get("hy_b_change_3m", 0) or 0
    spread = hy.get("hy_spread_current", 0) or 0
    override = hy.get("fast_widen_override", False)
    spy_ret = gem.get("spy_12m_return", 0)
    efa_ret = gem.get("efa_12m_return", 0) or 0
    bil_ret = gem.get("bil_12m_return", 0)
    abs_pass = gem.get("absolute_pass", False)

    # Absolute momentum explanation
    best_equity = max(spy_ret, efa_ret)
    if abs_pass:
        gem_english = (
            f"Equities cleared the T-bill hurdle: SPY returned {spy_ret:+.1%}, "
            f"EFA returned {efa_ret:+.1%}, both vs T-bills at {bil_ret:+.1%}. "
            "Absolute momentum gate passes — proceed to cross-asset ranking."
        )
    else:
        gem_english = (
            f"Neither US stocks ({spy_ret:+.1%}) nor international stocks ({efa_ret:+.1%}) "
            f"beat T-bills ({bil_ret:+.1%}) over the past 12 months. "
            "Absolute momentum gate fails — 100% short-term Treasuries."
        )

    # Regime explanation (dual-signal)
    regime_descriptions = {
        "TIGHT": (
            f"CCC-BB spread at {ccc_bb:.0f} bps ({ccc_bb_pctl:.0f}th percentile), "
            f"Single-B OAS at {b_oas:.0f} bps ({b_pctl:.0f}th pctl). No signs of stress."
        ),
        "NORMAL": (
            f"CCC-BB spread at {ccc_bb:.0f} bps ({ccc_bb_pctl:.0f}th percentile), "
            f"Single-B OAS at {b_oas:.0f} bps ({b_pctl:.0f}th pctl). Typical risk pricing."
        ),
        "STRESSED": (
            f"CCC-BB spread at {ccc_bb:.0f} bps ({ccc_bb_pctl:.0f}th percentile), "
            f"Single-B OAS at {b_oas:.0f} bps ({b_pctl:.0f}th pctl). Elevated stress — model reduces equity."
        ),
        "CRISIS": (
            f"CCC-BB spread at {ccc_bb:.0f} bps ({ccc_bb_pctl:.0f}th percentile), "
            f"Single-B OAS at {b_oas:.0f} bps ({b_pctl:.0f}th pctl). Crisis-level stress."
        ),
    }
    regime_english = regime_descriptions.get(regime, f"HY regime: {regime}")

    # Rate of change (keyed off Single-B OAS)
    if override:
        roc_english = (
            f"Single-B OAS widened {b_change:+.0f} bps in 3 months, triggering the "
            "WIDENING_FAST override. The model forces 100% Treasuries."
        )
    elif b_change > 50:
        roc_english = f"Single-B OAS widened {b_change:+.0f} bps over 3 months (WIDENING). Override triggers at +100 bps."
    elif b_change > 0:
        roc_english = f"Single-B OAS widened {b_change:+.0f} bps over 3 months. Override triggers at +100 bps."
    elif b_change > -50:
        roc_english = f"Single-B OAS moved {b_change:+.0f} bps over 3 months."
    else:
        roc_english = f"Single-B OAS tightened {b_change:+.0f} bps over 3 months (TIGHTENING)."

    # Conviction
    abs_margin = max(spy_ret, efa_ret) - bil_ret
    conviction_parts = []

    if abs_pass:
        if abs_margin > 0.10:
            conviction_parts.append("Equities well ahead of T-bills — strong case to stay invested.")
        elif abs_margin > 0.03:
            conviction_parts.append("Equities ahead of T-bills but the margin is moderate.")
        else:
            conviction_parts.append("Equities barely beat T-bills — a small move could push the model to defensive.")
    else:
        if abs_margin < -0.05:
            conviction_parts.append("Equities significantly underperforming T-bills — clear risk-off signal.")
        else:
            conviction_parts.append("Equities just barely missed the T-bill hurdle — could flip back next month.")

    if regime in ("TIGHT", "NORMAL") and not override:
        conviction_parts.append("Credit markets confirm no stress.")
    elif regime == "STRESSED":
        conviction_parts.append("Credit stress adds caution — the model hedges even if momentum is positive.")
    elif regime == "CRISIS":
        conviction_parts.append("Crisis-level credit stress — the model is heavily defensive.")

    conviction_english = " ".join(conviction_parts)

    # Momentum ranking summary
    import numpy as _np
    mom_lines = []
    if momentum_signal is not None:
        mom_lines.append("MOMENTUM RANKING (Stage 2)")
        mom_lines.append(f"Metric: {momentum_signal.ranking_metric} | Assets: {momentum_signal.available_assets}")
        for ticker in sorted(momentum_signal.ranks.keys(), key=lambda t: momentum_signal.ranks.get(t, 99)):
            raw = momentum_signal.raw_momentum.get(ticker, float('nan'))
            raw_s = f"{raw:+.1%}" if not _np.isnan(raw) else "N/A"
            terc = momentum_signal.terciles.get(ticker, "N/A")
            rw = momentum_signal.risky_weights.get(ticker, 0.0)
            mom_lines.append(f"  {ticker:<6} {terc:<8} wt={rw:.0%}  (12-1M: {raw_s})")
        mom_lines.append("")

    # Target allocation
    ticker_names = {
        "SPY": "US stocks", "EFA": "international stocks", "EEM": "emerging markets",
        "VNQ": "REITs", "DBC": "commodities", "GLD": "gold",
        "SHY": "short-term Treasuries", "ANGL": "fallen angel bonds",
    }
    alloc_parts = []
    for ticker in portfolio_v2.ALL_TICKERS:
        w = target.get(ticker, 0)
        if w > 0.001:
            alloc_parts.append(f"{w:.0%} {ticker}")
    alloc_str = " / ".join(alloc_parts)

    alloc_plain = ", ".join(
        f"{target[t]:.0%} in {ticker_names.get(t, t)}"
        for t in portfolio_v2.ALL_TICKERS if target.get(t, 0) > 0.001
    )

    # Yield curve
    yc_spread = yc.get("t10y2y", "N/A")
    yc_status = yc.get("status", "N/A")

    # Assemble email
    lines = [
        f"THREE-STAGE TAA WEEKLY SUMMARY",
        f"Signals as of {as_of_str}",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "=" * 50,
        "",
        "STAGE 1: ABSOLUTE MOMENTUM GATE",
        gem_english,
        "",
        f"  SPY 12M return:  {spy_ret:+.1%}",
        f"  EFA 12M return:  {efa_ret:+.1%}",
        f"  BIL 12M return:  {bil_ret:+.1%}",
        f"  Absolute pass:   {'YES' if abs_pass else 'NO'}",
        "",
    ]
    lines.extend(mom_lines)
    lines.extend([
        "STAGE 3: CREDIT CONDITIONS (HY REGIME)",
        regime_english,
        "",
        f"  CCC-BB spread:     {ccc_bb:.0f} bps (pctl: {ccc_bb_pctl:.0f})",
        f"  Single-B OAS:      {b_oas:.0f} bps (pctl: {b_pctl:.0f})",
        f"  Single-B 3M chg:   {b_change:+.0f} bps",
        f"  Regime:            {regime} (primary: {regime_primary})",
        f"  Override:          {'ACTIVE' if override else 'Inactive'}",
        "",
        "SPREAD VELOCITY",
        roc_english,
        "",
        "CONVICTION",
        conviction_english,
        "",
        f"YIELD CURVE: {yc_spread}% ({yc_status}) — monitoring only",
        "",
        "=" * 50,
        "",
        f"TARGET PORTFOLIO: {alloc_str}",
        f"That's {alloc_plain}.",
        "",
        "Rebalance on the last business day of the month.",
        "Between rebalances, do nothing.",
    ])

    return "\n".join(lines)


def run():
    """Fetch data, compute signals, send weekly email."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Weekly summary: {timestamp}")

    # Fetch data
    try:
        all_data = data_mod.fetch_all()
        prices = all_data["prices"]
        hy_spread = all_data["hy_spread"]
        ccc_bb_spread = all_data["ccc_bb_spread"]
        hy_b_spread = all_data["hy_b_spread"]
    except Exception as e:
        print(f"ERROR: Data fetch failed: {e}")
        sys.exit(1)

    # Compute signals (all three stages)
    as_of = prices.index[-1]
    as_of_str = as_of.strftime("%Y-%m-%d")

    all_signals = signals_mod.compute_all_signals(
        prices, hy_spread, as_of,
        ccc_bb_spread=ccc_bb_spread, hy_b_spread=hy_b_spread,
    )
    gem = all_signals["gem"]
    hy = all_signals["hy_regime"]
    mom = all_signals["momentum"]
    yc = all_signals["yield_curve"]

    target_v2 = portfolio_mod.construct_portfolio(gem, hy, mom)
    target = {k: v for k, v in target_v2.items() if k != "_metadata" and isinstance(v, (int, float))}

    # Build summary
    body = build_summary(gem, hy, yc, target, as_of_str, momentum_signal=mom)

    # Target allocation for subject line
    alloc_parts = []
    for ticker in portfolio_v2.ALL_TICKERS:
        w = target.get(ticker, 0)
        if w > 0.001:
            alloc_parts.append(f"{ticker} {w:.0%}")
    alloc_str = " / ".join(alloc_parts)

    abs_label = "PASS" if gem.get("absolute_pass") else "FAIL"
    subject = f"DM Weekly: AbsMom {abs_label} / {hy.get('regime', '?')} — {alloc_str}"

    # Send
    print(f"\n{body}\n")
    print("Sending email...")
    result = notifications.send_email(subject, body)
    if result:
        print("Email sent.")
    else:
        print("Email failed. Check SMTP configuration.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(run())
