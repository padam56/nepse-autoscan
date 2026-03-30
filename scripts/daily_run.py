#!/usr/bin/env python3
"""
Daily Runner - Execute from cron or manually.

Usage:
    python daily_run.py                  # Full daily analysis + email alert
    python daily_run.py --intraday       # Intraday monitor (every 15min during market hours)
    python daily_run.py --install-cron   # Show cron setup instructions
    python daily_run.py --quick          # Quick check: just fetch live price + signal
"""

import sys
import os

# Ensure we're in the right directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.scheduler import DailyScheduler


def quick_check(symbol: str = "ALICL"):
    """Quick price check with live signal - no full TA."""
    from src.realtime import RealtimeData
    from src.config import PORTFOLIO

    rt = RealtimeData()
    market = rt.fetch_market_summary()
    if not market:
        print("[!] Could not fetch market data")
        return

    stock = rt.get_stock_from_summary(symbol, market)
    overall = market.get("overall", {})

    print(f"\n  NEPSE Quick Check - {symbol}")
    print(f"  {'='*40}")
    print(f"  Market Date: {overall.get('date', 'N/A')}")
    print(f"  NEPSE Index: {overall.get('index', 'N/A')}")
    print(f"  LTP: NPR {stock.get('ltp', 'N/A')}")
    print(f"  Change: {stock.get('pct_change', 'N/A')}%")
    print(f"  High: NPR {stock.get('high', 'N/A')}")
    print(f"  Low: NPR {stock.get('low', 'N/A')}")
    print(f"  Volume: {stock.get('volume', 'N/A')}")
    print(f"  Turnover: NPR {stock.get('turnover', 'N/A')}")

    if symbol in PORTFOLIO:
        pos = PORTFOLIO[symbol]
        ltp = float(stock.get("ltp", 0))
        if ltp:
            pnl = (ltp - pos["wacc"]) * pos["shares"]
            pnl_pct = ((ltp - pos["wacc"]) / pos["wacc"]) * 100
            print(f"\n  Your P&L: NPR {pnl:+,.2f} ({pnl_pct:+.2f}%)")
            print(f"  Distance to BE: NPR {pos['wacc'] - ltp:+,.2f}")

    # Top movers
    movers = rt.get_top_movers(market)
    print(f"\n  Top Gainers:")
    for g in movers.get("top_gainers", [])[:5]:
        print(f"    {str(g['symbol']):10s} {float(g['change']):>+8.2f}%")
    print(f"  Top Losers:")
    for l in movers.get("top_losers", [])[:5]:
        print(f"    {str(l['symbol']):10s} {float(l['change']):>+8.2f}%")


def main():
    if "--install-cron" in sys.argv:
        DailyScheduler.install_cron()
    elif "--intraday" in sys.argv:
        # Start real-time intraday monitoring (11 AM - 3 PM market hours)
        from src.intraday_trader import IntradayTrader
        print("\n" + "="*70)
        print("  STARTING INTRADAY PROFIT-TAKING MONITOR")
        print("="*70)
        print("\n  Real-time monitoring: 11:00 AM - 3:00 PM Nepal Time")
        print("  Check interval: Every 60 seconds")
        print("  Profit-taking signals: +1%, +2%, +3%, +5%")
        print("  Alerts will be sent via email immediately")
        print("\n  To customize, use: python intraday_monitor.py [--check] [--interval N]")
        print("="*70 + "\n")
        trader = IntradayTrader("ALICL")
        trader.run_monitoring_loop(check_interval_seconds=60)
    elif "--quick" in sys.argv:
        symbol = sys.argv[sys.argv.index("--quick") + 1] if len(sys.argv) > sys.argv.index("--quick") + 1 else "ALICL"
        quick_check(symbol)
    else:
        scheduler = DailyScheduler()
        scheduler.run_daily_analysis("ALICL")


if __name__ == "__main__":
    main()
