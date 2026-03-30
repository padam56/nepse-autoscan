"""
Intraday Trader - Real-time monitoring during market hours (11 AM - 3 PM NPT).

Monitors ALICL price every minute and sends SELL signals when:
1. Price hits profitable exit zone
2. Technical indicators confirm profit opportunity
3. Volume validates the move

Works during market hours only (11 AM - 3 PM Nepal Time = 5:30 AM - 9:30 AM UTC)
Runs on your PC continuously when market is open.
"""

import os
import time
import json
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional, Dict, Tuple

import requests
from bs4 import BeautifulSoup

from src.config import DATA_DIR
from src.realtime import RealtimeData
from src.alerts import AlertSystem
from src.portfolio_manager import PortfolioManager


# Nepal Timezone (UTC+5:45)
class NepalTime(tzinfo):
    """Nepal timezone: UTC+5:45"""
    def utcoffset(self, dt):
        return timedelta(hours=5, minutes=45)

    def tzname(self, dt):
        return "NPT"

    def dst(self, dt):
        return timedelta(0)


class IntradayTrader:
    """Real-time intraday monitoring and profit-taking signals."""

    # NEPSE trading hours (in Nepal Time)
    MARKET_OPEN = (11, 0)      # 11:00 AM Nepal Time
    MARKET_CLOSE = (15, 0)     # 3:00 PM Nepal Time
    TRADING_DAYS = {6, 0, 1, 2, 3}  # Sun-Thu (weekday: 0=Monday, 6=Sunday)

    def __init__(self, symbol: str = "ALICL"):
        self.symbol = symbol.upper()
        self.realtime = RealtimeData()
        self.alerts = AlertSystem()
        self.portfolio = PortfolioManager(symbol)

        # Track price history during the day
        self.intraday_prices = []
        self.entry_price = None
        self.high_of_day = None
        self.low_of_day = None
        self.open_price = None
        self.sell_signals_sent = []  # Track which signals already sent

        os.makedirs(DATA_DIR, exist_ok=True)

    @staticmethod
    def get_nepal_time() -> datetime:
        """Get current time in Nepal (UTC+5:45)."""
        utc_now = datetime.now(timezone.utc)
        nepal_tz = NepalTime()
        return utc_now.astimezone(nepal_tz)

    def is_market_open(self) -> bool:
        """Check if NEPSE market is currently open (checks Nepal Time, not local time)."""
        # Get Nepal time
        nepal_now = self.get_nepal_time()
        current_time = (nepal_now.hour, nepal_now.minute)
        weekday = nepal_now.weekday()

        is_trading_day = weekday in self.TRADING_DAYS
        is_trading_hour = self.MARKET_OPEN <= current_time < self.MARKET_CLOSE

        return is_trading_day and is_trading_hour

    def get_current_price(self) -> Optional[float]:
        """Fetch current price from MeroLagani."""
        try:
            market = self.realtime.fetch_market_summary()
            if market:
                stock = self.realtime.get_stock_from_summary(self.symbol, market)
                return float(stock.get("ltp", 0))
        except Exception as e:
            print(f"[!] Error fetching price: {e}")
        return None

    def calculate_profit_targets(self, entry_price: float) -> Dict[str, float]:
        """Calculate profit targets based on entry price."""
        return {
            "conservative": entry_price * 1.01,      # +1% (safe, quick)
            "moderate": entry_price * 1.02,          # +2% (good, reasonable)
            "aggressive": entry_price * 1.03,        # +3% (excellent, risky)
            "very_aggressive": entry_price * 1.05,   # +5% (amazing, very risky)
        }

    def calculate_exit_zones(self, wacc: float, current_price: float) -> Dict[str, dict]:
        """
        Calculate exit zones based on your cost basis and current price.

        Returns where to sell for maximum profit.
        """
        profit_pct = ((current_price - wacc) / wacc * 100) if wacc > 0 else 0

        zones = {}

        if profit_pct > 5:
            # Already in good profit
            zones["exit_now"] = {
                "reason": f"Already +{profit_pct:.2f}% profit",
                "price": current_price,
                "qty": int(self.portfolio.position["shares"] * 0.5),
                "proceeds": int(self.portfolio.position["shares"] * 0.5) * current_price,
                "profit": round(int(self.portfolio.position["shares"] * 0.5) * (current_price - wacc), 2),
            }
            zones["sell_more_if_climbs"] = {
                "reason": "Sell rest if price climbs +7%",
                "price": current_price * 1.02,
                "qty": int(self.portfolio.position["shares"] * 0.5),
                "profit": round(int(self.portfolio.position["shares"] * 0.5) * (current_price * 1.02 - wacc), 2),
            }

        elif profit_pct > 2:
            zones["good_exit"] = {
                "reason": f"Already +{profit_pct:.2f}% profit, good exit point",
                "price": current_price,
                "qty": int(self.portfolio.position["shares"] * 0.33),
                "proceeds": int(self.portfolio.position["shares"] * 0.33) * current_price,
                "profit": round(int(self.portfolio.position["shares"] * 0.33) * (current_price - wacc), 2),
            }
            zones["wait_for_better"] = {
                "reason": "Wait for +3-4% for better exit",
                "price": current_price * 1.03,
                "qty": int(self.portfolio.position["shares"] * 0.33),
                "profit": round(int(self.portfolio.position["shares"] * 0.33) * (current_price * 1.03 - wacc), 2),
            }

        elif profit_pct > 0:
            zones["recover_position"] = {
                "reason": f"Break even reached! Now in +{profit_pct:.2f}% profit",
                "price": current_price,
                "qty": int(self.portfolio.position["shares"] * 0.25),
                "proceeds": int(self.portfolio.position["shares"] * 0.25) * current_price,
                "profit": round(int(self.portfolio.position["shares"] * 0.25) * (current_price - wacc), 2),
            }

        else:
            # Still in loss
            zones["wait_for_recovery"] = {
                "reason": f"Still in {profit_pct:.2f}% loss, hold for recovery signal",
                "price": "Wait for BUY signal",
            }

        return zones

    def check_sell_signal(self, current_price: float, high_of_day: float, volume: float) -> Dict:
        """
        Check if current price triggers a sell signal.

        Returns: {triggered: bool, reason: str, qty: int, price: float}
        """
        wacc = self.portfolio.position["wacc"]
        profit_pct = ((current_price - wacc) / wacc * 100) if wacc > 0 else 0

        signal = {
            "triggered": False,
            "reason": "",
            "qty": 0,
            "price": 0,
            "proceeds": 0,
            "profit": 0,
            "urgency": "NORMAL",
        }

        # Very aggressive: +5% or more
        if profit_pct >= 5:
            signal["triggered"] = True
            signal["reason"] = f"VERY PROFITABLE: {profit_pct:+.2f}% gain"
            signal["qty"] = int(self.portfolio.position["shares"] * 0.5)
            signal["price"] = current_price
            signal["proceeds"] = signal["qty"] * current_price
            signal["profit"] = round(signal["qty"] * (current_price - wacc), 2)
            signal["urgency"] = "EXTREME - SELL NOW"
            return signal

        # Aggressive: +3-5%
        elif profit_pct >= 3 and high_of_day > current_price * 1.01:
            # Reached high, now pulling back slightly - time to sell
            signal["triggered"] = True
            signal["reason"] = f"EXCELLENT: {profit_pct:+.2f}% gain, price pulled back from high"
            signal["qty"] = int(self.portfolio.position["shares"] * 0.4)
            signal["price"] = current_price
            signal["proceeds"] = signal["qty"] * current_price
            signal["profit"] = round(signal["qty"] * (current_price - wacc), 2)
            signal["urgency"] = "HIGH - SELL SOON"
            return signal

        # Moderate: +2-3%
        elif profit_pct >= 2 and volume > 50000:
            # Good volume confirms the move
            signal["triggered"] = True
            signal["reason"] = f"GOOD: {profit_pct:+.2f}% gain on high volume"
            signal["qty"] = int(self.portfolio.position["shares"] * 0.25)
            signal["price"] = current_price
            signal["proceeds"] = signal["qty"] * current_price
            signal["profit"] = round(signal["qty"] * (current_price - wacc), 2)
            signal["urgency"] = "MEDIUM - CONSIDER SELLING"
            return signal

        # Conservative: +1%
        elif profit_pct >= 1:
            signal["triggered"] = True
            signal["reason"] = f"PROFIT: {profit_pct:+.2f}% gain, small but sure"
            signal["qty"] = int(self.portfolio.position["shares"] * 0.15)
            signal["price"] = current_price
            signal["proceeds"] = signal["qty"] * current_price
            signal["profit"] = round(signal["qty"] * (current_price - wacc), 2)
            signal["urgency"] = "LOW - OPTIONAL"
            return signal

        return signal

    def send_intraday_alert(self, current_price: float, signal: Dict):
        """Send real-time profit-taking alert."""
        wacc = self.portfolio.position["wacc"]

        subject = f"INTRADAY SELL SIGNAL: {signal['urgency']} @ NPR {current_price}"

        body_html = f"""
        <html>
        <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
            <div style="background:#d32f2f;color:white;padding:20px;border-radius:8px 8px 0 0;">
                <h1 style="margin:0;font-size:24px;">SELL SIGNAL</h1>
                <p style="margin:5px 0 0;font-size:16px;">{signal['urgency']}</p>
            </div>

            <div style="padding:20px;background:#f5f5f5;">
                <h2 style="color:#d32f2f;margin-top:0;">OPPORTUNITY RIGHT NOW</h2>

                <table style="width:100%;border-collapse:collapse;">
                    <tr><td style="padding:8px;border-bottom:1px solid #ddd;"><strong>Current Price:</strong></td><td style="font-weight:bold;color:#2e7d32;">NPR {current_price:,.2f}</td></tr>
                    <tr><td style="padding:8px;border-bottom:1px solid #ddd;"><strong>Your WACC:</strong></td><td>NPR {wacc:,.2f}</td></tr>
                    <tr><td style="padding:8px;border-bottom:1px solid #ddd;"><strong>Profit Per Share:</strong></td><td style="color:#2e7d32;font-weight:bold;">NPR {current_price - wacc:+,.2f}</td></tr>
                    <tr><td style="padding:8px;border-bottom:1px solid #ddd;"><strong>Profit %:</strong></td><td style="color:#2e7d32;font-weight:bold;">{((current_price - wacc) / wacc * 100):+.2f}%</td></tr>
                </table>

                <h3 style="color:#333;margin-top:20px;">SUGGESTED ACTION</h3>
                <div style="background:white;padding:15px;border-left:4px solid #d32f2f;margin:10px 0;">
                    <p><strong>Reason:</strong> {signal['reason']}</p>
                    <p><strong>Sell:</strong> {signal['qty']:,} shares @ NPR {signal['price']:,.2f}</p>
                    <p><strong>Proceeds:</strong> NPR {signal['proceeds']:,.2f}</p>
                    <p style="color:#2e7d32;font-weight:bold;"><strong>Profit:</strong> NPR {signal['profit']:+,.2f}</p>
                </div>

                <h3 style="color:#333;">DECISION</h3>
                <p style="font-size:14px;color:#666;">
                    This signal is INTRADAY - valid only during market hours (11 AM - 3 PM NPT)<br>
                    If you agree with the price, <strong>execute the SELL order immediately</strong><br>
                    After selling, report: <strong>python trade_report.py SELL {signal['qty']} {signal['price']}</strong>
                </p>

                <h3 style="color:#333;">IF PRICE CONTINUES RISING</h3>
                <p style="font-size:14px;color:#666;">
                    Price might climb further. Watch for next signal or use trailing stop:
                    <br>• If price reaches +5%: SELL ALL immediately
                    <br>• If price drops 0.5% from high: SELL and lock in profits
                </p>
            </div>

            <div style="padding:15px;background:#e0e0e0;border-radius:0 0 8px 8px;font-size:12px;color:#666;">
                INTRADAY TRADING ALERT | Real-time monitoring during market hours
            </div>
        </body>
        </html>
        """

        body_text = f"""
INTRADAY SELL SIGNAL - {signal['urgency']}

Current Price: NPR {current_price:,.2f}
Your WACC: NPR {wacc:,.2f}
Profit: NPR {signal['profit']:+,.2f} ({((current_price - wacc) / wacc * 100):+.2f}%)

Suggested Action:
  SELL {signal['qty']:,} shares @ NPR {signal['price']:,.2f}
  Proceeds: NPR {signal['proceeds']:,.2f}

Reason: {signal['reason']}

After selling, report: python trade_report.py SELL {signal['qty']} {signal['price']}
        """

        return self.alerts.send_alert(subject, body_html, body_text)

    def run_monitoring_loop(self, check_interval_seconds: int = 60):
        """
        Main monitoring loop - runs during market hours (checks Nepal Time).

        Args:
            check_interval_seconds: How often to check price (default 60 = every minute)
        """
        nepal_now = self.get_nepal_time()
        local_now = datetime.now()
        local_tz = local_now.astimezone().tzinfo

        print(f"\n{'='*70}")
        print(f"  INTRADAY TRADER - {self.symbol}")
        print(f"  Market hours: 11:00 AM - 3:00 PM Nepal Time (UTC+5:45)")
        print(f"  Check interval: Every {check_interval_seconds} seconds")
        print(f"  Your location: Using local time (offset from Nepal: {(nepal_now.hour - local_now.hour) % 24}h {(nepal_now.minute - local_now.minute) % 60}m)")
        print(f"{'='*70}\n")

        last_price = None
        signals_sent_today = 0

        while True:
            # Get current times
            nepal_now = self.get_nepal_time()
            local_now = datetime.now()
            nepal_time_str = f"{nepal_now.hour:02d}:{nepal_now.minute:02d} NPT"
            local_time_str = f"{local_now.hour:02d}:{local_now.minute:02d} Local"

            if not self.is_market_open():
                weekday = nepal_now.weekday()
                if weekday > 3:
                    status = f"[{nepal_time_str}] MARKET CLOSED - Weekend/Holiday"
                else:
                    status = f"[{nepal_time_str}] BEFORE MARKET OPEN - Waiting for 11:00 AM NPT"
                print(status)

                time.sleep(300)  # Check every 5 min while market is closed
                continue

            # Market is open - check price
            current_price = self.get_current_price()
            if not current_price:
                print(f"[{nepal_time_str}] Error fetching price, retrying...")
                time.sleep(check_interval_seconds)
                continue

            # Track price movement
            if self.open_price is None:
                self.open_price = current_price
                self.high_of_day = current_price
                self.low_of_day = current_price
                print(f"[{nepal_time_str}] MARKET OPEN - ALICL opened at NPR {current_price:,.2f}")

            self.high_of_day = max(self.high_of_day, current_price)
            self.low_of_day = min(self.low_of_day, current_price)
            self.intraday_prices.append((nepal_now, current_price))

            # Calculate profit
            wacc = self.portfolio.position["wacc"]
            profit_pct = ((current_price - wacc) / wacc * 100) if wacc > 0 else 0

            # Check for sell signal
            market_data = self.realtime.fetch_market_summary()
            alicl_data = self.realtime.get_stock_from_summary(self.symbol, market_data)
            volume = float(alicl_data.get("volume", 0))

            signal = self.check_sell_signal(current_price, self.high_of_day, volume)

            # Print status
            price_change = ""
            if last_price:
                change = current_price - last_price
                if change > 0:
                    price_change = f" ↑ +NPR {change:.2f}"
                elif change < 0:
                    price_change = f" ↓ -NPR {abs(change):.2f}"

            print(f"[{nepal_time_str}] ALICL: NPR {current_price:,.2f} | "
                  f"P&L: {profit_pct:+.2f}% | "
                  f"High: {self.high_of_day:,.2f} | "
                  f"Low: {self.low_of_day:,.2f}{price_change}", end="")

            if signal["triggered"]:
                print(f" | [SIGNAL] {signal['urgency']}")

                # Send alert only once per signal level per day
                signal_key = f"{current_time}_{signal['urgency']}"
                if signal_key not in self.sell_signals_sent:
                    self.send_intraday_alert(current_price, signal)
                    self.sell_signals_sent.append(signal_key)
                    signals_sent_today += 1
            else:
                print()

            last_price = current_price
            time.sleep(check_interval_seconds)


if __name__ == "__main__":
    trader = IntradayTrader("ALICL")
    trader.run_monitoring_loop(check_interval_seconds=60)
