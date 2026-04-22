"""
Portfolio Manager - Track share buys/sells and recalculate WACC automatically.

When you trade (buy/sell), this system updates your position and recalculates
everything automatically. No manual database editing needed.
"""

import os
import json
from datetime import datetime, timezone, timedelta
NPT = timezone(timedelta(hours=5, minutes=45))
from typing import Optional

DATA_DIR = "data"


class PortfolioManager:
    """Manages your actual trading position."""

    def __init__(self, symbol: str = "ALICL"):
        self.symbol = symbol.upper()
        self.trades_file = os.path.join(DATA_DIR, f"{symbol}_trades.json")
        self.position_file = os.path.join(DATA_DIR, f"{symbol}_position.json")
        os.makedirs(DATA_DIR, exist_ok=True)

        # Load or initialize
        self.trades = self._load_trades()
        self.position = self._load_position()

    def record_trade(self, action: str, shares: int, price: float, notes: str = "") -> dict:
        """
        Record a buy or sell trade.

        Args:
            action: "BUY" or "SELL"
            shares: Number of shares
            price: Price per share
            notes: Optional notes (e.g., "sell 50% on signal")

        Returns:
            Updated position data
        """
        action = action.upper()
        if action not in ("BUY", "SELL"):
            raise ValueError("Action must be BUY or SELL")

        trade = {
            "timestamp": datetime.now(NPT).isoformat(),
            "action": action,
            "shares": shares,
            "price": price,
            "total": shares * price,
            "notes": notes,
        }

        self.trades.append(trade)
        self._save_trades()

        # Recalculate position
        self._recalculate_position()

        print(f"\n{'='*60}")
        print(f"  TRADE RECORDED: {action} {shares:,} @ NPR {price:,.2f}")
        print(f"{'='*60}")
        print(f"  Total value: NPR {trade['total']:,.2f}")
        print(f"  Notes: {notes if notes else 'None'}")
        print(f"\n  Updated Position:")
        print(f"    Shares: {self.position['shares']:,}")
        print(f"    WACC: NPR {self.position['wacc']:,.2f}")
        print(f"    Total cost: NPR {self.position['total_cost']:,.2f}")
        print(f"    Current P&L: NPR {self.position['current_pnl']:+,.2f}")
        print(f"{'='*60}\n")

        return self.position

    def _recalculate_position(self):
        """Recalculate total shares, WACC, and P&L."""
        total_shares = 0
        total_cost = 0.0

        for trade in self.trades:
            if trade["action"] == "BUY":
                total_shares += trade["shares"]
                total_cost += trade["total"]
            elif trade["action"] == "SELL":
                # When selling, we reduce shares and realize gains/losses
                total_shares -= trade["shares"]
                # Realize the P&L on sold shares
                shares_sold = trade["shares"]
                avg_cost_per_share = (
                    total_cost / (total_shares + shares_sold) if total_shares + shares_sold > 0 else 0
                )
                realized_gain = shares_sold * (trade["price"] - avg_cost_per_share)
                self.position["realized_pnl"] = self.position.get("realized_pnl", 0) + realized_gain
                # Reduce total cost proportionally
                total_cost -= shares_sold * avg_cost_per_share

        wacc = (total_cost / total_shares) if total_shares > 0 else 0

        self.position = {
            "symbol": self.symbol,
            "shares": total_shares,
            "wacc": round(wacc, 2),
            "total_cost": round(total_cost, 2),
            "trades_count": len(self.trades),
            "last_updated": datetime.now(NPT).isoformat(),
        }

        self._save_position()

    def get_trade_recommendations(self, current_price: float, signal: str) -> dict:
        """
        Get recommended buy/sell strategy based on your position.

        Args:
            current_price: Current market price
            signal: Trading signal ("BUY", "SELL", "HOLD")

        Returns:
            Recommendations with specific share counts
        """
        shares = self.position["shares"]
        wacc = self.position["wacc"]
        pnl_pct = ((current_price - wacc) / wacc * 100) if wacc > 0 else 0

        recommendations = {
            "current_price": current_price,
            "signal": signal,
            "shares_held": shares,
            "wacc": wacc,
            "pnl_pct": round(pnl_pct, 2),
            "strategies": [],
        }

        if signal == "BUY":
            # Aggressive: Buy more to average down
            if pnl_pct < -10:  # You're in significant loss
                buy_qty = max(1000, int(shares * 0.25))  # Buy 25% of holdings
                new_wacc = (
                    (shares * wacc + buy_qty * current_price) / (shares + buy_qty)
                )
                wacc_improvement = wacc - new_wacc

                recommendations["strategies"].append({
                    "name": "AGGRESSIVE - Average Down (Reduce Break-even)",
                    "action": f"BUY {buy_qty:,} shares @ NPR {current_price}",
                    "investment": round(buy_qty * current_price, 2),
                    "new_wacc": round(new_wacc, 2),
                    "wacc_improvement": round(wacc_improvement, 2),
                    "new_pnl_pct": round(((current_price - new_wacc) / new_wacc * 100), 2),
                    "risk": "HIGH - increases total investment",
                    "use_case": "Only if you're confident ALICL will recover",
                })

            # Moderate: Buy smaller position
            buy_qty = max(500, int(shares * 0.10))  # Buy 10%
            new_wacc = ((shares * wacc + buy_qty * current_price) / (shares + buy_qty))

            recommendations["strategies"].append({
                "name": "MODERATE - Small Accumulation",
                "action": f"BUY {buy_qty:,} shares @ NPR {current_price}",
                "investment": round(buy_qty * current_price, 2),
                "new_wacc": round(new_wacc, 2),
                "risk": "MEDIUM - tested position increase",
                "use_case": "Safe way to accumulate if you believe in stock",
            })

        elif signal == "SELL":
            # Take profits: Sell portion at good price
            if pnl_pct > 0:
                sell_qty = int(shares * 0.33)  # Sell 33%
                profit = sell_qty * (current_price - wacc)

                recommendations["strategies"].append({
                    "name": "PROFIT TAKING - Partial Exit (Lock in Gains)",
                    "action": f"SELL {sell_qty:,} shares @ NPR {current_price}",
                    "proceeds": round(sell_qty * current_price, 2),
                    "realized_profit": round(profit, 2),
                    "remaining_shares": shares - sell_qty,
                    "remaining_risk": round((shares - sell_qty) * current_price, 2),
                    "use_case": "Secure profits while keeping upside exposure",
                })

            # Stop loss: Exit to limit losses
            if pnl_pct < -15:
                sell_qty = int(shares * 0.5)  # Sell 50%
                loss = sell_qty * (current_price - wacc)

                recommendations["strategies"].append({
                    "name": "STOP LOSS - Damage Control (Cut Losses)",
                    "action": f"SELL {sell_qty:,} shares @ NPR {current_price}",
                    "proceeds": round(sell_qty * current_price, 2),
                    "realized_loss": round(loss, 2),
                    "remaining_capital": round((shares - sell_qty) * current_price, 2),
                    "capital_loss": round(abs(loss), 2),
                    "use_case": "Prevent catastrophic loss if stock crashes further",
                    "risk": "You miss recovery rally",
                })

        elif signal == "HOLD":
            recommendations["strategies"].append({
                "name": "NEUTRAL - Hold & Wait",
                "action": "Do nothing - wait for clearer signal",
                "current_value": round(shares * current_price, 2),
                "current_pnl": round(shares * (current_price - wacc), 2),
                "use_case": "Signal not strong enough to act",
            })

        return recommendations

    def get_specific_sell_target(self, signal_score: float, loss_pct: float) -> dict:
        """
        Calculate specific sell targets based on how bad your loss is.

        Args:
            signal_score: Trading signal score (-100 to +100)
            loss_pct: Your current loss percentage

        Returns:
            Specific pricing and volume targets
        """
        shares = self.position["shares"]
        wacc = self.position["wacc"]

        targets = {
            "assess": f"You're at {loss_pct:.1f}% loss, signal is {signal_score}",
            "current_shares": shares,
            "break_even": wacc,
            "sell_plan": [],
        }

        if loss_pct < -20:  # Critical loss (>20%)
            targets["situation"] = "CRITICAL LOSS - Consider exit strategy"
            targets["sell_plan"] = [
                {
                    "step": 1,
                    "trigger": f"If price hits NPR {wacc * 0.95:.2f} (near WACC)",
                    "qty": int(shares * 0.25),
                    "reason": "Exit 25% to recover some capital",
                    "proceeds": round(int(shares * 0.25) * (wacc * 0.95), 2),
                },
                {
                    "step": 2,
                    "trigger": f"If price drops to NPR {wacc * 0.85:.2f}",
                    "qty": int(shares * 0.5),
                    "reason": "Exit another 50% to cut losses completely",
                    "proceeds": round(int(shares * 0.5) * (wacc * 0.85), 2),
                },
            ]

        elif loss_pct < -10:  # Significant loss (10-20%)
            targets["situation"] = "SIGNIFICANT LOSS - Need recovery"
            targets["sell_plan"] = [
                {
                    "step": 1,
                    "trigger": f"If signal is BUY and price hits NPR {wacc * 1.05:.2f}",
                    "qty": int(shares * 0.15),
                    "reason": "Sell 15% to fund next buy (average down)",
                    "proceeds": round(int(shares * 0.15) * (wacc * 1.05), 2),
                },
                {
                    "step": 2,
                    "trigger": f"If price reaches NPR {wacc * 1.10:.2f}",
                    "qty": int(shares * 0.25),
                    "reason": "Sell another 25% to recover part of loss",
                    "proceeds": round(int(shares * 0.25) * (wacc * 1.10), 2),
                },
            ]

        elif loss_pct < 0:  # Small loss (<10%)
            targets["situation"] = "MINOR LOSS - Manageable"
            targets["sell_plan"] = [
                {
                    "step": 1,
                    "trigger": f"If signal turns SELL and price hits NPR {wacc * 1.03:.2f}",
                    "qty": int(shares * 0.33),
                    "reason": "Recover from loss, keep bulk of position",
                    "proceeds": round(int(shares * 0.33) * (wacc * 1.03), 2),
                },
            ]

        elif loss_pct >= 0:  # In profit
            targets["situation"] = "IN PROFIT - Protect gains!"
            targets["sell_plan"] = [
                {
                    "step": 1,
                    "trigger": f"If signal turns SELL and price hits NPR {wacc * 1.06:.2f}",
                    "qty": int(shares * 0.50),
                    "reason": "Lock in half the profit, keep upside",
                    "proceeds": round(int(shares * 0.50) * (wacc * 1.06), 2),
                },
                {
                    "step": 2,
                    "trigger": f"If momentum continues, sell at NPR {wacc * 1.12:.2f}",
                    "qty": int(shares * 0.5),
                    "reason": "Capture full rally, maximize gains",
                    "proceeds": round(int(shares * 0.5) * (wacc * 1.12), 2),
                },
            ]

        return targets

    def get_rebuy_strategy(self, last_sell_price: float, last_sell_qty: int) -> dict:
        """
        After you sell, when/how should you rebuy?

        Args:
            last_sell_price: Price at which you sold
            last_sell_qty: How many shares you sold

        Returns:
            Rebuy strategy with specific entry points
        """
        return {
            "you_just_sold": {
                "qty": last_sell_qty,
                "price": last_sell_price,
                "proceeds": round(last_sell_qty * last_sell_price, 2),
            },
            "rebuy_strategy": [
                {
                    "level": 1,
                    "trigger": f"Price drops {5}% to NPR {last_sell_price * 0.95:.2f}",
                    "buy_qty": int(last_sell_qty * 0.5),
                    "cost": round(int(last_sell_qty * 0.5) * (last_sell_price * 0.95), 2),
                    "reason": "Re-enter at 5% discount (good price)",
                },
                {
                    "level": 2,
                    "trigger": f"Price drops {10}% to NPR {last_sell_price * 0.90:.2f}",
                    "buy_qty": int(last_sell_qty * 0.75),
                    "cost": round(int(last_sell_qty * 0.75) * (last_sell_price * 0.90), 2),
                    "reason": "Aggressive re-entry (very good price)",
                },
            ],
            "timing": "Wait for BUY signal before re-entering - don't just chase dips",
            "total_you_have": round(last_sell_qty * last_sell_price, 2),
        }

    def _load_trades(self) -> list:
        if os.path.exists(self.trades_file):
            with open(self.trades_file) as f:
                return json.load(f)
        return []

    def _save_trades(self):
        with open(self.trades_file, "w") as f:
            json.dump(self.trades, f, indent=2, default=str)

    def _load_position(self) -> dict:
        if os.path.exists(self.position_file):
            with open(self.position_file) as f:
                return json.load(f)
        # Default: your initial position
        return {
            "symbol": self.symbol,
            "shares": 8046,
            "wacc": 549.87,
            "total_cost": 4424248.16,
            "realized_pnl": 0.0,
            "trades_count": 0,
        }

    def _save_position(self):
        with open(self.position_file, "w") as f:
            json.dump(self.position, f, indent=2, default=str)

    def view_all_trades(self):
        """Show all your trades in a formatted table."""
        if not self.trades:
            print("No trades recorded yet. Your position is the initial 8,046 shares.")
            return

        print(f"\n{'='*80}")
        print(f"  {self.symbol} TRADE HISTORY")
        print(f"{'='*80}\n")
        for i, trade in enumerate(self.trades, 1):
            print(f"{i}. {trade['action']} {trade['shares']:,} @ NPR {trade['price']:.2f}")
            print(f"   Value: NPR {trade['total']:,.2f}")
            print(f"   Date: {trade['timestamp']}")
            if trade.get("notes"):
                print(f"   Notes: {trade['notes']}")
            print()

    def print_position(self):
        """Print current position summary."""
        pos = self.position
        print(f"\n{'='*60}")
        print(f"  {self.symbol} POSITION")
        print(f"{'='*60}")
        print(f"  Shares: {pos['shares']:,}")
        print(f"  WACC (break-even): NPR {pos['wacc']:,.2f}")
        print(f"  Total cost: NPR {pos['total_cost']:,.2f}")
        print(f"  Trades: {pos['trades_count']}")
        print(f"  Realized P&L: NPR {pos.get('realized_pnl', 0):+,.2f}")
        print(f"{'='*60}\n")
