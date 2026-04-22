#!/usr/bin/env python3
"""
scripts/afternoon_scan.py -- Exit Signal Emails

Runs at 3:15 PM NPT (after market close) to check portfolio positions
against targets, stop losses, and momentum indicators. Sends alerts
for positions that need attention.

Usage:
    python scripts/afternoon_scan.py              # full run + email
    python scripts/afternoon_scan.py --print      # no email, print only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

NPT = timezone(timedelta(hours=5, minutes=45))
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, List, Optional

# -- Path setup ----------------------------------------------------------------
ROOT    = Path(__file__).parent.parent
SCRATCH = Path("/scratch/C00621463/pypackages")
if SCRATCH.exists():
    sys.path.insert(0, str(SCRATCH))
sys.path.insert(0, str(ROOT / "ml"))
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(line_buffering=True)

import numpy as np

# -- Config --------------------------------------------------------------------
HISTORY_DIR = ROOT / "data" / "price_history"
SIGNAL_LOG  = ROOT / "data" / "signal_log.json"

EMAIL_FROM     = os.getenv("EMAIL_FROM",    "")
EMAIL_TO       = os.getenv("EMAIL_TO",      "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
SMTP_HOST      = os.getenv("SMTP_HOST",     "smtp.gmail.com")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))

# Thresholds
RSI_OVERBOUGHT  = 75
MOMENTUM_WINDOW = 5


# ==============================================================================
# PORTFOLIO LOADING
# ==============================================================================

def load_portfolio() -> Dict[str, dict]:
    """Load portfolio holdings from portfolio/config.py."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "portfolio_config", ROOT / "portfolio" / "config.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "PORTFOLIO", {})
    except Exception:
        print("[PORTFOLIO] Could not import portfolio/config.py")
        return {}


# ==============================================================================
# SIGNAL LOG
# ==============================================================================

def load_recent_signals(days_back=5) -> Dict[str, dict]:
    """Load recent scanner picks with their targets/stop losses.

    Returns {symbol: {signal, score, ta, price_at_signal, ...}}
    """
    if not SIGNAL_LOG.exists():
        return {}
    try:
        records = json.loads(SIGNAL_LOG.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    recent = {}
    for rec in records:
        if rec.get("date", "") >= cutoff:
            sym = rec.get("symbol", "")
            if sym:
                recent[sym] = rec
    return recent


# ==============================================================================
# PRICE DATA
# ==============================================================================

def fetch_closing_prices() -> Dict[str, dict]:
    """Fetch today's closing prices from MeroLagani API.

    Returns {symbol: {price, change_pct, volume, high, low, open}}.
    """
    try:
        import urllib.request
        url = "https://merolagani.com/handlers/webrequesthandler.ashx?type=market_summary"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        prices = {}
        # Try the turnover detail first (has more fields)
        turnover_detail = (
            data.get("turnover", {}).get("detail", [])
            if isinstance(data.get("turnover"), dict) else []
        )
        for item in turnover_detail:
            sym = item.get("s", "")
            lp = item.get("lp", 0)
            if sym and lp:
                base = lp - item.get("c", 0) if item.get("c") else lp
                pct = (item.get("c", 0) / base * 100) if base > 0 else 0.0
                prices[sym] = {
                    "price":      float(lp),
                    "change_pct": round(float(item.get("pc", pct)), 2),
                    "volume":     int(item.get("q", 0)),
                    "high":       float(item.get("h", lp)),
                    "low":        float(item.get("l", lp)),
                    "open":       float(item.get("op", lp)),
                }

        # Fill in from stock detail if turnover is sparse
        stock_detail = (
            data.get("stock", {}).get("detail", [])
            if isinstance(data.get("stock"), dict) else []
        )
        for item in stock_detail:
            sym = item.get("s", "")
            lp = item.get("lp", 0)
            if sym and lp and sym not in prices:
                prices[sym] = {
                    "price":      float(lp),
                    "change_pct": 0.0,
                    "volume":     int(item.get("q", 0)),
                    "high":       float(lp),
                    "low":        float(lp),
                    "open":       float(lp),
                }

        print(f"[PRICES] Fetched closing prices for {len(prices)} stocks")
        return prices
    except Exception as e:
        print(f"[PRICES] Fetch failed: {e}")
        return {}


def load_price_history(symbol: str, days=30) -> list:
    """Load recent price history for a symbol from price_history JSON files.

    Returns list of floats (closing prices), oldest first.
    """
    if not HISTORY_DIR.exists():
        return []

    prices = []
    files = sorted(HISTORY_DIR.glob("*.json"))[-days:]
    for f in files:
        try:
            day_data = json.loads(f.read_text())
            stocks = day_data.get("stocks", {})
            if symbol in stocks:
                lp = stocks[symbol].get("lp", 0)
                if lp and lp > 0:
                    prices.append(float(lp))
        except Exception:
            continue
    return prices


# ==============================================================================
# TECHNICAL HELPERS
# ==============================================================================

def compute_rsi(prices: list, period: int = 14) -> float:
    """Compute RSI from a list of closing prices."""
    if len(prices) < period + 1:
        return 50.0  # neutral default

    arr = np.array(prices, dtype=float)
    deltas = np.diff(arr)

    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 2)


def check_momentum_reversal(prices: list, window: int = MOMENTUM_WINDOW) -> bool:
    """Check if momentum has reversed from positive to negative.

    Compares the average return over the prior window to the most recent window.
    Returns True if prior momentum was positive but current is negative.
    """
    if len(prices) < window * 2 + 1:
        return False

    recent = prices[-window:]
    prior = prices[-(window * 2):-window]

    recent_ret = (recent[-1] / recent[0] - 1) if recent[0] > 0 else 0
    prior_ret = (prior[-1] / prior[0] - 1) if prior[0] > 0 else 0

    return prior_ret > 0 and recent_ret < 0


def estimate_targets(entry_price: float) -> dict:
    """Estimate target and stop-loss levels for a position.

    Uses standard NEPSE swing-trade targets:
    - Target 1: +5% (partial profit)
    - Target 2: +10% (full exit)
    - Stop loss: -3%
    """
    return {
        "target_1":  round(entry_price * 1.05, 2),
        "target_2":  round(entry_price * 1.10, 2),
        "stop_loss": round(entry_price * 0.97, 2),
    }


# ==============================================================================
# EXIT SIGNAL ANALYSIS
# ==============================================================================

def analyze_positions(
    portfolio: Dict[str, dict],
    signals: Dict[str, dict],
    closing_prices: Dict[str, dict],
) -> List[dict]:
    """Analyze each portfolio position for exit signals.

    Returns a list of alert dicts:
        {symbol, alert_type, severity, message, price, wacc, pnl_pct}
    """
    alerts = []

    for sym, holding in portfolio.items():
        price_data = closing_prices.get(sym, {})
        current_price = price_data.get("price", 0)
        if current_price <= 0:
            continue

        wacc = holding.get("wacc", 0)
        shares = holding.get("shares", 0)
        pnl_pct = ((current_price / wacc) - 1) * 100 if wacc > 0 else 0

        # Load price history for technical checks
        history = load_price_history(sym, days=30)
        if current_price and (not history or history[-1] != current_price):
            history.append(current_price)

        # Get signal data if this stock was a recent pick
        sig = signals.get(sym, {})
        entry_price = sig.get("price_at_signal") or wacc
        targets = estimate_targets(entry_price)

        # Check 1: Target 1 hit (take partial profit)
        if current_price >= targets["target_1"]:
            alerts.append({
                "symbol": sym,
                "alert_type": "TAKE PARTIAL PROFIT",
                "severity": "info",
                "message": (
                    f"{sym} at Rs {current_price:.2f} has reached Target 1 "
                    f"(Rs {targets['target_1']:.2f}, +5% from entry). "
                    f"Consider booking partial profits."
                ),
                "price": current_price,
                "wacc": wacc,
                "pnl_pct": round(pnl_pct, 2),
            })

        # Check 2: Stop loss hit
        if current_price <= targets["stop_loss"]:
            alerts.append({
                "symbol": sym,
                "alert_type": "EXIT",
                "severity": "critical",
                "message": (
                    f"{sym} at Rs {current_price:.2f} has hit stop loss "
                    f"(Rs {targets['stop_loss']:.2f}, -3% from entry). "
                    f"Exit to limit losses."
                ),
                "price": current_price,
                "wacc": wacc,
                "pnl_pct": round(pnl_pct, 2),
            })

        # Check 3: RSI overbought
        rsi = compute_rsi(history)
        if rsi > RSI_OVERBOUGHT:
            alerts.append({
                "symbol": sym,
                "alert_type": "OVERBOUGHT WARNING",
                "severity": "warning",
                "message": (
                    f"{sym} RSI is {rsi:.0f} (overbought > {RSI_OVERBOUGHT}). "
                    f"Price at Rs {current_price:.2f}. Consider tightening stop loss."
                ),
                "price": current_price,
                "wacc": wacc,
                "pnl_pct": round(pnl_pct, 2),
            })

        # Check 4: Momentum reversal
        if check_momentum_reversal(history):
            alerts.append({
                "symbol": sym,
                "alert_type": "MOMENTUM REVERSAL",
                "severity": "warning",
                "message": (
                    f"{sym} momentum has turned negative after a positive run. "
                    f"Price at Rs {current_price:.2f} ({pnl_pct:+.1f}% from WACC). "
                    f"Watch for further weakness."
                ),
                "price": current_price,
                "wacc": wacc,
                "pnl_pct": round(pnl_pct, 2),
            })

    return alerts


# ==============================================================================
# EMAIL
# ==============================================================================

def build_alert_email(alerts: List[dict], portfolio_summary: dict) -> str:
    """Build HTML email for exit signals."""
    today = date.today().strftime("%Y-%m-%d")

    severity_colors = {
        "critical": "#d50000",
        "warning":  "#ff6d00",
        "info":     "#1565c0",
    }

    alert_type_colors = {
        "EXIT":                 "#d50000",
        "TAKE PARTIAL PROFIT":  "#00c853",
        "OVERBOUGHT WARNING":   "#ff6d00",
        "MOMENTUM REVERSAL":    "#e65100",
    }

    if not alerts:
        alert_rows = """
        <tr><td colspan="5" style="text-align:center;padding:20px;color:#666">
            No exit signals today. All positions within normal parameters.
        </td></tr>
        """
    else:
        alert_rows = ""
        for a in alerts:
            color = alert_type_colors.get(a["alert_type"], "#666")
            alert_rows += f"""
            <tr>
              <td style="font-weight:700">{a['symbol']}</td>
              <td style="text-align:center">
                <span style="background:{color};color:#fff;padding:2px 8px;
                       border-radius:3px;font-size:11px;font-weight:600">
                  {a['alert_type']}
                </span>
              </td>
              <td style="text-align:right">Rs {a['price']:.2f}</td>
              <td style="text-align:right;color:{'#00c853' if a['pnl_pct'] >= 0 else '#d50000'}">
                {a['pnl_pct']:+.1f}%
              </td>
              <td style="font-size:12px;color:#555">{a['message']}</td>
            </tr>
            """

    total_pnl = portfolio_summary.get("total_pnl", 0)
    total_pct = portfolio_summary.get("total_pct", 0)
    pnl_color = "#00c853" if total_pnl >= 0 else "#d50000"

    # Portfolio position rows
    port_rows = ""
    for pos in portfolio_summary.get("positions", []):
        pos_color = "#00c853" if pos["pnl_pct"] >= 0 else "#d50000"
        port_rows += f"""
        <tr>
          <td style="font-weight:600">{pos['symbol']}</td>
          <td style="text-align:right">{pos['shares']}</td>
          <td style="text-align:right">Rs {pos['wacc']:.2f}</td>
          <td style="text-align:right">Rs {pos['price']:.2f}</td>
          <td style="text-align:right;color:{pos_color};font-weight:600">
            {pos['pnl_pct']:+.1f}%
          </td>
          <td style="text-align:right;color:{pos_color}">
            Rs {pos['pnl']:+,.0f}
          </td>
        </tr>
        """

    n_critical = sum(1 for a in alerts if a["severity"] == "critical")
    n_warning  = sum(1 for a in alerts if a["severity"] == "warning")
    n_info     = sum(1 for a in alerts if a["severity"] == "info")

    summary_line = []
    if n_critical:
        summary_line.append(f"{n_critical} critical")
    if n_warning:
        summary_line.append(f"{n_warning} warning")
    if n_info:
        summary_line.append(f"{n_info} info")
    summary_text = ", ".join(summary_line) if summary_line else "No alerts"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
                 background:#f5f5f5;margin:0;padding:20px">
    <div style="max-width:700px;margin:0 auto">

      <div style="background:#1a237e;color:#fff;padding:16px 20px;border-radius:8px 8px 0 0">
        <h1 style="margin:0;font-size:18px">NEPSE Afternoon Scan -- {today}</h1>
        <p style="margin:4px 0 0;font-size:13px;opacity:0.85">
          Exit signals: {summary_text} |
          Portfolio P&L: <span style="color:{pnl_color}">Rs {total_pnl:+,.0f} ({total_pct:+.1f}%)</span>
        </p>
      </div>

      <div style="background:#fff;padding:20px;border-radius:0 0 8px 8px;
                  box-shadow:0 1px 3px rgba(0,0,0,.1);margin-bottom:16px">
        <h2 style="margin:0 0 12px;font-size:16px;color:#1a237e">Exit Signals</h2>
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <tr style="background:#f0f0f0">
            <th style="text-align:left;padding:6px">Symbol</th>
            <th style="text-align:center;padding:6px">Signal</th>
            <th style="text-align:right;padding:6px">Price</th>
            <th style="text-align:right;padding:6px">P&L</th>
            <th style="text-align:left;padding:6px">Details</th>
          </tr>
          {alert_rows}
        </table>
      </div>

      <div style="background:#fff;padding:20px;border-radius:8px;
                  box-shadow:0 1px 3px rgba(0,0,0,.1)">
        <h2 style="margin:0 0 12px;font-size:16px;color:#1a237e">Portfolio Status</h2>
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <tr style="background:#f0f0f0">
            <th style="text-align:left;padding:6px">Symbol</th>
            <th style="text-align:right;padding:6px">Shares</th>
            <th style="text-align:right;padding:6px">WACC</th>
            <th style="text-align:right;padding:6px">LTP</th>
            <th style="text-align:right;padding:6px">P&L %</th>
            <th style="text-align:right;padding:6px">P&L Rs</th>
          </tr>
          {port_rows}
          <tr style="border-top:2px solid #1a237e;font-weight:700">
            <td colspan="4" style="padding:8px">Total</td>
            <td style="text-align:right;padding:8px;color:{pnl_color}">{total_pct:+.1f}%</td>
            <td style="text-align:right;padding:8px;color:{pnl_color}">Rs {total_pnl:+,.0f}</td>
          </tr>
        </table>
      </div>

      <p style="text-align:center;font-size:11px;color:#999;margin-top:16px">
        Afternoon scan ran at {datetime.now(NPT).strftime('%H:%M NPT')} |
        Auto-generated -- review before acting
      </p>
    </div>
    </body>
    </html>
    """
    return html


def send_email(subject: str, html_body: str) -> bool:
    """Send HTML email via configured SMTP."""
    if not EMAIL_FROM or not EMAIL_TO or not EMAIL_PASSWORD:
        print("[EMAIL] Credentials not configured -- skipping")
        return False
    try:
        import smtplib
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print(f"[EMAIL] Sent to {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"[EMAIL] Failed: {e}")
        return False


def send_telegram(message: str) -> bool:
    """Send a Telegram message if bot token and chat ID are configured."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return False
    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = json.dumps({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        print("[TELEGRAM] Message sent")
        return True
    except Exception as e:
        print(f"[TELEGRAM] Failed: {e}")
        return False


# ==============================================================================
# MAIN
# ==============================================================================

def compute_portfolio_summary(
    portfolio: Dict[str, dict],
    closing_prices: Dict[str, dict],
) -> dict:
    """Compute P&L summary for all portfolio positions."""
    positions = []
    total_cost = 0.0
    total_value = 0.0

    for sym, pos in portfolio.items():
        shares = pos.get("shares", 0)
        wacc = pos.get("wacc", 0)
        cost = shares * wacc

        price_data = closing_prices.get(sym, {})
        price = price_data.get("price", 0.0)

        if price > 0:
            value = shares * price
            pnl = value - cost
            pct = (price / wacc - 1) * 100 if wacc > 0 else 0
        else:
            value = cost
            pnl = 0.0
            pct = 0.0

        total_cost += cost
        total_value += value

        positions.append({
            "symbol":  sym,
            "shares":  shares,
            "wacc":    wacc,
            "price":   price,
            "value":   value,
            "pnl":     pnl,
            "pnl_pct": round(pct, 2),
            "cost":    cost,
        })

    total_pnl = total_value - total_cost
    total_pct = (total_value / total_cost - 1) * 100 if total_cost > 0 else 0

    return {
        "positions":   positions,
        "total_cost":  total_cost,
        "total_value": total_value,
        "total_pnl":   total_pnl,
        "total_pct":   round(total_pct, 2),
    }


def run_afternoon_scan(send_email_flag: bool = True):
    """
    Main afternoon scan pipeline:

    1. Load portfolio holdings from portfolio/config.py
    2. Load today's picks and their targets/stop losses
    3. Fetch closing prices
    4. For each position:
       - Check if it hit Target 1 (send "TAKE PARTIAL PROFIT" alert)
       - Check if it hit stop loss (send "EXIT" alert)
       - Check if RSI > 75 (send "OVERBOUGHT WARNING")
       - Check 5-day momentum reversal (was positive, now negative)
    5. Send email with exit signals
    6. Send Telegram message if configured
    """
    t0 = time.time()
    today = date.today().strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"  NEPSE AFTERNOON SCAN -- {today}")
    print(f"{'='*60}\n")

    # Step 1: Load portfolio
    print("[1/6] Loading portfolio...")
    portfolio = load_portfolio()
    if not portfolio:
        print("  No portfolio holdings found. Exiting.")
        return
    print(f"  {len(portfolio)} positions loaded")

    # Step 2: Load recent signals
    print("[2/6] Loading recent scanner signals...")
    signals = load_recent_signals(days_back=5)
    print(f"  {len(signals)} recent signals loaded")

    # Step 3: Fetch closing prices
    print("[3/6] Fetching closing prices...")
    closing_prices = fetch_closing_prices()
    if not closing_prices:
        print("  Could not fetch prices. Exiting.")
        return

    # Step 4: Analyze positions
    print("[4/6] Analyzing positions for exit signals...")
    alerts = analyze_positions(portfolio, signals, closing_prices)

    # Sort alerts: critical first, then warning, then info
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: severity_order.get(a.get("severity", ""), 9))

    n_critical = sum(1 for a in alerts if a["severity"] == "critical")
    n_warning  = sum(1 for a in alerts if a["severity"] == "warning")
    print(f"  {len(alerts)} alerts ({n_critical} critical, {n_warning} warning)")

    # Print to console
    if alerts:
        print(f"\n{'---'*20}")
        for a in alerts:
            print(f"  [{a['alert_type']}] {a['message']}")
        print(f"{'---'*20}")
    else:
        print("  No exit signals. All positions look fine.")

    # Compute portfolio summary
    port_summary = compute_portfolio_summary(portfolio, closing_prices)

    # Step 5: Send email -- only if critical alerts AND haven't sent yet today
    print("\n[5/6] Sending email...")
    html = build_alert_email(alerts, port_summary)
    if send_email_flag:
        # Dedupe: don't re-email if the same alert set already went today
        alert_sig = ",".join(sorted(f"{a['symbol']}:{a['alert_type']}" for a in alerts))
        try:
            import sys as _sys
            _sys.path.insert(0, str(ROOT))
            from src.email_throttle import allow
            can_send = allow("afternoon_exits", max_per_day=1, dedupe_key=alert_sig)
        except Exception:
            can_send = True

        if can_send and (n_critical > 0 or alerts):
            subject_prefix = "EXIT ALERT" if n_critical > 0 else "Afternoon Scan"
            send_email(
                f"NEPSE {subject_prefix} -- {today}",
                html,
            )
        else:
            print("[EMAIL] Afternoon email already sent today (or no alerts) -- skipping")
    else:
        out_path = ROOT / "reports" / f"afternoon_{today}.html"
        out_path.parent.mkdir(exist_ok=True)
        out_path.write_text(html)
        print(f"  Report saved: {out_path}")

    # Step 6: Telegram (if configured and there are critical alerts)
    print("[6/6] Telegram notification...")
    if alerts and send_email_flag and n_critical > 0:
        tg_lines = [f"<b>NEPSE Afternoon Scan -- {today}</b>\n"]
        for a in alerts:
            tg_lines.append(f"<b>[{a['alert_type']}]</b> {a['symbol']} @ Rs {a['price']:.2f} ({a['pnl_pct']:+.1f}%)")
        tg_lines.append(f"\nPortfolio P&L: Rs {port_summary['total_pnl']:+,.0f} ({port_summary['total_pct']:+.1f}%)")
        send_telegram("\n".join(tg_lines))
    else:
        print("  Skipped (no alerts or --print mode)")

    elapsed = time.time() - t0
    print(f"\n  Afternoon scan completed in {elapsed:.1f}s\n")


def main():
    parser = argparse.ArgumentParser(description="NEPSE Afternoon Exit Signal Scanner")
    parser.add_argument("--print", action="store_true", help="Print only, no email")
    args = parser.parse_args()

    run_afternoon_scan(send_email_flag=not args.print)


if __name__ == "__main__":
    main()
