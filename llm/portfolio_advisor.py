"""
LLM Portfolio Advisor - Combines all data sources into actionable signals.
Runs technical analysis + news + market scan → LLM → final recommendations.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from typing import List, Dict, Optional
from portfolio.tracker import get_portfolio_snapshot, fetch_live_prices
from portfolio.config import PORTFOLIO
from scrapers.news_scraper import get_latest_news
from scrapers.nepse_scanner import scan_market
from llm.ollama_client import analyze_news, analyze_portfolio, find_opportunities, is_ollama_running


def run_full_analysis(use_cache: bool = True) -> dict:
    """
    Run the complete analysis pipeline:
    1. Portfolio snapshot (live prices + P&L)
    2. Market scan (all 342 stocks)
    3. News scraping (portfolio stocks)
    4. LLM analysis
    5. Return structured recommendations
    """
    print("\n[1/4] Fetching live portfolio prices...")
    price_map = fetch_live_prices()
    snapshot = get_portfolio_snapshot(price_map)

    total_cost = sum(r["total_cost"] for r in snapshot)
    total_value = sum(r["current_value"] for r in snapshot if r["current_value"] > 0)
    total_pnl = total_value - total_cost

    print(f"      Portfolio: NPR {total_value:,.0f} value | {total_pnl/total_cost*100:+.2f}% total P&L")

    print("\n[2/4] Scanning full NEPSE market (342 stocks)...")
    scan = scan_market()
    print(f"      Market: {scan.get('market_mood','?')} | "
          f"{scan.get('gainers',0)} gainers / {scan.get('losers',0)} losers")

    print("\n[3/4] Scraping latest news...")
    all_news = get_latest_news(use_cache=use_cache)
    news_by_symbol: Dict[str, list] = {}
    for n in all_news:
        sym = n["symbol"]
        news_by_symbol.setdefault(sym, []).append(n)

    # LLM news sentiment per stock
    print(f"\n[4/4] Running LLM analysis {'(Ollama GPU)' if is_ollama_running() else '(fallback mode)'}...")
    stock_sentiments = {}
    for sym in PORTFOLIO:
        sym_news = news_by_symbol.get(sym, [])
        sentiment = analyze_news(sym_news, sym)
        stock_sentiments[sym] = sentiment

    # Build news summary for portfolio LLM call
    news_headlines = []
    for sym, news_list in news_by_symbol.items():
        if sym != "MARKET":
            for n in news_list[:2]:
                news_headlines.append(f"[{sym}] {n['title']}")
    market_news = news_by_symbol.get("MARKET", [])
    for n in market_news[:3]:
        news_headlines.append(f"[MARKET] {n['title']}")
    news_summary = "\n".join(news_headlines[:12])

    # LLM portfolio analysis
    portfolio_advice = analyze_portfolio(snapshot, scan.get("market_mood", "MIXED"), news_summary)

    # LLM market opportunities
    market_opportunities = find_opportunities(scan, snapshot)

    # Build per-stock signals (rule-based + LLM sentiment)
    per_stock_signals = []
    for row in snapshot:
        sym = row["symbol"]
        sentiment = stock_sentiments.get(sym, {})
        news_score = sentiment.get("score", 0)
        pnl_pct = row["pnl_pct"]

        # Combine P&L + news sentiment → action
        if pnl_pct >= 5 or (pnl_pct >= 2 and news_score < -20):
            action = "SELL"
            reason = f"+{pnl_pct:.1f}% profit - take gains"
        elif pnl_pct >= 0 and news_score >= 30:
            action = "HOLD"
            reason = f"In profit + positive news"
        elif pnl_pct < -15 and news_score < -30:
            action = "CONSIDER_EXIT"
            reason = f"{pnl_pct:.1f}% loss + negative news = reduce exposure"
        elif pnl_pct < 0 and news_score >= 20:
            action = "HOLD_RECOVERY"
            reason = f"In loss but positive news - wait for recovery"
        else:
            action = "HOLD"
            reason = "No strong signal either way"

        per_stock_signals.append({
            "symbol": sym,
            "ltp": row["ltp"],
            "wacc": row["wacc"],
            "pnl_pct": pnl_pct,
            "pnl_abs": row["pnl_abs"],
            "shares": row["shares"],
            "action": action,
            "reason": reason,
            "news_sentiment": sentiment.get("sentiment", "NEUTRAL"),
            "news_score": news_score,
            "news_insight": sentiment.get("insight", ""),
            "sector": row["sector"],
        })

    # Sort: SELL first, then CONSIDER_EXIT, then HOLD
    action_order = {"SELL": 0, "CONSIDER_EXIT": 1, "HOLD_RECOVERY": 2, "HOLD": 3}
    per_stock_signals.sort(key=lambda x: action_order.get(x["action"], 9))

    return {
        "portfolio_snapshot": snapshot,
        "total_cost": total_cost,
        "total_value": total_value,
        "total_pnl": total_pnl,
        "total_pnl_pct": round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0,
        "market_scan": scan,
        "per_stock_signals": per_stock_signals,
        "llm_portfolio_advice": portfolio_advice,
        "llm_market_opportunities": market_opportunities,
        "news_by_symbol": news_by_symbol,
        "stock_sentiments": stock_sentiments,
        "ollama_active": is_ollama_running(),
    }
