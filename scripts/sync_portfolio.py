#!/usr/bin/env python3
"""
scripts/sync_portfolio.py — MeroShare Portfolio Auto-Sync
══════════════════════════════════════════════════════════
Fetches your current holdings from MeroShare (CDSC portal) and updates
the PORTFOLIO dict in daily_scanner.py automatically.

Credentials are read from .env (never hardcoded):
  MEROSHARE_DP       = 11000               (DP ID, e.g. NMB Capital = 11000)
  MEROSHARE_USERNAME = 794517
  MEROSHARE_PASSWORD = your_password

Usage:
  python scripts/sync_portfolio.py              # sync + print
  python scripts/sync_portfolio.py --dry-run    # fetch but don't write
  python scripts/sync_portfolio.py --json       # output raw JSON

Cron (before daily scanner):
  30 4 * * 0-4 python scripts/sync_portfolio.py && python scripts/daily_scanner.py
"""

from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Load .env ────────────────────────────────────────────────────────────────
def _load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

MEROSHARE_BASE = "https://webbackend.cdsc.com.np/api"
DP_ID          = int(os.getenv("MEROSHARE_DP",       "11000"))
USERNAME       = os.getenv("MEROSHARE_USERNAME",  "")
PASSWORD       = os.getenv("MEROSHARE_PASSWORD",  "")

SCANNER_FILE   = ROOT / "scripts" / "daily_scanner.py"
PORTFOLIO_FILE = ROOT / "data" / "portfolio.json"


# ═══════════════════════════════════════════════════════════════════════════════
# MEROSHARE API CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

class MeroShareClient:
    """
    Thin wrapper around the MeroShare CDSC REST API v2.

    Endpoints used:
      POST /auth/loginDetails       — authenticate, receive JWT
      GET  /portfolio/              — current holdings
      GET  /myPurchaseSources       — purchase price / WACC per stock
    """

    def __init__(self, dp_id: int, username: str, password: str):
        self.dp_id    = dp_id
        self.username = username
        self.password = password
        self._token   = ""
        self._session = None

    def _get_session(self):
        if self._session is None:
            import urllib.request
            self._session = urllib.request  # use stdlib, no requests dependency
        return self._session

    def _post(self, endpoint: str, body: dict) -> dict:
        import urllib.request, urllib.error
        url     = f"{MEROSHARE_BASE}{endpoint}"
        payload = json.dumps(body).encode()
        headers = {
            "Content-Type":  "application/json",
            "Authorization": self._token,
        }
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            raise RuntimeError(f"POST {endpoint} → {e.code}: {body[:300]}")

    def _get(self, endpoint: str, params: str = "") -> dict:
        import urllib.request, urllib.error
        url = f"{MEROSHARE_BASE}{endpoint}"
        if params:
            url += f"?{params}"
        req = urllib.request.Request(url, headers={"Authorization": self._token})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            raise RuntimeError(f"GET {endpoint} → {e.code}: {body[:300]}")

    def login(self) -> bool:
        """Authenticate and store JWT token."""
        try:
            # Correct endpoint: POST /api/meroShare/auth/
            data = self._post("/meroShare/auth/", {
                "clientId": self.dp_id,   # integer DP ID
                "username": self.username,
                "password": self.password,
            })
            token = data.get("token", "")
            if not token:
                print(f"[MEROSHARE] Login failed — no token: {data}")
                return False
            self._token = token
            print(f"[MEROSHARE] Login successful")
            return True
        except Exception as e:
            print(f"[MEROSHARE] Login error: {e}")
            return False

    def get_portfolio(self) -> list:
        """
        Fetch current portfolio holdings.
        Returns list of {script, currentBalance, previousClosingPrice, ...}

        Endpoint: GET /api/meroShare/myPortfolio/
        """
        try:
            all_items = []
            page = 1
            while True:
                data = self._get(
                    "/meroShare/myPortfolio/",
                    f"sortBy=script&sortAsc=true&page={page}&size=200"
                )
                items = data if isinstance(data, list) else data.get("items", data.get("portfolioDetails", []))
                if not items:
                    break
                all_items.extend(items)
                if len(items) < 200:
                    break
                page += 1
            return all_items
        except Exception as e:
            print(f"[MEROSHARE] Portfolio fetch error: {e}")
            return []

    def get_purchase_sources(self) -> list:
        """
        Fetch WACC per stock.
        Endpoint: GET /api/myPurchase/wacc/  (or waccReport)
        """
        try:
            # Primary: myPurchase WACC endpoint
            data = self._get("/myPurchase/wacc/")
            return data if isinstance(data, list) else data.get("items", data.get("waccDetails", []))
        except Exception as e:
            try:
                # Fallback: meroShare ownDetail
                data = self._get("/meroShare/ownDetail/")
                return data if isinstance(data, list) else []
            except Exception:
                print(f"[MEROSHARE] Purchase sources error: {e}")
                return []

    def get_complete_portfolio(self) -> Dict[str, dict]:
        """
        Combine holdings + purchase prices into a clean dict.
        Returns {SYMBOL: {shares, wacc, current_price, unrealized_pnl}}
        """
        portfolio_raw = self.get_portfolio()
        sources_raw   = self.get_purchase_sources()

        # Build WACC map from purchase sources
        wacc_map = {}
        for item in sources_raw:
            sym  = item.get("script", item.get("symbol", item.get("scrip", "")))
            wacc = item.get("wacc", item.get("averagePrice", item.get("costPrice", 0)))
            if sym and wacc:
                wacc_map[sym] = float(wacc)

        result = {}
        for item in portfolio_raw:
            sym    = item.get("script", item.get("symbol", item.get("scrip", "")))
            shares = item.get("currentBalance", item.get("quantity", item.get("balance", 0)))
            price  = item.get("previousClosingPrice", item.get("lastTradedPrice", 0))
            wacc   = wacc_map.get(sym, item.get("wacc", item.get("costPrice", 0)))

            if not sym or not shares:
                continue

            shares_int = int(float(shares)) if shares else 0
            price_f    = float(price) if price else 0.0
            wacc_f     = float(wacc) if wacc else price_f

            if shares_int <= 0:
                continue

            unrealized = shares_int * (price_f - wacc_f) if (price_f > 0 and wacc_f > 0) else 0
            pnl_pct    = (price_f / wacc_f - 1) * 100 if wacc_f > 0 else 0

            result[sym] = {
                "shares":    shares_int,
                "wacc":      round(wacc_f, 2),
                "price":     round(price_f, 2),
                "unrealized": round(unrealized, 0),
                "pnl_pct":   round(pnl_pct, 2),
            }

        return result


# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO UPDATER
# ═══════════════════════════════════════════════════════════════════════════════

def update_scanner_portfolio(portfolio: Dict[str, dict], dry_run: bool = False) -> bool:
    """
    Update the PORTFOLIO constant in daily_scanner.py with fresh data.
    Only updates symbols with valid shares + wacc.
    """
    if not portfolio:
        print("[SYNC] Empty portfolio — nothing to update")
        return False

    if not SCANNER_FILE.exists():
        print(f"[SYNC] Scanner file not found: {SCANNER_FILE}")
        return False

    # Build new PORTFOLIO dict string
    lines = ["PORTFOLIO: Dict[str, dict] = {"]
    for sym, data in sorted(portfolio.items()):
        shares = data["shares"]
        wacc   = data["wacc"]
        price  = data.get("price", 0)
        pnl    = data.get("pnl_pct", 0)
        lines.append(f'    "{sym}": {{"shares": {shares}, "wacc": {wacc:.2f}}},  # LTP={price:.2f}  P&L={pnl:+.1f}%')
    lines.append("}")
    new_portfolio_str = "\n".join(lines)

    # Read current scanner content
    content = SCANNER_FILE.read_text()

    # Replace PORTFOLIO block
    pattern = r'PORTFOLIO: Dict\[str, dict\] = \{[^}]*\}'
    if not re.search(pattern, content, re.DOTALL):
        # Try simpler pattern
        pattern = r'PORTFOLIO\s*=\s*\{[^}]*\}'

    if not re.search(pattern, content, re.DOTALL):
        print("[SYNC] Could not find PORTFOLIO block in scanner — manual update needed")
        print(f"\nNew portfolio:\n{new_portfolio_str}")
        return False

    new_content = re.sub(pattern, new_portfolio_str, content, flags=re.DOTALL)

    if dry_run:
        print("[SYNC] --dry-run: would write:")
        print(new_portfolio_str)
        return True

    SCANNER_FILE.write_text(new_content)
    print(f"[SYNC] Updated PORTFOLIO in {SCANNER_FILE}")
    return True


def save_portfolio_json(portfolio: Dict[str, dict]) -> None:
    """Save portfolio snapshot to data/portfolio.json."""
    PORTFOLIO_FILE.parent.mkdir(exist_ok=True)
    snapshot = {
        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "holdings":  portfolio,
        "summary": {
            "n_stocks":       len(portfolio),
            "total_shares":   sum(p["shares"] for p in portfolio.values()),
            "total_cost":     sum(p["shares"] * p["wacc"] for p in portfolio.values()),
            "total_value":    sum(p["shares"] * p.get("price", p["wacc"]) for p in portfolio.values()),
        }
    }
    PORTFOLIO_FILE.write_text(json.dumps(snapshot, indent=2))
    print(f"[SYNC] Portfolio saved → {PORTFOLIO_FILE}")


def print_portfolio_table(portfolio: Dict[str, dict]) -> None:
    """Pretty-print portfolio to console."""
    if not portfolio:
        print("  (no holdings found)")
        return

    print(f"\n{'Symbol':<10} {'Shares':>7} {'WACC':>8} {'LTP':>8} {'P&L%':>8} {'Unrealized':>12}")
    print("─" * 60)
    total_cost  = 0
    total_value = 0
    for sym, p in sorted(portfolio.items()):
        cost  = p["shares"] * p["wacc"]
        value = p["shares"] * p.get("price", p["wacc"])
        total_cost  += cost
        total_value += value
        arrow = "▲" if p.get("pnl_pct", 0) >= 0 else "▼"
        print(f"  {sym:<8} {p['shares']:>7,} {p['wacc']:>8.2f} {p.get('price', 0):>8.2f} "
              f"{arrow}{abs(p.get('pnl_pct', 0)):>6.1f}% {p.get('unrealized', 0):>+12,.0f}")
    print("─" * 60)
    total_pnl = total_value - total_cost
    total_pct = (total_value / total_cost - 1) * 100 if total_cost > 0 else 0
    print(f"  {'TOTAL':<8} {'':>7} {'':>8} {'':>8} {total_pct:>+7.1f}% {total_pnl:>+12,.0f}")
    print(f"\n  Invested: Rs {total_cost:,.0f}  |  Current: Rs {total_value:,.0f}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Sync MeroShare portfolio")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write to scanner")
    parser.add_argument("--json",    action="store_true", help="Print raw JSON output")
    args = parser.parse_args()

    # Validate credentials
    if not USERNAME or not PASSWORD:
        print("[SYNC] ERROR: Credentials not configured.")
        print("  Set in .env: MEROSHARE_DP, MEROSHARE_USERNAME, MEROSHARE_PASSWORD")
        sys.exit(1)

    print(f"[SYNC] Connecting to MeroShare (DP={DP_ID}, user={USERNAME})...")
    client = MeroShareClient(DP_ID, USERNAME, PASSWORD)

    if not client.login():
        print("[SYNC] Login failed — check credentials in .env")
        sys.exit(1)

    print("[SYNC] Fetching portfolio...")
    portfolio = client.get_complete_portfolio()

    if not portfolio:
        print("[SYNC] No holdings found (or API returned empty)")
        sys.exit(1)

    if args.json:
        print(json.dumps(portfolio, indent=2))
        return

    print_portfolio_table(portfolio)

    # Save JSON snapshot
    save_portfolio_json(portfolio)

    # Update scanner
    if not args.dry_run:
        updated = update_scanner_portfolio(portfolio, dry_run=False)
        if updated:
            print("[SYNC] [OK] daily_scanner.py PORTFOLIO updated")
    else:
        update_scanner_portfolio(portfolio, dry_run=True)


if __name__ == "__main__":
    main()
