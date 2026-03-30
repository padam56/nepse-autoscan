#!/usr/bin/env python3
"""
scripts/backfill_history.py — Backfill NEPSE price history from Sharesansar
════════════════════════════════════════════════════════════════════════════════
Fetches 5 years of daily OHLCV for all NEPSE stocks and saves to
data/price_history/YYYY-MM-DD.json (same format as existing files).

Usage:
  python scripts/backfill_history.py                  # all stocks, 5 years
  python scripts/backfill_history.py --years 3        # 3 years
  python scripts/backfill_history.py --symbol ADBL    # single stock
  python scripts/backfill_history.py --from 2020-01-01 --to 2024-01-01

Sharesansar rate-limits aggressively — use --delay 2 (default) between requests.
"""

from __future__ import annotations
import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

# Force line-buffered stdout so nohup/redirect shows output
sys.stdout.reconfigure(line_buffering=True)

ROOT       = Path(__file__).parent.parent
HIST_DIR   = ROOT / "data" / "price_history"
SECTORS_F  = ROOT / "data" / "sectors.json"
HIST_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL   = "https://www.sharesansar.com"
HEADERS    = {
    "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "Accept-Language":  "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Origin":           "https://www.sharesansar.com",
}


# ── Session handling (modeled after fetch_history.py) ────────────────────────

def make_session() -> requests.Session:
    """Create a requests.Session and visit the homepage to establish cookies.
    Each thread worker must call this to get its OWN session."""
    session = requests.Session()
    session.headers.update(HEADERS)
    r = session.get(BASE_URL + "/", timeout=15)
    print("[SESSION] status=%d  cookies=%s" % (r.status_code, list(session.cookies.keys())))

    # Set X-XSRF-TOKEN header from the XSRF-TOKEN cookie (URL-decoded)
    xsrf = session.cookies.get("XSRF-TOKEN", "")
    if xsrf:
        session.headers["X-XSRF-TOKEN"] = urllib.parse.unquote(xsrf)

    time.sleep(0.5)
    return session


def _refresh_xsrf(session: requests.Session) -> None:
    """Update X-XSRF-TOKEN header if cookie was refreshed by the server."""
    xsrf = session.cookies.get("XSRF-TOKEN", "")
    if xsrf:
        session.headers["X-XSRF-TOKEN"] = urllib.parse.unquote(xsrf)


def get_company_id_and_token(symbol: str, session: requests.Session) -> Tuple[Optional[int], str]:
    """Scrape company page to get numeric company ID and _token for POST forms."""
    url = "%s/company/%s" % (BASE_URL, symbol.lower())
    try:
        r = session.get(url, timeout=15)
    except Exception as e:
        print("  [GET] %s -> %s" % (url, e))
        return None, ""

    soup = BeautifulSoup(r.text, "html.parser")

    # Extract company ID
    cid = None
    cid_elem = soup.find(id="companyid")
    if cid_elem:
        text = cid_elem.get_text(strip=True)
        if text.isdigit():
            cid = int(text)

    if cid is None:
        m = re.search(r'company[_-]?id["\']?\s*[:=]\s*["\']?(\d+)', r.text, re.IGNORECASE)
        if m:
            cid = int(m.group(1))

    if cid is None:
        m = re.search(r'["\']company["\']\s*,\s*["\']?(\d+)', r.text)
        if m:
            cid = int(m.group(1))

    # Extract _token from hidden input
    csrf_token = ""
    token_inp = soup.find("input", {"name": "_token"})
    if token_inp:
        csrf_token = token_inp.get("value", "")

    # Refresh XSRF header from updated cookies
    _refresh_xsrf(session)

    return cid, csrf_token


def fetch_price_history(company_id: int, symbol: str, session: requests.Session,
                        csrf_token: str, date_from: str, date_to: str) -> List[dict]:
    """
    Fetch OHLCV history from Sharesansar DataTables endpoint.
    Returns list of {date, op, h, l, lp, pc, q, t}.
    """
    url      = BASE_URL + "/company-price-history"
    referer  = "%s/company/%s" % (BASE_URL, symbol.lower())
    all_rows = []
    start    = 0
    length   = 50   # Sharesansar caps at 50 (returns 202 for larger)

    while True:
        payload = {
            "company":   str(company_id),
            "draw":      str(start // length + 1),
            "start":     str(start),
            "length":    str(length),
            "date_from": date_from,
            "date_to":   date_to,
            "_token":    csrf_token,
        }
        try:
            resp = session.post(url, data=payload,
                                headers={"Referer": referer}, timeout=20)

            # Handle rate-limiting (202) with one retry
            if resp.status_code == 202:
                time.sleep(4)
                _refresh_xsrf(session)
                resp = session.post(url, data=payload,
                                    headers={"Referer": referer}, timeout=20)
                if resp.status_code == 202:
                    if all_rows:
                        break  # return partial results
                    return all_rows

            if resp.status_code != 200:
                print("  [POST] %s %s -> HTTP %d" % (url, symbol, resp.status_code))
                break

            j = resp.json()
        except Exception as e:
            print("  [POST] %s %s -> %s" % (url, symbol, e))
            break

        rows = j.get("data", [])
        if not rows:
            break

        for row in rows:
            try:
                rec = _parse_row(row, symbol)
                if rec:
                    all_rows.append(rec)
            except Exception:
                continue

        if len(rows) < length:
            break
        start += length
        time.sleep(0.15)

    return all_rows


def _parse_row(row, symbol: str) -> Optional[dict]:
    """Parse a DataTables row into {date, lp, pc, h, l, op, q, t}."""
    # Row is a list: [date_html, close, open, high, low, volume, turnover, ...]
    # OR dict with named keys
    if isinstance(row, dict):
        date_str = row.get("published_date", row.get("date", ""))
        close    = float(str(row.get("close", row.get("close_price", row.get("lp", 0)))).replace(",", "") or 0)
        open_p   = float(str(row.get("open", row.get("open_price", row.get("op", close)))).replace(",", "") or close)
        high     = float(str(row.get("high", row.get("max_price", row.get("h", close)))).replace(",", "") or close)
        low      = float(str(row.get("low", row.get("min_price", row.get("l", close)))).replace(",", "") or close)
        vol      = float(str(row.get("traded_quantity", row.get("total_traded_quantity", row.get("q", 0)))).replace(",", "") or 0)
        turn     = float(str(row.get("traded_amount", row.get("total_traded_value", row.get("t", 0)))).replace(",", "") or 0)
        prev_cl  = float(str(row.get("previous_closing", 0)).replace(",", "") or close)
    else:
        # List format — strip HTML tags
        def clean(v):
            return re.sub(r"<[^>]+>", "", str(v)).strip().replace(",", "")

        if len(row) < 5:
            return None
        date_str = clean(row[0])
        close    = float(clean(row[1]) or 0)
        open_p   = float(clean(row[2]) or close)
        high     = float(clean(row[3]) or close)
        low      = float(clean(row[4]) or close)
        vol      = float(clean(row[5]) if len(row) > 5 else 0)
        turn     = float(clean(row[6]) if len(row) > 6 else 0)
        prev_cl  = float(clean(row[7]) if len(row) > 7 else close)

    # Normalise date
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%B %d, %Y", "%d %b %Y"):
        try:
            date_str = datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
            break
        except ValueError:
            continue

    if not re.match(r"\d{4}-\d{2}-\d{2}", date_str):
        return None

    if close <= 0:
        return None

    pc = ((close / prev_cl) - 1) * 100 if prev_cl > 0 else 0.0

    return {
        "date": date_str,
        "lp":   round(close, 2),
        "pc":   round(pc, 2),
        "h":    round(high, 2),
        "l":    round(low, 2),
        "op":   round(open_p, 2),
        "q":    round(vol, 0),
        "t":    round(turn, 0),
    }


# ── Per-file locks for date file writes ──────────────────────────────────────

_file_locks: Dict[str, threading.Lock] = {}
_file_locks_meta = threading.Lock()


def _get_file_lock(date_str: str) -> threading.Lock:
    """Return a lock specific to the given date file."""
    with _file_locks_meta:
        if date_str not in _file_locks:
            _file_locks[date_str] = threading.Lock()
        return _file_locks[date_str]


def merge_to_date_files(symbol: str, records: List[dict]) -> int:
    """Merge stock records into existing per-date JSON files (thread-safe)."""
    saved = 0
    for rec in records:
        d    = rec["date"]
        path = HIST_DIR / ("%s.json" % d)
        lock = _get_file_lock(d)

        with lock:
            if path.exists():
                try:
                    day = json.loads(path.read_text())
                except Exception:
                    day = {"date": d, "stocks": {}}
            else:
                day = {"date": d, "stocks": {}}

            day["stocks"][symbol] = {k: v for k, v in rec.items() if k != "date"}
            path.write_text(json.dumps(day))
        saved += 1

    return saved


# ── Symbol list ───────────────────────────────────────────────────────────────

def get_all_symbols() -> List[str]:
    """Load all symbols from sectors.json + existing price history."""
    syms = set()

    if SECTORS_F.exists():
        data = json.loads(SECTORS_F.read_text())
        syms.update(data.get("symbol_to_sector", {}).keys())

    # Also pick up symbols already in history
    for f in HIST_DIR.glob("*.json"):
        try:
            day = json.loads(f.read_text())
            syms.update(day.get("stocks", {}).keys())
        except Exception:
            continue

    return sorted(syms)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backfill NEPSE price history from Sharesansar")
    parser.add_argument("--symbol",  help="Single stock symbol (default: all)")
    parser.add_argument("--years",   type=int, default=5, help="Years of history to fetch (default: 5)")
    parser.add_argument("--from",    dest="date_from", help="Start date YYYY-MM-DD")
    parser.add_argument("--to",      dest="date_to",   help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--delay",   type=float, default=0.3, help="Seconds between requests (default: 0.3)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip symbols with enough data already")
    args = parser.parse_args()

    date_to   = args.date_to   or date.today().strftime("%Y-%m-%d")
    date_from = args.date_from or (date.today() - timedelta(days=365 * args.years)).strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"  NEPSE Backfill  {date_from} → {date_to}")
    print(f"{'='*60}\n")

    # Get symbols
    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = get_all_symbols()
        print(f"[INFO] {len(symbols)} symbols to process")

    # Verify connectivity by creating a test session
    test_session = make_session()
    xsrf = test_session.cookies.get("XSRF-TOKEN", "")
    if not xsrf:
        print("[ERROR] Could not get XSRF-TOKEN cookie -- Sharesansar may be blocking.")
        print("  Try again in a few minutes, or check your internet connection.")
        sys.exit(1)
    del test_session  # each thread will make its own

    # Company ID cache (protected by id_cache_lock)
    id_cache_path = ROOT / "data" / "sharesansar_ids.json"
    id_cache: Dict[str, int] = {}
    if id_cache_path.exists():
        try:
            id_cache = json.loads(id_cache_path.read_text())
        except Exception:
            pass

    total_saved = 0
    failed      = []
    id_cache_lock = threading.Lock()
    semaphore     = threading.Semaphore(4)

    # Thread-local storage so each thread gets its own session
    _thread_local = threading.local()

    def _get_thread_session() -> requests.Session:
        """Return the session for the current thread, creating one if needed."""
        if not hasattr(_thread_local, "session"):
            _thread_local.session = make_session()
        return _thread_local.session

    def _process_symbol(sym):
        """Worker: fetch and merge history for one symbol."""
        with semaphore:
            session = _get_thread_session()

            # Get company ID -- lock the entire read-check-write cycle
            with id_cache_lock:
                cached_cid = id_cache.get(sym)
            if cached_cid is not None:
                cid = cached_cid
                csrf_token = ""
                # Still need to visit company page to get _token for POST
                cid_page, csrf_token = get_company_id_and_token(sym, session)
                if not csrf_token:
                    return sym, 0, "Could not get CSRF _token"
                time.sleep(0.5)
            else:
                cid, csrf_token = get_company_id_and_token(sym, session)
                if not cid:
                    return sym, 0, "Company ID not found"
                if not csrf_token:
                    return sym, 0, "Could not get CSRF _token"
                with id_cache_lock:
                    id_cache[sym] = cid
                    id_cache_path.write_text(json.dumps(id_cache, indent=2))
                time.sleep(0.5)

            # Fetch history
            records = fetch_price_history(cid, sym, session, csrf_token,
                                          date_from, date_to)

            if not records:
                return sym, 0, "No data returned"

            # Merge into date files (uses per-file locking internally)
            n = merge_to_date_files(sym, records)

            time.sleep(args.delay)
            return sym, n, "%d records -> %d date files updated" % (len(records), n)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_process_symbol, sym): sym for sym in symbols}
        for i, future in enumerate(as_completed(futures), 1):
            sym = futures[future]
            try:
                sym_out, count, status = future.result()
                if count > 0:
                    total_saved += count
                    print("  [%3d/%d] [OK] %-8s  %s" % (i, len(symbols), sym_out, status))
                else:
                    failed.append(sym_out)
                    print("  [%3d/%d] [FAIL] %-8s  %s" % (i, len(symbols), sym_out, status))
            except Exception as e:
                failed.append(sym)
                print("  [%3d/%d] [FAIL] %-8s  error: %s" % (i, len(symbols), sym, e))

    print(f"\n{'='*60}")
    print(f"  Done. {total_saved} total records saved.")
    if failed:
        print(f"  Failed ({len(failed)}): {', '.join(failed[:20])}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
