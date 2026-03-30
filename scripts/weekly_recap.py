#!/usr/bin/env python3
"""
Weekly performance recap -- runs every Thursday at 4 PM NPT.

Generates an HTML email summarizing the week's picks, paper trading P&L,
signal accuracy, and next week's watchlist. Also sends a Telegram summary.

Usage:
    python scripts/weekly_recap.py          # run immediately
    python scripts/weekly_recap.py --dry    # print HTML, don't send

Cron (Thursday 4:15 PM NPT = 10:30 UTC):
    30 10 * * 4  cd /home/C00621463/Workspace/NEPSE && python scripts/weekly_recap.py
"""
import os
import sys
import json
import smtplib
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

NPT = timezone(timedelta(hours=5, minutes=45))

EMAIL_FROM     = os.getenv("EMAIL_FROM", "")
EMAIL_TO       = os.getenv("EMAIL_TO", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
SMTP_HOST      = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))

SIGNAL_LOG  = ROOT / "data" / "signal_log.json"
PAPER_FILE  = ROOT / "data" / "paper_portfolio.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")


def _send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    import requests
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )


def _send_email(subject, html_body):
    if not EMAIL_FROM or not EMAIL_TO or not EMAIL_PASSWORD:
        print("[EMAIL] Credentials not configured -- skipping")
        return False
    try:
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
        print(f"[EMAIL] Weekly recap sent to {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"[EMAIL] Failed: {e}")
        return False


def _load_json(path, default=None):
    if not path.exists():
        return default if default is not None else []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, IOError):
        return default if default is not None else []


def _week_range():
    """Return (week_start, week_end) as date strings for the current NEPSE week.

    NEPSE trades Sun-Thu, so the week is Sunday through Thursday.
    """
    now = datetime.now(NPT).date()
    # Thursday = weekday 3.  Walk back to Sunday (weekday 6).
    weekday = now.weekday()
    # Days back to get to Sunday
    if weekday >= 6:  # Sunday
        days_back = 0
    else:
        days_back = weekday + 1  # Mon=0 -> 1, Tue=1 -> 2, ... Thu=3 -> 4
    week_start = now - timedelta(days=days_back)
    week_end = week_start + timedelta(days=4)  # Thursday
    return week_start.isoformat(), week_end.isoformat()


def _get_week_picks(signals, week_start, week_end):
    """Filter signal log entries to this week's date range."""
    return [
        s for s in signals
        if week_start <= s.get("date", "") <= week_end
    ]


def _get_watchlist():
    """Find stocks near breakout for next week's watchlist."""
    hist_dir = ROOT / "data" / "price_history"
    files = sorted(hist_dir.glob("*.json"))[-20:]
    if not files:
        return []

    all_stocks = {}
    for f in files:
        d = json.loads(f.read_text())
        for sym, v in d.get("stocks", {}).items():
            all_stocks.setdefault(sym, []).append(v)

    candidates = []
    for sym, recs in all_stocks.items():
        closes = [r["lp"] for r in recs if r.get("lp", 0) > 0]
        if len(closes) < 15 or closes[-1] < 50 or closes[-1] > 1500:
            continue

        c = np.array(closes)
        deltas = np.diff(c[-15:])
        ag = np.where(deltas > 0, deltas, 0).mean()
        al = np.where(deltas < 0, -deltas, 0).mean() + 0.001
        rsi = 100 - (100 / (1 + ag / al))

        ema5 = c[-5:].mean()
        ema20 = c[-20:].mean() if len(c) >= 20 else c.mean()

        # Near breakout: RSI 45-60, EMA5 just crossing above EMA20
        if 45 <= rsi <= 60 and 0.99 <= ema5 / ema20 <= 1.02:
            candidates.append({
                "symbol": sym,
                "price": c[-1],
                "rsi": round(rsi, 1),
            })

    candidates.sort(key=lambda x: x["rsi"])
    return candidates[:5]


def run_weekly_recap(dry_run=False):
    """Build and send the weekly performance recap."""
    now = datetime.now(NPT)
    week_start, week_end = _week_range()

    print(f"[WEEKLY] Recap for {week_start} to {week_end}")

    # 1. Load signal log -- this week's picks
    signals = _load_json(SIGNAL_LOG, [])
    week_picks = _get_week_picks(signals, week_start, week_end)

    # 2. Load paper portfolio
    paper = _load_json(PAPER_FILE, {})
    paper_trades = _load_json(ROOT / "data" / "paper_trades.json", [])

    # 3. Count stats
    total_picks = len(week_picks)
    evaluated = [p for p in week_picks if p.get("hit") is not None]
    hits = [p for p in evaluated if p.get("hit")]
    misses = [p for p in evaluated if not p.get("hit")]

    n_evaluated = len(evaluated)
    n_hits = len(hits)
    n_misses = len(misses)
    weekly_hit_rate = (n_hits / n_evaluated * 100) if n_evaluated else 0

    # 4. Returns
    returns = [p["return_5d_pct"] for p in evaluated if p.get("return_5d_pct") is not None]
    avg_return = sum(returns) / len(returns) if returns else 0

    best_pick = max(evaluated, key=lambda p: p.get("return_5d_pct", -999), default=None)
    worst_pick = min(evaluated, key=lambda p: p.get("return_5d_pct", 999), default=None)

    # 30-day hit rate for comparison
    cutoff_30d = (now.date() - timedelta(days=30)).isoformat()
    recent_30d = [s for s in signals if s.get("date", "") >= cutoff_30d and s.get("hit") is not None]
    hits_30d = [s for s in recent_30d if s.get("hit")]
    hit_rate_30d = (len(hits_30d) / len(recent_30d) * 100) if recent_30d else 0

    # 5. Paper trading P&L
    paper_equity = paper.get("equity_curve", [])
    current_equity = paper_equity[-1]["equity"] if paper_equity else paper.get("cash", 10_000_000)
    initial_capital = paper.get("initial_capital", 10_000_000)
    paper_return_pct = (current_equity / initial_capital - 1) * 100

    # Week's trades from paper_trades
    week_trades = [t for t in paper_trades if week_start <= t.get("exit_date", "") <= week_end]

    # Watchlist
    watchlist = _get_watchlist()

    # 6. Build HTML email
    week_label = f"Week of {_format_date(week_start)} - {_format_date(week_end)}"

    # Picks table rows
    picks_rows = ""
    for p in week_picks:
        sym = p.get("symbol", "?")
        sig = p.get("signal", "N/A")
        entry = p.get("price_at_signal")
        entry_str = f"Rs {entry:,.0f}" if entry else "N/A"
        current = p.get("price_after_5d")
        current_str = f"Rs {current:,.0f}" if current else "pending"
        ret = p.get("return_5d_pct")
        ret_str = f"{ret:+.1f}%" if ret is not None else "--"
        hit = p.get("hit")
        if hit is None:
            status = '<span style="color:#888">PENDING</span>'
        elif hit:
            status = '<span style="color:#27ae60;font-weight:bold">HIT</span>'
        else:
            status = '<span style="color:#e74c3c;font-weight:bold">MISS</span>'

        ret_color = "#27ae60" if ret and ret > 0 else "#e74c3c" if ret and ret < 0 else "#888"
        picks_rows += (
            f"<tr>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'><b>{sym}</b></td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{sig}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{entry_str}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{current_str}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;color:{ret_color}'>{ret_str}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{status}</td>"
            f"</tr>\n"
        )

    if not picks_rows:
        picks_rows = (
            "<tr><td colspan='6' style='padding:12px;color:#888;text-align:center'>"
            "No picks this week</td></tr>\n"
        )

    # Watchlist rows
    watchlist_html = ""
    if watchlist:
        wl_items = "".join(
            f"<li>{w['symbol']} -- Rs {w['price']:,.0f}, RSI {w['rsi']}</li>"
            for w in watchlist
        )
        watchlist_html = f"""
        <div style="margin-top:20px;padding:16px;background:#f0f7ff;border-radius:8px">
          <h3 style="margin-top:0;color:#1565c0">Next Week Watchlist</h3>
          <p style="font-size:13px;color:#555">Stocks near EMA breakout with RSI 45-60:</p>
          <ul style="margin:0;padding-left:20px">{wl_items}</ul>
        </div>
        """

    # Paper trades this week
    paper_trades_html = ""
    if week_trades:
        pt_rows = ""
        for t in week_trades:
            pnl_color = "#27ae60" if t["pnl_pct"] >= 0 else "#e74c3c"
            pt_rows += (
                f"<tr>"
                f"<td style='padding:4px 8px'>{t['symbol']}</td>"
                f"<td style='padding:4px 8px'>{t['entry_price']:.0f}</td>"
                f"<td style='padding:4px 8px'>{t['exit_price']:.0f}</td>"
                f"<td style='padding:4px 8px;color:{pnl_color}'>{t['pnl_pct']:+.2f}%</td>"
                f"<td style='padding:4px 8px'>{t['reason']}</td>"
                f"</tr>\n"
            )
        paper_trades_html = f"""
        <h3 style="margin-top:20px">Paper Trades This Week</h3>
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <tr style="background:#f5f5f5">
            <th style="padding:6px 8px;text-align:left">Symbol</th>
            <th style="padding:6px 8px;text-align:left">Entry</th>
            <th style="padding:6px 8px;text-align:left">Exit</th>
            <th style="padding:6px 8px;text-align:left">P&amp;L</th>
            <th style="padding:6px 8px;text-align:left">Reason</th>
          </tr>
          {pt_rows}
        </table>
        """

    best_html = ""
    if best_pick:
        best_html = (
            f"<p><b>Best pick:</b> {best_pick['symbol']} "
            f"({best_pick.get('signal', '')}) {best_pick.get('return_5d_pct', 0):+.1f}%</p>"
        )
    worst_html = ""
    if worst_pick:
        worst_html = (
            f"<p><b>Worst pick:</b> {worst_pick['symbol']} "
            f"({worst_pick.get('signal', '')}) {worst_pick.get('return_5d_pct', 0):+.1f}%</p>"
        )

    paper_color = "#27ae60" if paper_return_pct >= 0 else "#e74c3c"

    html = f"""
    <html>
    <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
                 max-width:700px;margin:0 auto;padding:20px;color:#333">
      <div style="background:linear-gradient(135deg,#1a237e,#283593);color:#fff;
                  padding:24px;border-radius:12px;margin-bottom:20px">
        <h1 style="margin:0;font-size:22px">NEPSE Weekly Recap</h1>
        <p style="margin:8px 0 0;opacity:0.9;font-size:14px">{week_label}</p>
      </div>

      <!-- Summary Cards -->
      <div style="display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap">
        <div style="flex:1;min-width:140px;background:#f5f5f5;padding:16px;border-radius:8px;text-align:center">
          <div style="font-size:28px;font-weight:bold">{total_picks}</div>
          <div style="font-size:12px;color:#666">Picks Generated</div>
        </div>
        <div style="flex:1;min-width:140px;background:#f5f5f5;padding:16px;border-radius:8px;text-align:center">
          <div style="font-size:28px;font-weight:bold">{n_evaluated}</div>
          <div style="font-size:12px;color:#666">Evaluated</div>
        </div>
        <div style="flex:1;min-width:140px;background:#f5f5f5;padding:16px;border-radius:8px;text-align:center">
          <div style="font-size:28px;font-weight:bold;color:{'#27ae60' if weekly_hit_rate >= 50 else '#e74c3c'}">{weekly_hit_rate:.0f}%</div>
          <div style="font-size:12px;color:#666">Hit Rate</div>
        </div>
        <div style="flex:1;min-width:140px;background:#f5f5f5;padding:16px;border-radius:8px;text-align:center">
          <div style="font-size:28px;font-weight:bold;color:{'#27ae60' if avg_return >= 0 else '#e74c3c'}">{avg_return:+.1f}%</div>
          <div style="font-size:12px;color:#666">Avg Return</div>
        </div>
      </div>

      <!-- Win rate trend -->
      <div style="background:#fff3e0;padding:12px 16px;border-radius:8px;margin-bottom:20px;font-size:13px">
        <b>Win Rate Trend:</b> This week {weekly_hit_rate:.0f}% vs 30-day {hit_rate_30d:.0f}%
        {'-- improving' if weekly_hit_rate > hit_rate_30d else '-- declining' if weekly_hit_rate < hit_rate_30d else '-- steady'}
      </div>

      {best_html}
      {worst_html}

      <!-- Picks Performance Table -->
      <h3 style="margin-top:24px">Picks Performance</h3>
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <tr style="background:#f5f5f5">
          <th style="padding:8px 10px;text-align:left">Symbol</th>
          <th style="padding:8px 10px;text-align:left">Signal</th>
          <th style="padding:8px 10px;text-align:left">Entry</th>
          <th style="padding:8px 10px;text-align:left">Current</th>
          <th style="padding:8px 10px;text-align:left">Return</th>
          <th style="padding:8px 10px;text-align:left">Result</th>
        </tr>
        {picks_rows}
      </table>

      <!-- Paper Trading -->
      <div style="margin-top:20px;padding:16px;background:#f9fbe7;border-radius:8px">
        <h3 style="margin-top:0">Paper Trading P&amp;L</h3>
        <p>Equity: <b style="color:{paper_color}">NPR {current_equity:,.0f}</b>
           (Return: <span style="color:{paper_color}">{paper_return_pct:+.2f}%</span>)</p>
        <p style="font-size:13px;color:#666">
          Open positions: {len(paper.get('positions', {}))} |
          Total trades: {len(paper_trades)}
        </p>
      </div>

      {paper_trades_html}
      {watchlist_html}

      <!-- Footer -->
      <div style="margin-top:24px;padding-top:16px;border-top:1px solid #eee;
                  font-size:11px;color:#999;text-align:center">
        Generated by NEPSE AutoScan |
        {now.strftime("%Y-%m-%d %H:%M")} NPT |
        Not financial advice
      </div>
    </body>
    </html>
    """

    # Subject line
    subject = f"NEPSE Weekly Recap | {week_label}"
    if n_evaluated:
        subject += f" | {weekly_hit_rate:.0f}% hit rate"

    if dry_run:
        out_path = ROOT / "reports" / f"weekly_{week_start}.html"
        out_path.parent.mkdir(exist_ok=True)
        out_path.write_text(html)
        print(f"[WEEKLY] Saved to {out_path}")
    else:
        _send_email(subject, html)

    # 7. Telegram summary
    tg_msg = f"<b>Weekly Recap: {week_label}</b>\n\n"
    tg_msg += f"Picks: {total_picks} | Evaluated: {n_evaluated}\n"
    tg_msg += f"Hits: {n_hits} | Misses: {n_misses}\n"
    tg_msg += f"Hit rate: {weekly_hit_rate:.0f}% (30d: {hit_rate_30d:.0f}%)\n"
    tg_msg += f"Avg return: {avg_return:+.1f}%\n\n"

    if best_pick:
        tg_msg += f"Best: {best_pick['symbol']} {best_pick.get('return_5d_pct', 0):+.1f}%\n"
    if worst_pick:
        tg_msg += f"Worst: {worst_pick['symbol']} {worst_pick.get('return_5d_pct', 0):+.1f}%\n"

    tg_msg += f"\nPaper P&L: NPR {current_equity:,.0f} ({paper_return_pct:+.1f}%)\n"

    if watchlist:
        tg_msg += "\nWatchlist: " + ", ".join(w["symbol"] for w in watchlist)

    if not dry_run:
        _send_telegram(tg_msg)
    else:
        print("[TELEGRAM] Would send:")
        print(tg_msg)

    print(f"[WEEKLY] Done. {total_picks} picks, {n_evaluated} evaluated, {weekly_hit_rate:.0f}% hit rate")


def _format_date(date_str):
    """Format YYYY-MM-DD to 'Mar 24' style."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%b %d")
    except ValueError:
        return date_str


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    run_weekly_recap(dry_run=dry)
