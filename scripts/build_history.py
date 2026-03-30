#!/usr/bin/env python3
"""
build_history.py — Backfill 1 year of OHLCV price history for all NEPSE stocks.

Fetches from Sharesansar (company-price-history endpoint).
Stores daily snapshots in data/price_history/YYYY-MM-DD.json
Runs in parallel (10 workers) to finish in ~15-20 minutes.

Usage:
  python scripts/build_history.py           # All 342 stocks, 1 year
  python scripts/build_history.py --days 90 # Last 90 days only
  python scripts/build_history.py --symbol ALICL  # Single stock test
"""
import os, sys, json, time, argparse, random
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "lib"))

import requests
from bs4 import BeautifulSoup

HISTORY_DIR = os.path.join(ROOT, "data", "price_history")
COMPANY_ID_CACHE = os.path.join(ROOT, "data", "company_ids.json")
os.makedirs(HISTORY_DIR, exist_ok=True)
os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.sharesansar.com",
}


def load_company_ids() -> dict:
    if os.path.exists(COMPANY_ID_CACHE):
        with open(COMPANY_ID_CACHE) as f:
            return json.load(f)
    return {}


def save_company_ids(ids: dict):
    with open(COMPANY_ID_CACHE, "w") as f:
        json.dump(ids, f, indent=2)


def get_company_id_and_csrf(symbol: str, session: requests.Session) -> tuple:
    """Discover Sharesansar company ID + CSRF token from the company page (single request)."""
    try:
        r = session.get(f"https://www.sharesansar.com/company/{symbol.lower()}",
                        timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        # Company ID
        cid = ""
        elem = soup.find(id="companyid")
        if elem:
            cid = elem.get_text(strip=True)
        if not cid:
            for inp in soup.find_all("input"):
                if inp.get("id") == "companyid" or inp.get("name") == "companyid":
                    cid = inp.get("value", "")
                    break
        if not cid:
            for el in soup.find_all(attrs={"data-company-id": True}):
                cid = el["data-company-id"]
                break
        # CSRF token — must come from same page as company ID
        csrf = ""
        for inp in soup.find_all("input", {"name": "_token"}):
            csrf = inp.get("value", "")
            break
        if not csrf:
            meta = soup.find("meta", {"name": "_token"})
            if meta:
                csrf = meta.get("content", "")
        return cid, csrf
    except Exception:
        pass
    return "", ""


def get_company_id(symbol: str, session: requests.Session) -> str:
    """Discover Sharesansar company ID for a given symbol."""
    cid, _ = get_company_id_and_csrf(symbol, session)
    return cid


def get_csrf_token(session: requests.Session) -> str:
    """Get CSRF token from Sharesansar homepage (fallback only)."""
    try:
        r = session.get("https://www.sharesansar.com/", timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        meta = soup.find("meta", {"name": "_token"})
        if meta:
            return meta.get("content", "")
        for inp in soup.find_all("input", {"name": "_token"}):
            return inp.get("value", "")
    except Exception:
        pass
    return ""


def fetch_stock_history(symbol: str, company_id: str, csrf_token: str,
                        session: requests.Session, days: int = 365) -> list:
    """Fetch OHLCV history for one stock from Sharesansar."""
    all_records = []
    page_size = 100
    max_pages = (days // page_size) + 2

    for page in range(max_pages):
        try:
            resp = session.post(
                "https://www.sharesansar.com/company-price-history",
                data={
                    "draw": str(page + 1),
                    "start": str(page * page_size),
                    "length": str(page_size),
                    "company": company_id,
                    "_token": csrf_token,
                },
                headers={"Referer": f"https://www.sharesansar.com/company/{symbol.lower()}"},
                timeout=20,
            )
            data = resp.json()
            rows = data.get("data", [])
            if not rows:
                # 202 = rate-limited; retry once after longer wait
                if resp.status_code == 202:
                    time.sleep(5)
                    continue
                break

            for row in rows:
                # Row format: [date, open, high, low, close, volume, turnover, ...]
                try:
                    if isinstance(row, list):
                        date_str  = str(row[0]).strip()
                        open_p    = float(str(row[1]).replace(",","").strip() or 0)
                        high_p    = float(str(row[2]).replace(",","").strip() or 0)
                        low_p     = float(str(row[3]).replace(",","").strip() or 0)
                        close_p   = float(str(row[4]).replace(",","").strip() or 0)
                        volume    = float(str(row[5]).replace(",","").strip() or 0)
                        turnover  = float(str(row[6]).replace(",","").strip() or 0) if len(row) > 6 else 0
                    elif isinstance(row, dict):
                        date_str  = str(row.get("published_date", row.get("date", "")))
                        open_p    = float(str(row.get("open", 0)).replace(",","") or 0)
                        high_p    = float(str(row.get("high", 0)).replace(",","") or 0)
                        low_p     = float(str(row.get("low", 0)).replace(",","") or 0)
                        close_p   = float(str(row.get("close", row.get("ltp", 0))).replace(",","") or 0)
                        # Sharesansar uses traded_quantity / traded_amount
                        volume    = float(str(row.get("traded_quantity", row.get("volume", row.get("qty", 0)))).replace(",","") or 0)
                        turnover  = float(str(row.get("traded_amount", row.get("turnover", 0))).replace(",","") or 0)
                    else:
                        continue

                    if close_p > 0 and date_str:
                        # Use per_change if available (accurate prev-close %, not open-to-close)
                        if isinstance(row, dict) and row.get("per_change") is not None:
                            try:
                                pc = float(str(row["per_change"]).replace(",","") or 0)
                            except (ValueError, TypeError):
                                prev = open_p if open_p > 0 else close_p
                                pc = (close_p - prev) / prev * 100 if prev > 0 else 0
                        else:
                            prev = open_p if open_p > 0 else close_p
                            pc = (close_p - prev) / prev * 100 if prev > 0 else 0
                        all_records.append({
                            "date": date_str[:10],
                            "open": open_p, "high": high_p,
                            "low": low_p, "close": close_p,
                            "lp": close_p, "pc": round(pc, 3),
                            "volume": volume, "turnover": turnover,
                        })
                except (ValueError, IndexError, TypeError):
                    continue

            # Stop if we have enough days
            if len(all_records) >= days:
                break
            time.sleep(0.3)  # polite delay

        except Exception as e:
            break

    return all_records


def records_to_daily_snapshots(symbol: str, records: list) -> dict:
    """Convert list of records into {date: {symbol: ohlcv}} format."""
    snapshots = {}
    for rec in records:
        date = rec.get("date", "")
        if date:
            if date not in snapshots:
                snapshots[date] = {}
            snapshots[date][symbol] = {
                "lp": rec["close"], "pc": rec["pc"],
                "h": rec["high"], "l": rec["low"],
                "op": rec["open"], "t": rec["turnover"],
                "q": rec["volume"],
            }
    return snapshots


def merge_into_history_files(all_snapshots: dict):
    """Merge new data into existing daily history files."""
    for date, stocks in all_snapshots.items():
        path = os.path.join(HISTORY_DIR, f"{date}.json")
        existing = {}
        if os.path.exists(path):
            try:
                with open(path) as f:
                    existing = json.load(f).get("stocks", {})
            except Exception:
                pass
        existing.update(stocks)
        with open(path, "w") as f:
            json.dump({"date": date, "stocks": existing}, f)


def process_symbol(symbol: str, company_ids: dict, csrf_token: str, days: int) -> tuple:
    """Worker function: fetch + store history for one symbol."""
    import urllib.parse
    session = requests.Session()
    session.headers.update(HEADERS)

    # Stagger worker start times to avoid IP rate-limiting by Sharesansar
    time.sleep(random.uniform(1.0, 5.0))

    # Step 1: Visit homepage to establish session (required by Sharesansar)
    try:
        session.get("https://www.sharesansar.com/", timeout=15)
    except Exception:
        pass
    time.sleep(1.0)

    cid = company_ids.get(symbol, "")
    # Step 2: Visit company page to get CID + CSRF
    cid_new, csrf_fresh = get_company_id_and_csrf(symbol, session)
    if not cid and cid_new:
        cid = cid_new
        company_ids[symbol] = cid
    elif cid_new:
        cid = cid_new  # refresh even if cached
    effective_csrf = csrf_fresh if csrf_fresh else csrf_token

    if not cid:
        return symbol, 0, "no company ID"

    # Step 3: Add X-XSRF-TOKEN header for subsequent requests (Laravel CSRF)
    xsrf = session.cookies.get("XSRF-TOKEN", "")
    if xsrf:
        session.headers.update({"X-XSRF-TOKEN": urllib.parse.unquote(xsrf)})

    time.sleep(1.0)

    records = fetch_stock_history(symbol, cid, effective_csrf, session, days)
    if not records:
        return symbol, 0, "no data"

    snapshots = records_to_daily_snapshots(symbol, records)
    merge_into_history_files(snapshots)
    return symbol, len(records), f"ok ({records[0]['date']} to {records[-1]['date']})"


def get_all_symbols() -> list:
    """Get all tradeable NEPSE symbols from MeroLagani."""
    try:
        r = requests.get(
            "https://merolagani.com/handlers/webrequesthandler.ashx?type=market_summary",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        data = r.json()
        stocks = data.get("stock", {}).get("detail", [])
        syms = [str(s.get("s","")).upper() for s in stocks if s.get("s")]
        return [s for s in syms if s]
    except Exception as e:
        print(f"[!] Could not get symbols: {e}")
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365, help="Days of history to fetch")
    parser.add_argument("--symbol", type=str, default="", help="Single symbol to test")
    parser.add_argument("--workers", type=int, default=6, help="Parallel workers")
    args = parser.parse_args()

    print(f"\n{'='*65}")
    print(f"  NEPSE Historical Data Builder — {args.days} days")
    print(f"{'='*65}")

    # Load cached company IDs
    company_ids = load_company_ids()
    print(f"[OK] Loaded {len(company_ids)} cached company IDs")
    # Each worker fetches its own CSRF from the company page (homepage CSRF causes rate-limiting)
    csrf = ""  # not used; workers get per-page CSRF

    # Get symbols
    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = get_all_symbols()
        print(f"[OK] Found {len(symbols)} symbols to process")

    # Process
    t0 = time.time()
    success, failed, total_records = 0, 0, 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process_symbol, sym, company_ids, csrf, args.days): sym
            for sym in symbols
        }
        for i, future in enumerate(as_completed(futures), 1):
            sym = futures[future]
            try:
                sym_out, count, status = future.result()
                if count > 0:
                    success += 1
                    total_records += count
                    print(f"  [{i:3d}/{len(symbols)}] [OK] {sym_out:8s} {count:4d} days  {status}")
                else:
                    failed += 1
                    if i <= 20 or i % 20 == 0:  # show first 20 + every 20th
                        print(f"  [{i:3d}/{len(symbols)}] [FAIL] {sym_out:8s}  {status}")
            except Exception as e:
                failed += 1
                print(f"  [{i:3d}/{len(symbols)}] [FAIL] {sym:8s}  error: {e}")

    # Save updated company IDs
    save_company_ids(company_ids)

    elapsed = time.time() - t0
    history_files = len(os.listdir(HISTORY_DIR))
    print(f"\n{'='*65}")
    print(f"  COMPLETE: {success} stocks, {total_records:,} records, {history_files} daily files")
    print(f"  Failed: {failed} | Time: {elapsed:.0f}s")
    print(f"  History dir: {HISTORY_DIR}")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    main()
