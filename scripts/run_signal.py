import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
#!/usr/bin/env python3
"""
Run the full Decision Backbone Signal and send email.

Usage:
    python run_signal.py           # Full analysis + send email
    python run_signal.py --print   # Print signal only (no email)
    python run_signal.py --quick   # Quick mode (skip deep TA, use cached data)
"""

import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv
load_dotenv()

from src.realtime import RealtimeData
from src.portfolio_manager import PortfolioManager
from src.scraper import NepseScraper
from src.technical import TechnicalAnalysis
from src.signals import SignalGenerator
from src.decision_engine import DecisionEngine
from src.enhanced_alert import EnhancedAlert


def run_full_signal(send_email: bool = True, quick: bool = False):
    print("\n" + "=" * 65)
    print("  ALICL DECISION BACKBONE SIGNAL")
    print("=" * 65)

    # Step 1: Real-time market data
    print("\n[1/5] Fetching live market data...")
    rt = RealtimeData()
    market = rt.fetch_market_summary()
    if not market:
        print("[!] Failed to fetch market data. Check internet connection.")
        return None

    alicl_live = rt.get_stock_from_summary("ALICL", market)
    current_price = float(alicl_live.get("ltp", 0) or 0)
    if not current_price:
        print("[!] Could not get ALICL live price.")
        return None
    print(f"    [OK] ALICL current price: NPR {current_price:,.2f}")

    # Step 2: Portfolio position
    print("\n[2/5] Loading your position...")
    pm = PortfolioManager("ALICL")
    position = pm.position
    wacc = position.get("wacc", 549.87)
    shares = position.get("shares", 8046)
    pnl_pct = ((current_price - wacc) / wacc * 100) if wacc > 0 else 0
    print(f"    [OK] Shares: {shares:,} | WACC: NPR {wacc:,.2f} | P&L: {pnl_pct:+.2f}%")

    # Step 3: Historical TA (skip if quick mode)
    print("\n[3/5] Running technical analysis...")
    tech_signal = None
    if not quick:
        try:
            scraper = NepseScraper("ALICL")
            df = scraper.fetch_price_history(days=300)
            if df is not None and len(df) > 50:
                ta = TechnicalAnalysis(df)
                ta_results = ta.run_all()
                sg = SignalGenerator(ta_results)
                tech_signal = sg.generate_all()
                print(f"    [OK] Tech score: {tech_signal['composite_score']:+.1f} -> {tech_signal['action']}")
            else:
                print("    [WARN] Insufficient historical data, using simplified tech analysis")
        except Exception as e:
            print(f"    [WARN] TA failed ({e}), using simplified tech analysis")

    # Fallback: simplified tech signal from live data
    if tech_signal is None:
        pct_change_today = float(alicl_live.get("pct_change", 0) or 0)
        simple_score = pct_change_today * 8  # Scale daily % to approximate score
        simple_score = max(-60, min(60, simple_score))
        tech_signal = _simple_tech_signal(simple_score, current_price, wacc, pct_change_today, alicl_live)
        print(f"    [OK] Simplified tech score: {simple_score:+.1f} (based on today's movement)")

    # Step 4: Decision Engine
    print("\n[4/5] Running decision backbone model...")
    engine = DecisionEngine(
        tech_signal=tech_signal,
        market_summary=market,
        position=position,
        current_price=current_price,
    )
    decision = engine.run()
    print(f"    [OK] Final decision: {decision['final_action']} (confidence: {decision['confidence']}%)")
    print(f"    [OK] Combined score: {decision['combined_score']:+.1f} | Regime: {decision['macro_regime']}")

    # Step 5: Send signal email
    print("\n[5/5] Sending signal email...")
    if send_email:
        alert = EnhancedAlert()
        success = alert.send(decision)
        if success:
            print("    [OK] Email sent to tpadamjung@gmail.com")
        else:
            print("    [WARN] Email failed, printed to console above")
    else:
        # Print to console
        print("\n" + "=" * 65)
        print(decision["rationale"])
        print("\n--- SELL TARGETS ---")
        for lvl in decision["sell_levels"]:
            print(f"  {lvl['label']}: NPR {lvl['price']:,.2f} | P&L: {lvl['pnl_at_this_price']:+.1f}% | Qty: {lvl['qty_suggested']:,}")
        print("\n--- BUY-BACK TARGETS ---")
        for lvl in decision["buyback_levels"]:
            print(f"  {lvl['label']}: NPR {lvl['price']:,.2f} | {lvl['discount_from_now']} | Qty: {lvl['qty_suggested']:,}")

    return decision


def _simple_tech_signal(score: float, price: float, wacc: float, pct_today: float, live_data: dict) -> dict:
    """Build a simplified tech signal when full TA is unavailable."""
    pct = pct_today

    if pct > 2:
        rsi_label, rsi_score = "APPROACHING OVERBOUGHT", -30
    elif pct > 0.5:
        rsi_label, rsi_score = "NEUTRAL-BULLISH", 10
    elif pct > -0.5:
        rsi_label, rsi_score = "NEUTRAL", 0
    elif pct > -2:
        rsi_label, rsi_score = "NEUTRAL-BEARISH", -10
    else:
        rsi_label, rsi_score = "APPROACHING OVERSOLD", 30

    high = float(live_data.get("high", price) or price)
    low = float(live_data.get("low", price) or price)
    spread_pct = ((high - low) / low * 100) if low > 0 else 0

    macd_label = "BULLISH" if pct > 0 else "BEARISH"
    macd_score = min(50, max(-50, pct * 10))

    if score >= 30:
        action = "BUY"
    elif score >= 10:
        action = "MILD BUY"
    elif score >= -10:
        action = "HOLD"
    elif score >= -30:
        action = "SELL"
    else:
        action = "STRONG SELL"

    price_from_wacc = ((price - wacc) / wacc * 100) if wacc > 0 else 0

    return {
        "composite_score": round(score, 1),
        "action": action,
        "signals": {
            "trend": {"score": round(score), "label": "UP" if pct > 0 else "DOWN", "detail": f"Today: {pct:+.2f}%"},
            "rsi": {"score": rsi_score, "label": rsi_label, "detail": f"Daily move: {pct:+.2f}%"},
            "macd": {"score": round(macd_score), "label": macd_label, "detail": f"Price momentum: {pct:+.2f}%"},
            "bollinger": {"score": 0, "label": f"Range: NPR {low:,.2f} - {high:,.2f}", "detail": f"Spread: {spread_pct:.1f}%"},
            "volume": {"score": 0, "label": "Volume data available", "detail": f"Turnover: {live_data.get('turnover', 'N/A')}"},
            "support_resistance": {"score": round(price_from_wacc), "label": f"{'Above' if price_from_wacc >= 0 else 'Below'} WACC", "detail": f"WACC: NPR {wacc:,.2f} | Price: NPR {price:,.2f}"},
            "moving_averages": {"score": round(score * 0.5), "label": "Based on live data only", "detail": "Full TA not available"},
        },
        "risk_level": {
            "level": "HIGH" if abs(pct) > 2 else "MEDIUM",
            "detail": "Political uncertainty active",
        },
        "key_levels": {
            "buy_zone_low": round(price * 0.95, 2),
            "buy_zone_high": round(price * 0.97, 2),
            "sell_zone_low": round(price * 1.02, 2),
            "sell_zone_high": round(price * 1.04, 2),
            "strong_buy": round(price * 0.90, 2),
            "strong_sell": round(price * 1.06, 2),
            "stop_loss": round(price * 0.88, 2),
        },
    }


if __name__ == "__main__":
    send = "--print" not in sys.argv
    quick = "--quick" in sys.argv
    run_full_signal(send_email=send, quick=quick)
