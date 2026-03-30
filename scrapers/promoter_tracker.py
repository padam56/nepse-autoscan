"""
Promoter Holding Tracker -- Sharesansar company pages.
Tracks changes in promoter/public holding percentages for NEPSE stocks.
Flags significant changes (>1%) that may indicate insider selling or buying.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE_URL = "https://www.sharesansar.com"
HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Patterns to find promoter/public holding percentages on company pages
PROMOTER_PATTERNS = [
    r"promoter.*?hold",
    r"promoter.*?share",
    r"promoter\s*%",
    r"promoter\s*\(",
]
PUBLIC_PATTERNS = [
    r"public.*?hold",
    r"public.*?share",
    r"public\s*%",
    r"public\s*\(",
]

# Threshold for "significant" change
CHANGE_THRESHOLD_PCT = 1.0


class PromoterTracker:
    """Track promoter holding changes from Sharesansar."""

    CACHE_FILE = ROOT / "data" / "promoter_holdings.json"

    def __init__(self):
        self.session = self._make_session()
        self.cache = self._load_cache()

    def _make_session(self) -> requests.Session:
        """Create a session with cookies from Sharesansar homepage."""
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            r = session.get(BASE_URL + "/", timeout=15)
            xsrf = session.cookies.get("XSRF-TOKEN", "")
            if xsrf:
                session.headers["X-XSRF-TOKEN"] = urllib.parse.unquote(xsrf)
        except Exception as e:
            print("[promoter_tracker] Session init warning: %s" % e)
        return session

    def fetch_promoter_data(self, symbol: str) -> dict:
        """Fetch promoter holding % from Sharesansar company page.

        Returns {symbol, promoter_pct, public_pct, last_updated}.
        Values are None if parsing fails.
        """
        symbol = symbol.upper().strip()
        url = "%s/company/%s" % (BASE_URL, symbol.lower())

        result = {
            "symbol":       symbol,
            "promoter_pct": None,
            "public_pct":   None,
            "last_updated": datetime.now().isoformat(),
        }

        try:
            resp = self.session.get(url, timeout=20)
            if resp.status_code != 200:
                print("[promoter_tracker] HTTP %d for %s" % (resp.status_code, symbol))
                return result
        except Exception as e:
            print("[promoter_tracker] Request failed for %s: %s" % (symbol, e))
            return result

        soup = BeautifulSoup(resp.text, "html.parser")
        pairs = self._extract_label_value_pairs(soup)

        for label_raw, value_raw in pairs:
            label = label_raw.lower().strip()
            value = value_raw.strip()

            # Check for promoter holding
            if result["promoter_pct"] is None:
                for pat in PROMOTER_PATTERNS:
                    if re.search(pat, label, re.IGNORECASE):
                        parsed = self._parse_pct(value)
                        if parsed is not None:
                            result["promoter_pct"] = parsed
                        break

            # Check for public holding
            if result["public_pct"] is None:
                for pat in PUBLIC_PATTERNS:
                    if re.search(pat, label, re.IGNORECASE):
                        parsed = self._parse_pct(value)
                        if parsed is not None:
                            result["public_pct"] = parsed
                        break

        # If we got promoter but not public, infer public = 100 - promoter
        if result["promoter_pct"] is not None and result["public_pct"] is None:
            result["public_pct"] = round(100.0 - result["promoter_pct"], 2)
        elif result["public_pct"] is not None and result["promoter_pct"] is None:
            result["promoter_pct"] = round(100.0 - result["public_pct"], 2)

        # Update cache
        old_entry = self.cache.get(symbol, {})
        if old_entry and result["promoter_pct"] is not None:
            result["prev_promoter_pct"] = old_entry.get("promoter_pct")
            result["prev_public_pct"]   = old_entry.get("public_pct")
            result["prev_updated"]      = old_entry.get("last_updated")

        self.cache[symbol] = result
        self._save_cache()

        return result

    def detect_changes(self) -> list:
        """Compare current promoter holdings vs cached values.

        Fetches fresh data for all cached symbols, then returns a list of
        dicts for symbols where the change exceeds CHANGE_THRESHOLD_PCT:
            {symbol, old_pct, new_pct, change, direction}
        """
        symbols = list(self.cache.keys())
        if not symbols:
            print("[promoter_tracker] No cached symbols to check")
            return []

        # Save old values before refresh
        old_values = {}
        for sym, entry in self.cache.items():
            pct = entry.get("promoter_pct")
            if pct is not None:
                old_values[sym] = pct

        changes = []
        for i, sym in enumerate(symbols):
            try:
                fresh = self.fetch_promoter_data(sym)
                new_pct = fresh.get("promoter_pct")
                old_pct = old_values.get(sym)

                if old_pct is not None and new_pct is not None:
                    delta = new_pct - old_pct
                    if abs(delta) >= CHANGE_THRESHOLD_PCT:
                        changes.append({
                            "symbol":    sym,
                            "old_pct":   round(old_pct, 2),
                            "new_pct":   round(new_pct, 2),
                            "change":    round(delta, 2),
                            "direction": "buying" if delta > 0 else "selling",
                        })

                # Rate limit
                if i < len(symbols) - 1:
                    time.sleep(0.5)
            except Exception as e:
                print("[promoter_tracker] Error checking %s: %s" % (sym, e))

        return changes

    def get_warnings(self, symbols: list) -> list:
        """Check if any symbols have recent promoter selling.

        Fetches fresh promoter data for each symbol and compares against
        cached values. Returns warning strings for picks where promoters
        are reducing their stake.
        """
        warnings = []
        for sym in symbols:
            sym = sym.upper().strip()
            old_entry = self.cache.get(sym, {})
            old_pct = old_entry.get("promoter_pct")

            try:
                fresh = self.fetch_promoter_data(sym)
                new_pct = fresh.get("promoter_pct")

                if old_pct is not None and new_pct is not None:
                    delta = new_pct - old_pct
                    if delta < -CHANGE_THRESHOLD_PCT:
                        warnings.append(
                            "%s: promoter holding dropped %.1f%% -> %.1f%% (-%0.1f%% change)"
                            % (sym, old_pct, new_pct, abs(delta))
                        )
                elif new_pct is not None and new_pct < 40:
                    # Low promoter holding is a flag even without history
                    warnings.append(
                        "%s: low promoter holding at %.1f%%" % (sym, new_pct)
                    )

                time.sleep(0.3)
            except Exception as e:
                print("[promoter_tracker] Warning check failed for %s: %s" % (sym, e))

        return warnings

    def fetch_bulk(self, symbols: list) -> Dict[str, dict]:
        """Fetch promoter data for multiple symbols. Returns {symbol: data}."""
        results = {}
        for i, sym in enumerate(symbols):
            sym = sym.upper().strip()
            try:
                data = self.fetch_promoter_data(sym)
                results[sym] = data
                if i < len(symbols) - 1:
                    time.sleep(0.5)
            except Exception as e:
                print("[promoter_tracker] Error fetching %s: %s" % (sym, e))
                results[sym] = {
                    "symbol": sym,
                    "promoter_pct": None,
                    "public_pct": None,
                    "last_updated": datetime.now().isoformat(),
                }
        return results

    # -- Parsing helpers -------------------------------------------------------

    def _extract_label_value_pairs(self, soup: BeautifulSoup) -> list:
        """Extract label-value pairs from the company page HTML.

        Uses the same strategies as fundamental_scraper.py.
        """
        pairs = []

        # Table rows
        for tr in soup.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if len(tds) >= 2:
                label = tds[0].get_text(separator=" ", strip=True)
                value = tds[1].get_text(separator=" ", strip=True)
                pairs.append((label, value))

        # Card-style divs
        for div in soup.find_all("div", class_=re.compile(
                r"company-info|key-indicator|summary|detail|shareholding", re.I)):
            children = div.find_all("div", recursive=False)
            for i in range(0, len(children) - 1, 2):
                label = children[i].get_text(separator=" ", strip=True)
                value = children[i + 1].get_text(separator=" ", strip=True)
                if label and value:
                    pairs.append((label, value))

        # Definition lists
        for dt in soup.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                pairs.append((dt.get_text(strip=True), dd.get_text(strip=True)))

        # Span/strong labels
        for span in soup.find_all(["span", "strong", "label"]):
            text = span.get_text(strip=True)
            if not text:
                continue
            is_target = False
            for patterns in (PROMOTER_PATTERNS, PUBLIC_PATTERNS):
                for pat in patterns:
                    if re.search(pat, text, re.IGNORECASE):
                        is_target = True
                        break
                if is_target:
                    break
            if is_target:
                sibling = span.find_next_sibling()
                if sibling:
                    pairs.append((text, sibling.get_text(strip=True)))
                else:
                    parent = span.parent
                    if parent:
                        parent_text = parent.get_text(separator="|", strip=True)
                        parts = parent_text.split("|")
                        for j, part in enumerate(parts):
                            if text in part and j + 1 < len(parts):
                                pairs.append((text, parts[j + 1].strip()))
                                break

        return pairs

    def _parse_pct(self, value: str) -> Optional[float]:
        """Parse a percentage string like '51.23%' or '51.23' into a float."""
        if not value:
            return None
        value = value.strip().replace("%", "").replace(",", "").strip()
        if value.lower() in ("n/a", "-", "--", "", "null", "none"):
            return None
        try:
            num = float(value)
            # Sanity check: holding percentage should be 0-100
            if 0 <= num <= 100:
                return round(num, 2)
            return None
        except (ValueError, TypeError):
            return None

    # -- Cache -----------------------------------------------------------------

    def _load_cache(self) -> dict:
        if self.CACHE_FILE.exists():
            try:
                return json.loads(self.CACHE_FILE.read_text())
            except (json.JSONDecodeError, OSError) as e:
                print("[promoter_tracker] Cache load error: %s" % e)
        return {}

    def _save_cache(self):
        try:
            self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.CACHE_FILE.write_text(json.dumps(self.cache, indent=2, default=str))
        except OSError as e:
            print("[promoter_tracker] Cache save error: %s" % e)


# -- CLI entry point -----------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Track promoter holding changes")
    parser.add_argument("symbols", nargs="*", help="Symbols to check (e.g. NABIL HBL)")
    parser.add_argument("--detect-changes", action="store_true",
                        help="Compare all cached symbols against fresh data")
    parser.add_argument("--warnings", nargs="*", metavar="SYM",
                        help="Check specific symbols for promoter selling warnings")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    tracker = PromoterTracker()

    if args.detect_changes:
        changes = tracker.detect_changes()
        if args.json:
            print(json.dumps(changes, indent=2))
        elif changes:
            print("\nSignificant promoter holding changes:")
            print("%-8s %10s %10s %8s  %s" % ("Symbol", "Old%", "New%", "Change", "Direction"))
            print("-" * 55)
            for c in changes:
                print("%-8s %9.2f%% %9.2f%% %+7.2f%%  %s" % (
                    c["symbol"], c["old_pct"], c["new_pct"], c["change"], c["direction"]))
        else:
            print("No significant promoter holding changes detected.")

    elif args.warnings is not None:
        syms = args.warnings if args.warnings else args.symbols
        if not syms:
            print("Provide symbols: --warnings NABIL HBL")
            sys.exit(1)
        warns = tracker.get_warnings(syms)
        if args.json:
            print(json.dumps(warns, indent=2))
        elif warns:
            print("\nPromoter warnings:")
            for w in warns:
                print("  - %s" % w)
        else:
            print("No promoter warnings for: %s" % ", ".join(syms))

    elif args.symbols:
        results = tracker.fetch_bulk(args.symbols)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print("\n%-8s %12s %12s  %s" % ("Symbol", "Promoter%", "Public%", "Updated"))
            print("-" * 50)
            for sym, data in sorted(results.items()):
                p = data.get("promoter_pct")
                pub = data.get("public_pct")
                print("%-8s %11s%% %11s%%  %s" % (
                    sym,
                    "%.2f" % p if p is not None else "N/A",
                    "%.2f" % pub if pub is not None else "N/A",
                    data.get("last_updated", "")[:10],
                ))
            print("\nCache: %s" % tracker.CACHE_FILE)

    else:
        print("Usage:")
        print("  python promoter_tracker.py NABIL HBL        # fetch promoter data")
        print("  python promoter_tracker.py --detect-changes  # check all cached symbols")
        print("  python promoter_tracker.py --warnings NABIL  # check for selling warnings")
