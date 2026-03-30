#!/usr/bin/env python3
"""
fetch_history.py — NEPSE historical data fetcher (single-session, sequential).
Uses ONE shared session (homepage visited once), processes stocks sequentially.
This avoids Sharesansar rate-limiting on new session starts.

Usage:
  python scripts/fetch_history.py              # All stocks, 1 year
  python scripts/fetch_history.py --days 180   # Last 180 days
  python scripts/fetch_history.py --symbol ALICL  # Single stock test
  python scripts/fetch_history.py --resume     # Skip already-fetched stocks
"""
import os, sys, json, time, argparse, urllib.parse
from concurrent.futures import ThreadPoolExecutor

# Force line-buffered stdout so nohup/redirect shows output
sys.stdout.reconfigure(line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'lib'))

import requests
from bs4 import BeautifulSoup

HISTORY_DIR = os.path.join(ROOT, 'data', 'price_history')
COMPANY_ID_CACHE = os.path.join(ROOT, 'data', 'company_ids.json')
PROGRESS_FILE = os.path.join(ROOT, 'data', 'fetch_progress.json')
os.makedirs(HISTORY_DIR, exist_ok=True)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'en-US,en;q=0.9',
    'X-Requested-With': 'XMLHttpRequest',
    'Origin': 'https://www.sharesansar.com',
}


def load_ids():
    if os.path.exists(COMPANY_ID_CACHE):
        try:
            with open(COMPANY_ID_CACHE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_ids(ids):
    with open(COMPANY_ID_CACHE, 'w') as f:
        json.dump(ids, f, indent=2)


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE) as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_progress(done):
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(list(done), f)


def get_all_symbols():
    try:
        r = requests.get(
            'https://merolagani.com/handlers/webrequesthandler.ashx?type=market_summary',
            headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        stocks = r.json().get('stock', {}).get('detail', [])
        return [str(s.get('s', '')).upper() for s in stocks if s.get('s')]
    except Exception as e:
        print('[!] Could not get symbols: %s' % e)
        return []


def make_session():
    """Create session and visit homepage to establish cookies (required by Sharesansar)."""
    session = requests.Session()
    session.headers.update(HEADERS)
    r = session.get('https://www.sharesansar.com/', timeout=15)
    print('[OK] Session ready (status=%d, cookies=%s)' % (r.status_code, list(session.cookies.keys())))
    xsrf = session.cookies.get('XSRF-TOKEN', '')
    if xsrf:
        session.headers['X-XSRF-TOKEN'] = urllib.parse.unquote(xsrf)
    time.sleep(0.5)
    return session


def fetch_one(session, symbol, company_ids, days):
    """Fetch history for one stock using the shared session. Returns (count, status_str)."""
    cid = company_ids.get(symbol, '')
    csrf = ''
    try:
        r = session.get('https://www.sharesansar.com/company/' + symbol.lower(), timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        cid_elem = soup.find(id='companyid')
        if cid_elem:
            cid = cid_elem.get_text(strip=True)
            company_ids[symbol] = cid
        token_inp = soup.find('input', {'name': '_token'})
        if token_inp:
            csrf = token_inp['value']
        xsrf = session.cookies.get('XSRF-TOKEN', '')
        if xsrf:
            session.headers['X-XSRF-TOKEN'] = urllib.parse.unquote(xsrf)
    except Exception as e:
        return 0, 'page error: %s' % str(e)[:50]

    if not cid:
        return 0, 'no company ID'

    time.sleep(0.6)

    all_records = []
    page_size = 50  # Sharesansar rate-limits requests with >50 records
    max_pages = (days // page_size) + 2

    for page in range(max_pages):
        try:
            resp = session.post(
                'https://www.sharesansar.com/company-price-history',
                data={
                    'draw': str(page + 1),
                    'start': str(page * page_size),
                    'length': str(page_size),
                    'company': cid,
                    '_token': csrf,
                },
                headers={'Referer': 'https://www.sharesansar.com/company/' + symbol.lower()},
                timeout=20,
            )

            if resp.status_code == 202:
                time.sleep(4)
                xsrf = session.cookies.get('XSRF-TOKEN', '')
                if xsrf:
                    session.headers['X-XSRF-TOKEN'] = urllib.parse.unquote(xsrf)
                resp = session.post(
                    'https://www.sharesansar.com/company-price-history',
                    data={'draw': str(page+1), 'start': str(page*page_size), 'length': str(page_size), 'company': cid, '_token': csrf},
                    headers={'Referer': 'https://www.sharesansar.com/company/' + symbol.lower()},
                    timeout=20,
                )
                if resp.status_code == 202:
                    if all_records:
                        return len(all_records), 'partial (rate-limited at page %d)' % page
                    return 0, '202 rate-limited'

            j = resp.json()
            rows = j.get('data', [])
            if not rows:
                break

            for row in rows:
                try:
                    date = str(row.get('published_date', row.get('date', '')))[:10]
                    close = float(str(row.get('close', 0)).replace(',', '') or 0)
                    if close > 0 and date and len(date) == 10:
                        all_records.append({
                            'date': date,
                            'lp': close,
                            'pc': float(str(row.get('per_change', 0)).replace(',', '') or 0),
                            'h': float(str(row.get('high', 0)).replace(',', '') or 0),
                            'l': float(str(row.get('low', 0)).replace(',', '') or 0),
                            'op': float(str(row.get('open', 0)).replace(',', '') or 0),
                            'q': float(str(row.get('traded_quantity', 0)).replace(',', '') or 0),
                            't': float(str(row.get('traded_amount', 0)).replace(',', '') or 0),
                        })
                except (ValueError, TypeError):
                    continue

            if len(all_records) >= days:
                break
            time.sleep(0.5)

        except Exception as e:
            break

    if not all_records:
        return 0, 'no data'

    date_range = '%s to %s' % (all_records[-1]['date'], all_records[0]['date'])
    for rec in all_records:
        d = rec['date']
        path = os.path.join(HISTORY_DIR, d + '.json')
        existing = {}
        if os.path.exists(path):
            try:
                with open(path) as f:
                    existing = json.load(f).get('stocks', {})
            except Exception:
                pass
        existing[symbol] = {k: v for k, v in rec.items() if k != 'date'}
        with open(path, 'w') as f:
            json.dump({'date': d, 'stocks': existing}, f)

    return len(all_records), date_range


def worker(symbols, company_ids, days, done, t0):
    """All work runs here in a single ThreadPoolExecutor thread."""
    success = failed = total_records = 0
    session = make_session()

    for i, symbol in enumerate(symbols, 1):
        try:
            count, status = fetch_one(session, symbol, company_ids, days)
            if count > 0:
                success += 1
                total_records += count
                done.add(symbol)
                print('  [%3d/%d] OK %-8s %4d days  %s' % (i, len(symbols), symbol, count, status))
            else:
                failed += 1
                if i <= 20 or i % 30 == 0:
                    print('  [%3d/%d] -- %-8s  %s' % (i, len(symbols), symbol, status))
        except Exception as e:
            failed += 1
            if i <= 20:
                print('  [%3d/%d] !! %-8s  error: %s' % (i, len(symbols), symbol, str(e)[:60]))

        if i % 20 == 0:
            save_ids(company_ids)
            save_progress(done)
            elapsed = time.time() - t0
            rate = i / elapsed * 60 if elapsed > 0 else 0
            eta = (len(symbols) - i) / (rate / 60) if rate > 0 else 0
            print('  ... %d/%d done, %.1f/min, ETA %.0fmin ...' % (i, len(symbols), rate, eta))

        time.sleep(0.75)

    return success, failed, total_records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=365)
    parser.add_argument('--symbol', type=str, default='')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()

    print('\n' + '='*65)
    print('  NEPSE Historical Data Fetcher — %d days' % args.days)
    print('='*65)

    company_ids = load_ids()
    print('[OK] Loaded %d cached company IDs' % len(company_ids))

    done = load_progress() if args.resume else set()
    if done:
        print('[OK] Resuming: %d already done' % len(done))

    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = get_all_symbols()
        if not symbols:
            print('[!] No symbols found.')
            return
        print('[OK] Found %d symbols' % len(symbols))
        if done:
            symbols = [s for s in symbols if s not in done]
            print('[OK] %d remaining' % len(symbols))

    t0 = time.time()

    # Run all in a single worker thread (ThreadPoolExecutor worker avoids main-thread SSL quirks)
    with ThreadPoolExecutor(max_workers=4) as ex:
        fut = ex.submit(worker, symbols, company_ids, args.days, done, t0)
        success, failed, total_records = fut.result()

    save_ids(company_ids)
    save_progress(done)

    elapsed = time.time() - t0
    history_files = len(os.listdir(HISTORY_DIR))
    print('\n' + '='*65)
    print('  DONE: %d stocks, %d records, %d daily files' % (success, total_records, history_files))
    print('  Failed: %d | Time: %.0fs (%.1f min)' % (failed, elapsed, elapsed/60))
    print('  Data: %s' % HISTORY_DIR)
    print('='*65)


if __name__ == '__main__':
    main()
