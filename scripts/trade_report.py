#!/usr/bin/env python3
"""
Trade Report Tool - Easily record your buys/sells and get smart recommendations.

Usage:
    python trade_report.py                    # View current position
    python trade_report.py BUY 1000 500      # Buy 1000 shares @ NPR 500
    python trade_report.py SELL 500 520      # Sell 500 shares @ NPR 520
    python trade_report.py recommend 520     # Get strategy for current price
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.portfolio_manager import PortfolioManager
from src.realtime import RealtimeData


def print_banner():
    print("\n" + "="*80)
    print("  ALICL PORTFOLIO MANAGER")
    print("="*80 + "\n")


def show_position():
    """Show current position."""
    pm = PortfolioManager("ALICL")
    pm.print_position()
    pm.view_all_trades()


def record_trade(action: str, shares: int, price: float, notes: str = ""):
    """Record a trade."""
    pm = PortfolioManager("ALICL")
    pm.record_trade(action, shares, price, notes)


def get_recommendations(price: str, signal_from_email: str = None):
    """Get smart buy/sell recommendations."""
    pm = PortfolioManager("ALICL")
    rt = RealtimeData()

    try:
        current_price = float(price)
    except ValueError:
        print(f"[!] Invalid price: {price}")
        return

    market = rt.fetch_market_summary()
    alicl = rt.get_stock_from_summary("ALICL", market)

    # If no signal provided, use current price
    if not signal_from_email:
        actual_price = float(alicl.get("ltp", current_price))
        print(f"\n[*] Using current market price: NPR {actual_price}")
        current_price = actual_price

    wacc = pm.position["wacc"]
    loss_pct = ((current_price - wacc) / wacc * 100)

    print(f"\n{'='*80}")
    print(f"  SMART SELL/BUY RECOMMENDATIONS")
    print(f"{'='*80}\n")

    print(f"Current situation:")
    print(f"  Price: NPR {current_price:,.2f}")
    print(f"  Your WACC: NPR {wacc:,.2f}")
    print(f"  P&L: {loss_pct:+.2f}%")
    print(f"  Shares held: {pm.position['shares']:,}")
    print()

    # Get targeted sell strategy
    targets = pm.get_specific_sell_target(0, loss_pct)  # signal_score = 0 (neutral)

    print(f"Assessment: {targets['assess']}")
    print(f"Situation: {targets['situation']}\n")

    print("SELL STRATEGY:")
    for plan in targets["sell_plan"]:
        print(f"\n  Step {plan['step']}:")
        print(f"    Trigger: {plan['trigger']}")
        print(f"    Action: SELL {plan['qty']:,} shares")
        print(f"    Proceeds: NPR {plan['proceeds']:,.2f}")
        print(f"    Why: {plan['reason']}")

    # If they sold, show rebuy strategy
    if targets["sell_plan"]:
        first_sell_price = float(targets["sell_plan"][0]["trigger"].split("NPR ")[-1])
        first_sell_qty = targets["sell_plan"][0]["qty"]

        print(f"\nAFTER YOU SELL - REBUY STRATEGY:")
        rebuy = pm.get_rebuy_strategy(first_sell_price, first_sell_qty)

        print(f"\nYou will have: NPR {rebuy['you_just_sold']['proceeds']:,.2f} in cash")
        print(f"\nRe-entry points (only buy on BUY signal):")
        for level in rebuy["rebuy_strategy"]:
            print(f"\n  Level {level['level']}: {level['trigger']}")
            print(f"    Buy: {level['buy_qty']:,} shares")
            print(f"    Cost: NPR {level['cost']:,.2f}")
            print(f"    {level['reason']}")

        print(f"\n[WARN] {rebuy['timing']}")

    print(f"\n{'='*80}\n")


def main():
    print_banner()

    if len(sys.argv) == 1:
        # No arguments - show position
        show_position()

    elif sys.argv[1].upper() == "BUY":
        # Record a buy
        if len(sys.argv) < 4:
            print("[!] Usage: python trade_report.py BUY <shares> <price> [notes]")
            return
        shares = int(sys.argv[2])
        price = float(sys.argv[3])
        notes = " ".join(sys.argv[4:]) if len(sys.argv) > 4 else ""
        record_trade("BUY", shares, price, notes)

    elif sys.argv[1].upper() == "SELL":
        # Record a sell
        if len(sys.argv) < 4:
            print("[!] Usage: python trade_report.py SELL <shares> <price> [notes]")
            return
        shares = int(sys.argv[2])
        price = float(sys.argv[3])
        notes = " ".join(sys.argv[4:]) if len(sys.argv) > 4 else ""
        record_trade("SELL", shares, price, notes)

    elif sys.argv[1].lower() == "recommend":
        # Get recommendations
        if len(sys.argv) < 3:
            # Use current price
            get_recommendations("current")
        else:
            price = sys.argv[2]
            signal = sys.argv[3] if len(sys.argv) > 3 else None
            get_recommendations(price, signal)

    elif sys.argv[1].lower() == "view":
        # View trades
        show_position()

    else:
        print(f"[!] Unknown command: {sys.argv[1]}")
        print("\nUsage:")
        print("  python trade_report.py                  # View position")
        print("  python trade_report.py BUY 1000 500    # Record buy")
        print("  python trade_report.py SELL 500 520    # Record sell")
        print("  python trade_report.py recommend 520   # Get strategy")


if __name__ == "__main__":
    main()
