"""
Paper Trading Simulator for NEPSE.

Simulates trading with virtual capital using ensemble signals.
Tracks: positions, cash, trades, P&L, equity curve.
Stores state in data/paper_portfolio.json.
"""

from __future__ import annotations

import json
import math
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Thread lock for file I/O safety
# ---------------------------------------------------------------------------
_FILE_LOCK = threading.Lock()


class PaperTrader:
    """Simulates trading with virtual capital using the full ensemble signals.

    Tracks positions, cash, completed trades, and an equity curve.
    State is persisted to JSON after every mutation so the simulator
    survives restarts.
    """

    INITIAL_CAPITAL = 10_000_000  # 10 million NPR
    STATE_FILE = ROOT / "data" / "paper_portfolio.json"
    TRADE_LOG = ROOT / "data" / "paper_trades.json"
    COMMISSION = 0.005  # 0.5 % per side
    MAX_POSITIONS = 10
    MAX_KELLY = 0.15  # cap kelly allocation at 15 % of portfolio
    MIN_HOLD_DAYS = 3  # no selling before 3 trading days
    STOP_LOSS_PCT = 0.05  # sell if position drops > 5 % from entry

    def __init__(self):
        self.cash: float = self.INITIAL_CAPITAL
        self.positions: dict = {}  # {symbol: {shares, avg_cost, entry_date, entry_idx}}
        self.trades: list = []  # completed (closed) trades
        self.equity_curve: list = []  # [{date, equity, cash, invested}]
        self._day_index: int = 0  # running counter of trading days processed
        self._load_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_signals(
        self,
        date: str,
        picks: list[dict],
        live_prices: dict[str, float],
    ) -> dict:
        """Called daily after the scanner runs.

        Logic:
        1. CHECK EXITS -- sell positions whose signal turned bearish or
           that breached the stop loss (and have been held >= MIN_HOLD_DAYS).
        2. CHECK ENTRIES -- buy top picks with BUY / STRONG BUY using
           Kelly sizing.  Max MAX_POSITIONS simultaneous positions.
        3. Record equity snapshot.

        Args:
            date:        trading date string (YYYY-MM-DD).
            picks:       list of dicts, each with at least
                         {symbol, signal, score, kelly_pct}.
            live_prices: {symbol: last_traded_price}.

        Returns:
            Summary dict of actions taken this day.
        """
        self._day_index += 1
        actions: list[str] = []

        # Build a quick lookup: symbol -> pick dict
        pick_map = {p["symbol"]: p for p in picks}

        # --- 1. Check exits ---------------------------------------------------
        symbols_to_sell: list[tuple[str, str]] = []  # (symbol, reason)
        for sym, pos in list(self.positions.items()):
            price = live_prices.get(sym)
            if price is None:
                continue  # no price data today, skip

            days_held = self._day_index - pos.get("entry_idx", self._day_index)
            if days_held < self.MIN_HOLD_DAYS:
                continue  # minimum hold period not met

            # Check signal
            pick = pick_map.get(sym, {})
            signal = pick.get("signal", "HOLD")
            if signal in ("SELL", "STRONG SELL"):
                symbols_to_sell.append((sym, f"signal={signal}"))
                continue

            # Check stop loss
            pct_change = (price - pos["avg_cost"]) / pos["avg_cost"]
            if pct_change < -self.STOP_LOSS_PCT:
                symbols_to_sell.append((sym, f"stop_loss ({pct_change:+.1%})"))

        for sym, reason in symbols_to_sell:
            price = live_prices.get(sym)
            if price is not None and price > 0:
                self._sell(sym, price, date, reason=reason)
                actions.append(f"SELL {sym} @ {price:.2f} ({reason})")

        # --- 2. Check entries --------------------------------------------------
        buy_signals = [
            p for p in picks
            if p.get("signal") in ("BUY", "STRONG BUY")
            and p["symbol"] not in self.positions
            and live_prices.get(p["symbol"], 0) > 0
        ]
        # Sort by score descending so best picks first
        buy_signals.sort(key=lambda p: p.get("score", 0), reverse=True)

        portfolio_value = self._portfolio_value(live_prices)

        for pick in buy_signals:
            if len(self.positions) >= self.MAX_POSITIONS:
                break

            sym = pick["symbol"]
            price = live_prices[sym]
            kelly_pct = min(pick.get("kelly_pct", 5.0), self.MAX_KELLY * 100) / 100.0
            amount_npr = portfolio_value * kelly_pct

            # Do not spend more than available cash
            amount_npr = min(amount_npr, self.cash * 0.95)  # keep 5% cash buffer
            if amount_npr < price * 10:
                continue  # not enough to buy a meaningful lot

            self._buy(sym, price, amount_npr, date)
            shares_bought = self.positions[sym]["shares"]
            actions.append(
                f"BUY {sym} x{shares_bought} @ {price:.2f} "
                f"(kelly={kelly_pct:.1%}, amt={amount_npr:,.0f})"
            )

        # --- 3. Equity snapshot ------------------------------------------------
        self._update_equity(date, live_prices)
        self._save_state()

        return {
            "date": date,
            "actions": actions,
            "positions": len(self.positions),
            "cash": round(self.cash, 2),
            "equity": self.equity_curve[-1]["equity"] if self.equity_curve else self.cash,
        }

    # ------------------------------------------------------------------
    # Trade execution helpers
    # ------------------------------------------------------------------

    def _buy(self, symbol: str, price: float, amount_npr: float, date: str):
        """Buy shares of *symbol*.  Deducts commission from the buy amount."""
        effective = amount_npr * (1 - self.COMMISSION)
        shares = int(effective / price)
        if shares <= 0:
            return

        cost = shares * price
        commission = cost * self.COMMISSION
        total_cost = cost + commission

        if total_cost > self.cash:
            # Reduce shares to fit cash
            shares = int((self.cash / (1 + self.COMMISSION)) / price)
            if shares <= 0:
                return
            cost = shares * price
            commission = cost * self.COMMISSION
            total_cost = cost + commission

        self.cash -= total_cost

        if symbol in self.positions:
            # Average up / down
            pos = self.positions[symbol]
            old_shares = pos["shares"]
            old_cost = pos["avg_cost"] * old_shares
            new_total = old_shares + shares
            pos["avg_cost"] = (old_cost + cost) / new_total
            pos["shares"] = new_total
        else:
            self.positions[symbol] = {
                "shares": shares,
                "avg_cost": price,
                "entry_date": date,
                "entry_idx": self._day_index,
            }

    def _sell(self, symbol: str, price: float, date: str, reason: str = "signal"):
        """Sell entire position of *symbol*.  Deducts commission from proceeds."""
        if symbol not in self.positions:
            return

        pos = self.positions.pop(symbol)
        shares = pos["shares"]
        gross = shares * price
        commission = gross * self.COMMISSION
        net_proceeds = gross - commission
        self.cash += net_proceeds

        entry_cost = shares * pos["avg_cost"]
        pnl = net_proceeds - entry_cost
        pnl_pct = (pnl / entry_cost) * 100 if entry_cost > 0 else 0.0

        # Count trading days held
        hold_days = self._day_index - pos.get("entry_idx", self._day_index)

        trade_record = {
            "symbol": symbol,
            "entry_date": pos["entry_date"],
            "exit_date": date,
            "entry_price": round(pos["avg_cost"], 2),
            "exit_price": round(price, 2),
            "shares": shares,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "hold_days": hold_days,
            "reason": reason,
        }
        self.trades.append(trade_record)

    # ------------------------------------------------------------------
    # Portfolio helpers
    # ------------------------------------------------------------------

    def _portfolio_value(self, live_prices: dict[str, float]) -> float:
        """Total portfolio value (cash + market value of positions)."""
        invested = sum(
            pos["shares"] * live_prices.get(sym, pos["avg_cost"])
            for sym, pos in self.positions.items()
        )
        return self.cash + invested

    def _update_equity(self, date: str, live_prices: dict[str, float]):
        """Append a snapshot to the equity curve."""
        invested = sum(
            pos["shares"] * live_prices.get(sym, pos["avg_cost"])
            for sym, pos in self.positions.items()
        )
        equity = self.cash + invested
        self.equity_curve.append({
            "date": date,
            "equity": round(equity, 2),
            "cash": round(self.cash, 2),
            "invested": round(invested, 2),
        })

    # ------------------------------------------------------------------
    # Performance metrics
    # ------------------------------------------------------------------

    def get_summary(self) -> dict:
        """Return performance metrics for the paper trading run."""
        if not self.equity_curve:
            return {
                "total_return_pct": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "win_rate": 0.0,
                "total_trades": 0,
                "avg_hold_days": 0.0,
                "best_trade": None,
                "worst_trade": None,
                "current_equity": self.cash,
                "current_positions": {},
                "equity_curve": [],
            }

        equities = [e["equity"] for e in self.equity_curve]
        current_equity = equities[-1]
        total_return_pct = (
            (current_equity - self.INITIAL_CAPITAL) / self.INITIAL_CAPITAL * 100
        )

        # Sharpe ratio (daily returns, annualised assuming 240 trading days)
        sharpe = 0.0
        if len(equities) >= 2:
            daily_returns = [
                (equities[i] - equities[i - 1]) / equities[i - 1]
                for i in range(1, len(equities))
                if equities[i - 1] > 0
            ]
            if daily_returns:
                mean_r = sum(daily_returns) / len(daily_returns)
                std_r = (
                    sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
                ) ** 0.5
                if std_r > 0:
                    sharpe = round((mean_r / std_r) * math.sqrt(240), 2)

        # Max drawdown
        max_drawdown_pct = 0.0
        peak = equities[0]
        for eq in equities:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            if dd > max_drawdown_pct:
                max_drawdown_pct = dd

        # Trade stats
        total_trades = len(self.trades)
        wins = [t for t in self.trades if t["pnl"] > 0]
        win_rate = (len(wins) / total_trades * 100) if total_trades else 0.0
        avg_hold = (
            sum(t["hold_days"] for t in self.trades) / total_trades
            if total_trades
            else 0.0
        )

        best_trade = max(self.trades, key=lambda t: t["pnl_pct"]) if self.trades else None
        worst_trade = min(self.trades, key=lambda t: t["pnl_pct"]) if self.trades else None

        return {
            "total_return_pct": round(total_return_pct, 2),
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "win_rate": round(win_rate, 2),
            "total_trades": total_trades,
            "avg_hold_days": round(avg_hold, 1),
            "best_trade": best_trade,
            "worst_trade": worst_trade,
            "current_equity": round(current_equity, 2),
            "current_positions": dict(self.positions),
            "equity_curve": self.equity_curve[-30:],
        }

    def summary_html(self) -> str:
        """HTML snippet for email report showing paper trading performance."""
        s = self.get_summary()
        equity = s["current_equity"]
        ret = s["total_return_pct"]
        ret_colour = "#27ae60" if ret >= 0 else "#e74c3c"

        lines = [
            '<div style="font-family:monospace; border:1px solid #ccc; '
            'border-radius:8px; padding:16px; max-width:600px; margin:12px auto;">',
            '<h3 style="margin-top:0;">Paper Trading Performance</h3>',
            "<table>",
            f"<tr><td>Starting Capital</td><td>NPR {self.INITIAL_CAPITAL:,.0f}</td></tr>",
            f'<tr><td>Current Equity</td><td style="color:{ret_colour}; '
            f'font-weight:bold;">NPR {equity:,.0f}</td></tr>',
            f'<tr><td>Total Return</td><td style="color:{ret_colour};">'
            f'{ret:+.2f}%</td></tr>',
            f"<tr><td>Win Rate</td><td>{s['win_rate']:.1f}%</td></tr>",
            f"<tr><td>Total Trades</td><td>{s['total_trades']}</td></tr>",
            f"<tr><td>Avg Hold Days</td><td>{s['avg_hold_days']:.1f}</td></tr>",
            f"<tr><td>Sharpe Ratio</td><td>{s['sharpe_ratio']:.2f}</td></tr>",
            f"<tr><td>Max Drawdown</td><td>{s['max_drawdown_pct']:.2f}%</td></tr>",
            "</table>",
        ]

        # Equity curve as ASCII bar chart (last 20 data points)
        curve = s.get("equity_curve", [])[-20:]
        if curve:
            lines.append("<h4>Equity Curve (last 20 days)</h4>")
            lines.append("<pre>")
            eq_vals = [pt["equity"] for pt in curve]
            eq_min = min(eq_vals)
            eq_max = max(eq_vals)
            eq_range = eq_max - eq_min if eq_max > eq_min else 1
            bar_width = 30
            for pt in curve:
                bar_len = int((pt["equity"] - eq_min) / eq_range * bar_width)
                bar = "#" * max(bar_len, 1)
                dt_short = pt["date"][-5:]  # MM-DD
                lines.append(f"{dt_short} |{bar} {pt['equity']:,.0f}")
            lines.append("</pre>")

        # Top 3 current positions
        positions = s.get("current_positions", {})
        if positions:
            lines.append("<h4>Current Positions</h4>")
            lines.append("<table><tr><th>Symbol</th><th>Shares</th>"
                         "<th>Avg Cost</th><th>Entry</th></tr>")
            sorted_pos = sorted(
                positions.items(),
                key=lambda kv: kv[1]["shares"] * kv[1]["avg_cost"],
                reverse=True,
            )
            for sym, pos in sorted_pos[:3]:
                lines.append(
                    f"<tr><td>{sym}</td><td>{pos['shares']}</td>"
                    f"<td>{pos['avg_cost']:.2f}</td>"
                    f"<td>{pos['entry_date']}</td></tr>"
                )
            if len(sorted_pos) > 3:
                lines.append(
                    f"<tr><td colspan='4'>... and {len(sorted_pos)-3} more</td></tr>"
                )
            lines.append("</table>")

        # Last 5 trades
        if self.trades:
            lines.append("<h4>Recent Trades</h4>")
            lines.append(
                "<table><tr><th>Symbol</th><th>Entry</th><th>Exit</th>"
                "<th>P&amp;L%</th><th>Reason</th></tr>"
            )
            for t in self.trades[-5:]:
                pnl_col = "#27ae60" if t["pnl_pct"] >= 0 else "#e74c3c"
                lines.append(
                    f"<tr><td>{t['symbol']}</td>"
                    f"<td>{t['entry_price']:.0f}</td>"
                    f"<td>{t['exit_price']:.0f}</td>"
                    f'<td style="color:{pnl_col};">{t["pnl_pct"]:+.2f}%</td>'
                    f"<td>{t['reason']}</td></tr>"
                )
            lines.append("</table>")

        lines.append("</div>")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # State persistence (thread-safe)
    # ------------------------------------------------------------------

    def _save_state(self):
        """Persist portfolio state and trades to JSON."""
        with _FILE_LOCK:
            state = {
                "cash": self.cash,
                "positions": self.positions,
                "equity_curve": self.equity_curve,
                "day_index": self._day_index,
                "initial_capital": self.INITIAL_CAPITAL,
            }
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.STATE_FILE.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2, default=str)
            tmp.replace(self.STATE_FILE)

            tmp2 = self.TRADE_LOG.with_suffix(".tmp")
            with open(tmp2, "w") as f:
                json.dump(self.trades, f, indent=2, default=str)
            tmp2.replace(self.TRADE_LOG)

    def _load_state(self):
        """Load from JSON or initialise fresh."""
        with _FILE_LOCK:
            if self.STATE_FILE.exists():
                try:
                    with open(self.STATE_FILE) as f:
                        state = json.load(f)
                    self.cash = state.get("cash", self.INITIAL_CAPITAL)
                    self.positions = state.get("positions", {})
                    self.equity_curve = state.get("equity_curve", [])
                    self._day_index = state.get("day_index", 0)
                except (json.JSONDecodeError, KeyError):
                    pass  # keep defaults

            if self.TRADE_LOG.exists():
                try:
                    with open(self.TRADE_LOG) as f:
                        self.trades = json.load(f)
                except (json.JSONDecodeError, KeyError):
                    pass

    def reset(self):
        """Wipe state and start fresh."""
        self.cash = self.INITIAL_CAPITAL
        self.positions = {}
        self.trades = []
        self.equity_curve = []
        self._day_index = 0
        self._save_state()
