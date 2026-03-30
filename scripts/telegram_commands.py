#!/usr/bin/env python3
"""
Telegram command handler — polls for /picks, /portfolio, /status commands.

Run as: python scripts/telegram_commands.py
Or add to cron to run during market hours alongside telegram_bot.py.
"""
import os
import sys
import json
import re
import time
import requests
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(line_buffering=True)

NPT = timezone(timedelta(hours=5, minutes=45))
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
OFFSET_FILE = ROOT / "data" / "telegram_offset.json"


def send(text):
    if not TOKEN or not CHAT_ID:
        return
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )


def is_excluded(sym):
    sym = sym.upper()
    if re.search(r'D\d{2,4}$', sym): return True
    if re.search(r'B[L]?D\d{2,4}$', sym): return True
    if re.search(r'(MF|GF|SF|EF|LF|BF|PF)\d*$', sym): return True
    if sym.endswith('PO'): return True
    for p in ['NMB50','NICBF','NIBSF','NIBLGF','NIBLSTF','NICSF','NICGF',
              'MBLEF','NMBMF','NMBHF','KEF','SEF','PSF','NFS','LVF']:
        if sym.startswith(p): return True
    return False


def get_top_picks(n=10):
    """Screen and return top N picks from latest data + live prices."""
    hist_dir = ROOT / "data" / "price_history"
    files = sorted(hist_dir.glob("*.json"))[-30:]
    all_stocks = {}
    for f in files:
        d = json.loads(f.read_text())
        for sym, v in d.get("stocks", {}).items():
            all_stocks.setdefault(sym, []).append({**v, "date": f.stem})

    # Overlay live prices on top of historical data
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from live_prices import fetch_live_prices
        live = fetch_live_prices()
        if live:
            today = datetime.now(NPT).strftime("%Y-%m-%d")
            for sym, lp in live.items():
                if sym in all_stocks:
                    # Replace or append today's data
                    if all_stocks[sym][-1].get("date") == today:
                        all_stocks[sym][-1] = {**lp, "date": today}
                    else:
                        all_stocks[sym].append({**lp, "date": today})
    except Exception:
        pass

    results = []
    for sym, recs in all_stocks.items():
        if is_excluded(sym) or len(recs) < 20:
            continue
        closes = [r["lp"] for r in recs if r.get("lp", 0) > 0]
        if len(closes) < 20 or closes[-1] < 50 or closes[-1] > 1500:
            continue

        c = np.array(closes)
        deltas = np.diff(c[-15:])
        ag = np.where(deltas > 0, deltas, 0).mean()
        al = np.where(deltas < 0, -deltas, 0).mean() + 0.001
        rsi = 100 - (100 / (1 + ag / al))
        ret_5d = (c[-1] / c[-6] - 1) * 100 if len(c) >= 6 else 0
        ema8 = c[-8:].mean()
        ema21 = c[-21:].mean() if len(c) >= 21 else c.mean()
        ema_aligned = ema8 > ema21
        vols = [r.get("q", 0) for r in recs[-10:]]
        vol_ratio = recs[-1].get("q", 0) / (np.mean(vols) + 1) if vols else 1

        score = 0
        if 45 <= rsi <= 65: score += 25
        elif 35 <= rsi <= 75: score += 15
        if ema_aligned: score += 20
        if c[-1] > c.mean(): score += 10
        if 1 < ret_5d < 8: score += 15
        elif 0 < ret_5d <= 1: score += 10
        if vol_ratio > 1.2: score += 10
        elif vol_ratio > 0.8: score += 5

        results.append({
            "symbol": sym, "close": c[-1], "rsi": rsi,
            "ret_5d": ret_5d, "vol_ratio": round(vol_ratio, 2), "score": score,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:n]


def handle_picks():
    picks = get_top_picks(10)
    if not picks:
        send("No picks available. Scanner may not have run yet.")
        return

    msg = "<b>📊 NEPSE AutoScan — Top 10 Picks</b>\n"
    msg += "<i>Rs 50-1,500 | No funds/debentures</i>\n\n"

    for i, p in enumerate(picks, 1):
        buy_lo = round(p["close"] * 0.985)
        buy_hi = round(p["close"] * 1.005)
        t1 = round(p["close"] * 1.06)
        t2 = round(p["close"] * 1.12)
        sl = round(p["close"] * 0.95)

        msg += f"<b>{i}. {p['symbol']}</b> — Rs {p['close']:,.0f}\n"
        msg += f"   RSI {p['rsi']:.0f} | 5d {p['ret_5d']:+.1f}% | Vol {p['vol_ratio']}x\n"
        msg += f"   🟢 Buy: {buy_lo}-{buy_hi}\n"
        msg += f"   🎯 T1: {t1} (+6%) | T2: {t2} (+12%)\n"
        msg += f"   🔴 SL: {sl} (-5%)\n\n"

    now = datetime.now(NPT).strftime("%H:%M NPT")
    msg += f"<i>{now} | Not financial advice</i>"
    send(msg)


def handle_portfolio():
    try:
        from portfolio.config import PORTFOLIO
        holdings = PORTFOLIO
    except Exception:
        holdings = [
            {"symbol": "ALICL", "shares": 8046, "wacc": 549.87},
            {"symbol": "TTL",   "shares": 368,  "wacc": 922.92},
            {"symbol": "NLIC",  "shares": 273,  "wacc": 746.84},
            {"symbol": "BPCL",  "shares": 200,  "wacc": 535.18},
            {"symbol": "BARUN", "shares": 400,  "wacc": 391.41},
        ]

    # Use live prices, fall back to historical
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from live_prices import fetch_live_prices
        latest = fetch_live_prices()
    except Exception:
        hist_dir = ROOT / "data" / "price_history"
        latest_file = sorted(hist_dir.glob("*.json"))[-1]
        latest = json.loads(latest_file.read_text()).get("stocks", {})

    msg = "<b>💼 Portfolio Status</b>\n\n"
    total_inv = 0
    total_cur = 0
    for h in holdings:
        sym = h["symbol"]
        ltp = latest.get(sym, {}).get("lp", h["wacc"])
        pnl_pct = (ltp / h["wacc"] - 1) * 100
        pnl_rs = (ltp - h["wacc"]) * h["shares"]
        total_inv += h["wacc"] * h["shares"]
        total_cur += ltp * h["shares"]
        arrow = "🟢" if pnl_rs >= 0 else "🔴"
        msg += f"{arrow} <b>{sym}</b> {h['shares']:,} @ {h['wacc']:.0f}\n"
        msg += f"   LTP: {ltp:.0f} | P&L: {pnl_pct:+.1f}% (Rs {pnl_rs:,.0f})\n\n"

    total_pnl = total_cur - total_inv
    total_pct = (total_cur / total_inv - 1) * 100
    arrow = "🟢" if total_pnl >= 0 else "🔴"
    msg += f"<b>{arrow} Total: {total_pct:+.1f}% (Rs {total_pnl:,.0f})</b>"
    send(msg)


def handle_status():
    now = datetime.now(NPT)
    msg = "<b>⚡ System Status</b>\n\n"
    msg += f"Time: {now.strftime('%Y-%m-%d %H:%M NPT')}\n"

    # Check data freshness
    hist_dir = ROOT / "data" / "price_history"
    latest = sorted(hist_dir.glob("*.json"))[-1].stem if list(hist_dir.glob("*.json")) else "N/A"
    msg += f"Latest data: {latest}\n"

    # Model count
    models = list((ROOT / "data" / "models").glob("*_gru.pt")) if (ROOT / "data" / "models").exists() else []
    msg += f"GRU models: {len(models)}\n"

    # Signal log
    sig_file = ROOT / "data" / "signal_log.json"
    if sig_file.exists():
        sigs = json.loads(sig_file.read_text())
        msg += f"Signals logged: {len(sigs)}\n"

    msg += "\n<b>Commands:</b>\n"
    msg += "/picks — Today's top 10 picks\n"
    msg += "/analyze SYMBOL — AI stock analysis\n"
    msg += "/portfolio — Portfolio P&L\n"
    msg += "/status — System status"
    send(msg)


def handle_analyze(symbol):
    """Fetch live data, compute indicators, get Claude analysis for a stock."""
    if not symbol:
        send("/analyze NABIL -- Get AI analysis for any NEPSE stock\n\n"
             "Usage: /analyze SYMBOL\n"
             "Example: /analyze TTL")
        return

    symbol = symbol.upper().strip()
    if is_excluded(symbol):
        send(f"{symbol} is a fund/debenture -- analysis not available.")
        return

    # Fetch live prices
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from live_prices import fetch_live_prices
        live = fetch_live_prices()
    except Exception as e:
        send(f"Could not fetch live prices: {e}")
        return

    lp_data = live.get(symbol)
    if not lp_data:
        send(f"{symbol} not found in live prices. Check symbol and try again.")
        return

    price = lp_data.get("lp", 0)
    change_pct = lp_data.get("pc", 0)
    volume = lp_data.get("q", 0)

    if price <= 0:
        send(f"{symbol} has no valid price data right now.")
        return

    # Load historical data for indicators
    hist_dir = ROOT / "data" / "price_history"
    files = sorted(hist_dir.glob("*.json"))[-30:]
    closes = []
    volumes = []
    for f in files:
        d = json.loads(f.read_text())
        rec = d.get("stocks", {}).get(symbol)
        if rec and rec.get("lp", 0) > 0:
            closes.append(rec["lp"])
            volumes.append(rec.get("q", 0))

    if len(closes) < 15:
        send(f"{symbol} -- not enough history for analysis (need 15+ days).")
        return

    # Compute indicators
    c = np.array(closes + [price])  # append live price
    deltas = np.diff(c[-15:])
    ag = np.where(deltas > 0, deltas, 0).mean()
    al = np.where(deltas < 0, -deltas, 0).mean() + 0.001
    rsi = 100 - (100 / (1 + ag / al))

    ret_5d = (c[-1] / c[-6] - 1) * 100 if len(c) >= 6 else 0
    ret_20d = (c[-1] / c[-21] - 1) * 100 if len(c) >= 21 else 0

    ema5 = c[-5:].mean()
    ema20 = c[-20:].mean() if len(c) >= 20 else c.mean()
    ema_aligned = ema5 > ema20
    trend = "Bullish" if ema_aligned else "Bearish"

    avg_vol = np.mean(volumes[-10:]) if len(volumes) >= 10 else np.mean(volumes) if volumes else 1
    vol_ratio = round(volume / (avg_vol + 1), 2)

    # Buy range, target, stop loss
    buy_lo = round(price * 0.985)
    buy_hi = round(price * 1.005)
    t1 = round(price * 1.06)
    t2 = round(price * 1.12)
    sl = round(price * 0.95)

    # Claude analysis
    verdict = ""
    try:
        sys.path.insert(0, str(ROOT / "llm"))
        from claude_analyst import _get_client, _call
        client = _get_client()
        if client:
            system = (
                "You are a senior NEPSE stock analyst. Give a brief 2-3 sentence "
                "analysis based on the data provided. Be specific about the setup, "
                "what to watch for, and one key risk. No disclaimers."
            )
            prompt = (
                f"Stock: {symbol}\n"
                f"Price: Rs {price:,.0f} ({change_pct:+.2f}% today)\n"
                f"RSI(14): {rsi:.1f}\n"
                f"5d return: {ret_5d:+.1f}%, 20d return: {ret_20d:+.1f}%\n"
                f"EMA5 vs EMA20: {'aligned (bullish)' if ema_aligned else 'not aligned (bearish)'}\n"
                f"Volume ratio: {vol_ratio}x average\n\n"
                f"Give a 2-3 sentence analysis."
            )
            verdict = _call(client, system, prompt, max_tokens=200)
    except Exception:
        pass

    # Format message
    msg = f"<b>{symbol} -- AI Analysis</b>\n\n"
    msg += f"Price: Rs {price:,.0f} ({change_pct:+.2f}%)\n"
    msg += f"RSI: {rsi:.0f} | Trend: {trend}\n"
    msg += f"5d: {ret_5d:+.1f}% | 20d: {ret_20d:+.1f}%\n"
    msg += f"Volume: {vol_ratio}x avg\n\n"

    if verdict:
        msg += f"<b>Verdict:</b> {verdict}\n\n"

    msg += f"Buy: {buy_lo}-{buy_hi}\n"
    msg += f"T1: {t1} (+6%) | T2: {t2} (+12%)\n"
    msg += f"SL: {sl} (-5%)\n\n"

    now = datetime.now(NPT).strftime("%H:%M NPT")
    msg += f"<i>{now} | Not financial advice</i>"
    send(msg)


def handle_news():
    send("Analyzing latest NEPSE news...")
    try:
        sys.path.insert(0, str(ROOT / "llm"))
        from news_intelligence import run_news_analysis
        analysis = run_news_analysis(regime="BULL", send_telegram=False)
        if analysis:
            from news_intelligence import format_telegram_alert
            msg = format_telegram_alert(analysis)
            send(msg)
        else:
            send("Could not analyze news right now. Try again later.")
    except Exception as e:
        send(f"News analysis error: {e}")


def handle_help():
    msg = "<b>NEPSE AutoScan Bot</b>\n\n"
    msg += "/picks — Top 10 stock picks with buy targets\n"
    msg += "/analyze SYMBOL — AI analysis for any stock\n"
    msg += "/news — AI news analysis with market impact\n"
    msg += "/portfolio — Your portfolio P&L\n"
    msg += "/status — System status\n"
    msg += "/help — This message\n\n"
    msg += "<i>Automatic alerts during market hours (11AM-3PM NPT)</i>"
    send(msg)


def poll_commands():
    """Poll for new commands and respond."""
    # Load last offset
    offset = 0
    if OFFSET_FILE.exists():
        try:
            offset = json.loads(OFFSET_FILE.read_text()).get("offset", 0)
        except Exception:
            pass

    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 1},
            timeout=5,
        )
        updates = resp.json().get("result", [])
    except Exception:
        return

    for update in updates:
        offset = update["update_id"] + 1
        msg = update.get("message", {})
        text = msg.get("text", "").strip().lower()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        if chat_id != CHAT_ID:
            continue

        if text == "/picks" or text == "/start":
            handle_picks()
        elif text.startswith("/analyze"):
            parts = text.split(None, 1)
            sym_arg = parts[1].strip() if len(parts) > 1 else ""
            handle_analyze(sym_arg)
        elif text == "/news":
            handle_news()
        elif text == "/portfolio":
            handle_portfolio()
        elif text == "/status":
            handle_status()
        elif text == "/help":
            handle_help()

    # Save offset
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(json.dumps({"offset": offset}))


if __name__ == "__main__":
    if not TOKEN or not CHAT_ID:
        print("[TELEGRAM] Not configured")
        sys.exit(1)

    if "--once" in sys.argv:
        poll_commands()
    else:
        print("[TELEGRAM] Command handler running...")
        while True:
            poll_commands()
            time.sleep(3)
