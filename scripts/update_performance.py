#!/usr/bin/env python3
"""
Update PERFORMANCE.md with latest paper trading results and push to GitHub.

Called by cron after daily_scanner.py finishes.
Reads paper_portfolio.json, generates a markdown performance report,
commits and pushes to GitHub automatically.
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(line_buffering=True)


def load_paper_state():
    state_file = ROOT / 'data' / 'paper_portfolio.json'
    if not state_file.exists():
        return None
    return json.loads(state_file.read_text())


def load_trades():
    trade_file = ROOT / 'data' / 'paper_trades.json'
    if not trade_file.exists():
        return []
    return json.loads(trade_file.read_text())


def generate_performance_md(state, trades):
    if not state:
        return "# Paper Trading Performance\n\nNo data yet. The bot will start trading on the next market day.\n"

    cash = state.get('cash', 0)
    positions = state.get('positions', {})
    equity_curve = state.get('equity_curve', [])

    # Calculate current equity
    invested = sum(p['shares'] * p.get('current_price', p['avg_cost'])
                   for p in positions.values())
    equity = cash + invested
    initial = 10_000_000
    total_return = (equity / initial - 1) * 100

    # Trade stats
    closed_trades = [t for t in trades if t.get('pnl_pct') is not None]
    n_trades = len(closed_trades)
    wins = [t for t in closed_trades if t.get('pnl_pct', 0) > 0]
    losses = [t for t in closed_trades if t.get('pnl_pct', 0) <= 0]
    win_rate = len(wins) / n_trades * 100 if n_trades > 0 else 0

    avg_win = sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0

    # Max drawdown from equity curve
    max_dd = 0
    peak = initial
    for pt in equity_curve:
        eq = pt.get('equity', initial)
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100
        max_dd = max(max_dd, dd)

    # Equity emoji
    trend = "up" if total_return >= 0 else "down"
    trend_arrow = "+" if total_return >= 0 else ""

    now = datetime.now().strftime("%Y-%m-%d %H:%M NPT")
    today = datetime.now().strftime("%Y-%m-%d")

    # Equity curve sparkline (last 20 points as text bars)
    sparkline = ""
    if equity_curve:
        recent = equity_curve[-20:]
        equities = [p.get('equity', initial) for p in recent]
        min_eq = min(equities)
        max_eq = max(equities)
        rng = max_eq - min_eq if max_eq > min_eq else 1
        for pt in recent:
            eq = pt.get('equity', initial)
            bar_len = int((eq - min_eq) / rng * 15) + 1
            date_str = pt.get('date', '?')[-5:]  # MM-DD
            color = "green" if eq >= initial else "red"
            sparkline += "  %s %s Rs %s\n" % (date_str, chr(9608) * bar_len, f"{eq:,.0f}")

    # Current positions table
    pos_table = ""
    if positions:
        pos_table = "| Symbol | Shares | Avg Cost | Current | P&L% |\n"
        pos_table += "|--------|-------:|--------:|---------:|------:|\n"
        for sym, p in sorted(positions.items()):
            cur = p.get('current_price', p['avg_cost'])
            pnl = (cur / p['avg_cost'] - 1) * 100 if p['avg_cost'] > 0 else 0
            pos_table += "| %s | %s | %s | %s | %s%% |\n" % (
                sym, f"{p['shares']:,}", f"{p['avg_cost']:.1f}",
                f"{cur:.1f}", f"{pnl:+.1f}")

    # Recent trades (last 10)
    trade_table = ""
    if closed_trades:
        recent_trades = closed_trades[-10:][::-1]
        trade_table = "| Date | Symbol | Action | Return | Hold |\n"
        trade_table += "|------|--------|--------|-------:|-----:|\n"
        for t in recent_trades:
            trade_table += "| %s | %s | %s | %s%% | %sd |\n" % (
                t.get('exit_date', '?'), t.get('symbol', '?'),
                t.get('reason', 'signal').upper(),
                f"{t.get('pnl_pct', 0):+.1f}",
                t.get('hold_days', '?'))

    md = f"""# Paper Trading Performance

> Virtual portfolio tracking the bot's signals with NPR 10,000,000 starting capital.
> Updated automatically after each trading day. No real money involved.

**Last updated:** {now}

---

## Current Status

| Metric | Value |
|--------|------:|
| Starting Capital | Rs 10,000,000 |
| Current Equity | Rs {equity:,.0f} |
| Cash | Rs {cash:,.0f} |
| Invested | Rs {invested:,.0f} |
| **Total Return** | **{trend_arrow}{total_return:.2f}%** |
| Max Drawdown | {max_dd:.1f}% |
| Total Trades | {n_trades} |
| Win Rate | {win_rate:.0f}% |
| Avg Win | {avg_win:+.1f}% |
| Avg Loss | {avg_loss:+.1f}% |

---

## Equity Curve (Last 20 Days)

```
{sparkline if sparkline else "  No data yet - will populate after first trading day"}
```

---

## Open Positions ({len(positions)})

{pos_table if pos_table else "*No open positions*"}

---

## Recent Trades

{trade_table if trade_table else "*No completed trades yet*"}

---

## How It Works

The bot starts each day with the full pipeline:
1. Scans 310+ stocks with XGBoost + GRU + Technical Analysis
2. Detects market regime (BULL/RANGE/BEAR)
3. Ranks stocks, applies sector cap (max 3 per sector)
4. Sizes positions with Kelly criterion (max 15%)
5. Buys BUY/STRONG BUY signals, sells on SELL signals or -5% stop loss
6. 0.5% commission on each trade, 3-day minimum hold

This is a simulation. Past performance does not predict future results.

---

<sub>Auto-generated by NEPSE AutoScan | [View full system](README.md)</sub>
"""
    return md


AUTO_COMMIT_PREFIX = "Daily auto-update"


def git_commit_and_push():
    """Commit dashboard + performance data and push to GitHub.

    If the last commit was a daily auto-update, amend it instead of
    creating a new one.  This keeps the git history clean -- only one
    auto-update commit exists at any time.
    """
    os.chdir(str(ROOT))
    try:
        # Check if there are changes
        result = subprocess.run(['git', 'diff', '--quiet', 'PERFORMANCE.md', 'docs/'],
                                capture_output=True)
        if result.returncode == 0:
            untracked = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard', 'docs/'],
                                       capture_output=True, text=True)
            if not untracked.stdout.strip():
                print("[PERF] No changes to push")
                return False

        today = datetime.now().strftime("%Y-%m-%d")
        subprocess.run(['git', 'add', 'PERFORMANCE.md', 'docs/'], check=True,
                        capture_output=True)

        # Check if last commit was an auto-update -- if so, amend it
        last_msg = subprocess.run(['git', 'log', '-1', '--format=%s'],
                                  capture_output=True, text=True).stdout.strip()
        if last_msg.startswith(AUTO_COMMIT_PREFIX):
            subprocess.run(['git', 'commit', '--amend',
                            '-m', f'{AUTO_COMMIT_PREFIX} ({today})'],
                           check=True, capture_output=True)
        else:
            subprocess.run(['git', 'commit', '-m',
                            f'{AUTO_COMMIT_PREFIX} ({today})'],
                           check=True, capture_output=True)

        result = subprocess.run(['git', 'push', '--force-with-lease', 'origin', 'main'],
                                capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print("[PERF] Pushed to GitHub")
            return True
        else:
            print("[PERF] Push failed: %s" % result.stderr)
            return False
    except Exception as e:
        print("[PERF] Git error: %s" % e)
        return False


def main():
    state = load_paper_state()
    trades = load_trades()

    md = generate_performance_md(state, trades)
    perf_path = ROOT / 'PERFORMANCE.md'
    perf_path.write_text(md)
    print("[PERF] Updated PERFORMANCE.md")

    # Generate dashboard HTML
    try:
        from generate_dashboard import generate as gen_dashboard
        gen_dashboard()
    except Exception as e:
        print("[PERF] Dashboard generation failed: %s" % e)

    if '--push' in sys.argv:
        git_commit_and_push()


if __name__ == '__main__':
    main()
