"""
Real-time NEPSE Market Data - Multiple data sources for live/daily updates.

Sources:
1. MeroLagani market_summary API (no auth, JSON, all 342 stocks)
2. Sharesansar live trading page (HTML parsing)
3. NepseUnofficialApi (full NEPSE API with auth bypass) [optional]
"""

import json
import os
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from src.config import DATA_DIR, HEADERS


class RealtimeData:
    """Fetch real-time/daily market data from multiple NEPSE sources."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        os.makedirs(DATA_DIR, exist_ok=True)

    # ── MeroLagani Market Summary (BEST - No Auth) ─────────────

    def fetch_market_summary(self) -> dict:
        """
        Fetch full market summary from MeroLagani.
        Returns all 342 stocks, 15 sectors, 92 brokers in a single call.
        No auth required.
        """
        url = "https://merolagani.com/handlers/webrequesthandler.ashx"
        params = {"type": "market_summary"}

        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, json.JSONDecodeError) as e:
            print(f"[!] Market summary fetch failed: {e}")
            return {}

        self._save_json(data, "market_summary.json")

        # API uses abbreviated keys:
        # stock: {date, detail: [{s: symbol, lp: last price, c: change, q: quantity}]}
        # turnover: {date, detail: [{s, n, lp, t: turnover, pc: pct_change, h, l, op, q}]}
        # overall: {d: date, t: turnover, q: quantity, tn: transactions, st: stocks, mc: market_cap}
        # sector: {date, detail: [{s, t, q}]}
        # broker: {date, detail: [{n, p, s, mm, t}]}

        stock_detail = data.get("stock", {}).get("detail", []) if isinstance(data.get("stock"), dict) else []
        sector_detail = data.get("sector", {}).get("detail", []) if isinstance(data.get("sector"), dict) else []

        print(f"[+] Market summary: {len(stock_detail)} stocks, {len(sector_detail)} sectors")
        return data

    def get_stock_from_summary(self, symbol: str, market_data: dict = None) -> dict:
        """Extract a specific stock's data from market summary."""
        if not market_data:
            market_data = self.fetch_market_summary()
        if not market_data:
            return {}

        symbol = symbol.upper()
        result = {"symbol": symbol}

        # From stock list (abbreviated keys: s=symbol, lp=last_price, c=change, q=quantity)
        stock_detail = market_data.get("stock", {}).get("detail", []) if isinstance(market_data.get("stock"), dict) else []
        for stock in stock_detail:
            if stock.get("s", "").upper() == symbol:
                result["ltp"] = stock.get("lp", 0)
                result["change"] = stock.get("c", 0)
                result["quantity"] = stock.get("q", 0)
                break

        # From turnover list (more detail: s, lp, t=turnover, pc=pct_change, h, l, op, q)
        turnover_detail = market_data.get("turnover", {}).get("detail", []) if isinstance(market_data.get("turnover"), dict) else []
        for item in turnover_detail:
            if item.get("s", "").upper() == symbol:
                result["ltp"] = item.get("lp", result.get("ltp", 0))
                result["high"] = item.get("h", 0)
                result["low"] = item.get("l", 0)
                result["open"] = item.get("op", 0)
                result["turnover"] = item.get("t", 0)
                result["volume"] = item.get("q", 0)
                result["pct_change"] = item.get("pc", 0)
                break

        # Overall market data (d=date, t=turnover, q=quantity, tn=transactions, st=stocks, mc=market_cap)
        overall = market_data.get("overall", {})
        result["market_date"] = overall.get("d", "")
        result["total_turnover"] = overall.get("t", 0)
        result["total_transactions"] = overall.get("tn", 0)
        result["total_stocks_traded"] = overall.get("st", 0)
        result["market_cap"] = overall.get("mc", 0)

        return result

    # ── Top Gainers/Losers/Turnover ────────────────────────────

    def get_top_movers(self, market_data: dict = None) -> dict:
        """Get top gainers, losers, and highest turnover from market data."""
        if not market_data:
            market_data = self.fetch_market_summary()
        if not market_data:
            return {}

        # Turnover detail has pct_change (pc), stock detail only has point change (c)
        turnover_detail = market_data.get("turnover", {}).get("detail", []) if isinstance(market_data.get("turnover"), dict) else []

        # Sort by percentage change
        valid = [s for s in turnover_detail if s.get("pc") is not None]
        by_change = sorted(valid, key=lambda x: float(x.get("pc", 0)), reverse=True)

        gainers = by_change[:10]
        losers = by_change[-10:][::-1]

        # Sort by turnover
        by_turnover = sorted(turnover_detail, key=lambda x: float(x.get("t", 0)), reverse=True)[:10]

        return {
            "top_gainers": [
                {"symbol": s.get("s"), "ltp": s.get("lp"), "change": s.get("pc")}
                for s in gainers
            ],
            "top_losers": [
                {"symbol": s.get("s"), "ltp": s.get("lp"), "change": s.get("pc")}
                for s in losers
            ],
            "top_turnover": [
                {"symbol": t.get("s"), "ltp": t.get("lp"), "turnover": t.get("t")}
                for t in by_turnover
            ],
        }

    # ── Sharesansar Live Data (Backup) ─────────────────────────

    def fetch_sharesansar_live(self, symbol: str) -> dict:
        """Scrape live data from Sharesansar company page."""
        url = f"https://www.sharesansar.com/company/{symbol.lower()}"
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[!] Sharesansar live fetch failed: {e}")
            return {}

        soup = BeautifulSoup(resp.text, "lxml")
        data = {"symbol": symbol.upper(), "source": "sharesansar"}

        # Extract support/resistance and MA data from company tables
        for table in soup.find_all("table", class_="company-table"):
            for row in table.find_all("tr"):
                tds = row.find_all("td")
                if len(tds) >= 2:
                    key = tds[0].get_text(strip=True)
                    val = tds[1].get_text(strip=True)
                    data[key] = val

        return data

    # ── NEPSE Unofficial API Integration ───────────────────────

    def fetch_via_nepse_api(self, symbol: str) -> dict:
        """
        Fetch data via NepseUnofficialApi library if installed.
        This provides the richest data: floorsheet, market depth, company details.
        """
        try:
            from nepse import Nepse
        except ImportError:
            print("[*] NepseUnofficialApi not installed. Run: pip install nepse")
            return {}

        try:
            nepse = Nepse()
            nepse.setTLSVerification(False)

            result = {}

            # Get company details
            company_list = nepse.getCompanyList()
            for company in company_list:
                if company.get("symbol", "").upper() == symbol.upper():
                    result["company"] = company
                    break

            # Get security detail
            security = nepse.getCompanyPriceVolumeHistory(symbol)
            if security:
                result["price_volume_history"] = security[:30]  # Last 30 days

            # Get market status
            result["market_open"] = nepse.isNepseOpen()

            # Top gainers/losers
            result["top_gainers"] = nepse.getTopGainers()[:5]
            result["top_losers"] = nepse.getTopLosers()[:5]

            return result

        except Exception as e:
            print(f"[!] NepseUnofficialApi error: {e}")
            return {}

    # ── Helpers ────────────────────────────────────────────────

    def _save_json(self, data, filename: str):
        path = os.path.join(DATA_DIR, filename)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)


if __name__ == "__main__":
    rt = RealtimeData()

    print("=== Market Summary ===")
    market = rt.fetch_market_summary()
    if market:
        overall = market.get("overall", {})
        print(f"  Date: {overall.get('date')}")
        print(f"  NEPSE Index: {overall.get('index')}")
        print(f"  Total Turnover: {overall.get('turnover')}")
        print(f"  Stocks Traded: {overall.get('noOfStocks')}")

        print("\n=== ALICL Live Data ===")
        alicl = rt.get_stock_from_summary("ALICL", market)
        for k, v in alicl.items():
            print(f"  {k}: {v}")

        print("\n=== Top Movers ===")
        movers = rt.get_top_movers(market)
        print("  Top Gainers:")
        for g in movers.get("top_gainers", [])[:5]:
            print(f"    {g['symbol']}: {g['change']}%")
        print("  Top Losers:")
        for l in movers.get("top_losers", [])[:5]:
            print(f"    {l['symbol']}: {l['change']}%")
