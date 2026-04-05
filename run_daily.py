"""
Daily Runner: Fetch data, compute signals, log, alert on changes.

Run via cron:
  0 0 * * 1-5 cd ~/dual-momentum && python run_daily.py >> logs/daily.log 2>&1
"""

import sys
from datetime import datetime

import data as data_mod
import signals as signals_mod
import portfolio as portfolio_mod
import state
import notifications


def run():
    """Execute daily signal check."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'=' * 55}")
    print(f"  Dual Momentum + HY Spread: Daily Check")
    print(f"  Run time: {timestamp}")
    print(f"{'=' * 55}")

    # 1. Fetch data
    try:
        all_data = data_mod.fetch_all()
        prices = all_data["prices"]
        hy_spread = all_data["hy_spread"]
    except Exception as e:
        print(f"\n*** ERROR: Data fetch failed: {e} ***")
        sys.exit(1)

    # 2. Determine as-of date (latest available price date)
    as_of = prices.index[-1]
    as_of_str = as_of.strftime("%Y-%m-%d")

    # 3. Compute signals
    all_signals = signals_mod.compute_all_signals(prices, hy_spread, as_of)
    gem = all_signals["gem"]
    hy = all_signals["hy_regime"]
    yc = all_signals["yield_curve"]

    # 4. Compute target portfolio
    target = portfolio_mod.construct_portfolio(gem, hy)

    # 5. Log to state
    state.log_daily(as_of_str, gem, hy, yc)

    # 6. Print summary
    gem_signal = gem.get("gem_signal", "N/A")
    gem_labels = {"SPY": "US Equities", "EFA": "Intl Equities", "SHY": "Short Treasuries"}

    print(f"\n  Date:          {as_of_str}")
    print(f"\n  GEM Signal:    {gem_signal} ({gem_labels.get(gem_signal, '')})")
    print(f"    SPY 12M:     {gem['spy_12m_return']:+.1%}")
    print(f"    EFA 12M:     {gem['efa_12m_return']:+.1%}")
    print(f"    BIL 12M:     {gem['bil_12m_return']:+.1%}")
    print(f"    Rel Winner:  {gem['relative_winner']}")
    print(f"    Abs Pass:    {'YES' if gem['absolute_pass'] else 'NO'}")

    regime = hy.get("regime", "N/A")
    spread = hy.get("hy_spread_current", 0)
    change = hy.get("hy_spread_change_3m", 0)
    roc = hy.get("rate_of_change", "N/A")
    override = hy.get("fast_widen_override", False)

    print(f"\n  HY Regime:     {regime} ({spread:.0f} bps)")
    print(f"    3M Change:   {change:+.0f} bps ({roc})")
    print(f"    Override:    {'YES ***' if override else 'NO'}")

    yc_spread = yc.get("t10y2y", "N/A")
    yc_status = yc.get("status", "N/A")
    print(f"\n  Yield Curve:   {yc_spread}% ({yc_status}) [monitoring only]")

    print(f"\n  Target Portfolio:")
    for ticker in ["SPY", "EFA", "SHY", "ANGL"]:
        w = target.get(ticker, 0)
        if w > 0:
            print(f"    {ticker}:        {w:.0%}")

    # 7. Check for alerts
    changes = state.detect_signal_change()
    alerts = []

    if changes.get("gem_changed"):
        alerts.append(
            f"GEM signal changed: {changes.get('previous_gem')} → {changes.get('current_gem')}"
        )
    if changes.get("regime_changed"):
        alerts.append(
            f"HY regime changed: {changes.get('previous_regime')} → {changes.get('current_regime')}"
        )
    if changes.get("override_activated"):
        alerts.append("WIDENING_FAST override ACTIVATED")

    if alerts:
        print(f"\n  {'*' * 40}")
        print(f"  *** ALERT ***")
        for a in alerts:
            print(f"  *** {a}")
        print(f"  {'*' * 40}")

        # Send notifications via all configured channels
        subject, body, short_body = notifications.format_signal_alert(
            changes, gem, hy, target
        )
        print(f"\n  Sending notifications...")
        results = notifications.send_all(subject, body, short_body)
        sent = [ch for ch, ok in results.items() if ok]
        if sent:
            print(f"  Notified via: {', '.join(sent)}")
    else:
        print(f"\n  No signal changes detected.")

    print(f"\n{'=' * 55}\n")
    return 0


if __name__ == "__main__":
    sys.exit(run())
