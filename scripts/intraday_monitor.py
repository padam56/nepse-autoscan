#!/usr/bin/env python3
"""
Intraday Market Monitor - Real-time profit-taking signals during market hours.

Usage:
    python intraday_monitor.py              # Run 11 AM - 3 PM monitoring
    python intraday_monitor.py --check      # Quick check if market is open
    python intraday_monitor.py --interval 30  # Check every 30 seconds instead of 60
"""

import sys
import os
from datetime import datetime, timezone, timedelta
NPT = timezone(timedelta(hours=5, minutes=45))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.intraday_trader import IntradayTrader


def print_banner():
    print("\n" + "="*80)
    print("  INTRADAY PROFIT-TAKING MONITOR - ALICL")
    print("="*80)
    print("\n  Market Hours: 11:00 AM - 3:00 PM Nepal Time (Sun-Thu)")
    print("  Real-time monitoring with instant sell signals")
    print("  PC can be minimized - alerts will arrive via email\n")
    print("="*80 + "\n")


def quick_check():
    """Check if market is currently open."""
    trader = IntradayTrader("ALICL")
    is_open = trader.is_market_open()

    now = datetime.now()
    current_time = f"{now.hour:02d}:{now.minute:02d}"
    weekday_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][now.weekday()]

    print(f"\n[{current_time}] {weekday_name}")

    if is_open:
        print("[OK] MARKET IS OPEN - Monitoring active\n")
        price = trader.get_current_price()
        if price:
            wacc = trader.portfolio.position["wacc"]
            profit_pct = ((price - wacc) / wacc * 100) if wacc > 0 else 0
            print(f"Current ALICL Price: NPR {price:,.2f}")
            print(f"Your WACC: NPR {wacc:,.2f}")
            print(f"Current P&L: {profit_pct:+.2f}%\n")
        else:
            print("[WARN] Could not fetch current price\n")
    else:
        if now.weekday() > 3:
            print("[INFO] MARKET CLOSED - Weekend/Holiday")
        else:
            print("[INFO] WAITING FOR MARKET OPEN - Next open: 11:00 AM")
        print(f"Market opens at 11:00 AM Nepal Time\n")


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "--check":
            quick_check()
            return
        elif sys.argv[1] == "--interval":
            if len(sys.argv) < 3:
                print("[!] Usage: python intraday_monitor.py --interval SECONDS")
                return
            try:
                interval = int(sys.argv[2])
                if interval < 10:
                    print("[!] Interval must be at least 10 seconds")
                    return
                print_banner()
                trader = IntradayTrader("ALICL")
                trader.run_monitoring_loop(check_interval_seconds=interval)
            except ValueError:
                print("[!] Interval must be an integer (seconds)")
                return
        else:
            print("[!] Unknown flag: " + sys.argv[1])
            print("\nUsage:")
            print("  python intraday_monitor.py              # Monitor market (60 sec interval)")
            print("  python intraday_monitor.py --check      # Quick check if market is open")
            print("  python intraday_monitor.py --interval N # Monitor with N second interval")
            return
    else:
        # Default: start monitoring with 60-second interval
        print_banner()
        trader = IntradayTrader("ALICL")
        trader.run_monitoring_loop(check_interval_seconds=60)


if __name__ == "__main__":
    main()
