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
import notifications


def build_summary(gem: dict, hy: dict, yc: dict, target: dict, as_of_str: str) -> str:
    """Build the plain English summary (mirrors dashboard logic)."""
    gem_signal = gem.get("gem_signal", "N/A")
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

    # GEM explanation
    if gem_signal == "SPY":
        gem_english = (
            f"US stocks (SPY) returned {spy_ret:+.1%} over the past 12 months, "
            f"beating international stocks (EFA) at {efa_ret:+.1%} and clearing "
            f"the T-bill hurdle of {bil_ret:+.1%}. "
            "Momentum favors staying in US equities."
        )
    elif gem_signal == "EFA":
        gem_english = (
            f"International stocks (EFA) returned {efa_ret:+.1%} over the past 12 months, "
            f"beating US stocks (SPY) at {spy_ret:+.1%} and clearing "
            f"the T-bill hurdle of {bil_ret:+.1%}. "
            "Momentum favors international equities."
        )
    else:
        gem_english = (
            f"Neither US stocks ({spy_ret:+.1%}) nor international stocks ({efa_ret:+.1%}) "
            f"beat T-bills ({bil_ret:+.1%}) over the past 12 months. "
            "Momentum says step aside into short-term Treasuries."
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
    margin = abs(spy_ret - efa_ret)
    abs_margin = max(spy_ret, efa_ret) - bil_ret
    conviction_parts = []

    if gem_signal in ("SPY", "EFA"):
        if margin > 0.10:
            conviction_parts.append("Wide gap between US and international — high conviction in the equity pick.")
        elif margin > 0.03:
            conviction_parts.append("Moderate gap between US and international — reasonably clear winner.")
        else:
            conviction_parts.append("US and international returns are very close — the winner could flip next month.")

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

    # Target allocation
    ticker_names = {"SPY": "US stocks", "EFA": "international stocks", "SHY": "short-term Treasuries", "ANGL": "fallen angel bonds"}
    alloc_parts = []
    for ticker in ["SPY", "EFA", "SHY", "ANGL"]:
        w = target.get(ticker, 0)
        if w > 0:
            alloc_parts.append(f"{w:.0%} {ticker}")
    alloc_str = " / ".join(alloc_parts)

    alloc_plain = ", ".join(
        f"{target[t]:.0%} in {ticker_names.get(t, t)}"
        for t in ["SPY", "EFA", "SHY", "ANGL"] if target.get(t, 0) > 0
    )

    # Yield curve
    yc_spread = yc.get("t10y2y", "N/A")
    yc_status = yc.get("status", "N/A")

    # Assemble email
    lines = [
        f"DUAL MOMENTUM WEEKLY SUMMARY",
        f"Signals as of {as_of_str}",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "=" * 50,
        "",
        "MOMENTUM (GEM)",
        gem_english,
        "",
        f"  SPY 12M return:  {spy_ret:+.1%}",
        f"  EFA 12M return:  {efa_ret:+.1%}",
        f"  BIL 12M return:  {bil_ret:+.1%}",
        f"  Relative winner: {gem.get('relative_winner', 'N/A')}",
        f"  Absolute pass:   {'YES' if abs_pass else 'NO'}",
        f"  GEM signal:      {gem_signal}",
        "",
        "CREDIT CONDITIONS (HY REGIME)",
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
    ]

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

    # Compute signals
    as_of = prices.index[-1]
    as_of_str = as_of.strftime("%Y-%m-%d")

    all_signals = signals_mod.compute_all_signals(
        prices, hy_spread, as_of,
        ccc_bb_spread=ccc_bb_spread, hy_b_spread=hy_b_spread,
    )
    gem = all_signals["gem"]
    hy = all_signals["hy_regime"]
    yc = all_signals["yield_curve"]

    target = portfolio_mod.construct_portfolio(gem, hy)

    # Build summary
    body = build_summary(gem, hy, yc, target, as_of_str)

    # Target allocation for subject line
    alloc_parts = []
    for ticker in ["SPY", "EFA", "SHY", "ANGL"]:
        w = target.get(ticker, 0)
        if w > 0:
            alloc_parts.append(f"{ticker} {w:.0%}")
    alloc_str = " / ".join(alloc_parts)

    subject = f"DM Weekly: {gem.get('gem_signal', '?')} / {hy.get('regime', '?')} — {alloc_str}"

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
