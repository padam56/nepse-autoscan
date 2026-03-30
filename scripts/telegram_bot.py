#!/usr/bin/env python3
"""
Telegram Bot for NEPSE AutoScan Intraday Alerts.

Sends real-time alerts during NEPSE market hours (11 AM - 3 PM NPT, Sun-Thu):
  - Morning picks summary when scanner output is available
  - Price alerts when a pick hits buy range, target, or stop loss
  - Portfolio holding level-break alerts
  - End-of-day summary

Usage:
    python scripts/telegram_bot.py                # run intraday monitor loop
    python scripts/telegram_bot.py --morning      # send morning picks only
    python scripts/telegram_bot.py --eod          # send end-of-day summary only
    python scripts/telegram_bot.py --test         # send a test message

Env vars (set in .env):
    TELEGRAM_BOT_TOKEN   - from @BotFather
    TELEGRAM_CHAT_ID     - your chat or group ID
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

# ── Path setup ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
load_dotenv(ROOT / ".env")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
try:
    from portfolio.config import PORTFOLIO
except ImportError:
    try:
        from config import PORTFOLIO
    except ImportError:
        PORTFOLIO = {}
try:
    from config import HEADERS as _cfg_headers
    HEADERS = _cfg_headers
except ImportError:
    pass

NPT = timezone(timedelta(hours=5, minutes=45))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SIGNAL_LOG = ROOT / "data" / "signal_log.json"
ALERT_STATE_FILE = ROOT / "data" / "telegram_alert_state.json"

# How often to poll prices (seconds) — every 2 hours during market
CHECK_INTERVAL = 7200

# Target / stop-loss percentages (applied to price_at_signal or live price at first load)
TARGET1_PCT = 3.0
TARGET2_PCT = 6.0
STOP_LOSS_PCT = -4.0

# Market hours in NPT
MARKET_OPEN_HOUR = 11
MARKET_OPEN_MIN = 0
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MIN = 0

# NEPSE trades Sun-Thu (weekday 6=Sun, 0=Mon, 1=Tue, 2=Wed, 3=Thu)
TRADING_DAYS = {6, 0, 1, 2, 3}  # Sun=6, Mon=0, Tue=1, Wed=2, Thu=3


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM MESSAGING
# ═══════════════════════════════════════════════════════════════════════════════

def send_message(text, parse_mode="HTML"):
    """Send a message via Telegram Bot API. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Not configured -- skipping")
        print(f"[TELEGRAM] Would send:\n{text[:300]}")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.ok:
            print("[TELEGRAM] Message sent")
            return True
        else:
            print(f"[TELEGRAM] API error {resp.status_code}: {resp.text[:200]}")
            return False
    except requests.RequestException as e:
        print(f"[TELEGRAM] Network error: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE PRICE FETCHING (mirrors src/realtime.py MeroLagani API)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_live_prices():
    """
    Fetch all stock prices from MeroLagani market_summary API.
    Returns dict: {SYMBOL: {price, change, change_pct, high, low, open, volume}}
    """
    url = "https://merolagani.com/handlers/webrequesthandler.ashx"
    params = {"type": "market_summary"}

    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"[PRICES] Network error: {e}")
        return {}
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[PRICES] JSON parse error: {e}")
        return {}

    prices = {}

    # stock.detail has: s=symbol, lp=last_price, c=change, q=quantity
    stock_detail = []
    if isinstance(data.get("stock"), dict):
        stock_detail = data["stock"].get("detail", [])
    for item in stock_detail:
        sym = item.get("s", "").upper()
        if sym:
            prices[sym] = {"price": _to_float(item.get("lp", 0))}

    # turnover.detail has richer data: s, lp, pc=pct_change, h, l, op, q, t
    turnover_detail = []
    if isinstance(data.get("turnover"), dict):
        turnover_detail = data["turnover"].get("detail", [])
    for item in turnover_detail:
        sym = item.get("s", "").upper()
        if not sym:
            continue
        entry = prices.get(sym, {})
        entry.update({
            "price": _to_float(item.get("lp", entry.get("price", 0))),
            "change_pct": _to_float(item.get("pc", 0)),
            "high": _to_float(item.get("h", 0)),
            "low": _to_float(item.get("l", 0)),
            "open": _to_float(item.get("op", 0)),
            "volume": _to_float(item.get("q", 0)),
            "turnover": _to_float(item.get("t", 0)),
        })
        prices[sym] = entry

    # overall market info
    overall = data.get("overall", {})
    prices["_MARKET"] = {
        "date": overall.get("d", overall.get("date", "")),
        "nepse_index": _to_float(overall.get("index", 0)),
        "turnover": _to_float(overall.get("t", overall.get("turnover", 0))),
        "stocks_traded": _to_float(overall.get("st", overall.get("noOfStocks", 0))),
    }

    print(f"[PRICES] Fetched {len(prices) - 1} stocks")
    return prices


def _to_float(val):
    """Safely convert to float."""
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# PICKS & TARGETS
# ═══════════════════════════════════════════════════════════════════════════════

def load_today_picks():
    """
    Load today's picks from signal_log.json.
    Returns list of dicts with symbol, signal, score, price, targets, etc.
    """
    if not SIGNAL_LOG.exists():
        print(f"[PICKS] signal_log.json not found at {SIGNAL_LOG}")
        return []

    try:
        entries = json.loads(SIGNAL_LOG.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"[PICKS] Error reading signal_log.json: {e}")
        return []

    today_str = now_npt().strftime("%Y-%m-%d")
    today_picks = [e for e in entries if e.get("date") == today_str]

    if not today_picks:
        # Fall back to most recent date in log
        dates = sorted(set(e.get("date", "") for e in entries))
        if dates:
            latest = dates[-1]
            today_picks = [e for e in entries if e.get("date") == latest]
            print(f"[PICKS] No picks for {today_str}, using latest: {latest}")

    return today_picks


def compute_targets(pick, live_price):
    """
    Compute target and stop-loss levels for a pick.
    Uses price_at_signal if available, else falls back to live_price.
    Returns dict with keys: buy_low, buy_high, tp1, tp2, sl
    """
    base = _to_float(pick.get("price_at_signal")) or live_price
    if base <= 0:
        return {}

    # Buy range: +/- 1.5% around signal price
    buy_low = round(base * 0.985, 2)
    buy_high = round(base * 1.015, 2)

    return {
        "base": base,
        "buy_low": buy_low,
        "buy_high": buy_high,
        "tp1": round(base * (1 + TARGET1_PCT / 100), 2),
        "tp2": round(base * (1 + TARGET2_PCT / 100), 2),
        "sl": round(base * (1 + STOP_LOSS_PCT / 100), 2),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ALERT STATE (avoid duplicate alerts)
# ═══════════════════════════════════════════════════════════════════════════════

def load_alert_state():
    """Load today's alert state so we don't spam the same alert twice."""
    if not ALERT_STATE_FILE.exists():
        return {"date": "", "sent": {}}
    try:
        state = json.loads(ALERT_STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"date": "", "sent": {}}

    today = now_npt().strftime("%Y-%m-%d")
    if state.get("date") != today:
        # New day, reset state
        return {"date": today, "sent": {}}
    return state


def save_alert_state(state):
    """Persist alert state to disk."""
    try:
        ALERT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ALERT_STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError as e:
        print(f"[STATE] Error saving alert state: {e}")


def was_sent(state, symbol, alert_type):
    """Check if a specific alert was already sent today."""
    key = f"{symbol}:{alert_type}"
    return key in state.get("sent", {})


def mark_sent(state, symbol, alert_type):
    """Mark an alert as sent."""
    key = f"{symbol}:{alert_type}"
    state.setdefault("sent", {})[key] = now_npt().strftime("%H:%M")


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET TIME HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def now_npt():
    """Current time in Nepal Time."""
    return datetime.now(NPT)


def is_market_hours():
    """Check if current time is within NEPSE trading hours (Sun-Thu 11AM-3PM NPT)."""
    t = now_npt()
    if t.weekday() not in TRADING_DAYS:
        return False
    market_open = t.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0)
    market_close = t.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0)
    return market_open <= t <= market_close


def is_trading_day():
    """Check if today is a NEPSE trading day (Sun-Thu)."""
    return now_npt().weekday() in TRADING_DAYS


# ═══════════════════════════════════════════════════════════════════════════════
# MORNING PICKS MESSAGE
# ═══════════════════════════════════════════════════════════════════════════════

def send_morning_picks(picks, regime=None):
    """Format and send today's scanner picks as a Telegram message."""
    if not picks:
        send_message("No scanner picks found for today.")
        return

    today = now_npt().strftime("%Y-%m-%d")
    regime_str = f" | Regime: {regime}" if regime else ""

    lines = [
        f"<b>NEPSE AutoScan Picks -- {today}</b>{regime_str}",
        f"{len(picks)} signals found\n",
    ]

    for i, p in enumerate(picks, 1):
        sym = p.get("symbol", "???")
        signal = p.get("signal", "?")
        score = p.get("score", 0)
        ta = p.get("ta", p.get("ta_score", 0))
        ml = p.get("ml", p.get("ml_score", 0))
        kelly = p.get("kelly_pct", 0)

        lines.append(
            f"{i}. <b>{sym}</b>  {signal}  "
            f"Score:{score}  TA:{ta}  ML:{ml}  "
            f"Kelly:{kelly}%"
        )

    lines.append(
        f"\nTargets: TP1 +{TARGET1_PCT}% | TP2 +{TARGET2_PCT}% | SL {STOP_LOSS_PCT}%"
    )
    lines.append("Monitoring starts at 11:00 AM NPT")

    send_message("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════════════════
# INTRADAY ALERT CHECKS
# ═══════════════════════════════════════════════════════════════════════════════

def check_alerts(picks, live_prices, state):
    """
    Compare live prices against targets for each pick.
    Sends alerts for: buy range entry, TP1 hit, TP2 hit, stop loss hit.
    """
    alerts_sent = 0

    for pick in picks:
        sym = pick.get("symbol", "")
        if not sym or sym not in live_prices:
            continue

        price_data = live_prices[sym]
        ltp = price_data.get("price", 0)
        if ltp <= 0:
            continue

        targets = compute_targets(pick, ltp)
        if not targets:
            continue

        change_pct = price_data.get("change_pct", 0)

        # -- Stop loss hit --
        if ltp <= targets["sl"] and not was_sent(state, sym, "sl"):
            msg = (
                f"\xf0\x9f\x94\xb4 <b>STOP LOSS HIT: {sym}</b>\n"
                f"Price: NPR {ltp:,.2f} ({change_pct:+.2f}%)\n"
                f"Stop: NPR {targets['sl']:,.2f}\n"
                f"Signal was: {pick.get('signal', '?')} (score {pick.get('score', 0)})\n"
                f"<b>Consider exiting position</b>"
            )
            if send_message(msg):
                mark_sent(state, sym, "sl")
                alerts_sent += 1

        # -- Target 2 hit (check before T1 so both can fire) --
        if ltp >= targets["tp2"] and not was_sent(state, sym, "tp2"):
            msg = (
                f"\xf0\x9f\x9f\xa2 <b>TARGET 2 HIT: {sym}</b>\n"
                f"Price: NPR {ltp:,.2f} ({change_pct:+.2f}%)\n"
                f"TP2: NPR {targets['tp2']:,.2f} (+{TARGET2_PCT}%)\n"
                f"<b>Take profits / trail stop</b>"
            )
            if send_message(msg):
                mark_sent(state, sym, "tp2")
                alerts_sent += 1

        # -- Target 1 hit --
        elif ltp >= targets["tp1"] and not was_sent(state, sym, "tp1"):
            msg = (
                f"\xf0\x9f\x9f\xa1 <b>TARGET 1 HIT: {sym}</b>\n"
                f"Price: NPR {ltp:,.2f} ({change_pct:+.2f}%)\n"
                f"TP1: NPR {targets['tp1']:,.2f} (+{TARGET1_PCT}%)\n"
                f"TP2: NPR {targets['tp2']:,.2f} (+{TARGET2_PCT}%)\n"
                f"<b>Partial profit / hold for TP2</b>"
            )
            if send_message(msg):
                mark_sent(state, sym, "tp1")
                alerts_sent += 1

        # -- Entered buy range --
        if (targets["buy_low"] <= ltp <= targets["buy_high"]
                and not was_sent(state, sym, "buy_range")):
            msg = (
                f"\xf0\x9f\x93\xa5 <b>BUY RANGE: {sym}</b>\n"
                f"Price: NPR {ltp:,.2f} ({change_pct:+.2f}%)\n"
                f"Buy zone: NPR {targets['buy_low']:,.2f} - {targets['buy_high']:,.2f}\n"
                f"Signal: {pick.get('signal', '?')} (score {pick.get('score', 0)})\n"
                f"TP1: {targets['tp1']:,.2f} | TP2: {targets['tp2']:,.2f} | SL: {targets['sl']:,.2f}"
            )
            if send_message(msg):
                mark_sent(state, sym, "buy_range")
                alerts_sent += 1

    return alerts_sent


def check_portfolio_alerts(live_prices, state):
    """
    Check portfolio holdings for key level breaks.
    Alerts when a holding drops below WACC or recovers above it.
    """
    alerts_sent = 0

    for sym, pos in PORTFOLIO.items():
        if sym not in live_prices:
            continue

        ltp = live_prices[sym].get("price", 0)
        if ltp <= 0:
            continue

        wacc = pos.get("wacc", 0)
        if wacc <= 0:
            continue

        change_pct = live_prices[sym].get("change_pct", 0)
        pnl_pct = ((ltp - wacc) / wacc) * 100
        pnl_abs = (ltp - wacc) * pos.get("shares", 0)

        # Alert if price crosses below WACC (breaks even going down)
        if ltp < wacc * 0.99 and not was_sent(state, sym, "below_wacc"):
            msg = (
                f"\xf0\x9f\x94\xbb <b>BELOW WACC: {sym}</b>\n"
                f"Price: NPR {ltp:,.2f} ({change_pct:+.2f}%)\n"
                f"WACC: NPR {wacc:,.2f}\n"
                f"P&L: NPR {pnl_abs:+,.0f} ({pnl_pct:+.2f}%)\n"
                f"Shares: {pos.get('shares', 0):,}"
            )
            if send_message(msg):
                mark_sent(state, sym, "below_wacc")
                alerts_sent += 1

        # Alert if price recovers above WACC (breakeven going up)
        if ltp > wacc * 1.01 and not was_sent(state, sym, "above_wacc"):
            msg = (
                f"\xf0\x9f\x93\x88 <b>ABOVE WACC: {sym}</b>\n"
                f"Price: NPR {ltp:,.2f} ({change_pct:+.2f}%)\n"
                f"WACC: NPR {wacc:,.2f}\n"
                f"P&L: NPR {pnl_abs:+,.0f} ({pnl_pct:+.2f}%)\n"
                f"Shares: {pos.get('shares', 0):,}"
            )
            if send_message(msg):
                mark_sent(state, sym, "above_wacc")
                alerts_sent += 1

        # Alert on large intraday moves (>3% either direction)
        if abs(change_pct) >= 3.0 and not was_sent(state, sym, "big_move"):
            direction = "UP" if change_pct > 0 else "DOWN"
            msg = (
                f"\xf0\x9f\x92\xa5 <b>BIG MOVE {direction}: {sym}</b>\n"
                f"Price: NPR {ltp:,.2f} ({change_pct:+.2f}%)\n"
                f"P&L: NPR {pnl_abs:+,.0f} ({pnl_pct:+.2f}%)"
            )
            if send_message(msg):
                mark_sent(state, sym, "big_move")
                alerts_sent += 1

    return alerts_sent


# ═══════════════════════════════════════════════════════════════════════════════
# END-OF-DAY SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

def send_eod_summary(picks, live_prices):
    """Send end-of-day summary with pick performance and portfolio status."""
    today = now_npt().strftime("%Y-%m-%d")
    lines = [f"<b>NEPSE End-of-Day Summary -- {today}</b>\n"]

    # Market overview
    market = live_prices.get("_MARKET", {})
    if market:
        lines.append(
            f"NEPSE Index: {market.get('nepse_index', 'N/A')} | "
            f"Turnover: NPR {_to_float(market.get('turnover', 0)) / 1e6:.0f}M | "
            f"Stocks: {int(_to_float(market.get('stocks_traded', 0)))}"
        )
        lines.append("")

    # Picks performance
    if picks:
        lines.append("<b>Today's Picks Performance:</b>")
        for pick in picks:
            sym = pick.get("symbol", "")
            price_data = live_prices.get(sym, {})
            ltp = price_data.get("price", 0)
            change_pct = price_data.get("change_pct", 0)
            score = pick.get("score", 0)

            status = ""
            if ltp > 0:
                targets = compute_targets(pick, ltp)
                if targets:
                    if ltp >= targets["tp2"]:
                        status = " -- TP2 HIT"
                    elif ltp >= targets["tp1"]:
                        status = " -- TP1 HIT"
                    elif ltp <= targets["sl"]:
                        status = " -- SL HIT"

            price_str = f"NPR {ltp:,.2f} ({change_pct:+.2f}%)" if ltp > 0 else "N/A"
            lines.append(f"  {sym}: {price_str}  [Score:{score}]{status}")

        lines.append("")

    # Portfolio summary
    if PORTFOLIO:
        lines.append("<b>Portfolio:</b>")
        total_invested = 0
        total_value = 0
        for sym, pos in PORTFOLIO.items():
            price_data = live_prices.get(sym, {})
            ltp = price_data.get("price", 0)
            wacc = pos.get("wacc", 0)
            shares = pos.get("shares", 0)
            invested = pos.get("total_cost", wacc * shares)
            total_invested += invested

            if ltp > 0:
                value = ltp * shares
                total_value += value
                pnl_pct = ((ltp - wacc) / wacc) * 100 if wacc > 0 else 0
                pnl_abs = (ltp - wacc) * shares
                lines.append(
                    f"  {sym}: NPR {ltp:,.2f} | "
                    f"P&L: NPR {pnl_abs:+,.0f} ({pnl_pct:+.2f}%)"
                )
            else:
                lines.append(f"  {sym}: Price unavailable")

        if total_invested > 0 and total_value > 0:
            total_pnl = total_value - total_invested
            total_pnl_pct = (total_pnl / total_invested) * 100
            lines.append(
                f"\n  Total: NPR {total_value:,.0f} | "
                f"P&L: NPR {total_pnl:+,.0f} ({total_pnl_pct:+.2f}%)"
            )

    send_message("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN MONITORING LOOP
# ═══════════════════════════════════════════════════════════════════════════════

def run_intraday_monitor():
    """
    Main loop: monitor prices every CHECK_INTERVAL seconds during market hours.

    Flow:
      1. Load today's picks from signal_log.json
      2. Send morning picks summary
      3. Loop 11:00 AM - 3:00 PM NPT, checking prices every 2 minutes
      4. At market close, send end-of-day summary
    """
    print("=" * 60)
    print("  NEPSE Telegram Intraday Monitor")
    print("=" * 60)
    print(f"  Market hours: {MARKET_OPEN_HOUR}:{MARKET_OPEN_MIN:02d} - "
          f"{MARKET_CLOSE_HOUR}:{MARKET_CLOSE_MIN:02d} NPT (Sun-Thu)")
    print(f"  Check interval: {CHECK_INTERVAL}s")
    print(f"  Targets: TP1 +{TARGET1_PCT}% | TP2 +{TARGET2_PCT}% | SL {STOP_LOSS_PCT}%")
    print(f"  Bot configured: {'YES' if TELEGRAM_BOT_TOKEN else 'NO'}")
    print("=" * 60)

    if not is_trading_day():
        print(f"[{now_npt().strftime('%H:%M')}] Not a trading day (Fri/Sat). Exiting.")
        return

    # Load picks
    picks = load_today_picks()
    if picks:
        print(f"[PICKS] Loaded {len(picks)} picks: "
              f"{', '.join(p.get('symbol', '?') for p in picks)}")
    else:
        print("[PICKS] No picks found -- will monitor portfolio only")

    # Wait for market open
    while not is_market_hours():
        t = now_npt()
        market_open = t.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0)
        if t > t.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0):
            print(f"[{t.strftime('%H:%M')}] Market already closed. Exiting.")
            return
        wait_secs = max(int((market_open - t).total_seconds()), 10)
        # Don't wait more than 5 minutes at a time (allows ctrl+c)
        wait_secs = min(wait_secs, 300)
        print(f"[{t.strftime('%H:%M')}] Waiting for market open... ({wait_secs}s)")
        time.sleep(wait_secs)

    # Send morning summary
    state = load_alert_state()
    state["date"] = now_npt().strftime("%Y-%m-%d")

    if picks and not was_sent(state, "_system", "morning"):
        send_morning_picks(picks)
        mark_sent(state, "_system", "morning")
        save_alert_state(state)

    # Main monitoring loop
    print(f"\n[{now_npt().strftime('%H:%M')}] Market is open -- starting price monitoring\n")
    check_count = 0
    total_alerts = 0

    while is_market_hours():
        check_count += 1
        t = now_npt()
        print(f"[{t.strftime('%H:%M:%S')}] Check #{check_count}...")

        try:
            live_prices = fetch_live_prices()
        except Exception as e:
            print(f"[ERROR] Price fetch failed: {e}")
            time.sleep(CHECK_INTERVAL)
            continue

        if not live_prices:
            print("[WARN] Empty price data, retrying in 30s...")
            time.sleep(30)
            continue

        state = load_alert_state()

        # Check scanner picks
        if picks:
            n = check_alerts(picks, live_prices, state)
            total_alerts += n
            if n:
                print(f"  -> {n} pick alert(s) sent")

        # Check portfolio holdings
        n = check_portfolio_alerts(live_prices, state)
        total_alerts += n
        if n:
            print(f"  -> {n} portfolio alert(s) sent")

        save_alert_state(state)
        time.sleep(CHECK_INTERVAL)

    # End of day
    print(f"\n[{now_npt().strftime('%H:%M')}] Market closed. "
          f"{check_count} checks, {total_alerts} alerts sent.")

    state = load_alert_state()
    if not was_sent(state, "_system", "eod"):
        try:
            live_prices = fetch_live_prices()
            send_eod_summary(picks, live_prices)
            mark_sent(state, "_system", "eod")
            save_alert_state(state)
        except Exception as e:
            print(f"[ERROR] EOD summary failed: {e}")
            traceback.print_exc()

    print("Done.")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    if "--test" in sys.argv:
        t = now_npt().strftime("%Y-%m-%d %H:%M NPT")
        ok = send_message(f"NEPSE AutoScan bot test -- {t}")
        print("Test message sent" if ok else "Test message failed (check token/chat ID)")
        return

    if "--morning" in sys.argv:
        picks = load_today_picks()
        send_morning_picks(picks)
        return

    if "--eod" in sys.argv:
        picks = load_today_picks()
        live_prices = fetch_live_prices()
        send_eod_summary(picks, live_prices)
        return

    run_intraday_monitor()


if __name__ == "__main__":
    main()
