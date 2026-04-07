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
import momentum_rank
import state
import notifications


def run():
    """Execute daily signal check."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'=' * 55}")
    print(f"  Three-Stage TAA: Daily Check")
    print(f"  Run time: {timestamp}")
    print(f"{'=' * 55}")

    # 1. Fetch data
    try:
        all_data = data_mod.fetch_all()
        prices = all_data["prices"]
        hy_spread = all_data["hy_spread"]
        ccc_bb_spread = all_data["ccc_bb_spread"]
        hy_b_spread = all_data["hy_b_spread"]
    except Exception as e:
        print(f"\n*** ERROR: Data fetch failed: {e} ***")
        sys.exit(1)

    # 2. Determine as-of date (latest available price date)
    as_of = prices.index[-1]
    as_of_str = as_of.strftime("%Y-%m-%d")

    # 3. Compute signals (all three stages)
    all_signals = signals_mod.compute_all_signals(
        prices, hy_spread, as_of,
        ccc_bb_spread=ccc_bb_spread, hy_b_spread=hy_b_spread,
    )
    gem = all_signals["gem"]
    hy = all_signals["hy_regime"]
    mom = all_signals["momentum"]
    yc = all_signals["yield_curve"]

    # 4. Compute target portfolio (three-stage v2)
    target_v2 = portfolio_mod.construct_portfolio_v2(gem, hy, mom)
    # Extract weights without metadata for display
    target = {k: v for k, v in target_v2.items() if k != "_metadata" and isinstance(v, (int, float))}

    # 5. Log to state
    state.log_daily(as_of_str, gem, hy, yc, momentum_signal=mom)

    # 6. Print summary
    gem_signal = gem.get("gem_signal", "N/A")
    abs_pass = gem.get("absolute_pass", False)

    print(f"\n  Date:          {as_of_str}")

    print(f"\n  Stage 1 — GEM Absolute Momentum:")
    print(f"    SPY 12M:     {gem['spy_12m_return']:+.1%}")
    print(f"    EFA 12M:     {gem['efa_12m_return']:+.1%}")
    print(f"    BIL 12M:     {gem['bil_12m_return']:+.1%}")
    print(f"    Abs Pass:    {'YES' if abs_pass else 'NO — 100% SHY'}")

    print(f"\n  Stage 2 — Momentum Ranking:")
    for ticker in sorted(mom.ranks.keys(), key=lambda t: mom.ranks.get(t, 99)):
        terc = mom.terciles.get(ticker, "N/A")
        rw = mom.risky_weights.get(ticker, 0.0)
        raw = mom.raw_momentum.get(ticker, float('nan'))
        raw_s = f"{raw:+.1%}" if not (raw != raw) else "N/A"
        print(f"    {ticker:<6} {terc:<8} wt={rw:.0%}  (12-1M: {raw_s})")

    regime = hy.get("regime", "N/A")
    regime_primary = hy.get("regime_primary", "N/A")
    ccc_bb = hy.get("ccc_bb_spread_current", 0) or 0
    ccc_bb_pctl = hy.get("ccc_bb_percentile", 0) or 0
    b_oas = hy.get("hy_b_current", 0) or 0
    b_pctl = hy.get("hy_b_percentile", 0) or 0
    b_change = hy.get("hy_b_change_3m", 0) or 0
    roc = hy.get("rate_of_change", "N/A")
    override = hy.get("fast_widen_override", False)

    print(f"\n  Stage 3 — HY Regime: {regime} (primary: {regime_primary})")
    print(f"    CCC-BB:      {ccc_bb:.0f} bps (pctl: {ccc_bb_pctl:.0f})")
    print(f"    Single-B:    {b_oas:.0f} bps (pctl: {b_pctl:.0f})")
    print(f"    B 3M Change: {b_change:+.0f} bps ({roc})")
    print(f"    Override:    {'YES ***' if override else 'NO'}")

    yc_spread = yc.get("t10y2y", "N/A")
    yc_status = yc.get("status", "N/A")
    print(f"\n  Yield Curve:   {yc_spread}% ({yc_status}) [monitoring only]")

    print(f"\n  Target Portfolio:")
    import portfolio_v2 as pv2
    for ticker in pv2.ALL_TICKERS:
        w = target.get(ticker, 0)
        if w > 0.001:
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
