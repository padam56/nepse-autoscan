"""
Fundamental Data Scraper - Sharesansar company pages.
Scrapes key financial metrics (P/E, EPS, Book Value, etc.) for NEPSE stocks.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://www.sharesansar.com"
HEADERS = {
    "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept":           "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":  "en-US,en;q=0.9",
}

# Fields we extract and the label patterns used on Sharesansar company pages
FIELD_PATTERNS = {
    "pe":         [r"p\s*/\s*e\s+ratio", r"price\s+earning", r"p/e"],
    "eps":        [r"\beps\b", r"earning\s+per\s+share", r"earnings\s+per\s+share"],
    "book_value": [r"book\s+value", r"book\s*value"],
    "pb":         [r"p\s*/\s*b\s+ratio", r"price\s+to?\s+book", r"pbv\s+ratio", r"pbv"],
    "div_yield":  [r"dividend\s+yield", r"div\.?\s+yield"],
    "market_cap": [r"market\s+cap", r"market\s+capitali[sz]ation"],
    "high_52w":   [r"52.*week.*high", r"52.*wk.*high", r"52w\s*high"],
    "low_52w":    [r"52.*week.*low", r"52.*wk.*low", r"52w\s*low"],
    "sector":     [r"\bsector\b"],
}


class FundamentalScraper:
    """Scrape fundamental data from Sharesansar company pages."""

    CACHE_FILE = ROOT / "data" / "fundamentals.json"

    def __init__(self):
        self.session = self._make_session()
        self.cache = self._load_cache()
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(2)

    # -- Session setup (mirrors backfill_history.py) -------------------------

    def _make_session(self) -> requests.Session:
        """Create a requests.Session and visit homepage to establish cookies."""
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            r = session.get(BASE_URL + "/", timeout=15)
            xsrf = session.cookies.get("XSRF-TOKEN", "")
            if xsrf:
                session.headers["X-XSRF-TOKEN"] = urllib.parse.unquote(xsrf)
        except Exception as e:
            print("[fundamental_scraper] Session init warning: %s" % e)
        return session

    # -- Public API ----------------------------------------------------------

    def fetch_one(self, symbol: str) -> dict:
        """Fetch fundamentals for a single stock.

        Returns dict with keys:
            pe, eps, book_value, pb, div_yield, market_cap,
            high_52w, low_52w, sector
        Missing values are None.
        """
        symbol = symbol.upper().strip()

        # Check cache first
        cached = self.cache.get(symbol)
        if cached:
            ts = cached.get("_fetched_at", "")
            if ts:
                try:
                    age_hours = (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 3600
                    if age_hours < 24:
                        return cached
                except (ValueError, TypeError):
                    pass

        url = "%s/company/%s" % (BASE_URL, symbol.lower())
        try:
            resp = self.session.get(url, timeout=20)
            if resp.status_code != 200:
                print("[fundamental_scraper] HTTP %d for %s" % (resp.status_code, symbol))
                return self._empty_result(symbol)
        except Exception as e:
            print("[fundamental_scraper] Request failed for %s: %s" % (symbol, e))
            return self._empty_result(symbol)

        result = self._parse_company_page(resp.text, symbol)

        # Update cache
        with self._lock:
            self.cache[symbol] = result
            self._save_cache()

        return result

    def fetch_all(self, symbols: List[str], max_workers: int = 4) -> Dict[str, dict]:
        """Fetch fundamentals for all symbols using ThreadPoolExecutor.

        Rate-limited with a semaphore.  Caches results to avoid refetching
        symbols already fetched within the last 24 hours.

        Returns: {symbol: {pe, eps, ...}, ...}
        """
        results: Dict[str, dict] = {}
        symbols = [s.upper().strip() for s in symbols]

        # Separate cached vs. needs-fetch
        to_fetch = []
        for sym in symbols:
            cached = self.cache.get(sym)
            if cached:
                ts = cached.get("_fetched_at", "")
                try:
                    age_hours = (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 3600
                    if age_hours < 24:
                        results[sym] = cached
                        continue
                except (ValueError, TypeError):
                    pass
            to_fetch.append(sym)

        if not to_fetch:
            return results

        print("[fundamental_scraper] Fetching %d symbols (%d cached)" % (
            len(to_fetch), len(results)))

        def _worker(sym: str) -> tuple:
            with self._semaphore:
                try:
                    data = self.fetch_one(sym)
                    time.sleep(0.5)  # rate-limit
                    return sym, data
                except Exception as e:
                    print("[fundamental_scraper] Error %s: %s" % (sym, e))
                    return sym, self._empty_result(sym)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_worker, sym): sym for sym in to_fetch}
            for i, future in enumerate(as_completed(futures), 1):
                sym = futures[future]
                try:
                    sym_out, data = future.result()
                    results[sym_out] = data
                    if i % 10 == 0 or i == len(to_fetch):
                        print("[fundamental_scraper]  %d/%d done" % (i, len(to_fetch)))
                except Exception as e:
                    print("[fundamental_scraper] Worker error %s: %s" % (sym, e))
                    results[sym] = self._empty_result(sym)

        return results

    # -- Parsing -------------------------------------------------------------

    def _parse_company_page(self, html: str, symbol: str) -> dict:
        """Extract fundamental metrics from company page HTML."""
        soup = BeautifulSoup(html, "html.parser")
        result = self._empty_result(symbol)

        # Strategy 1: Look through all table rows (td/th pairs, dt/dd pairs)
        pairs = self._extract_label_value_pairs(soup)

        for label_raw, value_raw in pairs:
            label = label_raw.lower().strip()
            value = value_raw.strip()

            for field, patterns in FIELD_PATTERNS.items():
                if result[field] is not None:
                    continue  # already found
                for pat in patterns:
                    if re.search(pat, label, re.IGNORECASE):
                        if field == "sector":
                            result["sector"] = value if value and value.lower() != "n/a" else None
                        else:
                            result[field] = self._parse_numeric(value)
                        break

        result["_fetched_at"] = datetime.now().isoformat()
        return result

    def _extract_label_value_pairs(self, soup: BeautifulSoup) -> List[tuple]:
        """Extract all label-value pairs from the page using multiple strategies."""
        pairs = []

        # Strategy A: <table> rows with <td> cells
        for tr in soup.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if len(tds) >= 2:
                label = tds[0].get_text(separator=" ", strip=True)
                value = tds[1].get_text(separator=" ", strip=True)
                pairs.append((label, value))

        # Strategy B: <div> or <li> with label/value children (card layouts)
        # Common patterns: <div class="col-6">Label</div><div class="col-6">Value</div>
        for div in soup.find_all("div", class_=re.compile(r"company-info|key-indicator|summary|detail", re.I)):
            children = div.find_all("div", recursive=False)
            for i in range(0, len(children) - 1, 2):
                label = children[i].get_text(separator=" ", strip=True)
                value = children[i + 1].get_text(separator=" ", strip=True)
                if label and value:
                    pairs.append((label, value))

        # Strategy C: <dt>/<dd> definition lists
        for dt in soup.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                pairs.append((dt.get_text(strip=True), dd.get_text(strip=True)))

        # Strategy D: <span>/<strong> label followed by value in same parent
        for span in soup.find_all(["span", "strong", "label"]):
            text = span.get_text(strip=True)
            if not text:
                continue
            # Check if any of our target labels match
            is_target = False
            for patterns in FIELD_PATTERNS.values():
                for pat in patterns:
                    if re.search(pat, text, re.IGNORECASE):
                        is_target = True
                        break
                if is_target:
                    break
            if is_target:
                # Try next sibling text node or next sibling element
                sibling = span.find_next_sibling()
                if sibling:
                    pairs.append((text, sibling.get_text(strip=True)))
                else:
                    # Value might be in the parent after the label
                    parent = span.parent
                    if parent:
                        parent_text = parent.get_text(separator="|", strip=True)
                        parts = parent_text.split("|")
                        for j, part in enumerate(parts):
                            if text in part and j + 1 < len(parts):
                                pairs.append((text, parts[j + 1].strip()))
                                break

        return pairs

    def _parse_numeric(self, value: str) -> Optional[float]:
        """Parse a numeric string, handling commas, %, 'N/A', 'B', 'M', etc.

        Returns None if the value cannot be parsed.
        """
        if not value:
            return None

        value = value.strip()

        # Handle N/A, -, empty
        if value.lower() in ("n/a", "-", "--", "", "null", "none"):
            return None

        # Remove % sign (we keep the number as-is; the field name tells us it's a %)
        value = value.replace("%", "").strip()

        # Handle multiplier suffixes
        multiplier = 1.0
        if value.upper().endswith("B"):
            multiplier = 1_000_000_000
            value = value[:-1].strip()
        elif value.upper().endswith("M"):
            multiplier = 1_000_000
            value = value[:-1].strip()
        elif value.upper().endswith("K"):
            multiplier = 1_000
            value = value[:-1].strip()
        elif value.upper().endswith("CR"):
            multiplier = 10_000_000  # 1 crore = 10 million
            value = value[:-2].strip()
        elif value.upper().endswith("ARAB") or value.upper().endswith("ARBA"):
            multiplier = 1_000_000_000
            value = re.sub(r"(arab|arba)$", "", value, flags=re.IGNORECASE).strip()

        # Strip commas and spaces
        value = value.replace(",", "").replace(" ", "")

        # Handle parenthesized negatives like (12.34)
        negative = False
        if value.startswith("(") and value.endswith(")"):
            negative = True
            value = value[1:-1]

        try:
            num = float(value) * multiplier
            return -num if negative else num
        except (ValueError, TypeError):
            return None

    # -- Cache ---------------------------------------------------------------

    def _load_cache(self) -> dict:
        """Load cached fundamentals. Entries older than 24 hours are kept in
        the file but will be re-fetched on access."""
        if self.CACHE_FILE.exists():
            try:
                return json.loads(self.CACHE_FILE.read_text())
            except (json.JSONDecodeError, OSError) as e:
                print("[fundamental_scraper] Cache load error: %s" % e)
        return {}

    def _save_cache(self):
        """Save the entire cache dict to disk (caller must hold self._lock)."""
        try:
            self.CACHE_FILE.write_text(json.dumps(self.cache, indent=2, default=str))
        except OSError as e:
            print("[fundamental_scraper] Cache save error: %s" % e)

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _empty_result(symbol: str) -> dict:
        """Return a result dict with all fields set to None."""
        return {
            "symbol": symbol,
            "pe": None,
            "eps": None,
            "book_value": None,
            "pb": None,
            "div_yield": None,
            "market_cap": None,
            "high_52w": None,
            "low_52w": None,
            "sector": None,
            "_fetched_at": datetime.now().isoformat(),
        }


# -- CLI entry point ---------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch fundamental data from Sharesansar")
    parser.add_argument("symbols", nargs="*", help="Stock symbols (e.g. NABIL HBL). If empty, reads from sectors.json")
    parser.add_argument("--all", action="store_true", help="Fetch all symbols from sectors.json")
    parser.add_argument("--workers", type=int, default=4, help="Max parallel workers (default: 4)")
    args = parser.parse_args()

    scraper = FundamentalScraper()

    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
    elif args.all:
        sectors_file = ROOT / "data" / "sectors.json"
        if sectors_file.exists():
            data = json.loads(sectors_file.read_text())
            symbols = sorted(data.get("symbol_to_sector", {}).keys())
        else:
            print("[ERROR] No sectors.json found. Provide symbols as arguments.")
            sys.exit(1)
    else:
        print("Usage: python fundamental_scraper.py NABIL HBL ADBL")
        print("       python fundamental_scraper.py --all")
        sys.exit(0)

    print("Fetching fundamentals for %d symbols..." % len(symbols))
    results = scraper.fetch_all(symbols, max_workers=args.workers)

    # Print summary table
    print("\n%-8s %8s %8s %10s %8s %8s %15s" % (
        "Symbol", "P/E", "EPS", "BookVal", "P/B", "Div%", "MarketCap"))
    print("-" * 72)
    for sym in sorted(results.keys()):
        r = results[sym]
        def fmt(v, w=8):
            return ("%.2f" % v).rjust(w) if v is not None else "N/A".rjust(w)
        def fmt_cap(v):
            if v is None:
                return "N/A".rjust(15)
            if v >= 1e9:
                return ("%.2fB" % (v / 1e9)).rjust(15)
            if v >= 1e6:
                return ("%.2fM" % (v / 1e6)).rjust(15)
            return ("%.0f" % v).rjust(15)

        print("%-8s %s %s %s %s %s %s  %s" % (
            sym,
            fmt(r.get("pe")),
            fmt(r.get("eps")),
            fmt(r.get("book_value"), 10),
            fmt(r.get("pb")),
            fmt(r.get("div_yield")),
            fmt_cap(r.get("market_cap")),
            r.get("sector") or "",
        ))

    print("\nCache saved to: %s" % scraper.CACHE_FILE)
