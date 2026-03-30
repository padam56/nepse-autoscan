#!/usr/bin/env python3
"""NEPSE Multi-Agent Analysis"""
import os, sys, time, argparse, json
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "lib"))

from dotenv import load_dotenv
load_dotenv()

def run_full_analysis(send_email=True, use_llm=True, use_news=True):
    print("\n" + "="*70)
    print("  NEPSE Multi-Agent Analysis")
    print("="*70)
    t0 = time.time()

    # ── Step 1: Live market data (OHLCV via turnover detail) ─────────
    print("\n[1/7] Fetching full OHLCV market data...")
    from src.realtime import RealtimeData
    from portfolio.tracker import get_portfolio_snapshot, print_portfolio_summary
    from portfolio.config import TOTAL_INVESTED

    rt = RealtimeData()
    market = rt.fetch_market_summary()

    if not market:
        print("    [ERROR] Failed to fetch market data")
        return None

    # Build price_map from turnover detail (has pc, h, l, op) — richer data
    turnover_stocks = market.get("turnover", {}).get("detail", [])
    price_map = {}
    for s in turnover_stocks:
        sym = str(s.get("s", "")).upper()
        if sym:
            try:
                lp = float(s.get("lp", 0) or 0)
                pc = float(s.get("pc", 0) or 0)
                price_map[sym] = {
                    "ltp": lp, "pct_change": pc, "change": lp * pc / 100,
                    "volume": float(s.get("q", 0) or 0),
                    "high": float(s.get("h", lp) or lp),
                    "low": float(s.get("l", lp) or lp),
                    "open": float(s.get("op", lp) or lp),
                    "turnover": float(s.get("t", 0) or 0),
                }
            except (ValueError, TypeError):
                pass

    # Fallback for stocks not in turnover detail (low volume)
    stock_detail = market.get("stock", {}).get("detail", [])
    for s in stock_detail:
        sym = str(s.get("s", "")).upper()
        if sym and sym not in price_map:
            try:
                lp = float(s.get("lp", 0) or 0)
                c  = float(s.get("c", 0) or 0)
                prev = lp - c
                pct  = (c / prev * 100) if prev != 0 else 0
                price_map[sym] = {"ltp": lp, "pct_change": pct, "change": c,
                                  "volume": float(s.get("q", 0) or 0)}
            except: pass

    portfolio_snapshot = get_portfolio_snapshot(price_map)
    for r in portfolio_snapshot:
        sym = r["symbol"]
        live = price_map.get(sym, {})
        r["current_price"] = r.get("ltp", 0)
        r["change_today"]  = live.get("pct_change", 0)
        r["action"] = "HOLD"

    total_value = sum(r["current_value"] for r in portfolio_snapshot if r["current_value"] > 0)
    total_pnl_pct = ((total_value - TOTAL_INVESTED) / TOTAL_INVESTED * 100) if TOTAL_INVESTED > 0 else 0
    print(f"    [OK] {len(portfolio_snapshot)} stocks | Value: NPR {total_value:,.0f} | P&L: {total_pnl_pct:+.2f}%")

    # ── Step 2: Macro analysis ────────────────────────────────────────
    print("\n[2/7] Macro analysis...")
    from src.macro_analyzer import MacroAnalyzer
    macro_analyzer = MacroAnalyzer(market)
    macro_data = macro_analyzer.get_macro_score()
    decision_bias = macro_analyzer.get_decision_bias(macro_data["score"], 0)
    print(f"    [OK] Regime: {macro_data['regime']} ({macro_data['score']:+.1f}) | "
          f"Breadth: {macro_data['breadth']['gainers']} up {macro_data['breadth']['losers']} down")

    # ── Step 3: Sector heat map ────────────────────────────────────────
    print("\n[3/7] Sector heat map analysis...")
    from scrapers.sector_analyzer import analyze_sectors
    sector_analysis = analyze_sectors(market)
    sectors = sector_analysis.get("sectors", [])
    print(f"    [OK] {len(sectors)} sectors analyzed")
    for sec in sectors[:4]:
        print(f"    {sec['heat']:12s} {sec['sector']:22s} {sec['avg_change_pct']:+.2f}%  → {sec['action']}")

    # ── Step 4: Advanced pre-breakout screener ────────────────────────
    print("\n[4/7] Advanced pre-breakout screener (OHLCV-based)...")
    from scrapers.advanced_screener import run_advanced_screen
    screen_result = run_advanced_screen(market)
    top_picks    = screen_result.get("top_buys", [])
    avoid_stocks = screen_result.get("avoid", [])
    hist_days    = screen_result.get("history_days", 0)
    print(f"    [OK] {len(top_picks)} pre-breakout candidates | {len(avoid_stocks)} avoid flags")
    print(f"    [OK] {hist_days} days of price history stored")
    if top_picks:
        for b in top_picks[:3]:
            t = b.get("targets",{})
            print(f"    → {b['symbol']:8s} NPR{b['ltp']:,.1f}  {b['pc']:+.2f}%  "
                  f"pos:{b['price_position']:.0%}  score:{b['composite_score']:.0f}  "
                  f"TP1:{t.get('tp1','?')}  | {b.get('reason','')[:45]}")
    if avoid_stocks:
        for a in avoid_stocks[:2]:
            print(f"    [AVOID] {a['symbol']:8s} {a['pc']:+.2f}% — {a.get('avoid_reason','')[:50]}")

    # ── Step 5: News ──────────────────────────────────────────────────
    news_summary = ""
    if use_news:
        print("\n[5/7] Scraping news...")
        try:
            from scrapers.news_scraper import NewsScraper
            key = sorted(portfolio_snapshot, key=lambda x: x.get("current_value",0), reverse=True)[:2]
            for stock in key:
                try:
                    news = NewsScraper().get_latest_news(stock["symbol"], days=2, use_cache=True)
                    if news:
                        news_summary += f"{stock['symbol']}: {'; '.join(n.get('title','') for n in news[:2])}. "
                        print(f"    [OK] {stock['symbol']}: {len(news)} items")
                except Exception as e:
                    print(f"    [WARN] {stock['symbol']}: {e}")
        except Exception as e:
            print(f"    [WARN] News: {e}")
    else:
        print("\n[5/7] Skipping news...")

    # ── Step 6: Per-stock signals ─────────────────────────────────────
    print("\n[6/7] Per-stock signals + multi-agent AI...")
    per_stock_signals = _build_signals(portfolio_snapshot, macro_data, decision_bias)
    sig_map = {s["symbol"]: s["action"] for s in per_stock_signals}
    for r in portfolio_snapshot:
        r["action"] = sig_map.get(r["symbol"], "HOLD")
    urgent = [s for s in per_stock_signals if s.get("action") in ("SELL","STRONG_SELL","SELL_PARTIAL","CONSIDER_EXIT")]
    print(f"    [OK] {len(urgent)} urgent signals | {len(per_stock_signals)-len(urgent)} hold")

    agent_outputs = {}
    if use_llm:
        try:
            from llm.multi_agent import run_multi_agent_analysis
            from llm.ollama_client import is_ollama_running
            if is_ollama_running():
                agent_outputs = run_multi_agent_analysis(
                    portfolio=portfolio_snapshot,
                    sector_data=sector_analysis,
                    top_picks=top_picks,
                    avoid_stocks=avoid_stocks,
                    macro_regime=macro_data.get("regime","UNCERTAIN"),
                    history_days=hist_days,
                    news=news_summary,
                )
                print(f"    [OK] Multi-agent complete | Model: {agent_outputs.get('model_used','?')}")
            else:
                print("    [WARN] Ollama not running")
        except Exception as e:
            print(f"    [WARN] LLM error: {e}")
    else:
        print("    Skipping LLM (--no-llm)")

    # ── Step 7: Send or print ─────────────────────────────────────────
    print("\n[7/7] Building email...")
    from alerts.portfolio_email import send_portfolio_email

    if send_email:
        ok = send_portfolio_email(
            portfolio_snapshot=portfolio_snapshot,
            per_stock_signals=per_stock_signals,
            macro_data=macro_data,
            top_opportunities=top_picks,
            avoid_stocks=avoid_stocks,
            sector_analysis=sector_analysis,
            agent_outputs=agent_outputs,
            total_invested=TOTAL_INVESTED,
            total_current_value=total_value,
        )
        print(f"    {'[OK] Email sent' if ok else '[ERROR] Email failed'}")
    else:
        print_portfolio_summary(portfolio_snapshot)
        print("\nSECTOR HEAT MAP:")
        for sec in sectors:
            print(f"  {sec['heat']:12s} {sec['sector']:22s} → {sec['action']}")
        print("\nTOP PRE-BREAKOUT PICKS:")
        for b in top_picks[:5]:
            t = b.get("targets",{})
            print(f"  {b['symbol']:8s} NPR{b['ltp']:,.1f}  {b['pc']:+.2f}%  "
                  f"TP1:{t.get('tp1','?')}  SL:{t.get('sl','?')}  Score:{b['composite_score']:.0f}")
        if avoid_stocks:
            print("\nAVOID:")
            for a in avoid_stocks:
                print(f"  {a['symbol']:8s} — {a.get('avoid_reason','')[:70]}")
        if agent_outputs:
            print("\n=== SECTOR ANALYSIS ===\n" + agent_outputs.get("sector_analysis",""))
            print("\n=== STOCK PICKS ===\n"     + agent_outputs.get("validated_picks",""))
            print("\n=== PORTFOLIO ADVICE ===\n"+ agent_outputs.get("portfolio_advice",""))

    print(f"\n[OK] Analysis complete in {time.time()-t0:.1f}s")


def _build_signals(portfolio_snapshot, macro_data, decision_bias):
    signals = []
    bias = decision_bias.get("bias","NEUTRAL")
    regime = macro_data.get("regime","UNCERTAIN")
    for s in portfolio_snapshot:
        ltp   = s.get("ltp", s.get("current_price", 0))
        wacc  = s.get("wacc", 0)
        shares = s.get("shares", 0)
        pnl   = s.get("pnl_pct", 0)
        action, reason, sell_t, qty = "HOLD", "", round(ltp*1.02,2), 0

        if bias == "SELL_FIRST":
            if pnl >= 5:
                action="SELL"; qty=max(100,(int(shares*0.40)//100)*100)
                reason=f"Profit +{pnl:.1f}% + {regime} regime — sell into strength before reversal"
                sell_t=ltp
            elif pnl >= 1:
                action="SELL_PARTIAL"; qty=max(100,(int(shares*0.25)//100)*100)
                reason=f"+{pnl:.1f}% profit — reduce 25% now, political uncertainty active"
                sell_t=ltp
            elif pnl >= -3:
                action="CONSIDER_EXIT"; qty=max(100,(int(shares*0.20)//100)*100)
                reason=f"Near breakeven {pnl:.1f}% — partial exit to protect capital"
                sell_t=round(wacc*0.98,2)
            else:
                action="HOLD_RECOVERY"
                reason=f"Loss {pnl:.1f}% — selling here locks in loss. Wait for NPR {wacc:,.0f} recovery"
        elif bias == "SELL_PARTIAL":
            if pnl >= 5:
                action="SELL_PARTIAL"; qty=max(100,(int(shares*0.25)//100)*100)
                reason=f"Profit {pnl:.1f}% — trim 25%"
                sell_t=ltp
            elif pnl > -5:
                action="HOLD"; reason=f"Range {pnl:.1f}%. No urgent action."
            else:
                action="HOLD_RECOVERY"; reason=f"Loss {pnl:.1f}%. Recovery mode."
        elif bias == "WAIT":
            action="HOLD"; reason=f"Market down — do NOT sell at lows. Wait for NPR {wacc:,.0f}"
        else:
            if pnl >= 10:
                action="SELL_PARTIAL"; qty=max(100,(int(shares*0.25)//100)*100)
                reason=f"Strong profit {pnl:.1f}% — lock in gains"
            elif pnl >= 0:
                action="HOLD"; reason=f"Profit {pnl:.1f}%. Monitor."
            else:
                action="HOLD_RECOVERY"; reason=f"Loss {pnl:.1f}%. Hold."

        signals.append({
            "symbol": s.get("symbol",""), "sector": s.get("sector",""),
            "current_price": ltp, "wacc": wacc, "shares": shares,
            "pnl_pct": pnl, "current_value": s.get("current_value",0),
            "action": action, "sell_target": sell_t,
            "qty_suggested": qty, "reason": reason,
        })

    priority = {"SELL":0,"STRONG_SELL":1,"SELL_PARTIAL":2,"CONSIDER_EXIT":3,"HOLD":4,"HOLD_RECOVERY":5}
    signals.sort(key=lambda x: priority.get(x["action"],9))
    return signals


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--print", dest="print_only", action="store_true")
    p.add_argument("--no-llm", dest="no_llm", action="store_true")
    p.add_argument("--no-news", dest="no_news", action="store_true")
    args = p.parse_args()
    run_full_analysis(send_email=not args.print_only, use_llm=not args.no_llm, use_news=not args.no_news)
