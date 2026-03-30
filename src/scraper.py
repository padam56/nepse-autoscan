"""
NEPSE Data Scraper - Fetches price history, volume, and fundamentals from Sharesansar & MeroLagani.
"""

import json
import csv
import os
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from src.config import (
    MEROLAGANI_BASE,
    MEROLAGANI_COMPANY,
    HEADERS,
    DATA_DIR,
)

# Sharesansar endpoints
SHARESANSAR_BASE = "https://www.sharesansar.com"
SHARESANSAR_COMPANY = f"{SHARESANSAR_BASE}/company"
SHARESANSAR_PRICE_HISTORY = f"{SHARESANSAR_BASE}/company-price-history"

# Known company IDs (Sharesansar internal IDs)
COMPANY_IDS = {
    "ALICL": "143",
}


class NepseScraper:
    """Scrapes NEPSE stock data from Sharesansar and MeroLagani."""

    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.csrf_token = None
        self.company_id = COMPANY_IDS.get(self.symbol)
        os.makedirs(DATA_DIR, exist_ok=True)

    def _init_sharesansar_session(self):
        """Initialize session with Sharesansar to get CSRF token and company ID."""
        url = f"{SHARESANSAR_COMPANY}/{self.symbol.lower()}"
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Get CSRF token
        token_meta = soup.find("meta", {"name": "_token"})
        if token_meta:
            self.csrf_token = token_meta["content"]

        # Get company ID if not known
        if not self.company_id:
            cid_elem = soup.find(id="companyid")
            if cid_elem:
                self.company_id = cid_elem.get_text(strip=True)
                COMPANY_IDS[self.symbol] = self.company_id

        return soup

    # ── Price History (Sharesansar) ────────────────────────────

    def fetch_price_history(self, days: int = 365) -> list[dict]:
        """Fetch OHLCV price history from Sharesansar (paginated)."""
        try:
            self._init_sharesansar_session()
        except requests.RequestException as e:
            print(f"[!] Session init failed: {e}")
            return self._load_cached_data(f"{self.symbol}_price_history.csv")

        if not self.csrf_token or not self.company_id:
            print("[!] Could not get CSRF token or company ID")
            return self._load_cached_data(f"{self.symbol}_price_history.csv")

        all_records = []
        page_size = 50
        max_pages = (days // page_size) + 2

        for page in range(max_pages):
            start = page * page_size
            try:
                resp = self.session.post(
                    SHARESANSAR_PRICE_HISTORY,
                    data={
                        "draw": str(page + 1),
                        "start": str(start),
                        "length": str(page_size),
                        "company": self.company_id,
                    },
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRF-Token": self.csrf_token,
                        "Referer": f"{SHARESANSAR_COMPANY}/{self.symbol.lower()}",
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("data", [])

                if not batch:
                    break

                for entry in batch:
                    all_records.append({
                        "date": entry.get("published_date", ""),
                        "open": self._safe_float(entry.get("open", "0")),
                        "high": self._safe_float(entry.get("high", "0")),
                        "low": self._safe_float(entry.get("low", "0")),
                        "close": self._safe_float(entry.get("close", "0")),
                        "volume": int(self._safe_float(entry.get("traded_quantity", "0"))),
                        "turnover": self._safe_float(entry.get("traded_amount", "0")),
                        "pct_change": self._safe_float(entry.get("per_change", "0")),
                    })

                if len(all_records) >= days:
                    break

                time.sleep(0.3)  # Be polite

            except (requests.RequestException, json.JSONDecodeError) as e:
                print(f"[!] Page {page} failed: {e}")
                break

        if all_records:
            self._save_csv(all_records, f"{self.symbol}_price_history.csv")
            print(f"[+] Fetched {len(all_records)} days of price history for {self.symbol}")
        else:
            print("[!] No price data fetched. Loading cache if available.")
            all_records = self._load_cached_data(f"{self.symbol}_price_history.csv")

        return all_records

    # ── Company Fundamentals (MeroLagani) ──────────────────────

    def fetch_fundamentals(self) -> dict:
        """Scrape fundamental data from MeroLagani company page."""
        url = f"{MEROLAGANI_COMPANY}?symbol={self.symbol}"
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[!] Fundamentals fetch failed: {e}")
            return {}

        soup = BeautifulSoup(resp.text, "lxml")
        fundamentals = {"symbol": self.symbol, "scraped_at": datetime.now().isoformat()}

        # Extract key-value pairs from the company info tables
        tables = soup.find_all("table", class_="table")
        for table in tables:
            for row in table.find_all("tr"):
                cols = row.find_all("td")
                if len(cols) == 2:
                    key = cols[0].get_text(strip=True)
                    val = cols[1].get_text(strip=True).replace(",", "")
                    if key and val:
                        fundamentals[key] = val

        # Get LTP from MeroLagani
        ltp_elem = soup.select_one("#ctl00_ContentPlaceHolder1_CompanyDetail1_lblMarketPrice")
        if ltp_elem:
            fundamentals["LTP"] = ltp_elem.get_text(strip=True).replace(",", "")

        change_elem = soup.select_one("#ctl00_ContentPlaceHolder1_CompanyDetail1_lblChange")
        if change_elem:
            fundamentals["Change"] = change_elem.get_text(strip=True)

        self._save_json(fundamentals, f"{self.symbol}_fundamentals.json")
        print(f"[+] Scraped fundamentals for {self.symbol}: {len(fundamentals)} fields")
        return fundamentals

    # ── Sharesansar Company Data (Better fundamentals) ─────────

    def fetch_sharesansar_fundamentals(self) -> dict:
        """Scrape fundamentals from Sharesansar company page."""
        try:
            soup = self._init_sharesansar_session()
        except requests.RequestException as e:
            print(f"[!] Sharesansar fundamentals failed: {e}")
            return {}

        data = {"symbol": self.symbol, "source": "sharesansar", "scraped_at": datetime.now().isoformat()}

        # Extract company tables
        for table in soup.find_all("table", class_="company-table"):
            for row in table.find_all("tr"):
                tds = row.find_all("td")
                if len(tds) == 2:
                    key = tds[0].get_text(strip=True)
                    val = tds[1].get_text(strip=True)
                    if key and val:
                        data[key] = val

        # Extract support/resistance and MA data
        for table in soup.find_all("table", class_="company-table"):
            for row in table.find_all("tr"):
                tds = row.find_all("td")
                texts = [td.get_text(strip=True) for td in tds]
                if len(texts) >= 2:
                    data[texts[0]] = texts[1]

        self._save_json(data, f"{self.symbol}_sharesansar_fundamentals.json")
        print(f"[+] Scraped Sharesansar data for {self.symbol}: {len(data)} fields")
        return data

    # ── Sector Data ────────────────────────────────────────────

    def fetch_sector_summary(self) -> dict:
        """Fetch life insurance sector summary from MeroLagani."""
        url = f"{MEROLAGANI_BASE}/SectorDetail.aspx?sector=Life%20Insurance"
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[!] Sector fetch failed: {e}")
            return {}

        soup = BeautifulSoup(resp.text, "lxml")
        sector_data = {"sector": "Life Insurance", "scraped_at": datetime.now().isoformat()}

        companies = []
        table = soup.find("table", class_="table")
        if table:
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            for row in table.find_all("tr")[1:]:
                cols = [td.get_text(strip=True).replace(",", "") for td in row.find_all("td")]
                if cols:
                    company = {}
                    for i, h in enumerate(headers):
                        if i < len(cols):
                            company[h] = cols[i]
                    companies.append(company)

        sector_data["companies"] = companies
        self._save_json(sector_data, "life_insurance_sector.json")
        print(f"[+] Fetched sector data: {len(companies)} life insurance companies")
        return sector_data

    # ── Helpers ────────────────────────────────────────────────

    def _save_csv(self, records: list[dict], filename: str):
        path = os.path.join(DATA_DIR, filename)
        if not records:
            return
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=records[0].keys())
            writer.writeheader()
            writer.writerows(records)

    def _save_json(self, data, filename: str):
        path = os.path.join(DATA_DIR, filename)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _load_cached_data(self, filename: str) -> list[dict]:
        path = os.path.join(DATA_DIR, filename)
        if not os.path.exists(path):
            print(f"[!] No cached data at {path}")
            return []
        with open(path, "r") as f:
            reader = csv.DictReader(f)
            records = []
            for row in reader:
                records.append({
                    "date": row["date"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(float(row["volume"])),
                })
            print(f"[*] Loaded {len(records)} cached records from {filename}")
            return records

    @staticmethod
    def _safe_float(val) -> float:
        try:
            return float(str(val).replace(",", "").strip())
        except (ValueError, AttributeError):
            return 0.0


if __name__ == "__main__":
    scraper = NepseScraper("ALICL")
    print("\n=== Fetching Price History ===")
    prices = scraper.fetch_price_history(days=400)
    print(f"Got {len(prices)} price records")

    if prices:
        print(f"Latest: {prices[0]['date']} Close={prices[0]['close']}")
        print(f"Oldest: {prices[-1]['date']} Close={prices[-1]['close']}")

    print("\n=== Fetching Fundamentals ===")
    fund = scraper.fetch_fundamentals()

    print("\n=== Fetching Sharesansar Data ===")
    ss = scraper.fetch_sharesansar_fundamentals()
