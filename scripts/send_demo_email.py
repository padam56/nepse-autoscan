#!/usr/bin/env python3
"""Send a demo email to show what daily signals look like."""

import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv
load_dotenv()

from src.alerts import AlertSystem
from src.realtime import RealtimeData
from src.portfolio_manager import PortfolioManager

# Get current data
print("Fetching current market data...")
rt = RealtimeData()
market = rt.fetch_market_summary()
alicl = rt.get_stock_from_summary("ALICL", market)
pm = PortfolioManager("ALICL")

current_price = float(alicl.get("ltp", 0))
wacc = pm.position["wacc"]
profit_pct = ((current_price - wacc) / wacc * 100) if wacc > 0 else 0

print(f"[OK] Current Price: NPR {current_price:,.2f}")
print(f"[OK] Your WACC: NPR {wacc:,.2f}")
print(f"[OK] P&L: {profit_pct:+.2f}%")
print("\nSending demo email...\n")

# Create demo signal
signal = "BUY"
signal_score = 35

# Send demo email
alerts = AlertSystem()

subject = f"ALICL Daily Signal Demo - {current_price:,.2f}"

body_html = f"""
<html>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
    <div style="background:#1976d2;color:white;padding:20px;border-radius:8px 8px 0 0;">
        <h1 style="margin:0;font-size:24px;">DAILY SIGNAL - DEMO</h1>
        <p style="margin:5px 0 0;font-size:16px;">This is what you'll get every day at 10:30 AM Nepal Time</p>
    </div>

    <div style="padding:20px;background:#f5f5f5;">
        <h2 style="color:#1976d2;margin-top:0;">TODAY'S SIGNAL</h2>

        <table style="width:100%;border-collapse:collapse;margin:15px 0;">
            <tr style="background:#e3f2fd;">
                <td style="padding:12px;border:1px solid #ddd;"><strong>Signal:</strong></td>
                <td style="padding:12px;border:1px solid #ddd;font-weight:bold;color:#2e7d32;font-size:18px;">{signal}</td>
            </tr>
            <tr>
                <td style="padding:12px;border:1px solid #ddd;"><strong>Confidence:</strong></td>
                <td style="padding:12px;border:1px solid #ddd;font-weight:bold;">{signal_score}/100 (Moderate)</td>
            </tr>
            <tr style="background:#e3f2fd;">
                <td style="padding:12px;border:1px solid #ddd;"><strong>Current Price:</strong></td>
                <td style="padding:12px;border:1px solid #ddd;color:#2e7d32;font-weight:bold;font-size:16px;">NPR {current_price:,.2f}</td>
            </tr>
        </table>

        <h3 style="color:#333;margin-top:20px;">YOUR POSITION</h3>
        <table style="width:100%;border-collapse:collapse;">
            <tr>
                <td style="padding:10px;border-bottom:1px solid #ddd;"><strong>Shares:</strong></td>
                <td style="padding:10px;border-bottom:1px solid #ddd;">8,046</td>
            </tr>
            <tr>
                <td style="padding:10px;border-bottom:1px solid #ddd;"><strong>WACC:</strong></td>
                <td style="padding:10px;border-bottom:1px solid #ddd;">NPR {wacc:,.2f}</td>
            </tr>
            <tr>
                <td style="padding:10px;border-bottom:1px solid #ddd;"><strong>Current P&L:</strong></td>
                <td style="padding:10px;border-bottom:1px solid #ddd;color:#d32f2f;font-weight:bold;">{profit_pct:+.2f}%</td>
            </tr>
        </table>

        <h3 style="color:#333;margin-top:20px;">ACTION LEVELS</h3>
        <div style="background:white;padding:15px;border-left:4px solid:#2e7d32;margin:10px 0;">
            <p style="margin:5px 0;"><strong>Buy Zone:</strong> NPR {current_price*0.99:,.2f} - {current_price:,.2f}</p>
            <p style="margin:5px 0;"><strong>Sell Zone:</strong> NPR {current_price*1.06:,.2f} - {current_price*1.08:,.2f}</p>
            <p style="margin:5px 0;"><strong>Stop Loss:</strong> NPR {current_price*0.92:,.2f}</p>
        </div>

        <h3 style="color:#333;">WHAT TO DO</h3>
        <ul style="font-size:14px;line-height:1.8;">
            <li><strong>Option 1: Buy more</strong> - If price reaches buy zone, buy and average down</li>
            <li><strong>Option 2: Monitor intraday</strong> - Run <code>python intraday_monitor.py</code> for real-time profit signals</li>
            <li><strong>Option 3: Wait</strong> - Signal is moderate, safe to wait for tomorrow</li>
        </ul>

        <h3 style="color:#333;">HOW TO TRADE</h3>
        <p style="font-size:13px;color:#666;background:#fff;padding:10px;border-left:3px solid #1976d2;">
            1. Execute trade on TMS portal (your broker)<br>
            2. Report to system: <code>python trade_report.py BUY 2000 488</code><br>
            3. System auto-updates WACC and next email<br>
            4. Continue trading daily!
        </p>
    </div>

    <div style="padding:15px;background:#e0e0e0;border-radius:0 0 8px 8px;font-size:12px;color:#666;">
        DEMO EMAIL - This shows you the format of daily signals<br>
        Real signals arrive daily at 10:30 AM Nepal Time<br>
        During market hours (11 AM-3 PM NPT), run: python intraday_monitor.py
    </div>
</body>
</html>
"""

body_text = f"""
ALICL DAILY SIGNAL - DEMO
==========================

Signal: {signal} (Confidence: {signal_score}/100)

CURRENT SITUATION
Price: NPR {current_price:,.2f}
Your WACC: NPR {wacc:,.2f}
Your P&L: {profit_pct:+.2f}%
Shares: 8,046

ACTION LEVELS
Buy Zone: NPR {current_price*0.99:,.2f}
Sell Zone: NPR {current_price*1.06:,.2f}
Stop Loss: NPR {current_price*0.92:,.2f}

WHAT TO DO
==========
Option 1: Buy more on the dip (if price hits buy zone)
Option 2: Monitor intraday with: python intraday_monitor.py
Option 3: Wait for clearer signal tomorrow

HOW TO TRADE
============
1. Execute on TMS portal
2. Report: python trade_report.py BUY/SELL qty price
3. System updates automatically
4. Next email tomorrow with new position

This is a DEMO. Real emails arrive daily at 10:30 AM Nepal Time.
"""

result = alerts.send_alert(subject, body_html, body_text)
print("[OK] Demo email sent!")
print(f"   To: tpadamjung@gmail.com")
print(f"\n[INFO] Check your inbox (also check SPAM folder)")
print(f"\nThis demo shows you exactly what daily signals look like.")
print(f"Real signals arrive every day at 10:30 AM Nepal Time.")
