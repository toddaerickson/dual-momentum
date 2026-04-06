"""
Notifications: Email, Slack, and SMS alerts for signal changes and rebalances.

Configure via environment variables:
  Email (SMTP):
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO

  Slack:
    SLACK_WEBHOOK_URL

  Twilio SMS:
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, ALERT_SMS_TO
"""

import json
import os
import smtplib
import urllib.request
import urllib.error
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ──────────────────────────────────────────────
# Configuration (all from env vars)
# ──────────────────────────────────────────────

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "")

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")
ALERT_SMS_TO = os.environ.get("ALERT_SMS_TO", "")


def is_email_configured() -> bool:
    return all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO])


def is_slack_configured() -> bool:
    return bool(SLACK_WEBHOOK_URL)


def is_sms_configured() -> bool:
    return all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, ALERT_SMS_TO])


# ──────────────────────────────────────────────
# Email (SMTP)
# ──────────────────────────────────────────────

def send_email(subject: str, body: str, html: bool = False) -> bool:
    """
    Send an email alert via SMTP.

    Returns True on success, False on failure.
    """
    if not is_email_configured():
        print("  [email] Not configured, skipping.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = ALERT_EMAIL_TO

        content_type = "html" if html else "plain"
        msg.attach(MIMEText(body, content_type))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)

        print(f"  [email] Sent to {ALERT_EMAIL_TO}")
        return True
    except Exception as e:
        print(f"  [email] Failed: {e}")
        return False


# ──────────────────────────────────────────────
# Slack Webhook
# ──────────────────────────────────────────────

def send_slack(message: str) -> bool:
    """
    Post a message to Slack via incoming webhook.

    Returns True on success, False on failure.
    """
    if not is_slack_configured():
        print("  [slack] Not configured, skipping.")
        return False

    try:
        payload = json.dumps({"text": message}).encode("utf-8")
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print("  [slack] Message sent.")
                return True
            else:
                print(f"  [slack] Unexpected status: {resp.status}")
                return False
    except Exception as e:
        print(f"  [slack] Failed: {e}")
        return False


# ──────────────────────────────────────────────
# Twilio SMS
# ──────────────────────────────────────────────

def send_sms(message: str) -> bool:
    """
    Send an SMS via Twilio REST API.

    Returns True on success, False on failure.
    """
    if not is_sms_configured():
        print("  [sms] Not configured, skipping.")
        return False

    try:
        import base64

        url = (
            f"https://api.twilio.com/2010-04-01/Accounts/"
            f"{TWILIO_ACCOUNT_SID}/Messages.json"
        )
        data = urllib.parse.urlencode({
            "To": ALERT_SMS_TO,
            "From": TWILIO_FROM_NUMBER,
            "Body": message,
        }).encode("utf-8")

        credentials = base64.b64encode(
            f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()
        ).decode()

        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Basic {credentials}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 201):
                print(f"  [sms] Sent to {ALERT_SMS_TO}")
                return True
            else:
                print(f"  [sms] Unexpected status: {resp.status}")
                return False
    except Exception as e:
        print(f"  [sms] Failed: {e}")
        return False


# ──────────────────────────────────────────────
# Unified Alert Dispatcher
# ──────────────────────────────────────────────

def send_all(subject: str, body: str, short_body: str = None) -> dict:
    """
    Send alert via all configured channels.

    Args:
        subject: Alert subject (used for email subject line and Slack header)
        body: Full alert body (used for email and Slack)
        short_body: Short version for SMS (defaults to first 160 chars of body)

    Returns:
        dict of {channel: success_bool}
    """
    if short_body is None:
        short_body = body[:160]

    results = {}
    results["email"] = send_email(subject, body)
    results["slack"] = send_slack(f"*{subject}*\n{body}")
    results["sms"] = send_sms(f"{subject}: {short_body}")
    return results


# ──────────────────────────────────────────────
# Pre-built Alert Formatters
# ──────────────────────────────────────────────

def format_signal_alert(changes: dict, gem: dict, hy: dict, target: dict) -> tuple[str, str, str]:
    """
    Format a signal change alert.

    Returns:
        (subject, body, short_body) tuple
    """
    parts = []
    if changes.get("gem_changed"):
        parts.append(f"GEM: {changes.get('previous_gem')} -> {changes.get('current_gem')}")
    if changes.get("regime_changed"):
        parts.append(f"HY Regime: {changes.get('previous_regime')} -> {changes.get('current_regime')}")
    if changes.get("override_activated"):
        parts.append("WIDENING_FAST override ACTIVATED")

    subject = "DM Signal Change: " + " | ".join(parts)

    # Full body
    gem_signal = gem.get("gem_signal", "N/A")
    regime = hy.get("regime", "N/A")
    ccc_bb = hy.get("ccc_bb_spread_current", 0) or 0
    ccc_bb_pctl = hy.get("ccc_bb_percentile", 0) or 0
    b_oas = hy.get("hy_b_current", 0) or 0
    b_change = hy.get("hy_b_change_3m", 0) or 0

    body_lines = [
        "Signal Change Detected",
        "=" * 30,
        "",
    ]
    for p in parts:
        body_lines.append(f"  * {p}")

    body_lines.extend([
        "",
        "Current State:",
        f"  GEM Signal:  {gem_signal}",
        f"  SPY 12M:     {gem.get('spy_12m_return', 0):+.1%}",
        f"  EFA 12M:     {gem.get('efa_12m_return', 0):+.1%}",
        f"  BIL 12M:     {gem.get('bil_12m_return', 0):+.1%}",
        f"  HY Regime:   {regime} (CCC-BB: {ccc_bb:.0f} bps, pctl: {ccc_bb_pctl:.0f})",
        f"  Single-B:    {b_oas:.0f} bps, 3M chg: {b_change:+.0f} bps",
        f"  Override:    {'YES' if hy.get('fast_widen_override') else 'NO'}",
        "",
        "Target Portfolio:",
    ])
    for ticker in ["SPY", "EFA", "SHY", "ANGL"]:
        w = target.get(ticker, 0)
        if w > 0:
            body_lines.append(f"  {ticker}: {w:.0%}")

    body = "\n".join(body_lines)
    short_body = " | ".join(parts)

    return subject, body, short_body


def format_rebalance_alert(
    date_str: str,
    gem: dict,
    hy: dict,
    target_weights: dict,
    prior_weights: dict,
    trades: list,
) -> tuple[str, str, str]:
    """
    Format a monthly rebalance alert.

    Returns:
        (subject, body, short_body) tuple
    """
    gem_signal = gem.get("gem_signal", "N/A")
    regime = hy.get("regime", "N/A")

    # Build target string
    alloc_parts = []
    for ticker in ["SPY", "EFA", "SHY", "ANGL"]:
        w = target_weights.get(ticker, 0)
        if w > 0:
            alloc_parts.append(f"{ticker} {w:.0%}")
    alloc_str = " / ".join(alloc_parts)

    subject = f"DM Rebalance {date_str}: {alloc_str}"

    body_lines = [
        f"Monthly Rebalance: {date_str}",
        "=" * 40,
        "",
        f"  GEM Signal:  {gem_signal}",
        f"  HY Regime:   {regime} (CCC-BB pctl: {hy.get('ccc_bb_percentile', 0) or 0:.0f})",
        "",
        "Target Allocation:",
    ]
    for ticker in ["SPY", "EFA", "SHY", "ANGL"]:
        tw = target_weights.get(ticker, 0)
        pw = prior_weights.get(ticker, 0)
        if tw > 0 or pw > 0:
            change = tw - pw
            change_str = f" ({change:+.0%})" if abs(change) > 1e-6 else ""
            body_lines.append(f"  {ticker}: {tw:.0%}{change_str}")

    if trades:
        body_lines.extend(["", "Trades:"])
        for t in trades:
            direction = "BUY" if t["change"] > 0 else "SELL"
            body_lines.append(f"  {direction} {abs(t['change']):.0%} {t['ticker']}")
    else:
        body_lines.extend(["", "No trades required."])

    body = "\n".join(body_lines)
    short_body = f"Rebal {date_str}: {alloc_str}"

    return subject, body, short_body


if __name__ == "__main__":
    import sys

    print("Notification channels configured:")
    print(f"  Email: {'YES' if is_email_configured() else 'NO'}")
    print(f"  Slack: {'YES' if is_slack_configured() else 'NO'}")
    print(f"  SMS:   {'YES' if is_sms_configured() else 'NO'}")

    if not any([is_email_configured(), is_slack_configured(), is_sms_configured()]):
        print("\nNo channels configured. Set environment variables:")
        print("  Email: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO")
        print("  Slack: SLACK_WEBHOOK_URL")
        print("  SMS:   TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, ALERT_SMS_TO")

    if "--test" in sys.argv:
        print("\nSending test alert...")
        subject = "Dual Momentum - Test Alert"
        body = (
            "This is a test alert from your Dual Momentum system.\n\n"
            "If you received this, notifications are working correctly."
        )
        results = send_all(subject, body, "DM test alert - notifications working.")
        print(f"\nResults: {results}")
