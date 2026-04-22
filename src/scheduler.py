"""
Daily Scheduler - Runs analysis and sends alerts on schedule.

Can run as:
1. Cron job: crontab -e -> "30 15 * * 0-4 /path/to/venv/bin/python /path/to/daily_run.py"
   (3:30 PM Nepal time, Sun-Thu = NEPSE trading days)
2. Systemd timer
3. Standalone daemon with built-in scheduler
"""

import os
import sys
import json
import time
import signal
import threading
from datetime import datetime, timedelta, timezone
NPT = timezone(timedelta(hours=5, minutes=45))

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scraper import NepseScraper
from src.realtime import RealtimeData
from src.technical import TechnicalAnalysis
from src.position import PositionTracker
from src.signals import SignalGenerator
from src.report import ReportGenerator
from src.alerts import AlertSystem
from src.config import PORTFOLIO, DATA_DIR


class DailyScheduler:
    """Manages scheduled analysis runs and alert dispatching."""

    # NEPSE trades Sun-Thu, 11:00 AM - 3:00 PM NPT
    TRADING_DAYS = {6, 0, 1, 2, 3}  # Sun=6, Mon=0, Tue=1, Wed=2, Thu=3
    MARKET_OPEN = (11, 0)   # 11:00 AM
    MARKET_CLOSE = (15, 0)  # 3:00 PM

    # Price alert thresholds (configurable)
    PRICE_ALERTS = {
        "ALICL": {
            "buy_below": 460,      # Strong buy if drops here
            "sell_above": 520,     # Take partial profits
            "breakeven": 550,      # Your break-even
            "stop_loss": 430,      # Exit to limit losses
        }
    }

    def __init__(self):
        self.alerts = AlertSystem()
        self.realtime = RealtimeData()
        self.last_alert_time = {}
        self._running = False
        os.makedirs(DATA_DIR, exist_ok=True)

    def run_daily_analysis(self, symbol: str = "ALICL") -> dict:
        """Run full analysis pipeline and send alerts. Called by cron or daemon."""
        print(f"\n{'='*60}")
        print(f"  DAILY ANALYSIS RUN - {symbol} - {datetime.now(NPT).strftime('%Y-%m-%d %H:%M')}")
        print(f"{'='*60}\n")

        results = {}

        # 1. Fetch real-time market data
        print("[1/6] Fetching real-time market data...")
        market_data = self.realtime.fetch_market_summary()
        if market_data:
            stock_live = self.realtime.get_stock_from_summary(symbol, market_data)
            results["live"] = stock_live
            print(f"  LTP: NPR {stock_live.get('ltp', 'N/A')}")

        # 2. Fetch price history
        print("[2/6] Fetching price history...")
        scraper = NepseScraper(symbol)
        price_data = scraper.fetch_price_history(days=400)

        if not price_data:
            print("[!] No price data. Aborting.")
            return {"error": "No price data"}

        # 3. Run technical analysis
        print("[3/6] Running technical analysis...")
        ta = TechnicalAnalysis(price_data)
        ta_results = ta.run_all()
        results["ta"] = {k: v for k, v in ta_results.items() if k != "dataframe"}

        # 4. Position tracking
        current_price = ta_results["price"]["close"]
        print(f"[4/6] Position tracking (price: NPR {current_price})...")
        position = {}
        if symbol in PORTFOLIO:
            tracker = PositionTracker(symbol, current_price)
            position = tracker.summary()
            results["position"] = position

        # 5. Generate signals
        print("[5/6] Generating signals...")
        sig_gen = SignalGenerator(ta_results)
        signals = sig_gen.generate_all()
        results["signals"] = signals
        print(f"  Signal: {signals['action']} (Score: {signals['composite_score']})")

        # 6. Generate report and send alert
        print("[6/6] Generating report and sending alerts...")
        report = ReportGenerator(symbol)
        if position:
            report.add_position_summary(position)
        report.add_technical_summary(ta_results)
        report.add_support_resistance(ta_results.get("support_resistance", {}))
        report.add_trend_analysis(ta_results.get("trend", {}))
        report.add_signals(signals)

        if position:
            tracker_obj = PositionTracker(symbol, current_price)
            report.add_target_prices(tracker_obj.target_price_analysis())

        report.save()

        # Send email alert
        self.alerts.send_signal_alert(symbol, signals, position, ta_results)

        # Check price alerts
        self._check_price_alerts(symbol, current_price)

        # Save run summary
        results["timestamp"] = datetime.now(NPT).isoformat()
        self._save_run(results)

        print(f"\n[+] Daily analysis complete for {symbol}")
        return results

    def _check_price_alerts(self, symbol: str, current_price: float):
        """Check if price has hit any alert levels."""
        alert_config = self.PRICE_ALERTS.get(symbol, {})
        if not alert_config:
            return

        # Avoid duplicate alerts (max 1 per level per day)
        today = datetime.now().date().isoformat()

        if current_price <= alert_config.get("stop_loss", 0):
            key = f"{symbol}_stop_loss_{today}"
            if key not in self.last_alert_time:
                self.alerts.send_price_alert(symbol, current_price, "STOP LOSS HIT", alert_config["stop_loss"])
                self.last_alert_time[key] = datetime.now()

        elif current_price <= alert_config.get("buy_below", 0):
            key = f"{symbol}_buy_{today}"
            if key not in self.last_alert_time:
                self.alerts.send_price_alert(symbol, current_price, "BUY ZONE REACHED", alert_config["buy_below"])
                self.last_alert_time[key] = datetime.now()

        elif current_price >= alert_config.get("breakeven", float("inf")):
            key = f"{symbol}_breakeven_{today}"
            if key not in self.last_alert_time:
                self.alerts.send_price_alert(symbol, current_price, "BREAK-EVEN REACHED!", alert_config["breakeven"])
                self.last_alert_time[key] = datetime.now()

        elif current_price >= alert_config.get("sell_above", float("inf")):
            key = f"{symbol}_sell_{today}"
            if key not in self.last_alert_time:
                self.alerts.send_price_alert(symbol, current_price, "SELL TARGET HIT", alert_config["sell_above"])
                self.last_alert_time[key] = datetime.now()

    def run_intraday_monitor(self, symbol: str = "ALICL", interval_minutes: int = 15):
        """
        Monitor stock during trading hours with periodic checks.
        Sends alerts only when signal changes or price hits key levels.
        """
        print(f"[*] Starting intraday monitor for {symbol} (every {interval_minutes}min)")
        self._running = True
        last_signal = None

        def signal_handler(sig, frame):
            print("\n[*] Stopping monitor...")
            self._running = False

        signal.signal(signal.SIGINT, signal_handler)

        while self._running:
            now = datetime.now()

            # Only run during trading hours on trading days
            if now.weekday() not in self.TRADING_DAYS:
                print(f"[*] Not a trading day. Sleeping until next trading day...")
                time.sleep(3600)  # Check again in an hour
                continue

            hour_min = (now.hour, now.minute)
            if hour_min < self.MARKET_OPEN or hour_min > self.MARKET_CLOSE:
                print(f"[*] Market closed. Next check at market open...")
                time.sleep(600)  # Check every 10 min outside hours
                continue

            # Fetch real-time data
            market_data = self.realtime.fetch_market_summary()
            if market_data:
                stock = self.realtime.get_stock_from_summary(symbol, market_data)
                ltp = stock.get("ltp", 0)
                change = stock.get("pct_change", 0)
                print(f"[{now.strftime('%H:%M')}] {symbol}: NPR {ltp} ({change:+.2f}%)")

                # Check price alerts
                if ltp:
                    self._check_price_alerts(symbol, float(ltp))

            time.sleep(interval_minutes * 60)

    def _save_run(self, results: dict):
        """Save run results to log."""
        log_path = os.path.join(DATA_DIR, "analysis_log.json")
        log = []
        if os.path.exists(log_path):
            try:
                with open(log_path) as f:
                    log = json.load(f)
            except json.JSONDecodeError:
                log = []

        log.append(results)
        # Keep last 90 days of logs
        log = log[-90:]

        with open(log_path, "w") as f:
            json.dump(log, f, indent=2, default=str)

    @staticmethod
    def install_cron():
        """Print cron installation instructions."""
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        venv_python = os.path.join(project_dir, "venv", "bin", "python")
        script = os.path.join(project_dir, "daily_run.py")

        print("\n=== CRON SETUP INSTRUCTIONS ===\n")
        print("Run 'crontab -e' and add these lines:\n")
        print(f"# NEPSE Daily Analysis - runs at 3:30 PM NPT on trading days (Sun-Thu)")
        print(f"30 15 * * 0-4 cd {project_dir} && {venv_python} {script} >> {project_dir}/data/cron.log 2>&1\n")
        print(f"# NEPSE Intraday Monitor - runs at 11:00 AM on trading days")
        print(f"0 11 * * 0-4 cd {project_dir} && {venv_python} {script} --intraday >> {project_dir}/data/cron.log 2>&1\n")
        print("To verify: crontab -l")
        print(f"Logs at: {project_dir}/data/cron.log\n")


if __name__ == "__main__":
    scheduler = DailyScheduler()
    if "--install-cron" in sys.argv:
        scheduler.install_cron()
    elif "--intraday" in sys.argv:
        scheduler.run_intraday_monitor("ALICL", interval_minutes=15)
    else:
        scheduler.run_daily_analysis("ALICL")
