"""
Position Tracker - Tracks P&L, break-even, and recovery scenarios for NEPSE holdings.
"""

from src.config import PORTFOLIO


class PositionTracker:
    """Track portfolio positions with P&L analysis and recovery planning."""

    def __init__(self, symbol: str, current_price: float):
        self.symbol = symbol.upper()
        self.current_price = current_price
        pos = PORTFOLIO.get(self.symbol)
        if not pos:
            raise ValueError(f"No position found for {self.symbol} in config")
        self.shares = pos["shares"]
        self.wacc = pos["wacc"]
        self.total_cost = pos["total_cost"]

    @property
    def current_value(self) -> float:
        return self.shares * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return self.current_value - self.total_cost

    @property
    def unrealized_pnl_pct(self) -> float:
        return (self.unrealized_pnl / self.total_cost) * 100

    @property
    def breakeven_price(self) -> float:
        return self.wacc

    def summary(self) -> dict:
        return {
            "symbol": self.symbol,
            "shares": self.shares,
            "wacc": round(self.wacc, 2),
            "total_cost": round(self.total_cost, 2),
            "current_price": self.current_price,
            "current_value": round(self.current_value, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "unrealized_pnl_pct": round(self.unrealized_pnl_pct, 2),
            "breakeven_price": round(self.breakeven_price, 2),
            "distance_to_breakeven": round(self.breakeven_price - self.current_price, 2),
            "distance_to_breakeven_pct": round(
                ((self.breakeven_price - self.current_price) / self.current_price) * 100, 2
            ),
        }

    # ── Recovery Scenarios ─────────────────────────────────────

    def averaging_down_scenario(self, buy_price: float, buy_qty: int) -> dict:
        """What happens if you buy more shares at a given price?"""
        new_total_shares = self.shares + buy_qty
        new_total_cost = self.total_cost + (buy_price * buy_qty)
        new_wacc = new_total_cost / new_total_shares
        new_value = new_total_shares * self.current_price
        new_pnl = new_value - new_total_cost

        return {
            "action": f"Buy {buy_qty} more at NPR {buy_price}",
            "new_total_shares": new_total_shares,
            "new_wacc": round(new_wacc, 2),
            "new_total_cost": round(new_total_cost, 2),
            "new_breakeven": round(new_wacc, 2),
            "wacc_reduction": round(self.wacc - new_wacc, 2),
            "additional_investment": round(buy_price * buy_qty, 2),
            "new_pnl_at_current": round(new_pnl, 2),
            "new_pnl_pct": round((new_pnl / new_total_cost) * 100, 2),
        }

    def partial_exit_scenario(self, sell_price: float, sell_qty: int) -> dict:
        """What happens if you sell some shares at a given price?"""
        sell_value = sell_qty * sell_price
        realized_pnl = sell_qty * (sell_price - self.wacc)
        remaining_shares = self.shares - sell_qty
        remaining_cost = remaining_shares * self.wacc
        remaining_value = remaining_shares * self.current_price

        return {
            "action": f"Sell {sell_qty} at NPR {sell_price}",
            "sell_value": round(sell_value, 2),
            "realized_pnl": round(realized_pnl, 2),
            "remaining_shares": remaining_shares,
            "remaining_cost": round(remaining_cost, 2),
            "remaining_value": round(remaining_value, 2),
            "remaining_unrealized_pnl": round(remaining_value - remaining_cost, 2),
        }

    def target_price_analysis(self) -> list[dict]:
        """Show P&L at various target prices."""
        targets = [
            ("52-Week Low Zone", 430),
            ("Strong Support", 450),
            ("Current Range", self.current_price),
            ("Near Resistance", 520),
            ("Break-even", round(self.wacc)),
            ("Moderate Target", 600),
            ("Optimistic Target", 650),
            ("52-Week High Recovery", 779),
        ]
        results = []
        for label, price in targets:
            value = self.shares * price
            pnl = value - self.total_cost
            results.append({
                "target": label,
                "price": price,
                "portfolio_value": round(value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round((pnl / self.total_cost) * 100, 2),
            })
        return results
