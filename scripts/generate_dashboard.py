#!/usr/bin/env python3
"""
Generate the NEPSE AutoScan dashboard for GitHub Pages.

Takes the Stitch-designed template and injects live data from:
- data/price_history/*.json (market data)
- data/paper_portfolio.json (paper trading)
- data/paper_trades.json (trade log)
- data/signal_log.json (signal tracking)
- data/sectors.json (sector mappings)
- portfolio/config.py (personal holdings)

Output: docs/index.html
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(line_buffering=True)

DOCS_DIR = ROOT / "docs"
OUTPUT = DOCS_DIR / "index.html"


def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}


def generate():
    import numpy as np

    hist_dir = ROOT / "data" / "price_history"
    all_files = sorted(hist_dir.glob("*.json"))
    files = all_files[-30:]  # Last 30 days for stock table
    chart_files = [f for f in all_files if f.stem >= '2023-01-01']  # All data from 2023+
    if not files:
        print("[DASHBOARD] No price history files found")
        return

    # ── Market data ───────────────────────────────────────────────────────
    all_stocks = {}
    daily_snapshots = []
    for f in files:
        d = json.loads(f.read_text())
        stocks = d.get("stocks", {})
        daily_snapshots.append({"date": f.stem, "stocks": stocks})
        for sym, v in stocks.items():
            all_stocks.setdefault(sym, []).append({**v, "date": f.stem})

    latest_snap = daily_snapshots[-1]
    latest_date = latest_snap["date"]
    latest = dict(latest_snap["stocks"])  # copy so we don't mutate snapshot
    total_stocks = len(latest)

    # Merge live prices on top of latest historical data
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from live_prices import fetch_live_prices, fetch_market_indices
        live = fetch_live_prices()
        market_indices = fetch_market_indices()
        if live:
            for sym, lp in live.items():
                if sym in latest:
                    latest[sym] = {**latest[sym], **lp}
                else:
                    latest[sym] = lp
            total_stocks = len(latest)
            print(f"[DASHBOARD] Merged {len(live)} live prices into latest data")
        if market_indices:
            nepse_val = market_indices.get('NEPSE', {}).get('value', 0)
            print(f"[DASHBOARD] Indices: NEPSE={nepse_val}")
    except Exception as e:
        print(f"[DASHBOARD] Live price fetch skipped: {e}")
        market_indices = {}

    if not market_indices:
        market_indices = {}

    # Extract index values for display
    nepse_idx = market_indices.get("NEPSE", {})
    nepse_value = nepse_idx.get("value", 0)
    nepse_change = nepse_idx.get("change", 0)
    nepse_turnover = nepse_idx.get("turnover", 0)
    banking_idx = market_indices.get("Banking", {})
    sensitive_idx = market_indices.get("Sensitive", {})
    float_idx = market_indices.get("Float", {})
    sen_float_idx = market_indices.get("Sen. Float", {})

    # Build index cards HTML (MeroLagani style)
    index_cards_html = ""
    for name, idx_data in [
        ("NEPSE", nepse_idx),
        ("Sensitive", sensitive_idx),
        ("Float", float_idx),
        ("Sen. Float", sen_float_idx),
        ("Banking", banking_idx),
    ]:
        val = idx_data.get("value", 0)
        chg = idx_data.get("change", 0)
        turn = idx_data.get("turnover", 0)
        if val == 0:
            continue
        chg_cls = "text-tertiary" if chg >= 0 else "text-error"
        bg_glow = "rgba(0,228,117,0.06)" if chg >= 0 else "rgba(255,82,82,0.06)"
        border_col = "rgba(0,228,117,0.15)" if chg >= 0 else "rgba(255,82,82,0.15)"
        arrow = "trending_up" if chg >= 0 else "trending_down"
        index_cards_html += f'''
        <div class="flex items-center gap-1.5 text-xs font-label">
          <span class="text-outline">{name}</span>
          <span class="font-bold text-on-surface">{val:,.1f}</span>
          <span class="{chg_cls} font-bold">{chg:+.1f}%</span>
        </div>'''

    # Sector data
    sectors_data = load_json(ROOT / "data" / "sectors.json")
    s2s = sectors_data.get("symbol_to_sector", {})
    SECTOR_LABELS = {
        "COMMERCIAL_BANK": "Banking", "DEVELOPMENT_BANK": "Dev Banks",
        "FINANCE": "Finance", "LIFE_INSURANCE": "Life Ins",
        "NONLIFE_INSURANCE": "Non-Life Ins", "HYDROPOWER": "Hydro",
        "MANUFACTURING": "Manu", "HOTEL_TOURISM": "Hotel",
        "MICROFINANCE": "Micro", "MUTUAL_FUND": "Mutual Fund",
        "TRADING": "Trading", "OTHER": "Others",
    }

    # Market stats
    pcs = [s.get("pc", 0) for s in latest.values() if s.get("pc") is not None]
    gainers_today = sum(1 for pc in pcs if pc > 0)
    losers_today = sum(1 for pc in pcs if pc < 0)
    unchanged_today = total_stocks - gainers_today - losers_today
    total_turnover = sum(s.get("t", 0) for s in latest.values())
    avg_change = np.mean(pcs) if pcs else 0
    gainer_pct = int(gainers_today / total_stocks * 100) if total_stocks > 0 else 50
    loser_pct = 100 - gainer_pct
    ad_ratio = round(gainers_today / max(losers_today, 1), 2)

    # Turnover formatting
    if total_turnover > 1e9:
        turnover_str = f"Rs {total_turnover/1e9:.1f}B"
    elif total_turnover > 1e6:
        turnover_str = f"Rs {total_turnover/1e6:.0f}M"
    elif total_turnover > 1e3:
        turnover_str = f"Rs {total_turnover/1e3:.0f}K"
    else:
        turnover_str = f"Rs {total_turnover:,.0f}"

    # Market regime — based on today's breadth, not just avg change
    # This is the DAILY snapshot, not the ML regime (which uses 20-day data)
    if gainers_today > losers_today * 2:
        market_regime = "STRONG BULL"
    elif gainers_today > losers_today * 1.2:
        market_regime = "BULL"
    elif losers_today > gainers_today * 2:
        market_regime = "STRONG BEAR"
    elif losers_today > gainers_today * 1.2:
        market_regime = "BEAR"
    else:
        market_regime = "NEUTRAL"

    # Sector performance
    sector_perf = {}
    for sym, data in latest.items():
        sec = s2s.get(sym, "OTHER")
        sector_perf.setdefault(sec, []).append(data.get("pc", 0))
    sector_avg = {s: round(np.mean(v), 2) for s, v in sector_perf.items() if v}
    sector_sorted = sorted(sector_avg.items(), key=lambda x: x[1], reverse=True)

    # Average turnover across recent days for comparison
    recent_turnovers = []
    for snap in daily_snapshots[-10:]:
        day_turn = sum(s.get("t", 0) for s in snap["stocks"].values())
        recent_turnovers.append(day_turn)
    avg_turnover = np.mean(recent_turnovers) if recent_turnovers else total_turnover
    turnover_vs_avg = (total_turnover / avg_turnover - 1) * 100 if avg_turnover > 0 else 0

    # Breadth (10 days) — compute from consecutive day price comparison
    # (pc field is often 0 in historical data, so we calculate it ourselves)
    breadth_data = []
    for di in range(max(1, len(daily_snapshots) - 10), len(daily_snapshots)):
        snap = daily_snapshots[di]
        prev_snap = daily_snapshots[di - 1]
        stocks = snap["stocks"]
        prev_stocks = prev_snap["stocks"]
        g, l = 0, 0
        for sym, d in stocks.items():
            cur_price = d.get("lp", 0)
            prev_price = prev_stocks.get(sym, {}).get("lp", 0)
            if cur_price > 0 and prev_price > 0:
                if cur_price > prev_price:
                    g += 1
                elif cur_price < prev_price:
                    l += 1
        if g > 0 or l > 0:
            breadth_data.append({"date": snap["date"], "g": g, "l": l})

    # Top movers
    by_change = sorted(latest.items(), key=lambda x: x[1].get("pc", 0), reverse=True)
    top_gainers = by_change[:6]
    top_losers = by_change[-6:][::-1]
    by_turnover = sorted(latest.items(), key=lambda x: x[1].get("t", 0), reverse=True)[:6]

    # ── Paper trading ─────────────────────────────────────────────────────
    paper = load_json(ROOT / "data" / "paper_portfolio.json", {})
    trades = load_json(ROOT / "data" / "paper_trades.json", [])
    signals = load_json(ROOT / "data" / "signal_log.json", [])

    cash = paper.get("cash", 10_000_000)
    positions = paper.get("positions", {})
    equity_curve = paper.get("equity_curve", [])
    initial = 10_000_000
    invested = sum(p["shares"] * p.get("current_price", p["avg_cost"]) for p in positions.values())
    equity = cash + invested
    total_return = (equity / initial - 1) * 100

    closed = [t for t in trades if t.get("return_pct") is not None]
    n_trades = len(closed)
    wins = [t for t in closed if t.get("return_pct", 0) > 0]
    win_rate = len(wins) / n_trades * 100 if n_trades > 0 else 0
    n_wins = len(wins)
    n_losses = n_trades - n_wins

    max_dd = 0
    peak = initial
    for pt in equity_curve:
        eq = pt.get("equity", initial)
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100
        max_dd = max(max_dd, dd)

    evaluated = [s for s in signals if s.get("hit") is not None]
    sig_hit_rate = sum(1 for s in evaluated if s.get("hit")) / len(evaluated) * 100 if evaluated else 0

    # ── Personal portfolio ────────────────────────────────────────────────
    portfolio_holdings = [
        {"symbol": "ALICL", "shares": 8046, "wacc": 549.87},
        {"symbol": "TTL",   "shares": 368,  "wacc": 922.92},
        {"symbol": "NLIC",  "shares": 273,  "wacc": 746.84},
        {"symbol": "BPCL",  "shares": 200,  "wacc": 535.18},
        {"symbol": "BARUN", "shares": 400,  "wacc": 391.41},
    ]

    # ── Build HTML sections ───────────────────────────────────────────────

    # Gainers table
    def gainer_rows(items):
        rows = ""
        for sym, d in items:
            pc = d.get("pc", 0)
            cls = "text-tertiary" if pc >= 0 else "text-error"
            rows += f'''<tr class="group hover:bg-surface-container transition-colors">
                <td class="py-3 font-bold">{sym}</td>
                <td class="py-3 text-right">{d.get("lp",0):,.1f}</td>
                <td class="py-3 text-right {cls} font-bold">{pc:+.2f}%</td>
            </tr>'''
        return rows

    def vol_rows(items):
        rows = ""
        for sym, d in items:
            t = d.get("t", 0)
            if t > 1e9: ts = "Rs %.1fB" % (t/1e9)
            elif t > 1e6: ts = "Rs %.0fM" % (t/1e6)
            else: ts = "Rs %.0fK" % (t/1e3)
            rows += f'''<tr class="group hover:bg-surface-container transition-colors">
                <td class="py-3 font-bold">{sym}</td>
                <td class="py-3 text-right text-primary">{ts}</td>
                <td class="py-3 text-right">{d.get("lp",0):,.1f}</td>
            </tr>'''
        return rows

    # Paper positions
    paper_pos_rows = ""
    for sym, p in sorted(positions.items()):
        cur = p.get("current_price", p["avg_cost"])
        pnl = (cur / p["avg_cost"] - 1) * 100 if p["avg_cost"] > 0 else 0
        cls = "text-tertiary" if pnl >= 0 else "text-error"
        paper_pos_rows += f'''<tr class="hover:bg-surface-container-highest/30 transition-colors">
            <td class="px-6 py-4 font-bold">{sym}</td>
            <td class="px-6 py-4 text-right">{p["avg_cost"]:.0f}</td>
            <td class="px-6 py-4 text-right {cls} font-bold">{pnl:+.1f}%</td>
        </tr>'''
    if not paper_pos_rows:
        paper_pos_rows = '<tr><td colspan="3" class="px-6 py-4 text-outline">No open positions — bot starts trading on next market day</td></tr>'

    # Closed trades
    trade_rows_html = ""
    for t in closed[-5:][::-1]:
        ret = t.get("return_pct", 0)
        cls = "text-tertiary" if ret > 0 else "text-error"
        sign = "+" if ret > 0 else ""
        trade_rows_html += f'''<tr class="hover:bg-surface-container-highest/30 transition-colors">
            <td class="px-6 py-4 font-bold">{t.get("symbol","?")}</td>
            <td class="px-6 py-4 text-right">{t.get("exit_date","?")[-5:]}</td>
            <td class="px-6 py-4 text-right {cls} font-bold">{sign}{ret:.1f}%</td>
        </tr>'''
    if not trade_rows_html:
        trade_rows_html = '<tr><td colspan="3" class="px-6 py-8 text-center"><div class="text-outline text-sm">Paper trading starts on the next market day. The bot will buy/sell based on ML signals and track performance here.</div></td></tr>'

    # Signal log
    recent_sigs = sorted(signals, key=lambda x: x.get("date", ""), reverse=True)[:6]
    sig_rows_html = ""
    for s in recent_sigs:
        hit = s.get("hit")
        if hit is True:
            outcome = '<span class="px-2 py-0.5 border border-tertiary text-tertiary text-[10px] font-bold rounded">HIT</span>'
        elif hit is False:
            outcome = '<span class="px-2 py-0.5 border border-error text-error text-[10px] font-bold rounded">MISS</span>'
        else:
            outcome = '<span class="px-2 py-0.5 border border-outline text-outline text-[10px] font-bold rounded uppercase">Pending</span>'

        signal = s.get("signal", "")
        if "BUY" in signal:
            sig_badge = f'<span class="px-2 py-0.5 bg-tertiary text-on-tertiary text-[10px] font-bold rounded">{signal}</span>'
        elif "SELL" in signal:
            sig_badge = f'<span class="px-2 py-0.5 bg-error text-on-error text-[10px] font-bold rounded">{signal}</span>'
        else:
            sig_badge = f'<span class="px-2 py-0.5 bg-outline text-on-surface text-[10px] font-bold rounded">{signal}</span>'

        ret_str = ""
        if s.get("return_5d_pct") is not None:
            r = s["return_5d_pct"]
            cls = "text-tertiary" if r >= 0 else "text-error"
            ret_str = f'<span class="{cls} font-bold">{r:+.1f}%</span>'

        sig_rows_html += f'''<tr class="hover:bg-surface-container-highest/40 transition-colors">
            <td class="px-8 py-4 text-on-surface-variant">{s.get("date","?")}</td>
            <td class="px-8 py-4 font-bold">{s.get("symbol","?")}</td>
            <td class="px-8 py-4">{sig_badge}</td>
            <td class="px-8 py-4">{s.get("score", 0):.0f}</td>
            <td class="px-8 py-4 text-right">{ret_str}</td>
            <td class="px-8 py-4 text-center">{outcome}</td>
        </tr>'''
    if not sig_rows_html:
        sig_rows_html = '<tr><td colspan="6" class="px-8 py-8 text-center"><div class="text-outline text-sm">Signal tracking starts on the next market day. The scanner will log BUY/SELL signals with confidence scores and track 5-day outcomes here.</div></td></tr>'

    # AI picks — use latest data to find non-overbought momentum stocks
    ai_picks = []
    for sym, recs in all_stocks.items():
        if len(recs) < 15:
            continue
        closes = [r["lp"] for r in recs if r.get("lp", 0) > 0]
        if len(closes) < 15:
            continue
        c = np.array(closes)
        deltas = np.diff(c[-15:])
        gains = np.where(deltas > 0, deltas, 0).mean()
        losses = np.where(deltas < 0, -deltas, 0).mean() + 0.001
        rsi = 100 - (100 / (1 + gains / losses))
        ret_5d = (c[-1] / c[-6] - 1) * 100 if len(c) >= 6 else 0
        vol_avg = np.mean([r.get("q", 0) for r in recs[-10:]])
        vol_ratio = recs[-1].get("q", 0) / vol_avg if vol_avg > 0 else 1
        ema8 = c[-8:].mean()
        ema21 = c[-21:].mean() if len(c) >= 21 else c.mean()

        if 45 <= rsi <= 65 and ema8 > ema21 and 0 < ret_5d < 8 and vol_ratio > 0.8 and c[-1] > 100 and recs[-1].get("q", 0) > 3000:
            score = (65 - abs(rsi - 55)) * 2 + min(ret_5d, 5) * 5 + min(vol_ratio, 3) * 10
            ai_picks.append({
                "symbol": sym, "price": c[-1], "rsi": rsi,
                "ret_5d": ret_5d, "vol_ratio": round(vol_ratio, 1),
                "score": score, "sector": SECTOR_LABELS.get(s2s.get(sym, "OTHER"), ""),
            })
    ai_picks.sort(key=lambda x: x["score"], reverse=True)
    ai_picks = ai_picks[:3]

    # Build AI pick cards
    ai_cards_html = ""
    borders = ["border-primary", "border-secondary", "border-outline"]
    bg_badges = ["bg-primary", "bg-secondary", "bg-outline"]
    txt_badges = ["text-on-primary", "text-on-secondary", "text-on-surface"]
    for i, pick in enumerate(ai_picks):
        t1 = round(pick["price"] * 1.08)
        sl = round(pick["price"] * 0.95)
        ai_cards_html += f'''
        <div class="glass-card rounded-2xl p-8 border-t-2 {borders[i]} relative">
            <div class="absolute -top-3 -right-3 w-10 h-10 {bg_badges[i]} rounded-full flex items-center justify-center font-headline font-black {txt_badges[i]} shadow-lg">#{i+1}</div>
            <div class="flex justify-between items-start mb-6">
                <div>
                    <h3 class="text-3xl font-headline font-black tracking-tighter">{pick["symbol"]}</h3>
                    <span class="text-xs font-label text-outline">{pick["sector"]}</span>
                </div>
                <div class="text-right">
                    <div class="text-xl font-headline font-bold">{pick["price"]:,.0f}</div>
                    <span class="px-2 py-0.5 bg-tertiary/20 text-tertiary text-[10px] font-bold rounded uppercase">RSI {pick["rsi"]:.0f}</span>
                </div>
            </div>
            <div class="grid grid-cols-3 gap-2 mb-6">
                <div class="bg-surface-container-lowest p-2 rounded text-center">
                    <span class="block text-[10px] text-outline uppercase">5D Return</span>
                    <span class="text-sm font-label font-bold text-tertiary">{pick["ret_5d"]:+.1f}%</span>
                </div>
                <div class="bg-surface-container-lowest p-2 rounded text-center">
                    <span class="block text-[10px] text-outline uppercase">Vol Ratio</span>
                    <span class="text-sm font-label font-bold">{pick["vol_ratio"]}x</span>
                </div>
                <div class="bg-surface-container-lowest p-2 rounded text-center">
                    <span class="block text-[10px] text-outline uppercase">AI Score</span>
                    <span class="text-sm font-label font-bold text-primary">{pick["score"]:.0f}</span>
                </div>
            </div>
            <div class="space-y-3">
                <div class="flex justify-between items-center text-sm font-label">
                    <span class="text-outline">Target (+8%)</span>
                    <span class="text-tertiary font-bold">{t1:,}</span>
                </div>
                <div class="flex justify-between items-center text-sm font-label">
                    <span class="text-outline">Stop Loss (-5%)</span>
                    <span class="text-error font-bold">{sl:,}</span>
                </div>
            </div>
        </div>'''
    if not ai_cards_html:
        ai_cards_html = '<div class="glass-card rounded-2xl p-8 col-span-3 text-center text-outline">No stocks currently match AI screening criteria (RSI 45-65, EMA aligned, moderate momentum)</div>'

    # Portfolio rows
    port_rows_html = ""
    port_total_inv = 0
    port_total_cur = 0
    for pos in portfolio_holdings:
        sym = pos["symbol"]
        recs = all_stocks.get(sym, [])
        ltp = recs[-1].get("lp", pos["wacc"]) if recs else pos["wacc"]
        pnl_pct = (ltp / pos["wacc"] - 1) * 100
        pnl_rs = (ltp - pos["wacc"]) * pos["shares"]
        port_total_inv += pos["wacc"] * pos["shares"]
        port_total_cur += ltp * pos["shares"]
        cls = "text-tertiary" if pnl_rs >= 0 else "text-error"
        port_rows_html += f'''<tr class="hover:bg-surface-container-highest/30 transition-colors">
            <td class="px-6 py-3 font-bold">{sym}</td>
            <td class="px-6 py-3 text-right">{pos["shares"]:,}</td>
            <td class="px-6 py-3 text-right">{pos["wacc"]:.0f}</td>
            <td class="px-6 py-3 text-right">{ltp:.0f}</td>
            <td class="px-6 py-3 text-right {cls} font-bold">{pnl_pct:+.1f}%</td>
        </tr>'''

    port_pnl = port_total_cur - port_total_inv
    port_pct = (port_total_cur / port_total_inv - 1) * 100 if port_total_inv > 0 else 0
    port_cls = "text-tertiary" if port_pnl >= 0 else "text-error"

    # Load newsletter HTML — prefer latest scanner report, fall back to sample
    reports_dir = ROOT / "reports"
    scanner_reports = sorted(reports_dir.glob("scanner_*.html")) if reports_dir.exists() else []
    sample_path = DOCS_DIR / "samples" / "sample_report.html"

    if scanner_reports:
        latest_report = scanner_reports[-1]
        newsletter_raw = latest_report.read_text()
        # Keep the sample copy in sync so the static dashboard also works
        sample_path.parent.mkdir(parents=True, exist_ok=True)
        sample_path.write_text(newsletter_raw)
        print(f"[DASHBOARD] Loaded newsletter from {latest_report.name}, copied to sample_report.html")
    elif sample_path.exists():
        newsletter_raw = sample_path.read_text()
    else:
        newsletter_raw = "<html><body><h2>No newsletter generated yet</h2><p>The daily scanner will generate the first report on the next trading day.</p></body></html>"
    # Escape for srcdoc attribute (single quotes)
    newsletter_html_escaped = newsletter_raw.replace("&", "&amp;").replace("'", "&#39;").replace('"', "&quot;")

    # Full stock table data with TA verdict
    all_stock_rows = ""
    stock_list_sorted = sorted(latest.items(), key=lambda x: x[1].get("t", 0), reverse=True)
    for sym, d in stock_list_sorted:
        pc = d.get("pc", 0)
        cls = "text-tertiary" if pc > 0 else ("text-error" if pc < 0 else "text-outline")
        sec = SECTOR_LABELS.get(s2s.get(sym, "OTHER"), "Other")[:12]
        lp = d.get("lp", 0)
        op = d.get("op", 0)
        h = d.get("h", 0)
        l = d.get("l", 0)
        vol = d.get("q", 0)
        t = d.get("t", 0)
        if t > 1e9: ts = "%.1fB" % (t/1e9)
        elif t > 1e6: ts = "%.0fM" % (t/1e6)
        elif t > 1e3: ts = "%.0fK" % (t/1e3)
        else: ts = str(int(t))

        # Compute RSI + TA verdict
        recs = all_stocks.get(sym, [])
        rsi = 50
        ret5 = 0
        ema_ok = False
        verdict = ""
        verdict_cls = "text-outline"
        if len(recs) >= 15:
            closes = [r["lp"] for r in recs if r.get("lp", 0) > 0]
            if len(closes) >= 15:
                c = np.array(closes)
                deltas = np.diff(c[-15:])
                g = np.where(deltas > 0, deltas, 0).mean()
                lo = np.where(deltas < 0, -deltas, 0).mean() + 0.001
                rsi = 100 - (100 / (1 + g / lo))
                ret5 = (c[-1] / c[-6] - 1) * 100 if len(c) >= 6 else 0
                ema_ok = c[-8:].mean() > (c[-21:].mean() if len(c) >= 21 else c.mean())

        # Build verdict
        tags = []
        if rsi > 70:
            tags.append(('<span class="px-1.5 py-0.5 bg-error/15 text-error text-[9px] font-bold rounded">OB</span>', "Overbought"))
            verdict_cls = "text-error"
        elif rsi < 30:
            tags.append(('<span class="px-1.5 py-0.5 bg-tertiary/15 text-tertiary text-[9px] font-bold rounded">OS</span>', "Oversold"))
            verdict_cls = "text-tertiary"

        if ema_ok and 45 <= rsi <= 65 and ret5 > 0:
            tags.append(('<span class="px-1.5 py-0.5 bg-tertiary/15 text-tertiary text-[9px] font-bold rounded">MOMENTUM</span>', ""))
        elif ema_ok and rsi > 50:
            tags.append(('<span class="px-1.5 py-0.5 bg-primary/15 text-primary text-[9px] font-bold rounded">TREND</span>', ""))

        if not ema_ok and rsi > 50 and ret5 < -2:
            tags.append(('<span class="px-1.5 py-0.5 bg-[#ffd740]/15 text-[#ffd740] text-[9px] font-bold rounded">WEAK</span>', ""))

        rsi_cls = "text-tertiary" if 40 <= rsi <= 60 else ("text-error" if rsi > 70 else ("text-[#ffd740]" if rsi > 60 else ("text-tertiary" if rsi < 30 else "text-outline")))
        tag_html = " ".join(t[0] for t in tags) if tags else '<span class="text-outline text-[9px]">--</span>'

        # Sparkline: store close prices as data attribute for lazy rendering
        spark_data = ""
        if len(recs) >= 5:
            closes = [r["lp"] for r in recs[-15:] if r.get("lp", 0) > 0]
            if len(closes) >= 5:
                spark_data = ",".join(str(round(c, 1)) for c in closes)

        spark_attr = f' data-spark="{spark_data}"' if spark_data else ""

        all_stock_rows += f'''<tr class="stock-row hover:bg-[#1a1d28] transition-colors duration-150 text-[12px]" data-sym="{sym.lower()}" data-sec="{sec.lower()}" style="border-bottom:1px solid rgba(66,71,83,0.06)" onclick="toggleDetail(this, '{sym}')">
            <td class="px-4 py-2.5 font-bold text-[13px] text-on-surface">{sym}</td>
            <td class="px-3 py-2.5 text-outline text-[11px] hidden sm:table-cell">{sec}</td>
            <td class="px-3 py-2.5 r font-medium">{lp:,.1f}</td>
            <td class="px-3 py-2.5 r {cls} font-bold">{pc:+.2f}%</td>
            <td class="px-3 py-2.5 hidden sm:table-cell spark-cell"{spark_attr}></td>
            <td class="px-3 py-2.5 r hidden md:table-cell text-on-surface-variant">{op:,.1f}</td>
            <td class="px-3 py-2.5 r hidden md:table-cell text-on-surface-variant">{h:,.1f}</td>
            <td class="px-3 py-2.5 r hidden md:table-cell text-on-surface-variant">{l:,.1f}</td>
            <td class="px-3 py-2.5 r hidden lg:table-cell text-on-surface-variant">{vol:,.0f}</td>
            <td class="px-3 py-2.5 r hidden lg:table-cell text-primary font-medium">{ts}</td>
            <td class="px-3 py-2.5 r {rsi_cls} font-bold">{rsi:.0f}</td>
            <td class="px-3 py-2.5">{tag_html}</td>
        </tr>'''

    # Stock detail data for top 50 by turnover (for expandable rows)
    top50_syms = [sym for sym, _ in stock_list_sorted[:50]]
    stock_detail_data = {}
    for sym in top50_syms:
        recs = all_stocks.get(sym, [])
        if len(recs) < 5:
            continue
        closes = [r["lp"] for r in recs if r.get("lp", 0) > 0]
        dates = [r["date"] for r in recs if r.get("lp", 0) > 0]
        volumes = [r.get("q", 0) for r in recs if r.get("lp", 0) > 0]
        if len(closes) < 5:
            continue
        high_52 = max(closes)
        low_52 = min(closes)
        avg_vol = int(np.mean(volumes)) if volumes else 0
        # TA verdict explanation
        c_arr = np.array(closes)
        rsi_val = 50
        ema_aligned = False
        ret5_val = 0
        if len(c_arr) >= 15:
            deltas = np.diff(c_arr[-15:])
            g = np.where(deltas > 0, deltas, 0).mean()
            lo = np.where(deltas < 0, -deltas, 0).mean() + 0.001
            rsi_val = 100 - (100 / (1 + g / lo))
            ret5_val = (c_arr[-1] / c_arr[-6] - 1) * 100 if len(c_arr) >= 6 else 0
            ema_aligned = float(c_arr[-8:].mean()) > float((c_arr[-21:].mean() if len(c_arr) >= 21 else c_arr.mean()))
        verdict_parts = []
        if rsi_val > 70:
            verdict_parts.append("Overbought (RSI %.0f)" % rsi_val)
        elif rsi_val < 30:
            verdict_parts.append("Oversold (RSI %.0f)" % rsi_val)
        else:
            verdict_parts.append("RSI %.0f (neutral)" % rsi_val)
        if ema_aligned:
            verdict_parts.append("EMA aligned (8 > 21)")
        else:
            verdict_parts.append("EMA not aligned")
        if ret5_val > 0:
            verdict_parts.append("5D return %+.1f%%" % ret5_val)
        else:
            verdict_parts.append("5D return %+.1f%%" % ret5_val)
        stock_detail_data[sym] = {
            "dates": dates[-30:],
            "closes": [round(c, 1) for c in closes[-30:]],
            "high52": round(high_52, 1),
            "low52": round(low_52, 1),
            "avgVol": avg_vol,
            "verdict": ". ".join(verdict_parts),
        }
    stock_detail_json = json.dumps(stock_detail_data, separators=(",", ":"))

    # Price chart data for portfolio + AI picks
    chart_symbols = [p["symbol"] for p in portfolio_holdings] + [p["symbol"] for p in ai_picks[:3]]
    chart_symbols = list(dict.fromkeys(chart_symbols))  # dedup, preserve order
    price_charts_html = ""
    price_chart_js = ""
    for ci, csym in enumerate(chart_symbols[:8]):
        recs = all_stocks.get(csym, [])
        if len(recs) < 5:
            continue
        closes = [r["lp"] for r in recs if r.get("lp", 0) > 0]
        dates = [r["date"][-5:] for r in recs if r.get("lp", 0) > 0]
        if len(closes) < 5:
            continue
        ret = (closes[-1] / closes[0] - 1) * 100
        ret_cls = "text-tertiary" if ret >= 0 else "text-error"
        chart_id = f"priceChart{ci}"

        is_portfolio = csym in [p["symbol"] for p in portfolio_holdings]
        badge = '<span class="px-1.5 py-0.5 bg-primary/15 text-primary text-[9px] font-bold rounded ml-2">PORTFOLIO</span>' if is_portfolio else '<span class="px-1.5 py-0.5 bg-tertiary/15 text-tertiary text-[9px] font-bold rounded ml-2">AI PICK</span>'

        price_charts_html += f'''
        <div class="glass-card rounded-xl p-4">
          <div class="flex justify-between items-center mb-3">
            <div class="flex items-center">
              <span class="font-bold font-headline text-lg">{csym}</span>
              {badge}
            </div>
            <div class="text-right">
              <span class="font-bold">{closes[-1]:,.1f}</span>
              <span class="{ret_cls} text-xs ml-2">{ret:+.1f}%</span>
            </div>
          </div>
          <div style="height:120px"><canvas id="{chart_id}"></canvas></div>
        </div>'''

        color = "#00e475" if ret >= 0 else "#ff5252"
        price_chart_js += f'''
try {{ new Chart(document.getElementById('{chart_id}'),{{
  type:'line',
  data:{{ labels:{json.dumps(dates)}, datasets:[{{
    data:{json.dumps(closes)}, borderColor:'{color}', borderWidth:1.5,
    fill:true, backgroundColor:'{color}11', tension:.3, pointRadius:0
  }}] }},
  options:{{ responsive:true, maintainAspectRatio:false,
    plugins:{{ legend:{{display:false}} }},
    scales:{{ x:{{display:false}}, y:{{display:false}} }}
  }}
}}); }} catch(e) {{ console.warn('Price chart {chart_id} failed:', e); }}
'''

    # OHLCV for TradingView Lightweight Charts + index data
    index_labels = []
    index_values = []
    tv_candles_data = []
    tv_volumes_data = []
    # Use full year of data for the chart
    for cf in chart_files:
        try:
            cdata = json.loads(cf.read_text())
        except Exception:
            continue
        stocks = cdata.get("stocks", {})
        if len(stocks) < 50:
            continue
        prices = [s.get("lp", 0) for s in stocks.values() if s.get("lp", 0) > 0]
        opens_list = [s.get("op", 0) for s in stocks.values() if s.get("op", 0) > 0]
        highs_list = [s.get("h", 0) for s in stocks.values() if s.get("h", 0) > 0]
        lows_list = [s.get("l", 0) for s in stocks.values() if s.get("l", 0) > 0]
        vols_list = [s.get("q", 0) for s in stocks.values()]
        avg_price = float(np.mean(prices))
        date_str = cf.stem  # YYYY-MM-DD from filename

        o = round(float(np.mean(opens_list)), 2) if opens_list else round(avg_price, 2)
        h = round(float(np.mean(highs_list)), 2) if highs_list else round(avg_price, 2)
        l = round(float(np.mean(lows_list)), 2) if lows_list else round(avg_price, 2)
        c = round(avg_price, 2)
        v = int(sum(vols_list))
        tv_candles_data.append({"time": date_str, "open": o, "high": h, "low": l, "close": c})
        color = "rgba(0,228,117,0.5)" if c >= o else "rgba(255,82,82,0.5)"
        tv_volumes_data.append({"time": date_str, "value": v, "color": color})

    tv_candles_json = json.dumps(tv_candles_data)
    tv_volumes_json = json.dumps(tv_volumes_data)

    index_labels_json = json.dumps(index_labels)
    index_values_json = json.dumps(index_values)
    index_ret = ((index_values[-1] / index_values[0]) - 1) * 100 if len(index_values) >= 2 and index_values[0] > 0 else 0
    index_color = "#00e475" if index_ret >= 0 else "#ff5252"

    # Per-stock OHLC data — save as individual JSON files for on-demand loading
    stock_data_dir = DOCS_DIR / "stockdata"
    stock_data_dir.mkdir(parents=True, exist_ok=True)

    stock_files_90 = all_files[-365:]  # 1 year per stock, loaded on demand so size is fine
    all_stock_quarterly = {}
    for cf in stock_files_90:
        try:
            cdata = json.loads(cf.read_text())
        except Exception:
            continue
        for sym, v in cdata.get("stocks", {}).items():
            all_stock_quarterly.setdefault(sym, []).append({**v, "date": cf.stem})

    chartable_syms = sorted([s for s, recs in all_stock_quarterly.items() if len(recs) >= 10])
    for sym in chartable_syms:
        recs = all_stock_quarterly[sym]
        candles = []
        vols = []
        for r in recs:
            lp = r.get("lp", 0)
            if lp <= 0:
                continue
            date = r.get("date", "")
            if not date or len(date) < 8:
                continue
            o = round(float(r.get("op", lp)), 1)
            h = round(float(r.get("h", lp)), 1)
            l = round(float(r.get("l", lp)), 1)
            c = round(float(lp), 1)
            q = int(r.get("q", 0))
            candles.append({"time": date, "open": o, "high": h, "low": l, "close": c})
            color = "rgba(0,228,117,0.5)" if c >= o else "rgba(255,82,82,0.5)"
            vols.append({"time": date, "value": q, "color": color})
        if len(candles) >= 10:
            safe_sym = sym.replace("/", "_")
            (stock_data_dir / f"{safe_sym}.json").write_text(
                json.dumps({"candles": candles, "volumes": vols}))

    # No inline stock data — loaded on demand via fetch()
    stock_chart_json = "{}"
    stock_chart_symbols = json.dumps(["NEPSE Index"] + chartable_syms)
    print(f"[DASHBOARD] Generated {len(chartable_syms)} stock data files in docs/stockdata/")

    # NEPSE Index with technical indicators (from all daily snapshots)
    idx_dates = []
    idx_opens = []
    idx_highs = []
    idx_lows = []
    idx_closes = []
    idx_volumes = []
    for snap in daily_snapshots:
        stocks = snap["stocks"]
        prices = [s.get("lp", 0) for s in stocks.values() if s.get("lp", 0) > 0]
        opens = [s.get("op", 0) for s in stocks.values() if s.get("op", 0) > 0]
        highs = [s.get("h", 0) for s in stocks.values() if s.get("h", 0) > 0]
        lows = [s.get("l", 0) for s in stocks.values() if s.get("l", 0) > 0]
        vols = [s.get("q", 0) for s in stocks.values()]
        if len(prices) > 50:
            idx_dates.append(snap["date"][-5:])
            idx_opens.append(round(np.mean(opens), 2) if opens else round(np.mean(prices), 2))
            idx_highs.append(round(np.mean(highs), 2) if highs else round(np.mean(prices), 2))
            idx_lows.append(round(np.mean(lows), 2) if lows else round(np.mean(prices), 2))
            idx_closes.append(round(np.mean(prices), 2))
            idx_volumes.append(int(sum(vols)))

    c = np.array(idx_closes) if idx_closes else np.array([0])
    n_idx = len(c)

    # SMA 10 and SMA 20
    sma10 = [None] * min(9, n_idx)
    sma20 = [None] * min(19, n_idx)
    for i in range(9, n_idx):
        sma10.append(round(float(c[i-9:i+1].mean()), 2))
    for i in range(19, n_idx):
        sma20.append(round(float(c[i-19:i+1].mean()), 2))

    # RSI 14
    rsi_vals = [None] * min(14, n_idx)
    if n_idx > 14:
        deltas = np.diff(c)
        for i in range(14, n_idx):
            window = deltas[i-14:i]
            ag = np.where(window > 0, window, 0).mean()
            al = np.where(window < 0, -window, 0).mean() + 0.001
            rsi_vals.append(round(float(100 - (100 / (1 + ag / al))), 1))

    # MACD (12, 26, 9)
    def ema_calc(data, period):
        result = [float(data[0])]
        mult = 2 / (period + 1)
        for i in range(1, len(data)):
            result.append(float(data[i]) * mult + result[-1] * (1 - mult))
        return result

    macd_line = [None] * n_idx
    macd_signal = [None] * n_idx
    macd_hist = [None] * n_idx
    if n_idx >= 26:
        ema12 = ema_calc(c, 12)
        ema26 = ema_calc(c, 26)
        ml = [round(ema12[i] - ema26[i], 2) for i in range(n_idx)]
        # Signal line (EMA 9 of MACD)
        valid_macd = ml[25:]  # MACD meaningful after 26 periods
        if len(valid_macd) >= 9:
            sig = ema_calc(valid_macd, 9)
            for i in range(len(valid_macd)):
                idx_pos = 25 + i
                macd_line[idx_pos] = ml[idx_pos]
                macd_signal[idx_pos] = round(sig[i], 2) if i < len(sig) else None
                if macd_line[idx_pos] is not None and macd_signal[idx_pos] is not None:
                    macd_hist[idx_pos] = round(macd_line[idx_pos] - macd_signal[idx_pos], 2)

    # Bollinger Bands (20, 2)
    bb_upper = [None] * n_idx
    bb_lower = [None] * n_idx
    for i in range(19, n_idx):
        window = c[i-19:i+1]
        mean = float(window.mean())
        std = float(window.std())
        bb_upper[i] = round(mean + 2 * std, 2)
        bb_lower[i] = round(mean - 2 * std, 2)

    idx_dates_json = json.dumps(idx_dates)
    idx_opens_json = json.dumps(idx_opens)
    idx_highs_json = json.dumps(idx_highs)
    idx_lows_json = json.dumps(idx_lows)
    idx_closes_json = json.dumps(idx_closes)
    idx_volumes_json = json.dumps(idx_volumes)
    sma10_json = json.dumps(sma10)
    sma20_json = json.dumps(sma20)
    rsi_json = json.dumps(rsi_vals)
    macd_line_json = json.dumps(macd_line)
    macd_signal_json = json.dumps(macd_signal)
    macd_hist_json = json.dumps(macd_hist)
    bb_upper_json = json.dumps(bb_upper)
    bb_lower_json = json.dumps(bb_lower)

    # Chart.js data (for other charts)
    breadth_labels = json.dumps([b["date"][-5:] for b in breadth_data])
    breadth_g = json.dumps([b["g"] for b in breadth_data])
    breadth_l = json.dumps([b["l"] for b in breadth_data])

    sec_labels = json.dumps([SECTOR_LABELS.get(s, s)[:10] for s, _ in sector_sorted[:10]])
    sec_values = json.dumps([v for _, v in sector_sorted[:10]])

    eq_labels = json.dumps([p.get("date", "")[-5:] for p in equity_curve[-30:]])
    eq_values = json.dumps([round(p.get("equity", initial)) for p in equity_curve[-30:]])
    eq_len = len(equity_curve[-30:]) if equity_curve else 1

    now = datetime.now().strftime("%H:%M:%S")
    avg_cls = "text-tertiary" if avg_change >= 0 else "text-error"
    avg_icon = "trending_up" if avg_change >= 0 else "trending_down"
    ret_cls = "text-tertiary" if total_return >= 0 else "text-error"
    turnover_str = "Rs %.1fB" % (total_turnover / 1e9) if total_turnover > 1e9 else "Rs %.0fM" % (total_turnover / 1e6)

    # Paper trading equity section: show informative state when empty
    if n_trades == 0 and total_return == 0:
        paper_equity_section = (
            '<div class="glass-card p-8 rounded-2xl text-center">'
            '<div class="py-8">'
            '<span class="material-symbols-outlined text-4xl text-outline mb-4">hourglass_empty</span>'
            '<p class="text-sm text-on-surface-variant mt-4">'
            'Paper trading starts on the next market day. The bot will buy/sell based on ML signals and track performance here.</p>'
            '<p class="text-xs text-outline mt-2">Starting capital: Rs 10,000,000</p>'
            '</div></div>'
        )
    else:
        paper_equity_section = f'''<div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
    <div class="lg:col-span-2 glass-card p-8 rounded-2xl">
      <h3 class="text-sm font-label uppercase tracking-widest font-bold mb-6">Equity Curve</h3>
      <div style="height:250px"><canvas id="equityChart"></canvas></div>
    </div>
    <div class="glass-card p-8 rounded-2xl flex flex-col items-center justify-center">
      <h3 class="text-sm font-label uppercase tracking-widest font-bold mb-6 self-start">Win / Loss</h3>
      <div class="relative w-48 h-48"><canvas id="winRateChart"></canvas>
        <div class="absolute inset-0 flex flex-col items-center justify-center">
          <span class="text-4xl font-headline font-black">{win_rate:.0f}%</span>
          <span class="text-[10px] text-outline uppercase">{n_trades} trades</span>
        </div>
      </div>
      <div class="flex gap-8 mt-6">
        <div class="text-center"><span class="block text-xs text-outline mb-1">Wins</span><span class="text-tertiary font-bold">{n_wins}</span></div>
        <div class="text-center"><span class="block text-xs text-outline mb-1">Losses</span><span class="text-error font-bold">{n_losses}</span></div>
      </div>
    </div>
  </div>'''

    html = f'''<!DOCTYPE html>
<html class="dark" lang="en"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>NEPSE AutoScan | Live Dashboard</title>
<meta name="description" content="Automated NEPSE stock scanner with ML trading signals, market analysis, and paper trading. 310+ stocks scanned daily.">
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=Space+Grotesk:wght@300;400;500;600;700&display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet"/>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
<script>
tailwind.config = {{
  darkMode: "class",
  theme: {{
    extend: {{
      colors: {{
        "on-secondary":"#32009a","on-surface-variant":"#c2c6d5","surface-container-low":"#191b22",
        "on-tertiary":"#003918","surface-tint":"#acc7ff","background":"#111319",
        "secondary":"#cabeff","on-surface":"#e2e2eb","tertiary":"#00e475",
        "surface-variant":"#33343b","on-error":"#690005","outline":"#8c909e",
        "surface-container":"#1e1f26","surface-dim":"#111319",
        "surface-container-lowest":"#0c0e14","tertiary-container":"#00a754",
        "on-background":"#e2e2eb","secondary-container":"#4918c8",
        "surface-bright":"#373940","primary":"#acc7ff","primary-container":"#4f8ff7",
        "outline-variant":"#424753","on-primary":"#002f67",
        "surface-container-highest":"#33343b","surface-container-high":"#282a30",
        "error":"#ffb4ab","error-container":"#93000a","surface":"#111319"
      }},
      fontFamily: {{ "headline":["Inter"],"body":["Inter"],"label":["Space Grotesk"] }},
    }},
  }},
}}
</script>
<style>
/* Glass morphism cards with subtle glow */
.glass-card {{
  background: rgba(18,21,31,0.65);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid rgba(140,144,158,0.08);
  box-shadow: 0 4px 24px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.03);
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}}
.glass-card:hover {{
  border-color: rgba(79,143,247,0.15);
  box-shadow: 0 8px 32px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.04);
  transform: translateY(-1px);
}}

/* Glow effects */
.text-glow-primary {{ text-shadow: 0 0 20px rgba(79,143,247,0.5), 0 0 40px rgba(79,143,247,0.2); }}
.text-glow-tertiary {{ text-shadow: 0 0 20px rgba(0,228,117,0.5), 0 0 40px rgba(0,228,117,0.2); }}

/* Base */
html {{ background-color: #080a10; }}
body {{ scroll-behavior: smooth; background: transparent; }}

/* Custom scrollbar */
::-webkit-scrollbar {{ width: 5px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{ background: rgba(79,143,247,0.3); border-radius: 10px; }}
::-webkit-scrollbar-thumb:hover {{ background: rgba(79,143,247,0.5); }}

/* Material icons */
.material-symbols-outlined {{ font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24; vertical-align: middle; }}

/* Select dropdown dark theme */
select {{ background-color: #0c0e14 !important; color: #e2e2eb !important; }}
select option {{ background-color: #0c0e14; color: #e2e2eb; padding: 8px; }}
select option:hover {{ background-color: #1e1f26; }}
select:focus {{ border-color: rgba(79,143,247,0.4) !important; box-shadow: 0 0 0 2px rgba(79,143,247,0.15); }}

/* 3D Background */
#bg-3d {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; border: none; }}

/* Tab navigation */
.tab-btn {{
  cursor: pointer; padding: 6px 16px; font-size: 13px; font-weight: 600;
  border-radius: 8px; transition: all 0.25s ease;
  border: 1px solid transparent;
}}
.tab-btn.active {{
  background: rgba(79,143,247,0.15); color: #4f8ff7;
  border-color: rgba(79,143,247,0.3);
  box-shadow: 0 0 12px rgba(79,143,247,0.15);
}}
.tab-btn:not(.active) {{ color: #8c909e; }}
.tab-btn:not(.active):hover {{ color: #c2c6d5; background: rgba(255,255,255,0.04); }}

/* Content tabs */
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; animation: fadeIn 0.3s ease; }}

/* Animations */
@keyframes fadeIn {{
  from {{ opacity: 0; transform: translateY(8px); }}
  to {{ opacity: 1; transform: translateY(0); }}
}}
@keyframes slideUp {{
  from {{ opacity: 0; transform: translateY(20px); }}
  to {{ opacity: 1; transform: translateY(0); }}
}}
@keyframes pulseGlow {{
  0%, 100% {{ box-shadow: 0 0 8px rgba(79,143,247,0.2); }}
  50% {{ box-shadow: 0 0 16px rgba(79,143,247,0.4); }}
}}

/* Section animations */
section {{ animation: slideUp 0.4s ease; }}

/* Newsletter */
.newsletter-frame {{ width: 100%; border: none; border-radius: 12px; background: #fff; min-height: 600px; }}

/* Stock table */
.overflow-table {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
.stock-row {{
  cursor: pointer;
  transition: background 0.15s ease;
}}
.stock-row:hover {{
  background: rgba(79,143,247,0.06) !important;
}}
.detail-row td {{ border-top: none !important; }}

/* Indicator buttons */
.ind-btn {{
  transition: all 0.2s ease;
}}
.ind-btn.active {{
  border-color: rgba(79,143,247,0.5) !important;
  background: rgba(79,143,247,0.12) !important;
  color: #acc7ff;
  box-shadow: 0 0 8px rgba(79,143,247,0.15);
}}

/* Stat cards number animation */
.stat-value {{
  transition: color 0.3s ease;
}}

/* Positive/negative pill badges */
.pill-pos {{ background: rgba(0,228,117,0.12); color: #00e475; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }}
.pill-neg {{ background: rgba(255,82,82,0.12); color: #ff5252; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }}
.pill-neutral {{ background: rgba(255,215,64,0.12); color: #ffd740; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }}

/* Chart containers */
canvas {{ border-radius: 8px; }}

/* Z-indexing */
header, main, footer, .tab-content {{ position: relative; z-index: 10; pointer-events: auto; }}

/* Mobile responsive */
@media (max-width: 768px) {{
  .newsletter-frame {{ min-height: 500px; }}
  #bg-3d {{ opacity: 0.08; }}
  .glass-card:hover {{ transform: none; }}
  section {{ animation: none; }}
}}

/* Gradient borders for featured cards */
.gradient-border {{
  position: relative;
}}
.gradient-border::before {{
  content: '';
  position: absolute;
  inset: -1px;
  border-radius: inherit;
  background: linear-gradient(135deg, rgba(79,143,247,0.3), rgba(124,92,252,0.3), rgba(0,228,117,0.3));
  z-index: -1;
  opacity: 0;
  transition: opacity 0.3s ease;
}}
.gradient-border:hover::before {{
  opacity: 1;
}}
</style>
</head>
<body class="text-on-surface font-body selection:bg-primary-container selection:text-on-primary-container">

<iframe id="bg-3d" src="3d-bg.html" loading="eager" title="3D Background" allow="accelerometer"></iframe>
<script>
// Forward mouse events to the 3D iframe (same-origin)
(function(){{
  const iframe = document.getElementById('bg-3d');
  if (!iframe) return;
  iframe.addEventListener('load', function() {{
    document.addEventListener('mousemove', function(e) {{
      try {{
        const iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
        const evt = new MouseEvent('mousemove', {{
          clientX: e.clientX, clientY: e.clientY,
          screenX: e.screenX, screenY: e.screenY,
          bubbles: true, cancelable: true,
        }});
        iframeDoc.dispatchEvent(evt);
        if (iframe.contentWindow) {{
          iframe.contentWindow.dispatchEvent(evt);
        }}
      }} catch(ex) {{}}
    }});
  }});
}})();
</script>

<header class="bg-[#111319]/80 backdrop-blur-xl sticky top-0 z-50 shadow-[0_20px_40px_rgba(0,0,0,0.4)]">
<nav class="flex justify-between items-center w-full px-6 py-4 max-w-[1920px] mx-auto">
  <div class="flex items-center gap-8">
    <span class="text-xl font-black bg-clip-text text-transparent bg-gradient-to-r from-[#4f8ff7] to-[#cabeff] font-headline">NEPSE AutoScan</span>
    <div class="hidden md:flex gap-6 items-center">
      <div class="flex items-center gap-2 px-3 py-1 bg-surface-container-lowest rounded-full border border-outline-variant/20">
        <span class="flex h-2 w-2 rounded-full bg-tertiary animate-pulse"></span>
        <span class="text-[10px] font-label uppercase tracking-widest text-on-surface-variant">{latest_date}</span>
        <span class="text-[10px] font-label text-outline ml-2">{total_stocks} stocks</span>
      </div>
    </div>
  </div>
  <div class="flex items-center gap-4">
    <!-- Tab Navigation (desktop) -->
    <div class="hidden md:flex gap-2 font-label">
      <button class="tab-btn active" onclick="switchTab('dashboard',this)">
        <span class="material-symbols-outlined text-sm mr-1">dashboard</span> Dashboard
      </button>
      <button class="tab-btn" onclick="switchTab('newsletter',this)">
        <span class="material-symbols-outlined text-sm mr-1">mail</span> Newsletter
      </button>
    </div>
    <a class="hidden md:inline-block px-4 py-2 bg-primary-container text-on-primary font-label text-sm font-bold rounded-lg hover:brightness-125 transition-all" href="https://github.com/padam56/nepse-autoscan">GitHub</a>
  </div>
</nav>
<!-- Mobile tab bar -->
<div class="md:hidden flex justify-center gap-2 px-4 py-2 border-t border-outline-variant/10 font-label">
  <button class="tab-btn active flex-1 text-center" onclick="switchTab('dashboard',this)">
    <span class="material-symbols-outlined text-sm">dashboard</span> Dashboard
  </button>
  <button class="tab-btn flex-1 text-center" onclick="switchTab('newsletter',this)">
    <span class="material-symbols-outlined text-sm">mail</span> Newsletter
  </button>
  <a class="tab-btn flex-1 text-center text-primary" href="https://github.com/padam56/nepse-autoscan">
    <span class="material-symbols-outlined text-sm">code</span> GitHub
  </a>
</div>
</header>

<!-- Dashboard Tab -->
<div id="tab-dashboard" class="tab-content active">
<main class="max-w-[1920px] mx-auto px-4 pt-2 pb-24 space-y-8 relative z-10">

<!-- CHART FIRST — No scrolling needed -->
<section>
  <div style="background:rgba(8,10,16,0.9);backdrop-filter:blur(20px);border:1px solid rgba(66,71,83,0.1);border-radius:16px;overflow:hidden">

    <!-- Index ticker strip inside chart card -->
    <div class="flex flex-wrap items-center gap-3 px-4 py-2 border-b border-outline-variant/5" style="background:rgba(12,14,20,0.6)">
      {index_cards_html}
      <div style="width:1px;height:24px;background:rgba(66,71,83,0.15);margin:0 4px" class="hidden lg:block"></div>
      <div class="flex items-center gap-1.5 text-xs font-label">
        <span class="text-tertiary font-bold">{gainers_today}</span>
        <div class="w-12 h-1 rounded-full flex overflow-hidden" style="background:rgba(66,71,83,0.2)">
          <div style="width:{gainer_pct}%;background:#00e475;height:100%"></div>
          <div style="width:{loser_pct}%;background:#ff5252;height:100%"></div>
        </div>
        <span class="text-error font-bold">{losers_today}</span>
        <span class="text-outline ml-2">{total_stocks} stocks</span>
        <span class="text-outline">|</span>
        <span class="text-primary font-bold">{turnover_str}</span>
        <span class="text-outline">|</span>
        <span class="font-bold {'text-tertiary' if 'BULL' in market_regime else ('text-error' if 'BEAR' in market_regime else 'text-[#ffd740]')}">{market_regime}</span>
      </div>
    </div>

    <!-- Chart header -->
    <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2 px-5 pt-3 pb-1">
      <div class="flex items-center gap-3">
        <select id="chart-symbol-select" onchange="switchChartSymbol(this.value)" style="min-width:180px;background:#0c0e14;color:#e2e2eb;border:1px solid rgba(66,71,83,0.2);border-radius:8px;padding:6px 12px;font-size:16px;font-weight:700;font-family:Inter,sans-serif;cursor:pointer;outline:none;appearance:auto">
          <option value="NEPSE Index" style="background:#0c0e14;color:#e2e2eb">NEPSE Index</option>
        </select>
        <div>
          <span id="tv-price" class="text-2xl font-headline font-bold {'text-tertiary' if nepse_change >= 0 else 'text-error'}">{nepse_value:,.2f}</span>
          <span id="tv-change" class="text-sm {'text-tertiary' if nepse_change >= 0 else 'text-error'} font-bold ml-1">{nepse_change:+.2f}%</span>
        </div>
      </div>
      <div id="tv-ohlc-legend" class="text-xs font-label text-outline"></div>
    </div>

    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-1.5 px-5 py-2 border-y border-outline-variant/5" style="background:rgba(15,17,25,0.5)">
      <!-- Chart type -->
      <div class="flex rounded-md overflow-hidden border border-outline-variant/15" style="font-size:11px">
        <button onclick="setChartType('candle')" id="ct-candle" class="ct-btn px-2.5 py-1 font-label font-semibold text-[#4f8ff7]" style="background:rgba(79,143,247,0.12)">
          <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle">candlestick_chart</span> Candle
        </button>
        <button onclick="setChartType('line')" id="ct-line" class="ct-btn px-2.5 py-1 font-label font-semibold text-outline" style="border-left:1px solid rgba(66,71,83,0.15)">
          <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle">show_chart</span> Line
        </button>
        <button onclick="setChartType('area')" id="ct-area" class="ct-btn px-2.5 py-1 font-label font-semibold text-outline" style="border-left:1px solid rgba(66,71,83,0.15)">
          <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle">area_chart</span> Area
        </button>
      </div>

      <div style="width:1px;height:20px;background:rgba(66,71,83,0.15);margin:0 4px"></div>

      <!-- Time range -->
      <div class="flex gap-1" style="font-size:10px">
        <button onclick="setRange(30)" class="range-btn px-2 py-1 rounded font-label font-semibold text-outline hover:text-on-surface transition-colors">1M</button>
        <button onclick="setRange(90)" class="range-btn px-2 py-1 rounded font-label font-semibold text-[#4f8ff7]" style="background:rgba(79,143,247,0.1)">3M</button>
        <button onclick="setRange(180)" class="range-btn px-2 py-1 rounded font-label font-semibold text-outline hover:text-on-surface transition-colors">6M</button>
        <button onclick="setRange(365)" class="range-btn px-2 py-1 rounded font-label font-semibold text-outline hover:text-on-surface transition-colors">1Y</button>
        <button onclick="setRange(0)" class="range-btn px-2 py-1 rounded font-label font-semibold text-outline hover:text-on-surface transition-colors">ALL</button>
      </div>

      <div style="width:1px;height:20px;background:rgba(66,71,83,0.15);margin:0 4px"></div>

      <!-- Indicators -->
      <div class="flex flex-wrap gap-1" style="font-size:10px">
        <button onclick="toggleInd('sma')" id="ind-sma" class="ind-toggle px-2 py-1 rounded font-label font-semibold text-[#ffd740]" style="background:rgba(255,215,64,0.1)">SMA</button>
        <button onclick="toggleInd('ema')" id="ind-ema" class="ind-toggle px-2 py-1 rounded font-label font-semibold text-outline">EMA</button>
        <button onclick="toggleInd('bb')" id="ind-bb" class="ind-toggle px-2 py-1 rounded font-label font-semibold text-outline">BB</button>
        <button onclick="toggleInd('vol')" id="ind-vol" class="ind-toggle px-2 py-1 rounded font-label font-semibold text-[#4f8ff7]" style="background:rgba(79,143,247,0.1)">VOL</button>
        <button onclick="toggleInd('rsi')" id="ind-rsi" class="ind-toggle px-2 py-1 rounded font-label font-semibold text-outline">RSI</button>
        <button onclick="toggleInd('macd')" id="ind-macd" class="ind-toggle px-2 py-1 rounded font-label font-semibold text-outline">MACD</button>
      </div>
    </div>

    <!-- Main chart -->
    <div id="tv-chart" style="height:450px;width:100%"></div>
    <!-- RSI panel (hidden by default) -->
    <div id="rsi-panel" style="height:0;overflow:hidden;transition:height 0.3s ease"></div>
    <!-- MACD panel (hidden by default) -->
    <div id="macd-panel" style="height:0;overflow:hidden;transition:height 0.3s ease"></div>
  </div>
  <!-- Market Stats + Sector Performance side by side -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
    <div class="glass-card p-6 rounded-2xl">
      <h3 class="text-lg font-headline font-bold mb-4">Market Indices</h3>
      <div class="space-y-2 text-sm font-label">
        <div class="flex justify-between items-center py-2 border-b border-outline-variant/10">
          <span class="text-on-surface-variant">NEPSE</span>
          <span class="font-bold">{nepse_value:,.2f} <span class="{'text-tertiary' if nepse_change >= 0 else 'text-error'} text-xs">{nepse_change:+.2f}%</span></span>
        </div>
        <div class="flex justify-between items-center py-2 border-b border-outline-variant/10">
          <span class="text-on-surface-variant">Banking</span>
          <span class="font-bold">{banking_idx.get('value', 0):,.2f} <span class="{'text-tertiary' if banking_idx.get('change', 0) >= 0 else 'text-error'} text-xs">{banking_idx.get('change', 0):+.2f}%</span></span>
        </div>
        <div class="flex justify-between items-center py-2 border-b border-outline-variant/10">
          <span class="text-on-surface-variant">Sensitive</span>
          <span class="font-bold">{sensitive_idx.get('value', 0):,.2f} <span class="{'text-tertiary' if sensitive_idx.get('change', 0) >= 0 else 'text-error'} text-xs">{sensitive_idx.get('change', 0):+.2f}%</span></span>
        </div>
        <div class="flex justify-between items-center py-2 border-b border-outline-variant/10">
          <span class="text-on-surface-variant">Float</span>
          <span class="font-bold">{float_idx.get('value', 0):,.2f} <span class="{'text-tertiary' if float_idx.get('change', 0) >= 0 else 'text-error'} text-xs">{float_idx.get('change', 0):+.2f}%</span></span>
        </div>
        <div class="flex justify-between items-center py-2 border-b border-outline-variant/10">
          <span class="text-on-surface-variant">Advancing / Declining</span>
          <span class="font-bold"><span class="text-tertiary">{gainers_today}</span> / <span class="text-error">{losers_today}</span></span>
        </div>
        <div class="flex justify-between items-center py-2 border-b border-outline-variant/10">
          <span class="text-on-surface-variant">Turnover</span>
          <span class="text-primary font-bold">Rs {nepse_turnover/1e9:.1f}B</span>
        </div>
        <div class="flex justify-between items-center py-2">
          <span class="text-on-surface-variant">Market</span>
          <span class="font-bold {'text-tertiary' if 'BULL' in market_regime else ('text-error' if 'BEAR' in market_regime else 'text-[#ffd740]')}">{market_regime}</span>
        </div>
      </div>
    </div>
    <div class="glass-card p-6 rounded-2xl">
      <h3 class="text-lg font-headline font-bold mb-4">Sector Performance <span class="text-sm font-normal text-outline ml-2">(% Change)</span></h3>
      <div style="height:280px"><canvas id="sectorChart"></canvas></div>
    </div>
  </div>
</section>

<!-- Top Movers -->
<section class="grid grid-cols-1 xl:grid-cols-3 gap-6">
  <div class="bg-surface-container-low rounded-xl p-6 border-t border-tertiary/20">
    <div class="flex items-center gap-2 mb-6">
      <span class="material-symbols-outlined text-tertiary">trending_up</span>
      <h3 class="text-sm font-label uppercase tracking-widest font-bold">Top Gainers</h3>
    </div>
    <table class="w-full text-left text-sm font-label">
      <thead class="text-outline border-b border-outline-variant/10">
        <tr><th class="pb-3">Symbol</th><th class="pb-3 text-right">Price</th><th class="pb-3 text-right">Change%</th></tr>
      </thead>
      <tbody class="divide-y divide-outline-variant/5">{gainer_rows(top_gainers)}</tbody>
    </table>
  </div>
  <div class="bg-surface-container-low rounded-xl p-6 border-t border-error/20">
    <div class="flex items-center gap-2 mb-6">
      <span class="material-symbols-outlined text-error">trending_down</span>
      <h3 class="text-sm font-label uppercase tracking-widest font-bold">Top Losers</h3>
    </div>
    <table class="w-full text-left text-sm font-label">
      <thead class="text-outline border-b border-outline-variant/10">
        <tr><th class="pb-3">Symbol</th><th class="pb-3 text-right">Price</th><th class="pb-3 text-right">Change%</th></tr>
      </thead>
      <tbody class="divide-y divide-outline-variant/5">{gainer_rows(top_losers)}</tbody>
    </table>
  </div>
  <div class="bg-surface-container-low rounded-xl p-6 border-t border-primary/20">
    <div class="flex items-center gap-2 mb-6">
      <span class="material-symbols-outlined text-primary">bar_chart</span>
      <h3 class="text-sm font-label uppercase tracking-widest font-bold">Volume Leaders</h3>
    </div>
    <table class="w-full text-left text-sm font-label">
      <thead class="text-outline border-b border-outline-variant/10">
        <tr><th class="pb-3">Symbol</th><th class="pb-3 text-right">Turnover</th><th class="pb-3 text-right">LTP</th></tr>
      </thead>
      <tbody class="divide-y divide-outline-variant/5">{vol_rows(by_turnover)}</tbody>
    </table>
  </div>
</section>

<!-- AI Picks -->
<section class="space-y-6">
  <div>
    <h2 class="text-2xl font-headline font-black">AI Top 3 Picks</h2>
    <p class="text-on-surface-variant text-sm font-label">RSI 45-65, EMA aligned, moderate momentum, volume confirmed</p>
  </div>
  <div class="grid grid-cols-1 md:grid-cols-3 gap-6">{ai_cards_html}</div>
</section>

<!-- Market Index + Price Charts -->
<section class="space-y-6">
  <div class="flex items-center justify-between">
    <div>
      <h2 class="text-2xl font-headline font-black">Market Pulse</h2>
      <p class="text-on-surface-variant text-sm font-label">30-day average price trend across {total_stocks} stocks</p>
    </div>
    <span class="{('text-tertiary' if index_ret >= 0 else 'text-error')} font-bold font-headline text-xl">{index_ret:+.1f}%</span>
  </div>
  <div class="glass-card rounded-2xl p-6">
    <div style="height:180px"><canvas id="indexChart"></canvas></div>
  </div>
</section>

<section class="space-y-6">
  <div>
    <h2 class="text-2xl font-headline font-black">Price Charts</h2>
    <p class="text-on-surface-variant text-sm font-label">Portfolio holdings + AI picks — 30-day price action</p>
  </div>
  <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
    {price_charts_html}
  </div>
</section>

<!-- Paper Trading -->
<section class="space-y-8">
  <div class="flex flex-col md:flex-row justify-between items-start md:items-end gap-4">
    <div>
      <h2 class="text-2xl font-headline font-black">Paper Trading Portfolio</h2>
      <p class="text-on-surface-variant text-sm font-label">Starting Capital: <span class="text-white font-bold">Rs 10,000,000</span></p>
    </div>
    <div class="flex gap-4">
      <div class="text-right">
        <p class="text-[10px] font-label text-outline uppercase tracking-widest">Net Value</p>
        <p class="text-xl font-headline font-bold {ret_cls}">Rs {equity:,.0f}</p>
      </div>
      <div class="w-px h-10 bg-outline-variant/20"></div>
      <div class="text-right">
        <p class="text-[10px] font-label text-outline uppercase tracking-widest">Win Rate</p>
        <p class="text-xl font-headline font-bold">{win_rate:.0f}%</p>
      </div>
    </div>
  </div>
  {paper_equity_section}
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
    <div class="bg-surface-container rounded-xl overflow-hidden">
      <div class="px-6 py-4 bg-surface-container-high/40 border-b border-outline-variant/10">
        <h3 class="text-xs font-label uppercase tracking-widest font-bold">Open Positions</h3>
      </div>
      <table class="w-full text-left text-sm font-label">
        <thead class="text-outline bg-surface-container-low/50">
          <tr><th class="px-6 py-3">Symbol</th><th class="px-6 py-3 text-right">Avg Cost</th><th class="px-6 py-3 text-right">P&L</th></tr>
        </thead>
        <tbody class="divide-y divide-outline-variant/10">{paper_pos_rows}</tbody>
      </table>
    </div>
    <div class="bg-surface-container rounded-xl overflow-hidden">
      <div class="px-6 py-4 bg-surface-container-high/40 border-b border-outline-variant/10">
        <h3 class="text-xs font-label uppercase tracking-widest font-bold">Recent Trades</h3>
      </div>
      <table class="w-full text-left text-sm font-label">
        <thead class="text-outline bg-surface-container-low/50">
          <tr><th class="px-6 py-3">Symbol</th><th class="px-6 py-3 text-right">Date</th><th class="px-6 py-3 text-right">Return</th></tr>
        </thead>
        <tbody class="divide-y divide-outline-variant/10">{trade_rows_html}</tbody>
      </table>
    </div>
  </div>
</section>

<!-- Signal Log -->
<section class="space-y-6">
  <h2 class="text-2xl font-headline font-black">Signal Tracking Log</h2>
  <div class="bg-surface-container rounded-2xl overflow-hidden overflow-x-auto">
    <table class="w-full text-left text-sm font-label min-w-[800px]">
      <thead class="text-outline border-b border-outline-variant/20 bg-surface-container-high">
        <tr><th class="px-8 py-4">Date</th><th class="px-8 py-4">Symbol</th><th class="px-8 py-4">Signal</th><th class="px-8 py-4">Score</th><th class="px-8 py-4 text-right">5D Return</th><th class="px-8 py-4 text-center">Outcome</th></tr>
      </thead>
      <tbody class="divide-y divide-outline-variant/10">{sig_rows_html}</tbody>
    </table>
  </div>
</section>

<!-- All Stocks -->
<section class="space-y-4">
  <div class="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
    <div>
      <h2 class="text-2xl font-headline font-black">All Stocks <span class="text-primary">({total_stocks})</span></h2>
      <p class="text-on-surface-variant text-sm font-label">Complete market data &middot; {latest_date}</p>
    </div>
    <div class="flex gap-2 w-full md:w-auto">
      <div class="relative flex-1 md:flex-none">
        <span class="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-outline text-sm">search</span>
        <input id="stockSearch" type="text" placeholder="Search..."
          class="pl-9 pr-3 py-2 bg-[#0f1119] border border-outline-variant/15 rounded-lg text-sm font-label text-on-surface placeholder:text-outline/50 focus:outline-none focus:border-primary-container/50 focus:ring-1 focus:ring-primary-container/20 w-full md:w-44 transition-all"
          oninput="filterStocks()">
      </div>
      <select id="sectorFilter" onchange="filterStocks()"
        class="px-3 py-2 bg-[#0f1119] border border-outline-variant/15 rounded-lg text-sm font-label text-on-surface focus:outline-none focus:border-primary-container/50 appearance-none cursor-pointer">
        <option value="">All Sectors</option>
        <option value="banking">Banking</option>
        <option value="hydro">Hydro</option>
        <option value="life ins">Life Ins</option>
        <option value="dev banks">Dev Banks</option>
        <option value="finance">Finance</option>
        <option value="micro">Micro</option>
        <option value="manu">Manu</option>
        <option value="hotel">Hotel</option>
        <option value="mutual">Mutual Fund</option>
        <option value="non-life">Non-Life Ins</option>
        <option value="other">Other</option>
      </select>
      <div class="hidden md:flex items-center gap-1 text-[10px] font-label text-outline bg-[#0f1119] border border-outline-variant/15 rounded-lg px-3">
        <span id="stockCount">{total_stocks}</span> stocks
      </div>
    </div>
  </div>
  <div style="background:rgba(15,17,25,0.85);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid rgba(66,71,83,0.12);border-radius:16px;overflow:hidden">
    <div class="overflow-x-auto" style="max-height:560px;overflow-y:auto" id="stockScroller">
      <table class="w-full text-left font-label" id="stockTable" style="border-collapse:separate;border-spacing:0">
        <thead style="position:sticky;top:0;z-index:2">
          <tr style="background:rgba(15,17,25,0.95);backdrop-filter:blur(8px)">
            <th class="px-4 py-3 text-[10px] uppercase tracking-wider text-outline font-semibold cursor-pointer hover:text-primary transition-colors border-b border-outline-variant/10" onclick="sortStocks('sym')">
              <span class="flex items-center gap-1">Symbol <span class="material-symbols-outlined text-[10px]">unfold_more</span></span></th>
            <th class="px-3 py-3 text-[10px] uppercase tracking-wider text-outline font-semibold hidden sm:table-cell border-b border-outline-variant/10">Sector</th>
            <th class="px-3 py-3 text-[10px] uppercase tracking-wider text-outline font-semibold r cursor-pointer hover:text-primary transition-colors border-b border-outline-variant/10" onclick="sortStocks('ltp')">
              <span class="flex items-center justify-end gap-1">LTP <span class="material-symbols-outlined text-[10px]">unfold_more</span></span></th>
            <th class="px-3 py-3 text-[10px] uppercase tracking-wider text-outline font-semibold r cursor-pointer hover:text-primary transition-colors border-b border-outline-variant/10" onclick="sortStocks('chg')">
              <span class="flex items-center justify-end gap-1">Chg% <span class="material-symbols-outlined text-[10px]">unfold_more</span></span></th>
            <th class="px-3 py-3 text-[10px] uppercase tracking-wider text-outline font-semibold hidden sm:table-cell border-b border-outline-variant/10">15d</th>
            <th class="px-3 py-3 text-[10px] uppercase tracking-wider text-outline font-semibold r hidden md:table-cell border-b border-outline-variant/10">Open</th>
            <th class="px-3 py-3 text-[10px] uppercase tracking-wider text-outline font-semibold r hidden md:table-cell border-b border-outline-variant/10">High</th>
            <th class="px-3 py-3 text-[10px] uppercase tracking-wider text-outline font-semibold r hidden md:table-cell border-b border-outline-variant/10">Low</th>
            <th class="px-3 py-3 text-[10px] uppercase tracking-wider text-outline font-semibold r hidden lg:table-cell cursor-pointer hover:text-primary transition-colors border-b border-outline-variant/10" onclick="sortStocks('vol')">Vol</th>
            <th class="px-3 py-3 text-[10px] uppercase tracking-wider text-outline font-semibold r hidden lg:table-cell cursor-pointer hover:text-primary transition-colors border-b border-outline-variant/10" onclick="sortStocks('turn')">Turnover</th>
            <th class="px-3 py-3 text-[10px] uppercase tracking-wider text-outline font-semibold r cursor-pointer hover:text-primary transition-colors border-b border-outline-variant/10" onclick="sortStocks('rsi')">
              <span class="flex items-center justify-end gap-1">RSI <span class="material-symbols-outlined text-[10px]">unfold_more</span></span></th>
            <th class="px-3 py-3 text-[10px] uppercase tracking-wider text-outline font-semibold border-b border-outline-variant/10">Signal</th>
          </tr>
        </thead>
        <tbody id="stockBody">
          {all_stock_rows}
        </tbody>
      </table>
    </div>
    <div class="flex items-center justify-between px-4 py-2.5 border-t border-outline-variant/8 text-[10px] font-label" style="background:rgba(15,17,25,0.9)">
      <span class="text-outline">Showing <span class="text-on-surface-variant font-semibold" id="stockCountBottom">{total_stocks}</span> of {total_stocks} stocks</span>
      <span class="text-outline hidden sm:inline">Click headers to sort &middot; Click row to expand &middot; Type to search</span>
    </div>
  </div>
</section>

<!-- My Portfolio -->
<section>
  <h2 class="text-2xl font-headline font-black mb-6">My Portfolio</h2>
  <div class="bg-surface-container rounded-2xl overflow-hidden">
    <table class="w-full text-left text-sm font-label">
      <thead class="text-outline border-b border-outline-variant/20 bg-surface-container-high">
        <tr><th class="px-6 py-4">Symbol</th><th class="px-6 py-4 text-right">Shares</th><th class="px-6 py-4 text-right">WACC</th><th class="px-6 py-4 text-right">LTP</th><th class="px-6 py-4 text-right">P&L%</th></tr>
      </thead>
      <tbody class="divide-y divide-outline-variant/10">{port_rows_html}</tbody>
      <tfoot>
        <tr class="font-bold border-t-2 border-outline-variant/20">
          <td class="px-6 py-4" colspan="3">Total Portfolio</td>
          <td class="px-6 py-4 text-right">Rs {port_total_cur:,.0f}</td>
          <td class="px-6 py-4 text-right {port_cls}">{port_pct:+.1f}%</td>
        </tr>
      </tfoot>
    </table>
  </div>
</section>

<!-- How It Works -->
<section class="grid grid-cols-1 md:grid-cols-3 gap-8 pt-8">
  <div class="p-8 rounded-2xl bg-surface-container-low border-l-2 border-primary/40">
    <span class="material-symbols-outlined text-primary text-4xl mb-6">database</span>
    <h3 class="text-xl font-headline font-bold mb-4">Data Collection</h3>
    <p class="text-sm text-on-surface-variant leading-relaxed">Scrapes 310+ stocks daily from Sharesansar. 56 features per stock including technicals, NEPSE calendar effects, and sector dynamics.</p>
  </div>
  <div class="p-8 rounded-2xl bg-surface-container-low border-l-2 border-secondary/40">
    <span class="material-symbols-outlined text-secondary text-4xl mb-6">psychology</span>
    <h3 class="text-xl font-headline font-bold mb-4">ML Analysis</h3>
    <p class="text-sm text-on-surface-variant leading-relaxed">XGBoost + LightGBM ensemble, 310 GRU models, Temporal Transformer, regime detection. All retrained daily on NVIDIA Titan RTX.</p>
  </div>
  <div class="p-8 rounded-2xl bg-surface-container-low border-l-2 border-tertiary/40">
    <span class="material-symbols-outlined text-tertiary text-4xl mb-6">security</span>
    <h3 class="text-xl font-headline font-bold mb-4">Risk Controls</h3>
    <p class="text-sm text-on-surface-variant leading-relaxed">Kelly sizing, sector cap (max 3), drawdown brake, signal tracking with closed-loop feedback. Calibrated predictions via isotonic regression.</p>
  </div>
</section>

</main>
</div><!-- end dashboard tab -->

<!-- Newsletter Tab -->
<div id="tab-newsletter" class="tab-content">
<div class="max-w-[800px] mx-auto px-6 pt-12 pb-24 relative z-10">
  <div class="flex items-center justify-between mb-8">
    <div>
      <h2 class="text-2xl font-headline font-black text-on-surface">Daily Newsletter</h2>
      <p class="text-sm font-label text-outline mt-1">The same report that lands in your inbox every trading morning</p>
    </div>
    <span class="px-3 py-1 bg-surface-container-highest border border-outline-variant/20 rounded-full text-xs font-label text-on-surface-variant">{latest_date}</span>
  </div>
  <div class="glass-card rounded-2xl overflow-hidden p-1">
    <iframe class="newsletter-frame" srcdoc='{newsletter_html_escaped}'></iframe>
  </div>
</div>
</div><!-- end newsletter tab -->

<footer class="bg-[#0b0d13]/80 backdrop-blur-md border-t border-[#1e2233]/15 relative z-10">
<div class="w-full px-8 py-12 flex flex-col md:flex-row justify-between items-center gap-4 max-w-[1920px] mx-auto">
  <div class="space-y-2 text-center md:text-left">
    <span class="text-sm font-bold text-[#c2c6d5]">NEPSE AutoScan</span>
    <p class="text-[#c2c6d5] text-xs italic">Paper trading only. Not financial advice. Built for Nepal's retail investor community.</p>
  </div>
  <div class="flex gap-8 items-center">
    <a class="text-[#c2c6d5] font-label uppercase tracking-widest text-[10px] hover:text-[#4f8ff7] transition-colors" href="#">Dashboard</a>
    <a class="text-[#c2c6d5] font-label uppercase tracking-widest text-[10px] hover:text-[#4f8ff7] transition-colors" href="https://github.com/padam56/nepse-autoscan">GitHub</a>
    <a class="text-[#c2c6d5] font-label uppercase tracking-widest text-[10px] hover:text-[#4f8ff7] transition-colors" href="https://github.com/padam56/nepse-autoscan/blob/main/PERFORMANCE.md">Performance</a>
    <a class="text-[#c2c6d5] font-label uppercase tracking-widest text-[10px] hover:text-[#4f8ff7] transition-colors" href="https://github.com/padam56/nepse-autoscan/blob/main/README.md#disclaimer">Disclaimer</a>
  </div>
</div>
</footer>

<script>
// Stock detail data for expandable rows (top 50 by turnover)
const stockData = {stock_detail_json};
</script>
<script>
Chart.defaults.color='#8c909e';
Chart.defaults.font.family='Space Grotesk';

// TradingView Lightweight Charts — Full Trading Terminal
try {{
  const tvData = {tv_candles_json};
  const tvVols = {tv_volumes_json};
  const stockChartData = {stock_chart_json};
  const chartSymbols = {stock_chart_symbols};
  const tvContainer = document.getElementById('tv-chart');

  // Populate symbol dropdown
  const symSelect = document.getElementById('chart-symbol-select');
  if (symSelect) {{
    chartSymbols.forEach(sym => {{
      if (sym === 'NEPSE Index') return; // already added
      const opt = document.createElement('option');
      opt.value = sym; opt.textContent = sym;
      opt.style.background = '#0c0e14'; opt.style.color = '#e2e2eb';
      symSelect.appendChild(opt);
    }});
  }}

  // Current active data
  let activeData = tvData;
  let activeVols = tvVols;

  // Compute indicators from candle data
  function calcSMA(data, period) {{
    return data.map((d, i, arr) => {{
      if (i < period - 1) return null;
      const sum = arr.slice(i - period + 1, i + 1).reduce((s, c) => s + c.close, 0);
      return {{ time: d.time, value: Math.round(sum / period * 100) / 100 }};
    }}).filter(d => d !== null);
  }}
  function calcEMA(data, period) {{
    const mult = 2 / (period + 1);
    let ema = data[0].close;
    return data.map((d, i) => {{
      if (i === 0) return {{ time: d.time, value: Math.round(d.close * 100) / 100 }};
      ema = d.close * mult + ema * (1 - mult);
      return {{ time: d.time, value: Math.round(ema * 100) / 100 }};
    }});
  }}
  function calcRSI(data, period) {{
    const result = [];
    for (let i = 0; i < data.length; i++) {{
      if (i < period) {{ result.push(null); continue; }}
      let gains = 0, losses = 0;
      for (let j = i - period + 1; j <= i; j++) {{
        const diff = data[j].close - data[j-1].close;
        if (diff > 0) gains += diff; else losses -= diff;
      }}
      const rs = gains / (losses + 0.001);
      result.push({{ time: data[i].time, value: Math.round((100 - 100 / (1 + rs)) * 10) / 10 }});
    }}
    return result.filter(d => d !== null);
  }}
  function calcMACD(data) {{
    const ema12 = []; const ema26 = [];
    let e12 = data[0].close, e26 = data[0].close;
    const m12 = 2/13, m26 = 2/27;
    data.forEach(d => {{
      e12 = d.close * m12 + e12 * (1 - m12); ema12.push(e12);
      e26 = d.close * m26 + e26 * (1 - m26); ema26.push(e26);
    }});
    const macdLine = ema12.map((v, i) => v - ema26[i]);
    let sig = macdLine[0]; const m9 = 2/10;
    const signal = macdLine.map(v => {{ sig = v * m9 + sig * (1 - m9); return sig; }});
    return {{
      macd: data.map((d, i) => ({{ time: d.time, value: Math.round(macdLine[i] * 100) / 100 }})),
      signal: data.map((d, i) => ({{ time: d.time, value: Math.round(signal[i] * 100) / 100 }})),
      hist: data.map((d, i) => {{
        const v = Math.round((macdLine[i] - signal[i]) * 100) / 100;
        return {{ time: d.time, value: v, color: v >= 0 ? 'rgba(0,228,117,0.6)' : 'rgba(255,82,82,0.6)' }};
      }}),
    }};
  }}
  function calcBB(data, period) {{
    return data.map((d, i, arr) => {{
      if (i < period - 1) return null;
      const slice = arr.slice(i - period + 1, i + 1).map(c => c.close);
      const mean = slice.reduce((a, b) => a + b) / period;
      const std = Math.sqrt(slice.reduce((s, v) => s + (v - mean) ** 2, 0) / period);
      return {{ time: d.time, upper: Math.round((mean + 2 * std) * 100) / 100, lower: Math.round((mean - 2 * std) * 100) / 100 }};
    }}).filter(d => d !== null);
  }}

  // Pre-compute all indicators
  const sma10Data = calcSMA(tvData, 10);
  const sma20Data = calcSMA(tvData, 20);
  const ema12Data = calcEMA(tvData, 12);
  const ema26Data = calcEMA(tvData, 26);
  const bbData = calcBB(tvData, 20);
  const rsiData = calcRSI(tvData, 14);
  const macdResult = calcMACD(tvData);

  // State
  let currentType = 'candle';
  let mainSeries = null;
  let indicators = {{
    sma: {{ active: true, series: [] }},
    ema: {{ active: false, series: [] }},
    bb: {{ active: false, series: [] }},
    vol: {{ active: true, series: null }},
    rsi: {{ active: false, chart: null }},
    macd: {{ active: false, chart: null }},
  }};

  // Create main chart
  const chartOpts = {{
    width: tvContainer.clientWidth,
    height: 450,
    layout: {{ background: {{ type:'solid', color:'transparent' }}, textColor:'#8c909e', fontFamily:'Space Grotesk,Inter,sans-serif' }},
    grid: {{ vertLines:{{ color:'rgba(66,71,83,0.04)' }}, horzLines:{{ color:'rgba(66,71,83,0.04)' }} }},
    crosshair: {{ mode:LightweightCharts.CrosshairMode.Normal, vertLine:{{ color:'rgba(79,143,247,0.3)',width:1,style:2 }}, horzLine:{{ color:'rgba(79,143,247,0.3)',width:1,style:2 }} }},
    rightPriceScale: {{ borderColor:'rgba(66,71,83,0.08)', scaleMargins:{{ top:0.05, bottom:0.2 }} }},
    timeScale: {{ borderColor:'rgba(66,71,83,0.08)', timeVisible:false, dayVisible:true }},
    handleScroll: {{ vertTouchDrag:false }},
  }};
  const chart = LightweightCharts.createChart(tvContainer, chartOpts);

  // Main series — start with candlestick
  mainSeries = chart.addCandlestickSeries({{ upColor:'#00e475', downColor:'#ff5252', borderUpColor:'#00e475', borderDownColor:'#ff5252', wickUpColor:'#00e475', wickDownColor:'#ff5252' }});
  mainSeries.setData(tvData);

  // SMA overlays (on by default)
  indicators.sma.series.push(chart.addLineSeries({{ color:'#ffd740', lineWidth:1, lineStyle:2, lastValueVisible:false, priceLineVisible:false }}));
  indicators.sma.series[0].setData(sma10Data);
  indicators.sma.series.push(chart.addLineSeries({{ color:'#ff9800', lineWidth:1, lineStyle:2, lastValueVisible:false, priceLineVisible:false }}));
  indicators.sma.series[1].setData(sma20Data);

  // Volume (on by default)
  indicators.vol.series = chart.addHistogramSeries({{ priceFormat:{{ type:'volume' }}, priceScaleId:'vol' }});
  chart.priceScale('vol').applyOptions({{ scaleMargins:{{ top:0.82, bottom:0 }} }});
  indicators.vol.series.setData(tvVols);

  // Set initial view — last 90 days
  if (tvData.length > 90) {{
    chart.timeScale().setVisibleRange({{ from: tvData[tvData.length - 90].time, to: tvData[tvData.length - 1].time }});
  }}

  // Responsive
  new ResizeObserver(entries => {{
    for (const e of entries) chart.applyOptions({{ width: e.contentRect.width }});
  }}).observe(tvContainer);

  // OHLC legend on crosshair
  const ohlcLegend = document.getElementById('tv-ohlc-legend');
  chart.subscribeCrosshairMove(param => {{
    if (!param.time || !param.seriesData) {{ ohlcLegend.innerHTML = ''; return; }}
    const c = param.seriesData.get(mainSeries);
    if (c && c.open !== undefined) {{
      const col = c.close >= c.open ? '#00e475' : '#ff5252';
      ohlcLegend.innerHTML = '<span style="color:'+col+'">O: '+c.open.toFixed(1)+' &nbsp;H: '+c.high.toFixed(1)+' &nbsp;L: '+c.low.toFixed(1)+' &nbsp;C: '+c.close.toFixed(1)+'</span>';
    }} else if (c && c.value !== undefined) {{
      ohlcLegend.innerHTML = '<span style="color:#4f8ff7">Close: '+c.value.toFixed(1)+'</span>';
    }}
  }});

  // Chart type switcher
  window.setChartType = function(type) {{
    currentType = type;
    chart.removeSeries(mainSeries);
    if (type === 'candle') {{
      mainSeries = chart.addCandlestickSeries({{ upColor:'#00e475', downColor:'#ff5252', borderUpColor:'#00e475', borderDownColor:'#ff5252', wickUpColor:'#00e475', wickDownColor:'#ff5252' }});
      mainSeries.setData(tvData);
    }} else if (type === 'line') {{
      mainSeries = chart.addLineSeries({{ color:'#4f8ff7', lineWidth:2, lastValueVisible:true, priceLineVisible:true }});
      mainSeries.setData(tvData.map(d => ({{ time:d.time, value:d.close }})));
    }} else if (type === 'area') {{
      mainSeries = chart.addAreaSeries({{ topColor:'rgba(79,143,247,0.3)', bottomColor:'rgba(79,143,247,0)', lineColor:'#4f8ff7', lineWidth:2 }});
      mainSeries.setData(tvData.map(d => ({{ time:d.time, value:d.close }})));
    }}
    // Update button styles
    document.querySelectorAll('.ct-btn').forEach(b => {{ b.style.background=''; b.style.color='#8c909e'; }});
    const btn = document.getElementById('ct-'+type);
    if (btn) {{ btn.style.background='rgba(79,143,247,0.12)'; btn.style.color='#4f8ff7'; }}
  }};

  // Time range selector
  // Symbol switcher
  // Cache fetched stock data
  const stockCache = {{}};

  window.switchChartSymbol = async function(sym) {{
    // Remove current overlays
    ['sma','ema','bb','vol'].forEach(id => {{
      const ind = indicators[id];
      if (ind.active) {{
        if (ind.series && Array.isArray(ind.series)) {{ ind.series.forEach(s => chart.removeSeries(s)); ind.series = []; }}
        else if (ind.series) {{ chart.removeSeries(ind.series); ind.series = null; }}
      }}
    }});
    // Remove main series
    chart.removeSeries(mainSeries);

    if (sym === 'NEPSE Index') {{
      activeData = tvData; activeVols = tvVols;
    }} else {{
      // Fetch on demand if not cached
      if (!stockCache[sym]) {{
        try {{
          const safeSym = sym.replace('/', '_');
          const resp = await fetch('stockdata/' + safeSym + '.json');
          if (!resp.ok) throw new Error('Not found');
          stockCache[sym] = await resp.json();
        }} catch(e) {{
          console.warn('Failed to load', sym, e);
          // Revert to NEPSE Index
          document.getElementById('chart-symbol-select').value = 'NEPSE Index';
          activeData = tvData; activeVols = tvVols;
          return switchChartSymbol('NEPSE Index');
        }}
      }}
      activeData = stockCache[sym].candles;
      activeVols = stockCache[sym].volumes;
    }}

    // Recompute indicators for new data
    const newSma10 = calcSMA(activeData, 10);
    const newSma20 = calcSMA(activeData, 20);

    // Recreate main series
    if (currentType === 'candle') {{
      mainSeries = chart.addCandlestickSeries({{ upColor:'#00e475', downColor:'#ff5252', borderUpColor:'#00e475', borderDownColor:'#ff5252', wickUpColor:'#00e475', wickDownColor:'#ff5252' }});
      mainSeries.setData(activeData);
    }} else if (currentType === 'line') {{
      mainSeries = chart.addLineSeries({{ color:'#4f8ff7', lineWidth:2 }});
      mainSeries.setData(activeData.map(d => ({{ time:d.time, value:d.close }})));
    }} else {{
      mainSeries = chart.addAreaSeries({{ topColor:'rgba(79,143,247,0.3)', bottomColor:'rgba(79,143,247,0)', lineColor:'#4f8ff7', lineWidth:2 }});
      mainSeries.setData(activeData.map(d => ({{ time:d.time, value:d.close }})));
    }}

    // Re-add active overlays
    if (indicators.sma.active) {{
      indicators.sma.series.push(chart.addLineSeries({{ color:'#ffd740',lineWidth:1,lineStyle:2,lastValueVisible:false,priceLineVisible:false }}));
      indicators.sma.series[0].setData(newSma10);
      indicators.sma.series.push(chart.addLineSeries({{ color:'#ff9800',lineWidth:1,lineStyle:2,lastValueVisible:false,priceLineVisible:false }}));
      indicators.sma.series[1].setData(newSma20);
    }}
    if (indicators.vol.active) {{
      indicators.vol.series = chart.addHistogramSeries({{ priceFormat:{{ type:'volume' }}, priceScaleId:'vol' }});
      chart.priceScale('vol').applyOptions({{ scaleMargins:{{ top:0.82, bottom:0 }} }});
      indicators.vol.series.setData(activeVols);
    }}

    // Update price display
    const last = activeData[activeData.length - 1];
    const prev = activeData.length > 1 ? activeData[activeData.length - 2] : last;
    const chg = ((last.close / prev.close) - 1) * 100;
    const col = chg >= 0 ? '#00e475' : '#ff5252';
    document.getElementById('tv-price').innerHTML = last.close.toLocaleString(undefined, {{ minimumFractionDigits:1 }});
    document.getElementById('tv-price').style.color = col;
    document.getElementById('tv-change').innerHTML = (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%';
    document.getElementById('tv-change').style.color = col;

    // Set view to last 90 days
    if (activeData.length > 90) {{
      chart.timeScale().setVisibleRange({{ from: activeData[activeData.length - 90].time, to: activeData[activeData.length - 1].time }});
    }} else {{ chart.timeScale().fitContent(); }}
  }};

  window.setRange = function(days) {{
    document.querySelectorAll('.range-btn').forEach(b => {{ b.style.background=''; b.style.color='#8c909e'; }});
    event.target.style.background='rgba(79,143,247,0.1)'; event.target.style.color='#4f8ff7';
    if (days === 0) {{ chart.timeScale().fitContent(); return; }}
    if (activeData.length > days) {{
      chart.timeScale().setVisibleRange({{ from: activeData[activeData.length - days].time, to: activeData[activeData.length - 1].time }});
    }} else {{ chart.timeScale().fitContent(); }}
  }};

  // Indicator toggle
  window.toggleInd = function(id) {{
    const btn = document.getElementById('ind-'+id);
    const ind = indicators[id];
    if (!ind) return;
    ind.active = !ind.active;
    // Update button style
    if (ind.active) {{
      const colors = {{ sma:'#ffd740', ema:'#00bcd4', bb:'#cabeff', vol:'#4f8ff7', rsi:'#ff9800', macd:'#00e475' }};
      btn.style.background='rgba(79,143,247,0.1)'; btn.style.color=colors[id]||'#4f8ff7';
    }} else {{
      btn.style.background=''; btn.style.color='#8c909e';
    }}

    if (id === 'sma') {{
      if (ind.active && ind.series.length === 0) {{
        ind.series.push(chart.addLineSeries({{ color:'#ffd740',lineWidth:1,lineStyle:2,lastValueVisible:false,priceLineVisible:false }}));
        ind.series[0].setData(sma10Data);
        ind.series.push(chart.addLineSeries({{ color:'#ff9800',lineWidth:1,lineStyle:2,lastValueVisible:false,priceLineVisible:false }}));
        ind.series[1].setData(sma20Data);
      }} else if (!ind.active) {{ ind.series.forEach(s => chart.removeSeries(s)); ind.series = []; }}
    }}
    if (id === 'ema') {{
      if (ind.active && ind.series.length === 0) {{
        ind.series.push(chart.addLineSeries({{ color:'#00bcd4',lineWidth:1,lastValueVisible:false,priceLineVisible:false }}));
        ind.series[0].setData(ema12Data);
        ind.series.push(chart.addLineSeries({{ color:'#e040fb',lineWidth:1,lastValueVisible:false,priceLineVisible:false }}));
        ind.series[1].setData(ema26Data);
      }} else if (!ind.active) {{ ind.series.forEach(s => chart.removeSeries(s)); ind.series = []; }}
    }}
    if (id === 'bb') {{
      if (ind.active && ind.series.length === 0) {{
        ind.series.push(chart.addLineSeries({{ color:'rgba(202,190,255,0.5)',lineWidth:1,lastValueVisible:false,priceLineVisible:false }}));
        ind.series[0].setData(bbData.map(d => ({{ time:d.time, value:d.upper }})));
        ind.series.push(chart.addLineSeries({{ color:'rgba(202,190,255,0.5)',lineWidth:1,lastValueVisible:false,priceLineVisible:false }}));
        ind.series[1].setData(bbData.map(d => ({{ time:d.time, value:d.lower }})));
      }} else if (!ind.active) {{ ind.series.forEach(s => chart.removeSeries(s)); ind.series = []; }}
    }}
    if (id === 'vol') {{
      if (ind.active && !ind.series) {{
        ind.series = chart.addHistogramSeries({{ priceFormat:{{ type:'volume' }}, priceScaleId:'vol' }});
        chart.priceScale('vol').applyOptions({{ scaleMargins:{{ top:0.82, bottom:0 }} }});
        ind.series.setData(tvVols);
      }} else if (!ind.active && ind.series) {{ chart.removeSeries(ind.series); ind.series = null; }}
    }}
    if (id === 'rsi') {{
      const panel = document.getElementById('rsi-panel');
      if (ind.active) {{
        panel.style.height = '120px';
        if (!ind.chart) {{
          ind.chart = LightweightCharts.createChart(panel, {{
            width:tvContainer.clientWidth, height:120,
            layout:{{ background:{{ type:'solid',color:'transparent' }}, textColor:'#8c909e', fontFamily:'Space Grotesk' }},
            grid:{{ vertLines:{{ color:'rgba(66,71,83,0.03)' }}, horzLines:{{ color:'rgba(66,71,83,0.03)' }} }},
            rightPriceScale:{{ borderColor:'rgba(66,71,83,0.08)' }},
            timeScale:{{ visible:false }},
          }});
          const rsiSeries = ind.chart.addLineSeries({{ color:'#ff9800', lineWidth:1.5, priceLineVisible:false }});
          rsiSeries.setData(rsiData);
          // OB/OS lines
          const ob = ind.chart.addLineSeries({{ color:'rgba(255,82,82,0.3)', lineWidth:1, lineStyle:2, lastValueVisible:false, priceLineVisible:false }});
          ob.setData(rsiData.map(d => ({{ time:d.time, value:70 }})));
          const os = ind.chart.addLineSeries({{ color:'rgba(0,228,117,0.3)', lineWidth:1, lineStyle:2, lastValueVisible:false, priceLineVisible:false }});
          os.setData(rsiData.map(d => ({{ time:d.time, value:30 }})));
        }}
      }} else {{
        panel.style.height = '0';
      }}
    }}
    if (id === 'macd') {{
      const panel = document.getElementById('macd-panel');
      if (ind.active) {{
        panel.style.height = '120px';
        if (!ind.chart) {{
          ind.chart = LightweightCharts.createChart(panel, {{
            width:tvContainer.clientWidth, height:120,
            layout:{{ background:{{ type:'solid',color:'transparent' }}, textColor:'#8c909e', fontFamily:'Space Grotesk' }},
            grid:{{ vertLines:{{ color:'rgba(66,71,83,0.03)' }}, horzLines:{{ color:'rgba(66,71,83,0.03)' }} }},
            rightPriceScale:{{ borderColor:'rgba(66,71,83,0.08)' }},
            timeScale:{{ visible:false }},
          }});
          ind.chart.addLineSeries({{ color:'#00e475', lineWidth:1.5, priceLineVisible:false }}).setData(macdResult.macd);
          ind.chart.addLineSeries({{ color:'#ff5252', lineWidth:1, lineStyle:2, priceLineVisible:false }}).setData(macdResult.signal);
          ind.chart.addHistogramSeries({{ priceFormat:{{ type:'price' }} }}).setData(macdResult.hist);
        }}
      }} else {{
        panel.style.height = '0';
      }}
    }}
  }};

}} catch(e) {{ console.warn('TradingView chart error:', e); }}

// Sector chart
try {{ new Chart(document.getElementById('sectorChart'),{{
  type:'bar',
  data:{{ labels:{sec_labels}, datasets:[{{ data:{sec_values},
    backgroundColor: ctx => ctx.raw > 0 ? '#4f8ff7' : '#ffb4ab', borderRadius:4 }}] }},
  options:{{ indexAxis:'y', responsive:true, maintainAspectRatio:false,
    scales:{{ x:{{grid:{{color:'rgba(66,71,83,0.1)'}}, ticks:{{callback:v=>v+'%'}} }}, y:{{grid:{{display:false}}}} }},
    plugins:{{ legend:{{display:false}} }}
  }}
}}); }} catch(e) {{ console.warn("Chart error:", e); }}

const eqGrad = document.getElementById('equityChart').getContext('2d').createLinearGradient(0,0,0,250);
eqGrad.addColorStop(0,'rgba(79,143,247,0.3)');
eqGrad.addColorStop(1,'rgba(79,143,247,0.0)');
try {{ new Chart(document.getElementById('equityChart'),{{
  type:'line',
  data:{{ labels:{eq_labels}, datasets:[
    {{ label:'Equity', data:{eq_values}, borderColor:'#4f8ff7', borderWidth:3, fill:true, backgroundColor:eqGrad, tension:.4, pointRadius:0 }},
    {{ label:'Baseline', data:Array({eq_len}).fill({initial}), borderColor:'#333', borderDash:[5,5], pointRadius:0, borderWidth:1 }}
  ]}},
  options:{{ responsive:true, maintainAspectRatio:false,
    scales:{{ x:{{grid:{{display:false}}}}, y:{{grid:{{color:'rgba(66,71,83,0.1)'}}}} }},
    plugins:{{ legend:{{display:false}} }}
  }}
}}); }} catch(e) {{ console.warn("Chart error:", e); }}

try {{ new Chart(document.getElementById('winRateChart'),{{
  type:'doughnut',
  data:{{ labels:['Win','Loss'], datasets:[{{ data:[{n_wins},{n_losses}], backgroundColor:['#00e475','#1e1f26'], borderWidth:0, cutout:'85%' }}] }},
  options:{{ responsive:true, maintainAspectRatio:false, plugins:{{ legend:{{display:false}} }} }}
}}); }} catch(e) {{ console.warn("Chart error:", e); }}

// Stock table search, filter, sort
function filterStocks() {{
  // Collapse any open detail
  if (openDetailRow) {{
    if (openDetailChart) {{ openDetailChart.destroy(); openDetailChart = null; }}
    openDetailRow.remove();
    openDetailRow = null;
  }}
  const q = document.getElementById('stockSearch').value.toLowerCase();
  const sec = document.getElementById('sectorFilter').value.toLowerCase();
  let visible = 0;
  document.querySelectorAll('.stock-row').forEach(row => {{
    const sym = row.dataset.sym;
    const rsec = row.dataset.sec;
    const matchSym = !q || sym.includes(q);
    const matchSec = !sec || rsec.includes(sec);
    row.style.display = (matchSym && matchSec) ? '' : 'none';
    if (matchSym && matchSec) visible++;
  }});
  document.getElementById('stockCount').textContent = visible;
  const bot = document.getElementById('stockCountBottom');
  if (bot) bot.textContent = visible;
}}

let sortDir = {{}};
function sortStocks(key) {{
  // Collapse any open detail
  if (openDetailRow) {{
    if (openDetailChart) {{ openDetailChart.destroy(); openDetailChart = null; }}
    openDetailRow.remove();
    openDetailRow = null;
  }}
  sortDir[key] = !(sortDir[key] || false);
  const tbody = document.getElementById('stockBody');
  const rows = Array.from(tbody.querySelectorAll('.stock-row'));
  rows.sort((a, b) => {{
    let va, vb;
    if (key === 'sym') {{
      va = a.dataset.sym; vb = b.dataset.sym;
      return sortDir[key] ? va.localeCompare(vb) : vb.localeCompare(va);
    }}
    const cells = {{ ltp: 2, chg: 3, vol: 8, turn: 9, rsi: 10 }};
    const ci = cells[key] || 2;
    va = parseFloat(a.children[ci]?.textContent.replace(/[^0-9.\\-]/g, '') || '0');
    vb = parseFloat(b.children[ci]?.textContent.replace(/[^0-9.\\-]/g, '') || '0');
    return sortDir[key] ? va - vb : vb - va;
  }});
  rows.forEach(r => tbody.appendChild(r));
}}

// Market Index Chart
const idxGrad = document.getElementById('indexChart').getContext('2d').createLinearGradient(0,0,0,180);
idxGrad.addColorStop(0, '{index_color}22');
idxGrad.addColorStop(1, '{index_color}02');
try {{ new Chart(document.getElementById('indexChart'),{{
  type:'line',
  data:{{ labels:{index_labels_json}, datasets:[{{
    label:'Avg Price', data:{index_values_json}, borderColor:'{index_color}',
    borderWidth:2, fill:true, backgroundColor:idxGrad, tension:.3, pointRadius:0
  }}] }},
  options:{{ responsive:true, maintainAspectRatio:false,
    plugins:{{ legend:{{display:false}} }},
    scales:{{ x:{{grid:{{display:false}}, ticks:{{maxTicksLimit:8,font:{{size:10}}}}}},
              y:{{grid:{{color:'rgba(66,71,83,0.1)'}}, ticks:{{font:{{size:10}}}} }} }}
  }}
}}); }} catch(e) {{ console.warn("Chart error:", e); }}

// Tab switching
function switchTab(tab, btn) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
  // Activate all matching buttons (mobile + desktop)
  document.querySelectorAll('.tab-btn').forEach(el => {{
    if (el.textContent.trim().toLowerCase().includes(tab)) el.classList.add('active');
  }});
  if (btn) btn.classList.add('active');
}}

// Lazy sparkline rendering via IntersectionObserver
(function() {{
  function renderSparkline(td) {{
    const data = td.getAttribute('data-spark');
    if (!data || td.querySelector('svg')) return;
    const pts = data.split(',').map(Number);
    if (pts.length < 3) return;
    const mn = Math.min(...pts), mx = Math.max(...pts);
    const rng = mx - mn || 1;
    const w = 60, h = 20;
    const coords = pts.map((v, i) => {{
      const x = (i / (pts.length - 1) * w).toFixed(1);
      const y = (h - (v - mn) / rng * h).toFixed(1);
      return x + ',' + y;
    }}).join(' ');
    const color = pts[pts.length - 1] >= pts[0] ? '#00e475' : '#ff5252';
    td.innerHTML = '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" style="vertical-align:middle"><polyline points="' + coords + '" fill="none" stroke="' + color + '" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  }}

  const observer = new IntersectionObserver(function(entries) {{
    entries.forEach(function(entry) {{
      if (entry.isIntersecting) {{
        renderSparkline(entry.target);
        observer.unobserve(entry.target);
      }}
    }});
  }}, {{ root: document.getElementById('stockScroller'), rootMargin: '100px' }});

  document.querySelectorAll('.spark-cell[data-spark]').forEach(function(td) {{
    observer.observe(td);
  }});
}})();

// Stock row expand/collapse detail
let openDetailRow = null;
let openDetailChart = null;

function toggleDetail(tr, sym) {{
  // If clicking same row, collapse
  const existing = tr.nextElementSibling;
  if (existing && existing.classList.contains('detail-row')) {{
    if (openDetailChart) {{ openDetailChart.destroy(); openDetailChart = null; }}
    existing.remove();
    openDetailRow = null;
    return;
  }}

  // Collapse any other open detail
  if (openDetailRow) {{
    if (openDetailChart) {{ openDetailChart.destroy(); openDetailChart = null; }}
    openDetailRow.remove();
    openDetailRow = null;
  }}

  const colCount = tr.children.length;
  const detailTr = document.createElement('tr');
  detailTr.className = 'detail-row';
  const detailTd = document.createElement('td');
  detailTd.setAttribute('colspan', colCount);
  detailTd.className = 'px-4 py-4';
  detailTd.style.background = 'rgba(30,31,38,0.6)';
  detailTd.style.borderBottom = '1px solid rgba(66,71,83,0.15)';

  const data = stockData[sym];
  if (data) {{
    // Rich detail with chart for top 50 stocks
    const chartId = 'detail-chart-' + sym;
    detailTd.innerHTML = '<div class="grid grid-cols-1 md:grid-cols-3 gap-4">' +
      '<div class="md:col-span-2"><div style="height:160px"><canvas id="' + chartId + '"></canvas></div></div>' +
      '<div class="space-y-3 text-xs font-label">' +
        '<div class="flex justify-between"><span class="text-outline">52W High</span><span class="font-bold text-tertiary">' + data.high52.toLocaleString() + '</span></div>' +
        '<div class="flex justify-between"><span class="text-outline">52W Low</span><span class="font-bold text-error">' + data.low52.toLocaleString() + '</span></div>' +
        '<div class="flex justify-between"><span class="text-outline">Avg Volume</span><span class="font-bold">' + data.avgVol.toLocaleString() + '</span></div>' +
        '<div class="mt-3 p-2 bg-surface-container-lowest rounded text-[11px] text-on-surface-variant">' + data.verdict + '</div>' +
      '</div></div>';
    detailTr.appendChild(detailTd);
    tr.parentNode.insertBefore(detailTr, tr.nextSibling);
    openDetailRow = detailTr;

    // Render chart lazily
    setTimeout(function() {{
      const canvas = document.getElementById(chartId);
      if (!canvas) return;
      const ret = data.closes.length >= 2 ? (data.closes[data.closes.length-1] / data.closes[0] - 1) * 100 : 0;
      const color = ret >= 0 ? '#00e475' : '#ff5252';
      const grad = canvas.getContext('2d').createLinearGradient(0,0,0,160);
      grad.addColorStop(0, color + '22');
      grad.addColorStop(1, color + '02');
      openDetailChart = new Chart(canvas, {{
        type: 'line',
        data: {{ labels: data.dates.map(function(d){{ return d.slice(-5); }}), datasets: [{{
          data: data.closes, borderColor: color, borderWidth: 1.5,
          fill: true, backgroundColor: grad, tension: 0.3, pointRadius: 0
        }}] }},
        options: {{ responsive: true, maintainAspectRatio: false, animation: false,
          plugins: {{ legend: {{display: false}} }},
          scales: {{ x: {{display: false}}, y: {{ grid: {{color:'rgba(66,71,83,0.08)'}}, ticks: {{font:{{size:9}}}} }} }}
        }}
      }});
    }}, 50);
  }} else {{
    // Simple text detail for stocks outside top 50
    const cells = tr.children;
    const price = cells[2] ? cells[2].textContent.trim() : '?';
    const change = cells[3] ? cells[3].textContent.trim() : '?';
    const rsiVal = cells[10] ? cells[10].textContent.trim() : '?';
    detailTd.innerHTML = '<div class="text-xs font-label text-on-surface-variant space-y-1">' +
      '<div><span class="text-outline">Symbol:</span> <span class="font-bold">' + sym + '</span></div>' +
      '<div><span class="text-outline">LTP:</span> ' + price + ' | <span class="text-outline">Change:</span> ' + change + ' | <span class="text-outline">RSI:</span> ' + rsiVal + '</div>' +
      '<div class="text-outline mt-2">Detailed chart data available for top 50 stocks by turnover.</div>' +
      '</div>';
    detailTr.appendChild(detailTd);
    tr.parentNode.insertBefore(detailTr, tr.nextSibling);
    openDetailRow = detailTr;
  }}
}}
</script>
<script>
// Price Charts — separate script tag so main charts don't kill these
{price_chart_js}
</script>
</body></html>'''

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html)
    print("[DASHBOARD] Generated %s (%d bytes)" % (OUTPUT, len(html)))


if __name__ == "__main__":
    generate()
