#!/usr/bin/env python3
"""
Walk-forward backtest of the paper trading simulator.

Simulates trading from the earliest available date (or --from) to the latest,
processing simplified TA-based signals day by day.  Uses actual price history
with NO lookahead.

This intentionally uses a SIMPLIFIED signal generation (RSI, momentum, volume)
rather than the full ML ensemble because re-running XGB + GRU for every
historical day would take hours.  The goal here is to stress-test the TRADING
LOGIC: entries, exits, position sizing, risk controls, and stop-loss handling.

Usage:
    python scripts/run_backtest.py                       # full backtest
    python scripts/run_backtest.py --from 2025-01-01     # from specific date
    python scripts/run_backtest.py --from 2025-01-01 --to 2026-01-01
    python scripts/run_backtest.py --capital 5000000      # custom starting capital
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.paper_trader import PaperTrader
from src.risk_controls import apply_sector_cap

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HISTORY_DIR = ROOT / "data" / "price_history"
SECTORS_FILE = ROOT / "data" / "sectors.json"

# ---------------------------------------------------------------------------
# Simplified TA signal generator (no ML, no lookahead)
# ---------------------------------------------------------------------------

def _compute_rsi(closes: list[float], period: int = 14) -> float:
    """Compute RSI from a list of closing prices (oldest first)."""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _sma(values: list[float], period: int) -> float | None:
    """Simple moving average of the last *period* values."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def generate_ta_signals(
    symbol_histories: dict[str, list[dict]],
    current_prices: dict[str, float],
) -> list[dict]:
    """Generate simplified TA-based signals for all symbols.

    For each symbol with enough history, compute:
      - RSI (14)
      - 20-day vs 50-day SMA cross
      - 5-day momentum (rate of change)
      - Volume surge (today vs 20-day avg)

    Combine into a score (-100 to +100) and derive a signal string.

    Returns a list of pick dicts compatible with PaperTrader.process_signals:
      [{symbol, signal, score, kelly_pct}, ...]
    """
    picks: list[dict] = []

    for sym, history in symbol_histories.items():
        if len(history) < 50:
            continue

        closes = [d["close"] for d in history]
        volumes = [d.get("volume", d.get("q", 0)) for d in history]
        price = current_prices.get(sym)
        if price is None or price <= 0:
            continue

        # -- RSI component (-40 to +40) --
        rsi = _compute_rsi(closes)
        if rsi < 30:
            rsi_score = 40.0
        elif rsi < 40:
            rsi_score = 20.0
        elif rsi < 60:
            rsi_score = 0.0
        elif rsi < 70:
            rsi_score = -20.0
        else:
            rsi_score = -40.0

        # -- SMA cross component (-25 to +25) --
        sma20 = _sma(closes, 20)
        sma50 = _sma(closes, 50)
        sma_score = 0.0
        if sma20 is not None and sma50 is not None and sma50 > 0:
            cross_pct = (sma20 - sma50) / sma50 * 100
            sma_score = max(-25.0, min(25.0, cross_pct * 5))

        # -- Momentum component (-20 to +20) --
        if len(closes) >= 6:
            mom = (closes[-1] - closes[-6]) / closes[-6] * 100
            mom_score = max(-20.0, min(20.0, mom * 4))
        else:
            mom_score = 0.0

        # -- Volume surge component (-15 to +15) --
        vol_score = 0.0
        vol_avg = _sma(volumes, 20)
        if vol_avg and vol_avg > 0 and volumes:
            vol_ratio = volumes[-1] / vol_avg
            if vol_ratio > 2.0 and mom_score > 0:
                vol_score = 15.0
            elif vol_ratio > 1.5 and mom_score > 0:
                vol_score = 8.0
            elif vol_ratio > 2.0 and mom_score < 0:
                vol_score = -10.0  # high volume sell-off

        composite = rsi_score + sma_score + mom_score + vol_score

        # Derive signal label
        if composite >= 40:
            signal = "STRONG BUY"
        elif composite >= 15:
            signal = "BUY"
        elif composite >= -15:
            signal = "HOLD"
        elif composite >= -40:
            signal = "SELL"
        else:
            signal = "STRONG SELL"

        # Kelly sizing: normalise score to 0-1 range for kelly formula
        norm_score = max(0.0, composite + 100) / 200.0  # 0..1
        win_rate = 0.55
        win_loss_ratio = 1.5
        full_kelly = max(0.0, (win_rate * win_loss_ratio - (1 - win_rate)) / win_loss_ratio)
        kelly_pct = round(full_kelly * 0.5 * norm_score * 100, 1)
        kelly_pct = min(kelly_pct, 15.0)

        picks.append({
            "symbol": sym,
            "signal": signal,
            "score": round(composite, 1),
            "kelly_pct": kelly_pct,
            "rsi": round(rsi, 1),
        })

    # Sort descending by score
    picks.sort(key=lambda p: p["score"], reverse=True)
    return picks


# ---------------------------------------------------------------------------
# History builder (no lookahead)
# ---------------------------------------------------------------------------

def _build_histories_up_to(
    all_dates: list[str],
    date_idx: int,
    date_data_cache: dict[str, dict],
) -> tuple[dict[str, list[dict]], dict[str, float]]:
    """Build per-symbol OHLCV histories using only data up to and including
    all_dates[date_idx].  Returns (symbol_histories, current_prices).

    Uses a rolling window of the most recent 200 trading days for efficiency.
    """
    lookback_start = max(0, date_idx - 200)
    current_date = all_dates[date_idx]

    symbol_histories: dict[str, list[dict]] = {}
    current_prices: dict[str, float] = {}

    for i in range(lookback_start, date_idx + 1):
        dt = all_dates[i]
        day_data = date_data_cache.get(dt)
        if day_data is None:
            continue
        stocks = day_data.get("stocks", {})
        for sym, bar in stocks.items():
            price = bar.get("lp", 0)
            if price <= 0:
                continue

            entry = {
                "date": dt,
                "close": price,
                "open": bar.get("op", price),
                "high": bar.get("h", price),
                "low": bar.get("l", price),
                "volume": bar.get("q", 0),
            }
            symbol_histories.setdefault(sym, []).append(entry)

            if dt == current_date:
                current_prices[sym] = price

    return symbol_histories, current_prices


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------

def run_backtest(
    date_from: str | None = None,
    date_to: str | None = None,
    initial_capital: float | None = None,
):
    """Walk-forward backtest through all available price history files."""

    # 1. Discover all date files
    date_files = sorted(HISTORY_DIR.glob("*.json"))
    if not date_files:
        print("ERROR: No price history files found in", HISTORY_DIR)
        sys.exit(1)

    # all_dates_full includes every available date (needed for lookback).
    # trade_dates is the subset we actually process signals on.
    all_dates_full = [f.stem for f in date_files]
    print(f"Found {len(all_dates_full)} trading days "
          f"({all_dates_full[0]} to {all_dates_full[-1]})")

    # Determine the trading window
    trade_dates = list(all_dates_full)
    if date_from:
        trade_dates = [d for d in trade_dates if d >= date_from]
    if date_to:
        trade_dates = [d for d in trade_dates if d <= date_to]

    if not trade_dates:
        print("ERROR: No dates remain after filtering.")
        sys.exit(1)

    print(f"Backtest window: {trade_dates[0]} to {trade_dates[-1]} "
          f"({len(trade_dates)} days)")

    # 2. Load sector map
    sectors: dict[str, str] = {}
    if SECTORS_FILE.exists():
        with open(SECTORS_FILE) as f:
            sec_data = json.load(f)
        sectors = sec_data.get("symbol_to_sector", {})
        print(f"Loaded {len(sectors)} sector mappings")

    # 3. Pre-load price data into memory for speed.
    #    We need all dates from (trade_start - 200 days lookback) to trade_end.
    first_trade_idx = all_dates_full.index(trade_dates[0])
    load_start = max(0, first_trade_idx - 200)
    dates_to_load = all_dates_full[load_start:]
    if date_to:
        dates_to_load = [d for d in dates_to_load if d <= date_to]

    print(f"Loading price history into memory ({len(dates_to_load)} files) ...")
    date_data_cache: dict[str, dict] = {}
    for dt in dates_to_load:
        fpath = HISTORY_DIR / f"{dt}.json"
        try:
            with open(fpath) as f:
                date_data_cache[dt] = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # 4. Initialise paper trader (fresh -- do not load previous state)
    trader = PaperTrader()
    trader.reset()
    if initial_capital:
        trader.cash = initial_capital
        trader.INITIAL_CAPITAL = initial_capital

    # 5. Walk forward through trade_dates.
    #    _build_histories_up_to uses all_dates_full for lookback so TA
    #    indicators have enough warm-up data even on the first trade day.
    total = len(trade_dates)
    print(f"Processing {total} trading days ...")
    print()

    milestone_interval = max(1, total // 20)  # print progress every ~5%

    for idx, current_date in enumerate(trade_dates):
        global_idx = all_dates_full.index(current_date)

        # Build histories with no lookahead
        histories, prices = _build_histories_up_to(
            all_dates_full, global_idx, date_data_cache
        )

        # Generate TA signals
        picks = generate_ta_signals(histories, prices)

        # Apply sector cap
        if sectors:
            buy_picks = [p for p in picks if p["signal"] in ("BUY", "STRONG BUY")]
            other_picks = [p for p in picks if p["signal"] not in ("BUY", "STRONG BUY")]
            buy_picks = apply_sector_cap(buy_picks, sectors, max_per_sector=3)
            picks = buy_picks + other_picks

        # Feed to paper trader
        result = trader.process_signals(current_date, picks, prices)

        # Progress output
        if idx % milestone_interval == 0 or idx == total - 1:
            eq = result["equity"]
            n_pos = result["positions"]
            ret = (eq - trader.INITIAL_CAPITAL) / trader.INITIAL_CAPITAL * 100
            print(
                f"  [{idx+1:>5}/{total}] {current_date}  "
                f"Equity: {eq:>14,.0f}  Positions: {n_pos:>2}  "
                f"Return: {ret:>+7.2f}%"
            )

        # Print trade actions if any
        for action in result.get("actions", []):
            if idx % milestone_interval == 0 or "SELL" in action:
                pass  # keep output manageable

    # 6. Final summary
    print()
    print("=" * 70)
    print("BACKTEST COMPLETE")
    print("=" * 70)

    summary = trader.get_summary()
    cap = trader.INITIAL_CAPITAL

    print(f"  Period:           {trade_dates[0]} to {trade_dates[-1]}")
    print(f"  Trading days:     {total}")
    print(f"  Starting capital: NPR {cap:>14,.0f}")
    print(f"  Final equity:     NPR {summary['current_equity']:>14,.0f}")
    print(f"  Total return:     {summary['total_return_pct']:>+8.2f}%")
    print(f"  Sharpe ratio:     {summary['sharpe_ratio']:>8.2f}")
    print(f"  Max drawdown:     {summary['max_drawdown_pct']:>8.2f}%")
    print(f"  Total trades:     {summary['total_trades']:>8}")
    print(f"  Win rate:         {summary['win_rate']:>8.1f}%")
    print(f"  Avg hold (days):  {summary['avg_hold_days']:>8.1f}")

    if summary["best_trade"]:
        bt = summary["best_trade"]
        print(f"  Best trade:       {bt['symbol']} {bt['pnl_pct']:+.2f}% "
              f"({bt['entry_date']} -> {bt['exit_date']})")
    if summary["worst_trade"]:
        wt = summary["worst_trade"]
        print(f"  Worst trade:      {wt['symbol']} {wt['pnl_pct']:+.2f}% "
              f"({wt['entry_date']} -> {wt['exit_date']})")

    # Open positions
    positions = summary.get("current_positions", {})
    if positions:
        print(f"\n  Open positions ({len(positions)}):")
        for sym, pos in sorted(positions.items()):
            val = pos["shares"] * pos["avg_cost"]
            print(f"    {sym:>10}  {pos['shares']:>6} shares "
                  f"@ {pos['avg_cost']:>8.2f}  (NPR {val:>12,.0f})")

    print()

    # Save final state
    trader._save_state()
    print(f"State saved to {trader.STATE_FILE}")
    print(f"Trade log saved to {trader.TRADE_LOG}")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Walk-forward backtest of the paper trading simulator.",
    )
    parser.add_argument(
        "--from", dest="date_from", default=None,
        help="Start date (YYYY-MM-DD).  Default: earliest available.",
    )
    parser.add_argument(
        "--to", dest="date_to", default=None,
        help="End date (YYYY-MM-DD).  Default: latest available.",
    )
    parser.add_argument(
        "--capital", type=float, default=None,
        help="Starting capital in NPR.  Default: 10,000,000.",
    )
    args = parser.parse_args()

    run_backtest(
        date_from=args.date_from,
        date_to=args.date_to,
        initial_capital=args.capital,
    )


if __name__ == "__main__":
    main()
