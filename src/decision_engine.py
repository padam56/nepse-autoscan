"""
Decision Engine - The backbone model that combines:
1. Technical signals (RSI, MACD, Bollinger, etc.)
2. Macro/market-wide analysis (breadth, sector, volatility)
3. Position context (your P&L, WACC, shares held)
4. Market regime (political uncertainty → sell-first bias)

Outputs a single actionable decision with:
- PRIMARY ACTION: SELL / BUY / HOLD with urgency
- SELL LEVELS: Specific prices to sell at (resistance zones)
- BUY-BACK LEVELS: Where to re-enter after selling
- CONFIDENCE: How strong the signal is (0-100%)
- RATIONALE: Plain-English explanation of the decision

User Strategy: SELL FIRST into strength, then BUY BACK on confirmed dip.
"""

from typing import Optional
from src.macro_analyzer import MacroAnalyzer


class DecisionEngine:
    """
    The backbone model.
    Combines tech + macro + position context → single actionable decision.
    """

    # Technical vs macro weight balance
    # In high political uncertainty, macro matters more
    TECH_WEIGHT = 0.45
    MACRO_WEIGHT = 0.55   # Slightly higher macro weight given Nepal political context

    def __init__(
        self,
        tech_signal: dict,       # Output from SignalGenerator.generate_all()
        market_summary: dict,    # Output from RealtimeData.fetch_market_summary()
        position: dict,          # PortfolioManager.position
        current_price: float,
    ):
        self.tech = tech_signal
        self.macro_analyzer = MacroAnalyzer(market_summary)
        self.position = position
        self.price = current_price

        self.wacc = position.get("wacc", 0)
        self.shares = position.get("shares", 0)
        self.pnl_pct = ((current_price - self.wacc) / self.wacc * 100) if self.wacc > 0 else 0

    def run(self) -> dict:
        """
        Run the full decision model.
        Returns a complete decision with sell levels + buy-back levels.
        """
        # 1. Get macro analysis
        macro = self.macro_analyzer.get_macro_score()
        decision_bias = self.macro_analyzer.get_decision_bias(macro["score"], self.pnl_pct)

        # 2. Get tech score
        tech_score = self.tech.get("composite_score", 0)
        tech_action = self.tech.get("action", "HOLD")
        key_levels = self.tech.get("key_levels", {})

        # 3. Combine into final score
        combined_score = (
            tech_score * self.TECH_WEIGHT
            + macro["score"] * self.MACRO_WEIGHT
        )

        # 4. Determine final action (user's sell-first priority overrides if macro is bullish)
        final_action, urgency, confidence = self._determine_action(
            combined_score, decision_bias, tech_action
        )

        # 5. Calculate specific SELL levels (resistance zones)
        sell_levels = self._calculate_sell_levels(key_levels)

        # 6. Calculate BUY-BACK levels (support zones after selling)
        buyback_levels = self._calculate_buyback_levels(key_levels, sell_levels)

        # 7. Quantity recommendations
        quantities = self._calculate_quantities(final_action, confidence)

        # 8. Build rationale
        rationale = self._build_rationale(
            final_action, combined_score, tech_score, macro, decision_bias, confidence
        )

        return {
            "final_action": final_action,
            "urgency": urgency,
            "confidence": confidence,
            "combined_score": round(combined_score, 1),
            "tech_score": tech_score,
            "macro_score": macro["score"],
            "macro_regime": macro["regime"],
            "decision_bias": decision_bias,
            "sell_levels": sell_levels,
            "buyback_levels": buyback_levels,
            "quantities": quantities,
            "rationale": rationale,
            "macro_detail": macro,
            "tech_detail": self.tech,
            "position_context": {
                "price": self.price,
                "wacc": self.wacc,
                "pnl_pct": round(self.pnl_pct, 2),
                "shares": self.shares,
                "total_value": round(self.shares * self.price, 2),
            },
        }

    def _determine_action(self, combined_score, decision_bias, tech_action):
        """Determine final action from combined score + user's sell-first strategy."""
        bias = decision_bias["bias"]

        # User's strategy: SELL FIRST
        # If bias is SELL_FIRST or SELL_PARTIAL, we prioritize that
        if bias == "SELL_FIRST":
            if combined_score >= -10:  # Market not in freefall
                return "SELL", "HIGH", self._score_to_confidence(abs(combined_score) + 20)
            else:
                return "SELL", "MEDIUM", self._score_to_confidence(abs(combined_score))

        elif bias == "SELL_PARTIAL":
            return "SELL_PARTIAL", "MEDIUM", self._score_to_confidence(abs(combined_score) + 10)

        elif bias == "WAIT":
            return "HOLD", "LOW", self._score_to_confidence(30)

        else:
            # Default: follow combined score
            if combined_score >= 40:
                return "STRONG_BUY", "HIGH", self._score_to_confidence(combined_score)
            elif combined_score >= 15:
                return "BUY", "MEDIUM", self._score_to_confidence(combined_score)
            elif combined_score >= -15:
                return "HOLD", "LOW", self._score_to_confidence(30)
            elif combined_score >= -40:
                return "SELL", "MEDIUM", self._score_to_confidence(abs(combined_score))
            else:
                return "STRONG_SELL", "HIGH", self._score_to_confidence(abs(combined_score))

    def _score_to_confidence(self, score: float) -> int:
        """Convert a score to a 0-100% confidence."""
        return min(95, max(30, int(abs(score))))

    def _calculate_sell_levels(self, key_levels: dict) -> list:
        """
        Calculate specific prices where you should SELL.
        Uses technical resistance + profit targets from WACC.
        Prioritized by urgency (sell nearest first).
        """
        price = self.price
        wacc = self.wacc

        levels = []

        # Level 1: Immediate - current price or very near (if already profitable or near BE)
        gain_to_BE = ((wacc - price) / price * 100) if price < wacc else 0
        if self.pnl_pct >= 0:
            levels.append({
                "priority": 1,
                "label": "SELL NOW (In Profit)",
                "price": round(price, 2),
                "pnl_at_this_price": round(self.pnl_pct, 2),
                "qty_suggested": self._pct_of_shares(0.33),
                "reason": f"Already in profit at +{self.pnl_pct:.1f}%. Lock in gains.",
            })

        # Level 2: Near resistance (technical key levels)
        sell_zone_high = key_levels.get("sell_zone_high", price * 1.03)
        sell_zone_low = key_levels.get("sell_zone_low", price * 1.01)
        r1 = key_levels.get("strong_sell", price * 1.05)

        levels.append({
            "priority": 2,
            "label": f"SELL ZONE (Resistance)",
            "price": round(sell_zone_low, 2),
            "pnl_at_this_price": round(((sell_zone_low - wacc) / wacc * 100) if wacc > 0 else 0, 2),
            "qty_suggested": self._pct_of_shares(0.40),
            "reason": f"Technical resistance zone. Sell 40% here to capture partial recovery.",
        })

        levels.append({
            "priority": 3,
            "label": "STRONG SELL (Upper Resistance)",
            "price": round(r1, 2),
            "pnl_at_this_price": round(((r1 - wacc) / wacc * 100) if wacc > 0 else 0, 2),
            "qty_suggested": self._pct_of_shares(0.30),
            "reason": "Strong resistance. If price reaches here, sell most of remaining position.",
        })

        # Level 4: Break-even sell (if you're down)
        if self.pnl_pct < 0:
            levels.append({
                "priority": 4,
                "label": "BREAK-EVEN SELL (Full Recovery)",
                "price": round(wacc, 2),
                "pnl_at_this_price": 0.0,
                "qty_suggested": self._pct_of_shares(0.50),
                "reason": f"Sell 50% at break-even (NPR {wacc:,.2f}) to recover half your capital at zero loss.",
            })

        # Sort by price (nearest target first = most achievable)
        levels.sort(key=lambda x: x["price"])
        for i, lvl in enumerate(levels):
            lvl["priority"] = i + 1

        return levels

    def _calculate_buyback_levels(self, key_levels: dict, sell_levels: list) -> list:
        """
        After selling, where to BUY BACK.
        These are support levels BELOW current price.
        Only buy when: BUY signal confirmed + price hits these levels.
        """
        price = self.price
        wacc = self.wacc

        buy_zone_low = key_levels.get("buy_zone_low", price * 0.97)
        strong_buy = key_levels.get("strong_buy", price * 0.93)
        stop_loss = key_levels.get("stop_loss", price * 0.90)

        # Support levels (buy dips at these prices)
        levels = [
            {
                "level": 1,
                "label": "First Support (Minor Dip)",
                "price": round(price * 0.97, 2),
                "discount_from_now": "-3%",
                "qty_suggested": self._pct_of_shares(0.25),
                "condition": "Wait for BUY signal confirmation first",
                "reason": f"Minor 3% dip from current. Good entry if signal turns BUY.",
            },
            {
                "level": 2,
                "label": "Main Support (Good Entry)",
                "price": round(buy_zone_low, 2),
                "discount_from_now": f"-{round((price - buy_zone_low) / price * 100, 1)}%",
                "qty_suggested": self._pct_of_shares(0.40),
                "condition": "Wait for RSI < 40 + BUY signal",
                "reason": "Strong technical support. Best risk/reward entry zone.",
            },
            {
                "level": 3,
                "label": "Deep Support (Aggressive Entry)",
                "price": round(strong_buy, 2),
                "discount_from_now": f"-{round((price - strong_buy) / price * 100, 1)}%",
                "qty_suggested": self._pct_of_shares(0.50),
                "condition": "Only buy if STRONG BUY signal + high volume bounce",
                "reason": "Aggressive entry - good for averaging down significantly.",
            },
            {
                "level": 4,
                "label": "Emergency Support (Stop Loss Level)",
                "price": round(stop_loss, 2),
                "discount_from_now": f"-{round((price - stop_loss) / price * 100, 1)}%",
                "qty_suggested": 0,
                "condition": "DO NOT buy here - this is your STOP LOSS",
                "reason": "If price breaks below this, something is fundamentally wrong. Do not catch the falling knife.",
            },
        ]

        return levels

    def _calculate_quantities(self, action: str, confidence: int) -> dict:
        """
        Specific share quantities for each action.
        Based on current position and confidence level.
        """
        shares = self.shares
        pnl_pct = self.pnl_pct

        if action in ("SELL", "STRONG_SELL"):
            # In loss → sell to reduce exposure + plan rebuy
            qty_immediate = self._pct_of_shares(0.40 if confidence > 70 else 0.25)
            qty_at_resistance = self._pct_of_shares(0.35)
            qty_at_break_even = self._pct_of_shares(0.25)
            return {
                "sell_now": qty_immediate,
                "sell_at_resistance": qty_at_resistance,
                "sell_at_break_even": qty_at_break_even,
                "keep_core": shares - qty_immediate - qty_at_resistance,
                "rationale": "Sell in tranches: some now, more at resistance, rest at break-even.",
            }

        elif action == "SELL_PARTIAL":
            qty = self._pct_of_shares(0.25)
            return {
                "sell_now": qty,
                "keep_core": shares - qty,
                "rationale": "Sell partial (25%) to reduce exposure. Keep rest for potential upside.",
            }

        elif action in ("BUY", "STRONG_BUY"):
            invest = self._pct_of_shares(0.20)
            return {
                "buy_qty": invest,
                "rationale": "Buy cautiously - market up but political uncertainty limits upside.",
            }

        else:
            return {
                "action": "NONE",
                "rationale": "No trade recommended. Watch and wait.",
            }

    def _pct_of_shares(self, pct: float) -> int:
        """Return N% of current holdings, rounded to nearest 100."""
        qty = int(self.shares * pct)
        return max(100, (qty // 100) * 100)  # Round to nearest 100

    def _build_rationale(self, action, score, tech_score, macro, decision_bias, confidence):
        """Build plain-English explanation of the decision."""
        breadth = macro["breadth"]
        insurance = macro["insurance_sector"]
        regime = macro["regime"]
        bias_reason = decision_bias["reason"]
        political = decision_bias.get("political_uncertainty", False)

        lines = []

        # Action summary
        lines.append(f"ACTION: {action} | Confidence: {confidence}% | Combined Score: {score:+.1f}")
        lines.append("")

        # Political context
        if political:
            lines.append("[WARN] POLITICAL CONTEXT:")
            lines.append("   Nepal is experiencing major political changes.")
            lines.append("   Political uncertainty = market volatility = ideal time to SELL into strength.")
            lines.append("   Strategy: SELL on any pop, then BUY BACK after dust settles.")
            lines.append("")

        # Macro context
        lines.append("MACRO SNAPSHOT:")
        lines.append(f"   Market breadth: {breadth['gainers']} gainers vs {breadth['losers']} losers ({breadth['mood']})")
        lines.append(f"   Insurance sector: {insurance['change_pct']:+.2f}% ({insurance['sentiment']})")
        lines.append(f"   Volatility: {macro['volatility']}")
        lines.append(f"   Regime: {regime}")
        lines.append(f"   Macro stance: {macro['stance']}")
        lines.append("")

        # Technical context
        lines.append("TECHNICAL SNAPSHOT:")
        lines.append(f"   Tech score: {tech_score:+.1f}/100 → {self.tech.get('action', 'N/A')}")
        ta_sigs = self.tech.get("signals", {})
        for k, v in ta_sigs.items():
            label = v.get("label", "")
            if label:
                lines.append(f"   {k.upper()}: {label}")
        lines.append("")

        # Decision logic
        lines.append("DECISION LOGIC:")
        lines.append(f"   {bias_reason}")
        lines.append("")

        # Your position
        lines.append("YOUR POSITION:")
        lines.append(f"   Shares: {self.shares:,}")
        lines.append(f"   WACC: NPR {self.wacc:,.2f}")
        lines.append(f"   Current Price: NPR {self.price:,.2f}")
        lines.append(f"   Current P&L: {self.pnl_pct:+.2f}%")
        lines.append(f"   Total value: NPR {self.shares * self.price:,.2f}")

        return "\n".join(lines)
