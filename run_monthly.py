"""
Monthly Runner: Full rebalance cycle.

Computes signals, determines target allocation, logs trades,
generates markdown memo.

Run via cron:
  0 1 28 * * cd ~/dual-momentum && python run_monthly.py >> logs/monthly.log 2>&1

Or: run on every weekday and check if last business day of month.
"""

import calendar
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

import config
import data as data_mod
import signals as signals_mod
import portfolio as portfolio_mod
import state
import notifications


def is_last_business_day_of_month(date: datetime = None) -> bool:
    """Check if today (or given date) is the last business day of the month."""
    if date is None:
        date = datetime.now()

    year, month = date.year, date.month
    last_day = calendar.monthrange(year, month)[1]

    # Walk backward from last day to find last weekday
    for day in range(last_day, 0, -1):
        candidate = datetime(year, month, day)
        if candidate.weekday() < 5:  # Monday=0 ... Friday=4
            return date.date() == candidate.date()
    return False


def generate_memo(
    date_str: str,
    gem: dict,
    hy: dict,
    yc: dict,
    target_weights: dict,
    prior_weights: dict,
    trades: list,
) -> str:
    """Generate markdown memo for the monthly rebalance."""
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

    # Determine prior month values from state
    prior_gem = "—"
    prior_regime = "—"
    try:
        hist = state.get_signal_history(60)
        if len(hist) >= 2:
            prev = hist.iloc[-2]
            prior_gem = str(prev.get("gem_signal", "—"))
            prior_regime = str(prev.get("hy_regime", "—"))
    except Exception:
        pass

    gem_changed = "CHANGED" if gem_signal != prior_gem else "—"
    regime_changed = "CHANGED" if regime != prior_regime else "—"

    # Build memo
    lines = [
        f"# Monthly Portfolio Update: {date_str}",
        "",
        "## Signals",
        "",
        "| Signal | Value | Prior Month | Change |",
        "|---|---|---|---|",
        f"| GEM | {gem_signal} | {prior_gem} | {gem_changed} |",
        f"| CCC-BB Spread | {ccc_bb:.0f} bps (pctl: {ccc_bb_pctl:.0f}) | — | — |",
        f"| Single-B OAS | {b_oas:.0f} bps (pctl: {b_pctl:.0f}) | {hy.get('hy_b_3m_ago', '—')} bps (3mo ago) | {b_change:+.0f} bps |",
        f"| HY Regime | {regime} (primary: {regime_primary}) | {prior_regime} | {regime_changed} |",
        f"| Override | {'YES' if override else 'NO'} | — | — |",
        f"| Yield Curve | {yc.get('t10y2y', 'N/A')}% ({yc.get('status', 'N/A')}) | — | monitoring only |",
        "",
        "## GEM Detail",
        "",
        f"- SPY 12M return: {gem.get('spy_12m_return', 0):+.1%}",
        f"- EFA 12M return: {gem.get('efa_12m_return', 0):+.1%}",
        f"- BIL 12M return: {gem.get('bil_12m_return', 0):+.1%}",
        f"- Relative winner: {gem.get('relative_winner', 'N/A')}",
        f"- Absolute momentum pass: {'YES' if gem.get('absolute_pass') else 'NO'}",
        "",
        "## Target Allocation",
        "",
        "| ETF | Weight | Prior | Change |",
        "|---|---|---|---|",
    ]

    for ticker in config.ALL_TICKERS:
        tw = target_weights.get(ticker, 0)
        pw = prior_weights.get(ticker, 0)
        change = tw - pw
        if tw > 0 or pw > 0:
            change_str = f"{change:+.0%}" if abs(change) > 1e-6 else "—"
            lines.append(f"| {ticker} | {tw:.0%} | {pw:.0%} | {change_str} |")

    lines.extend(["", "## Trade List", ""])

    if trades:
        for t in trades:
            direction = "BUY" if t["change"] > 0 else "SELL"
            lines.append(f"- {direction} {abs(t['change']):.0%} {t['ticker']}")
    else:
        lines.append("- No trades required.")

    lines.extend([
        "",
        "## Decision Matrix State",
        "",
        f"- GEM signal: **{gem_signal}**",
        f"- HY regime: **{regime}** (primary: {regime_primary})",
        f"- CCC-BB spread: {ccc_bb:.0f} bps (percentile: {ccc_bb_pctl:.0f})",
        f"- Single-B OAS: {b_oas:.0f} bps (percentile: {b_pctl:.0f})",
        f"- Single-B rate of change: {hy.get('rate_of_change', 'N/A')} ({b_change:+.0f} bps over 3 months)",
        f"- WIDENING_FAST override: {'**ACTIVE**' if override else 'Inactive'}",
        "",
        "---",
        f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
    ])

    return "\n".join(lines)


def run(force: bool = False):
    """Execute monthly rebalance cycle."""
    timestamp = datetime.now()

    # Check if this is the right day (unless forced)
    if not force and not is_last_business_day_of_month(timestamp):
        print(f"{timestamp.strftime('%Y-%m-%d')}: Not last business day of month. Skipping.")
        return 0

    print(f"\n{'=' * 60}")
    print(f"  Dual Momentum + HY Spread: Monthly Rebalance")
    print(f"  {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}\n")

    # 1. Fetch data
    try:
        all_data = data_mod.fetch_all()
        prices = all_data["prices"]
        hy_spread = all_data["hy_spread"]
        ccc_bb_spread = all_data["ccc_bb_spread"]
        hy_b_spread = all_data["hy_b_spread"]
    except Exception as e:
        print(f"*** ERROR: Data fetch failed: {e} ***")
        sys.exit(1)

    # 2. Compute signals
    as_of = prices.index[-1]
    as_of_str = as_of.strftime("%Y-%m-%d")

    all_signals = signals_mod.compute_all_signals(
        prices, hy_spread, as_of,
        ccc_bb_spread=ccc_bb_spread, hy_b_spread=hy_b_spread,
    )
    gem = all_signals["gem"]
    hy = all_signals["hy_regime"]
    yc = all_signals["yield_curve"]

    # 3. Get target weights
    target_weights = portfolio_mod.construct_portfolio(gem, hy)

    # 4. Get prior portfolio
    prior_weights = state.get_current_portfolio()

    # 5. Compute trades
    trades = portfolio_mod.compute_trades(prior_weights, target_weights)

    # 6. Log
    state.log_daily(as_of_str, gem, hy, yc)
    state.log_monthly(
        date=as_of_str,
        target_weights=target_weights,
        gem_signal=gem.get("gem_signal", "N/A"),
        hy_regime=hy.get("regime", "N/A"),
        prior_weights=prior_weights,
        trades=trades,
    )

    # 7. Generate memo
    memo = generate_memo(as_of_str, gem, hy, yc, target_weights, prior_weights, trades)

    # Save memo
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    memo_filename = f"{timestamp.strftime('%Y-%m')}_memo.md"
    memo_path = config.REPORTS_DIR / memo_filename
    memo_path.write_text(memo)

    # 8. Print summary
    print(memo)
    print(f"\n  Memo saved to: {memo_path}")

    # 9. Send rebalance notifications
    subject, body, short_body = notifications.format_rebalance_alert(
        as_of_str, gem, hy, target_weights, prior_weights, trades
    )
    print(f"\n  Sending rebalance notifications...")
    results = notifications.send_all(subject, body, short_body)
    sent = [ch for ch, ok in results.items() if ok]
    if sent:
        print(f"  Notified via: {', '.join(sent)}")

    print(f"\n{'=' * 60}\n")

    return 0


if __name__ == "__main__":
    # Allow --force flag to run regardless of date
    force = "--force" in sys.argv
    sys.exit(run(force=force))
