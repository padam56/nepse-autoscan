"""
Report Generator - Produces comprehensive analysis reports for NEPSE stocks.
"""

import json
import os
from datetime import datetime, timezone, timedelta
NPT = timezone(timedelta(hours=5, minutes=45))

from tabulate import tabulate

from src.config import REPORTS_DIR


class ReportGenerator:
    """Generates formatted terminal + file reports from analysis data."""

    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        os.makedirs(REPORTS_DIR, exist_ok=True)
        self.timestamp = datetime.now(NPT).strftime("%Y-%m-%d %H:%M")
        self.lines = []

    def _h(self, title: str, char: str = "="):
        """Add a header."""
        self.lines.append("")
        self.lines.append(f" {title} ".center(80, char))
        self.lines.append("")

    def _p(self, text: str):
        self.lines.append(text)

    def _table(self, data: list[list], headers: list[str]):
        self.lines.append(tabulate(data, headers=headers, tablefmt="simple", floatfmt=".2f"))

    # ── Report Sections ────────────────────────────────────────

    def add_position_summary(self, position: dict):
        self._h(f"POSITION SUMMARY - {self.symbol}")
        rows = [
            ["Shares Held", f"{position['shares']:,}"],
            ["WACC (Avg Cost)", f"NPR {position['wacc']:,.2f}"],
            ["Total Investment", f"NPR {position['total_cost']:,.2f}"],
            ["Current Price", f"NPR {position['current_price']:,.2f}"],
            ["Current Value", f"NPR {position['current_value']:,.2f}"],
            ["Unrealized P&L", f"NPR {position['unrealized_pnl']:,.2f}"],
            ["P&L %", f"{position['unrealized_pnl_pct']:+.2f}%"],
            ["Break-even Price", f"NPR {position['breakeven_price']:,.2f}"],
            ["Distance to BE", f"NPR {position['distance_to_breakeven']:,.2f} ({position['distance_to_breakeven_pct']:+.2f}%)"],
        ]
        self._table(rows, ["Metric", "Value"])

    def add_technical_summary(self, ta: dict):
        self._h("TECHNICAL INDICATORS")

        # Price
        price = ta.get("price", {})
        self._p(f"  Last Close: NPR {price.get('close', 'N/A')}  |  "
                f"High: {price.get('high', 'N/A')}  |  "
                f"Low: {price.get('low', 'N/A')}  |  "
                f"Volume: {price.get('volume', 0):,}")
        self._p("")

        # Moving Averages
        self._p("  Moving Averages:")
        ma = ta.get("moving_averages", {})
        for k, v in ma.items():
            current = price.get("close", 0)
            position = "ABOVE" if current > v else "BELOW"
            self._p(f"    {k}: NPR {v:,.2f}  [{position}]")
        self._p("")

        # RSI
        rsi = ta.get("rsi", "N/A")
        rsi_zone = "OVERSOLD" if rsi < 30 else "OVERBOUGHT" if rsi > 70 else "NEUTRAL" if isinstance(rsi, (int, float)) else "N/A"
        self._p(f"  RSI(14): {rsi}  [{rsi_zone}]")

        # MACD
        macd = ta.get("macd", {})
        self._p(f"  MACD: {macd.get('macd', 'N/A')}  |  Signal: {macd.get('signal', 'N/A')}  |  Hist: {macd.get('histogram', 'N/A')}")

        # Bollinger
        bb = ta.get("bollinger", {})
        self._p(f"  Bollinger: Upper={bb.get('upper', 'N/A')}  Mid={bb.get('mid', 'N/A')}  Lower={bb.get('lower', 'N/A')}")
        self._p(f"  BB Position: {bb.get('position', 'N/A')} (0=lower band, 1=upper band)")

        # ATR
        atr = ta.get("atr", {})
        self._p(f"  ATR(14): {atr.get('atr', 'N/A')}  ({atr.get('atr_pct', 'N/A')}% of price)")

        # Volume
        vol = ta.get("volume", {})
        self._p(f"  Volume: {vol.get('current', 0):,}  |  20D Avg: {vol.get('avg_20d', 0):,.0f}  |  Ratio: {vol.get('ratio', 'N/A')}x")

    def add_support_resistance(self, sr: dict):
        self._h("SUPPORT & RESISTANCE LEVELS", "-")
        self._p(f"  Current Price: NPR {sr.get('current_price', 'N/A')}")
        self._p(f"  Pivot Point: NPR {sr.get('pivot_point', 'N/A')}")
        self._p("")

        supports = sr.get("support_levels", [])
        resistances = sr.get("resistance_levels", [])

        self._p("  Resistance Levels (sell zones):")
        for i, r in enumerate(resistances[:5], 1):
            self._p(f"    R{i}: NPR {r:,.2f}")
        if not resistances:
            self._p("    No resistance levels detected")

        self._p("")
        self._p("  Support Levels (buy zones):")
        for i, s in enumerate(supports[:5], 1):
            self._p(f"    S{i}: NPR {s:,.2f}")
        if not supports:
            self._p("    No support levels detected")

        self._p("")
        self._p(f"  Pivot R1: {sr.get('pivot_r1', 'N/A')}  |  R2: {sr.get('pivot_r2', 'N/A')}")
        self._p(f"  Pivot S1: {sr.get('pivot_s1', 'N/A')}  |  S2: {sr.get('pivot_s2', 'N/A')}")

    def add_trend_analysis(self, trend: dict):
        self._h("TREND ANALYSIS", "-")
        self._p(f"  Overall Trend: {trend.get('overall_trend', 'N/A')}")
        self._p(f"  Trend Score: {trend.get('trend_score', 'N/A')} (-1=bearish, +1=bullish)")
        self._p(f"  Cross Signal: {trend.get('cross_signal', 'N/A')}")
        self._p(f"  5-Day ROC: {trend.get('roc_5d', 'N/A')}%")
        self._p(f"  20-Day ROC: {trend.get('roc_20d', 'N/A')}%")
        self._p(f"  HH/HL Ratio: {trend.get('hh_hl_ratio', 'N/A')}")

    def add_signals(self, signals: dict):
        self._h("TRADING SIGNALS")

        action = signals.get("action", "N/A")
        score = signals.get("composite_score", 0)

        # Big action banner
        self._p(f"  >>> RECOMMENDATION: {action} (Score: {score}/100) <<<")
        self._p("")

        # Individual signals
        rows = []
        for name, sig in signals.get("signals", {}).items():
            rows.append([
                name.upper(),
                sig.get("score", 0),
                sig.get("label", "N/A"),
            ])
        self._table(rows, ["Indicator", "Score", "Signal"])

        # Risk
        self._p("")
        risk = signals.get("risk_level", {})
        self._p(f"  Volatility Risk: {risk.get('volatility_risk', 'N/A')}")
        self._p(f"  ATR %: {risk.get('atr_pct', 'N/A')}")
        self._p(f"  Bollinger Squeeze: {'YES - Breakout Imminent!' if risk.get('bollinger_squeeze') else 'No'}")
        self._p(f"  Volume Conviction: {risk.get('volume_conviction', 'N/A')}")

        # Key levels
        self._p("")
        levels = signals.get("key_levels", {})
        self._p("  Action Levels:")
        self._p(f"    Buy Zone:    NPR {levels.get('buy_zone', 'N/A')}")
        self._p(f"    Strong Buy:  NPR {levels.get('strong_buy', 'N/A')}")
        self._p(f"    Sell Zone:   NPR {levels.get('sell_zone', 'N/A')}")
        self._p(f"    Strong Sell: NPR {levels.get('strong_sell', 'N/A')}")
        self._p(f"    Stop Loss:   NPR {levels.get('stop_loss', 'N/A')}")

    def add_target_prices(self, targets: list[dict]):
        self._h("TARGET PRICE SCENARIOS", "-")
        rows = []
        for t in targets:
            pnl_str = f"NPR {t['pnl']:+,.2f}"
            rows.append([t["target"], f"NPR {t['price']}", f"NPR {t['portfolio_value']:,.2f}", pnl_str, f"{t['pnl_pct']:+.2f}%"])
        self._table(rows, ["Scenario", "Price", "Portfolio Value", "P&L", "P&L %"])

    def add_averaging_scenarios(self, scenarios: list[dict]):
        self._h("AVERAGING DOWN SCENARIOS", "-")
        rows = []
        for s in scenarios:
            rows.append([
                s["action"],
                f"NPR {s['additional_investment']:,.0f}",
                f"NPR {s['new_wacc']:,.2f}",
                f"NPR {s['wacc_reduction']:,.2f}",
                f"{s['new_pnl_pct']:+.2f}%",
            ])
        self._table(rows, ["Action", "Add. Investment", "New WACC", "WACC Reduction", "P&L @ Current"])

    def add_fundamentals(self, fund: dict):
        self._h("FUNDAMENTAL DATA", "-")
        # Show key fields
        key_fields = [
            "LTP", "Change", "% Change", "Market Capitalization",
            "52 Weeks High", "52 Weeks Low", "120 Day Average",
            "EPS", "P/E Ratio", "Book Value", "PBV",
            "Sector", "Listed Shares", "Market Capitalization",
        ]
        for field in key_fields:
            if field in fund:
                self._p(f"  {field}: {fund[field]}")

        # Show remaining fields
        shown = set(key_fields) | {"symbol", "scraped_at"}
        remaining = {k: v for k, v in fund.items() if k not in shown}
        if remaining:
            self._p("")
            for k, v in list(remaining.items())[:20]:
                self._p(f"  {k}: {v}")

    # ── Output ─────────────────────────────────────────────────

    def build(self) -> str:
        header = [
            "=" * 80,
            f"  NEPSE STOCK ANALYSIS REPORT - {self.symbol}".center(80),
            f"  Generated: {self.timestamp}".center(80),
            "=" * 80,
        ]
        footer = [
            "",
            "=" * 80,
            "  DISCLAIMER: This analysis is for informational purposes only.".center(80),
            "  Not financial advice. Always do your own research.".center(80),
            "=" * 80,
        ]
        return "\n".join(header + self.lines + footer)

    def save(self) -> str:
        report = self.build()
        filename = f"{self.symbol}_analysis_{datetime.now(NPT).strftime('%Y%m%d_%H%M')}.txt"
        path = os.path.join(REPORTS_DIR, filename)
        with open(path, "w") as f:
            f.write(report)
        print(f"\n[+] Report saved to {path}")
        return path

    def print_report(self):
        print(self.build())
