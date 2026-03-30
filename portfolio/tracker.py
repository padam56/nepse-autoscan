"""
Multi-stock Portfolio Tracker.
Fetches live prices for all holdings, calculates P&L, ranks by opportunity.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import requests
from typing import Dict, List, Optional
from portfolio.config import PORTFOLIO, TOTAL_INVESTED


MEROLAGNAI_URL = "https://merolagani.com/handlers/webrequesthandler.ashx?type=market_summary"


def fetch_live_prices() -> Dict[str, dict]:
    """Fetch live prices for all NEPSE stocks from MeroLagani."""
    try:
        r = requests.get(MEROLAGNAI_URL, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        data = r.json()
        stocks = data.get("d", data) if isinstance(data, dict) else data
        if isinstance(stocks, dict):
            stocks = stocks.get("stock", stocks.get("stocks", []))

        price_map = {}
        for s in stocks:
            sym = str(s.get("s", "")).upper()
            if sym:
                try:
                    price_map[sym] = {
                        "ltp": float(s.get("lp", 0) or 0),
                        "change": float(s.get("c", 0) or 0),
                        "pct_change": float(s.get("pc", 0) or 0),
                        "volume": float(s.get("q", 0) or 0),
                        "high": float(s.get("h", 0) or 0),
                        "low": float(s.get("l", 0) or 0),
                        "open": float(s.get("op", 0) or 0),
                        "turnover": float(s.get("t", 0) or 0),
                    }
                except (ValueError, TypeError):
                    pass
        return price_map
    except Exception as e:
        print(f"[!] Price fetch error: {e}")
        return {}


def get_portfolio_snapshot(price_map: Optional[Dict] = None) -> List[dict]:
    """
    Returns list of holdings with live P&L, sorted by loss severity.
    """
    if price_map is None:
        price_map = fetch_live_prices()

    rows = []
    for symbol, pos in PORTFOLIO.items():
        live = price_map.get(symbol, {})
        ltp = live.get("ltp", 0)
        wacc = pos["wacc"]
        shares = pos["shares"]
        total_cost = pos["total_cost"]

        if ltp > 0 and wacc > 0:
            pnl_pct = (ltp - wacc) / wacc * 100
            pnl_abs = (ltp - wacc) * shares
            current_value = ltp * shares
        else:
            pnl_pct = 0.0
            pnl_abs = 0.0
            current_value = 0.0

        rows.append({
            "symbol": symbol,
            "company": pos["company"],
            "sector": pos["sector"],
            "shares": shares,
            "wacc": wacc,
            "ltp": ltp,
            "pnl_pct": round(pnl_pct, 2),
            "pnl_abs": round(pnl_abs, 2),
            "total_cost": total_cost,
            "current_value": round(current_value, 2),
            "change_today": live.get("pct_change", 0),
            "volume": live.get("volume", 0),
            "break_even_gap": round(wacc - ltp, 2) if ltp > 0 else 0,
        })

    # Sort: biggest losses first, then by absolute P&L
    rows.sort(key=lambda x: x["pnl_pct"])
    return rows


def print_portfolio_summary(snapshot: Optional[List] = None):
    """Print a formatted portfolio summary table."""
    if snapshot is None:
        snapshot = get_portfolio_snapshot()

    total_cost = sum(r["total_cost"] for r in snapshot)
    total_value = sum(r["current_value"] for r in snapshot if r["current_value"] > 0)
    total_pnl = total_value - total_cost

    print(f"\n{'='*90}")
    print(f"  NEPSE PORTFOLIO SNAPSHOT")
    print(f"{'='*90}")
    print(f"  {'Symbol':<8} {'Shares':>7} {'WACC':>8} {'LTP':>8} {'P&L%':>8} {'P&L (NPR)':>14} {'Sector':<20}")
    print(f"  {'-'*80}")

    for r in snapshot:
        pnl_str = f"{r['pnl_abs']:+,.0f}"
        pnl_pct_str = f"{r['pnl_pct']:+.2f}%"
        ltp_str = f"{r['ltp']:,.1f}" if r['ltp'] > 0 else "N/A"
        print(f"  {r['symbol']:<8} {r['shares']:>7,} {r['wacc']:>8,.2f} {ltp_str:>8} "
              f"{pnl_pct_str:>8} {pnl_str:>14} {r['sector']:<20}")

    print(f"  {'-'*80}")
    print(f"  {'TOTAL':.<8} {'':>7} {'':>8} {'':>8} {'':>8} {total_pnl:>+14,.0f}")
    print(f"\n  Total Invested : NPR {total_cost:>15,.2f}")
    print(f"  Current Value  : NPR {total_value:>15,.2f}")
    print(f"  Total P&L      : NPR {total_pnl:>+15,.2f} ({total_pnl/total_cost*100:+.2f}%)")
    print(f"{'='*90}\n")


if __name__ == "__main__":
    print("Fetching live prices...")
    snap = get_portfolio_snapshot()
    print_portfolio_summary(snap)
