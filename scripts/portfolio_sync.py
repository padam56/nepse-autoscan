#!/usr/bin/env python3
"""
scripts/portfolio_sync.py -- Real Portfolio Sync & Comparison
=============================================================
Syncs actual holdings from MeroShare and compares with paper trading
performance. Outputs a side-by-side comparison showing which paper picks
you actually bought, P&L differences, missed opportunities, and your
own picks not in the paper portfolio.

Usage:
  python scripts/portfolio_sync.py              # full sync + compare
  python scripts/portfolio_sync.py --paper-only # just load paper, skip MeroShare
  python scripts/portfolio_sync.py --json       # output raw comparison JSON
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.sync_portfolio import MeroShareClient, _load_env

_load_env()

MEROSHARE_BASE = "https://webbackend.cdsc.com.np/api"
DP_ID          = int(os.getenv("MEROSHARE_DP", "11000"))
USERNAME       = os.getenv("MEROSHARE_USERNAME", "")
PASSWORD       = os.getenv("MEROSHARE_PASSWORD", "")

PAPER_FILE      = ROOT / "data" / "paper_portfolio.json"
PORTFOLIO_FILE  = ROOT / "data" / "portfolio.json"
COMPARISON_FILE = ROOT / "data" / "portfolio_comparison.json"


def load_paper_portfolio() -> dict:
    """Load paper portfolio from data/paper_portfolio.json.

    Returns dict with keys: cash, positions, equity_curve, etc.
    Positions is {symbol: {shares, entry_price, ...}}.
    """
    if not PAPER_FILE.exists():
        print("[SYNC] Paper portfolio not found: %s" % PAPER_FILE)
        return {}
    try:
        return json.loads(PAPER_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print("[SYNC] Error loading paper portfolio: %s" % e)
        return {}


def load_real_portfolio_cached() -> Dict[str, dict]:
    """Load the last synced real portfolio from data/portfolio.json.

    Returns {symbol: {shares, wacc, price, unrealized, pnl_pct}}.
    """
    if not PORTFOLIO_FILE.exists():
        return {}
    try:
        data = json.loads(PORTFOLIO_FILE.read_text())
        return data.get("holdings", {})
    except (json.JSONDecodeError, OSError) as e:
        print("[SYNC] Error loading cached portfolio: %s" % e)
        return {}


def fetch_real_portfolio() -> Dict[str, dict]:
    """Fetch live portfolio from MeroShare API.

    Returns {symbol: {shares, wacc, price, unrealized, pnl_pct}}.
    Falls back to cached data if login fails.
    """
    if not USERNAME or not PASSWORD:
        print("[SYNC] MeroShare credentials not configured, using cached portfolio")
        return load_real_portfolio_cached()

    client = MeroShareClient(DP_ID, USERNAME, PASSWORD)
    if not client.login():
        print("[SYNC] MeroShare login failed, falling back to cached portfolio")
        return load_real_portfolio_cached()

    portfolio = client.get_complete_portfolio()
    if not portfolio:
        print("[SYNC] MeroShare returned empty portfolio, using cached")
        return load_real_portfolio_cached()

    # Save fresh snapshot
    _save_portfolio_snapshot(portfolio)
    return portfolio


def _save_portfolio_snapshot(portfolio: Dict[str, dict]) -> None:
    """Save a timestamped portfolio snapshot."""
    PORTFOLIO_FILE.parent.mkdir(exist_ok=True)
    snapshot = {
        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "holdings":  portfolio,
        "summary": {
            "n_stocks":    len(portfolio),
            "total_shares": sum(p["shares"] for p in portfolio.values()),
            "total_cost":   sum(p["shares"] * p["wacc"] for p in portfolio.values()),
            "total_value":  sum(p["shares"] * p.get("price", p["wacc"])
                                for p in portfolio.values()),
        },
    }
    PORTFOLIO_FILE.write_text(json.dumps(snapshot, indent=2))


def compare_portfolios(
    real: Dict[str, dict],
    paper: dict,
) -> dict:
    """Compare real holdings with paper portfolio.

    Returns a comparison dict with:
      matched    - positions in both real and paper
      missed     - paper picks you did not buy
      own_picks  - real positions not in paper
      summary    - aggregate stats
    """
    paper_positions = paper.get("positions", {})
    paper_initial = paper.get("initial_capital", 10_000_000)

    real_symbols  = set(real.keys())
    paper_symbols = set(paper_positions.keys())

    matched_syms = real_symbols & paper_symbols
    missed_syms  = paper_symbols - real_symbols
    own_syms     = real_symbols - paper_symbols

    # -- Matched positions (in both) --
    matched = []
    for sym in sorted(matched_syms):
        r = real[sym]
        p = paper_positions[sym]

        p_entry  = p.get("entry_price", p.get("wacc", p.get("avg_price", 0)))
        p_shares = p.get("shares", p.get("quantity", 0))
        p_curr   = p.get("current_price", p.get("price", p_entry))

        real_pnl_pct  = r.get("pnl_pct", 0)
        paper_pnl_pct = ((p_curr / p_entry - 1) * 100) if p_entry > 0 else 0

        matched.append({
            "symbol":         sym,
            "real_shares":    r["shares"],
            "real_wacc":      r["wacc"],
            "real_price":     r.get("price", 0),
            "real_pnl_pct":   round(real_pnl_pct, 2),
            "real_unrealized": r.get("unrealized", 0),
            "paper_shares":   p_shares,
            "paper_entry":    round(p_entry, 2),
            "paper_price":    round(p_curr, 2),
            "paper_pnl_pct":  round(paper_pnl_pct, 2),
            "pnl_gap":        round(real_pnl_pct - paper_pnl_pct, 2),
        })

    # -- Missed paper picks (paper only) --
    missed = []
    for sym in sorted(missed_syms):
        p = paper_positions[sym]
        p_entry  = p.get("entry_price", p.get("wacc", p.get("avg_price", 0)))
        p_shares = p.get("shares", p.get("quantity", 0))
        p_curr   = p.get("current_price", p.get("price", p_entry))
        pnl_pct  = ((p_curr / p_entry - 1) * 100) if p_entry > 0 else 0

        missed.append({
            "symbol":        sym,
            "paper_shares":  p_shares,
            "paper_entry":   round(p_entry, 2),
            "paper_price":   round(p_curr, 2),
            "paper_pnl_pct": round(pnl_pct, 2),
            "verdict":       "missed gain" if pnl_pct > 0 else "avoided loss",
        })

    # -- Own picks (real only, not in paper) --
    own_picks = []
    for sym in sorted(own_syms):
        r = real[sym]
        own_picks.append({
            "symbol":     sym,
            "shares":     r["shares"],
            "wacc":       r["wacc"],
            "price":      r.get("price", 0),
            "pnl_pct":    r.get("pnl_pct", 0),
            "unrealized": r.get("unrealized", 0),
        })

    # -- Summary stats --
    real_total_cost  = sum(r["shares"] * r["wacc"] for r in real.values())
    real_total_value = sum(r["shares"] * r.get("price", r["wacc"]) for r in real.values())
    real_total_pnl   = real_total_value - real_total_cost

    paper_total_cost  = 0
    paper_total_value = 0
    for p in paper_positions.values():
        entry  = p.get("entry_price", p.get("wacc", p.get("avg_price", 0)))
        shares = p.get("shares", p.get("quantity", 0))
        curr   = p.get("current_price", p.get("price", entry))
        paper_total_cost  += shares * entry
        paper_total_value += shares * curr

    paper_total_pnl = paper_total_value - paper_total_cost

    summary = {
        "compared_at":       time.strftime("%Y-%m-%dT%H:%M:%S"),
        "real_positions":    len(real),
        "paper_positions":   len(paper_positions),
        "matched":           len(matched),
        "missed":            len(missed),
        "own_picks":         len(own_picks),
        "real_total_cost":   round(real_total_cost, 0),
        "real_total_value":  round(real_total_value, 0),
        "real_total_pnl":    round(real_total_pnl, 0),
        "real_pnl_pct":      round((real_total_value / real_total_cost - 1) * 100, 2) if real_total_cost > 0 else 0,
        "paper_total_cost":  round(paper_total_cost, 0),
        "paper_total_value": round(paper_total_value, 0),
        "paper_total_pnl":   round(paper_total_pnl, 0),
        "paper_pnl_pct":     round((paper_total_value / paper_total_cost - 1) * 100, 2) if paper_total_cost > 0 else 0,
    }

    return {
        "matched":   matched,
        "missed":    missed,
        "own_picks": own_picks,
        "summary":   summary,
    }


def save_comparison(comparison: dict) -> None:
    """Save comparison result to data/portfolio_comparison.json."""
    COMPARISON_FILE.parent.mkdir(exist_ok=True)
    COMPARISON_FILE.write_text(json.dumps(comparison, indent=2))
    print("[SYNC] Comparison saved -> %s" % COMPARISON_FILE)


def print_comparison(comp: dict) -> None:
    """Print a readable summary of the portfolio comparison."""
    summary = comp["summary"]

    print("\n" + "=" * 70)
    print("  PORTFOLIO COMPARISON: Real vs Paper")
    print("=" * 70)

    print("\n  Real portfolio:  %d positions, Rs %s invested, P&L %+.1f%%" % (
        summary["real_positions"],
        "{:,.0f}".format(summary["real_total_cost"]),
        summary["real_pnl_pct"],
    ))
    print("  Paper portfolio: %d positions, Rs %s invested, P&L %+.1f%%" % (
        summary["paper_positions"],
        "{:,.0f}".format(summary["paper_total_cost"]),
        summary["paper_pnl_pct"],
    ))

    # Matched positions
    matched = comp["matched"]
    if matched:
        print("\n  MATCHED (in both real and paper):")
        print("  %-8s %8s %8s %8s %8s %8s" % (
            "Symbol", "R.WACC", "P.Entry", "R.P&L%", "P.P&L%", "Gap"))
        print("  " + "-" * 55)
        for m in matched:
            print("  %-8s %8.2f %8.2f %+7.1f%% %+7.1f%% %+7.1f%%" % (
                m["symbol"],
                m["real_wacc"],
                m["paper_entry"],
                m["real_pnl_pct"],
                m["paper_pnl_pct"],
                m["pnl_gap"],
            ))

    # Missed paper picks
    missed = comp["missed"]
    if missed:
        print("\n  MISSED PAPER PICKS (paper only):")
        print("  %-8s %8s %8s %8s  %s" % ("Symbol", "Entry", "Current", "P&L%", "Verdict"))
        print("  " + "-" * 55)
        for m in missed:
            print("  %-8s %8.2f %8.2f %+7.1f%%  %s" % (
                m["symbol"],
                m["paper_entry"],
                m["paper_price"],
                m["paper_pnl_pct"],
                m["verdict"],
            ))

    # Own picks
    own = comp["own_picks"]
    if own:
        print("\n  OWN PICKS (real only, not in paper):")
        print("  %-8s %7s %8s %8s %8s" % ("Symbol", "Shares", "WACC", "Price", "P&L%"))
        print("  " + "-" * 45)
        for o in own:
            print("  %-8s %7d %8.2f %8.2f %+7.1f%%" % (
                o["symbol"],
                o["shares"],
                o["wacc"],
                o["price"],
                o["pnl_pct"],
            ))

    if not matched and not missed and not own:
        print("\n  No positions to compare (both portfolios are empty).")

    print()


def sync_and_compare(paper_only: bool = False) -> dict:
    """Main entry: fetch real holdings, load paper portfolio, compare.

    Args:
        paper_only: if True, skip MeroShare fetch and use cached real portfolio

    Returns:
        comparison dict
    """
    # 1. Get real portfolio
    if paper_only:
        real = load_real_portfolio_cached()
        if not real:
            print("[SYNC] No cached real portfolio found")
    else:
        real = fetch_real_portfolio()

    # 2. Load paper portfolio
    paper = load_paper_portfolio()

    # 3. Compare
    comparison = compare_portfolios(real or {}, paper or {})

    # 4. Save comparison
    save_comparison(comparison)

    # 5. Print summary
    print_comparison(comparison)

    return comparison


def main():
    parser = argparse.ArgumentParser(description="Sync real portfolio and compare with paper trading")
    parser.add_argument("--paper-only", action="store_true",
                        help="Use cached real portfolio instead of fetching from MeroShare")
    parser.add_argument("--json", action="store_true",
                        help="Output raw comparison JSON")
    args = parser.parse_args()

    comparison = sync_and_compare(paper_only=args.paper_only)

    if args.json:
        print(json.dumps(comparison, indent=2))


if __name__ == "__main__":
    main()
