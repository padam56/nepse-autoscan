"""
src/live_prices.py -- Fetch real-time NEPSE prices from Sharesansar live trading.

Returns current LTP, change, OHLC for all traded stocks.
Used by: daily scanner, telegram bot, afternoon scan, dashboard.
"""
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = ROOT / "data" / "live_prices_cache.json"
NPT = timezone(timedelta(hours=5, minutes=45))
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
CACHE_TTL = 120  # seconds


def fetch_live_prices() -> dict:
    """Fetch current prices from Sharesansar live trading page.

    Returns: {SYMBOL: {lp, pc, op, h, l, q, t}, ...}
    Uses cache if data is < 2 minutes old.
    """
    # Check cache
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text())
            age = time.time() - cache.get("timestamp", 0)
            if age < CACHE_TTL:
                return cache.get("prices", {})
        except Exception:
            pass

    prices = {}
    try:
        r = requests.get("https://www.sharesansar.com/live-trading",
                         headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")

        table = soup.find("table", {"class": "table"}) or soup.find("table")
        if not table:
            return _load_fallback()

        rows = table.find_all("tr")
        for row in rows[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 10:
                continue
            try:
                sym = cells[1].strip()
                if not sym:
                    continue
                ltp = _parse_num(cells[2])
                vol = _parse_num(cells[8])
                prev_close = _parse_num(cells[9])
                # Estimate turnover as volume * LTP (Sharesansar live page doesn't show turnover)
                turnover = vol * ltp if vol > 0 and ltp > 0 else 0
                prices[sym] = {
                    "lp": ltp,
                    "pc": _parse_num(cells[4]),
                    "op": _parse_num(cells[5]),
                    "h": _parse_num(cells[6]),
                    "l": _parse_num(cells[7]),
                    "q": vol,
                    "t": turnover,
                    "prev_close": prev_close,
                }
            except (IndexError, ValueError):
                continue

        if prices:
            # Save cache atomically (write to .tmp then rename)
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = CACHE_FILE.with_suffix(".tmp")
            tmp_file.write_text(json.dumps({
                "timestamp": time.time(),
                "fetched_at": datetime.now(NPT).isoformat(),
                "count": len(prices),
                "prices": prices,
            }, indent=2))
            tmp_file.rename(CACHE_FILE)

    except Exception as e:
        print(f"[LIVE] Fetch failed: {e}")
        return _load_fallback()

    return prices


def _parse_num(text: str) -> float:
    """Parse a number string, handling commas and dashes."""
    text = text.strip().replace(",", "").replace("−", "-")
    if not text or text == "-" or text == "--":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def fetch_market_indices() -> dict:
    """Fetch NEPSE, Sensitive, Float, Banking index values from Sharesansar.

    Returns: {
        'NEPSE': {'value': 2879.11, 'change': -2.40, 'turnover': 15033310868},
        'Sensitive': {...}, 'Float': {...}, 'Banking': {...}
    }
    """
    import re
    indices = {}
    try:
        r = requests.get("https://www.sharesansar.com/live-trading",
                         headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")

        for div in soup.find_all("div", class_="market-update"):
            text = div.get_text(" ", strip=True)
            patterns = [
                ("NEPSE", r'NEPSE Index\s*([\d,]+)\s*([\d,.]+)\s*(-?[\d.]+)%'),
                ("Sensitive", r'Sensitive Index\s*([\d,]+)\s*([\d,.]+)\s*(-?[\d.]+)%'),
                ("Float", r'Float Index\s*([\d,]+)\s*([\d,.]+)\s*(-?[\d.]+)%'),
                ("Sen. Float", r'Sensitive Float\s*\w*\.?\s*([\d,]+)\s*([\d,.]+)\s*(-?[\d.]+)%'),
                ("Banking", r'Banking SubIndex\s*([\d,]+)\s*([\d,.]+)\s*(-?[\d.]+)%'),
            ]
            for name, pattern in patterns:
                m = re.search(pattern, text)
                if m:
                    indices[name] = {
                        "value": float(m.group(2).replace(",", "")),
                        "change": float(m.group(3)),
                        "turnover": float(m.group(1).replace(",", "")),
                    }
    except Exception as e:
        print(f"[LIVE] Index fetch failed: {e}")
    return indices


def _load_fallback() -> dict:
    """Fall back to cached prices or latest price_history file."""
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text()).get("prices", {})
        except Exception:
            pass

    # Last resort: latest price_history file
    hist_dir = ROOT / "data" / "price_history"
    if hist_dir.exists():
        files = sorted(hist_dir.glob("*.json"))
        if files:
            try:
                return json.loads(files[-1].read_text()).get("stocks", {})
            except Exception:
                pass
    return {}


def get_price(symbol: str) -> float:
    """Get current price for a single symbol."""
    prices = fetch_live_prices()
    return prices.get(symbol, {}).get("lp", 0)


def save_to_history():
    """Save current live prices to today's price_history file."""
    prices = fetch_live_prices()
    if not prices:
        return

    today = datetime.now(NPT).strftime("%Y-%m-%d")
    hist_file = ROOT / "data" / "price_history" / f"{today}.json"

    data = {"date": today, "stocks": prices}
    hist_file.parent.mkdir(parents=True, exist_ok=True)
    hist_file.write_text(json.dumps(data))
    print(f"[LIVE] Saved {len(prices)} prices to {hist_file.name}")


if __name__ == "__main__":
    prices = fetch_live_prices()
    print(f"Fetched {len(prices)} live prices")
    for sym in ["TTL", "ALICL", "NABIL", "BHCL", "SANVI"]:
        p = prices.get(sym, {})
        if p:
            print(f"  {sym}: Rs {p['lp']:,.1f} ({p['pc']:+.2f}%)")
