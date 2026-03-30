#!/usr/bin/env python3
"""
NEPSE Stock Analyzer - Main Entry Point

Usage:
    python analyze.py                    # Full analysis of ALICL (default)
    python analyze.py SYMBOL             # Full analysis of any NEPSE stock
    python analyze.py ALICL --scrape     # Scrape fresh data only
    python analyze.py ALICL --report     # Generate report from cached data
"""

import sys
import argparse

from dotenv import load_dotenv
load_dotenv()

from src.scraper import NepseScraper
from src.technical import TechnicalAnalysis
from src.position import PositionTracker
from src.signals import SignalGenerator
from src.report import ReportGenerator
from src.config import PORTFOLIO


def run_full_analysis(symbol: str, skip_scrape: bool = False):
    """Run complete analysis pipeline for a stock."""

    symbol = symbol.upper()
    print(f"\n{'='*60}")
    print(f"  NEPSE STOCK ANALYZER - {symbol}")
    print(f"{'='*60}\n")

    # ── Step 1: Scrape Data ────────────────────────────────────
    scraper = NepseScraper(symbol)

    if not skip_scrape:
        print("[1/5] Scraping price history...")
        price_data = scraper.fetch_price_history(days=365)

        print("[2/5] Scraping fundamentals...")
        fundamentals = scraper.fetch_fundamentals()

        print("[3/5] Scraping sector data...")
        sector_data = scraper.fetch_sector_summary()
    else:
        print("[*] Using cached data (--report mode)...")
        price_data = scraper._load_cached_data(f"{symbol}_price_history.csv")
        fundamentals = {}
        sector_data = {}

    if not price_data:
        print("\n[!] ERROR: No price data available. Cannot proceed.")
        print("    Try running without --report flag to scrape fresh data.")
        sys.exit(1)

    # ── Step 2: Technical Analysis ─────────────────────────────
    print("[3/5] Running technical analysis...")
    ta = TechnicalAnalysis(price_data)
    ta_results = ta.run_all()

    # ── Step 3: Position Tracking ──────────────────────────────
    current_price = ta_results["price"]["close"]
    position_data = None
    target_prices = None
    avg_scenarios = None

    if symbol in PORTFOLIO:
        print("[4/5] Calculating position P&L...")
        tracker = PositionTracker(symbol, current_price)
        position_data = tracker.summary()
        target_prices = tracker.target_price_analysis()

        # Generate averaging scenarios
        avg_scenarios = [
            tracker.averaging_down_scenario(current_price, 500),
            tracker.averaging_down_scenario(current_price, 1000),
            tracker.averaging_down_scenario(current_price, 2000),
            tracker.averaging_down_scenario(current_price * 0.95, 1000),
            tracker.averaging_down_scenario(current_price * 0.90, 2000),
        ]
    else:
        print(f"[4/5] No position found for {symbol} - skipping P&L")

    # ── Step 4: Generate Signals ───────────────────────────────
    print("[5/5] Generating trading signals...")
    signal_gen = SignalGenerator(ta_results)
    signals = signal_gen.generate_all()

    # ── Step 5: Build Report ───────────────────────────────────
    report = ReportGenerator(symbol)

    if position_data:
        report.add_position_summary(position_data)

    report.add_technical_summary(ta_results)
    report.add_support_resistance(ta_results.get("support_resistance", {}))
    report.add_trend_analysis(ta_results.get("trend", {}))
    report.add_signals(signals)

    if target_prices:
        report.add_target_prices(target_prices)

    if avg_scenarios:
        report.add_averaging_scenarios(avg_scenarios)

    if fundamentals:
        report.add_fundamentals(fundamentals)

    # Print and save
    report.print_report()
    saved_path = report.save()

    print(f"\n[+] Analysis complete! Report saved to: {saved_path}")
    print(f"[+] Data files saved in: data/")

    return ta_results, signals, position_data


def main():
    parser = argparse.ArgumentParser(description="NEPSE Stock Analyzer")
    parser.add_argument("symbol", nargs="?", default="ALICL", help="Stock symbol (default: ALICL)")
    parser.add_argument("--scrape", action="store_true", help="Only scrape data, no analysis")
    parser.add_argument("--report", action="store_true", help="Use cached data only")
    args = parser.parse_args()

    if args.scrape:
        scraper = NepseScraper(args.symbol)
        scraper.fetch_price_history(days=365)
        scraper.fetch_fundamentals()
        scraper.fetch_sector_summary()
        scraper.fetch_recent_trades()
        print("\n[+] Scraping complete! Check data/ directory.")
    else:
        run_full_analysis(args.symbol, skip_scrape=args.report)


if __name__ == "__main__":
    main()
