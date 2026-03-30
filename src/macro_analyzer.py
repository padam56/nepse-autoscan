"""
Macro Analyzer - Market-wide sentiment and regime detection.

Analyzes:
- NEPSE index trend (market breadth)
- Sector performance (insurance sector vs market)
- Gainers vs losers ratio
- Volatility regime (high volatility = political events)
- Overall market mood (risk-on vs risk-off)

Political/macro events in Nepal significantly impact market direction.
This module detects the prevailing regime to bias signals accordingly.
"""

from typing import Optional


class MacroAnalyzer:
    """
    Analyzes market-wide conditions to determine macro regime.

    Regime output: RISK_ON, RISK_OFF, UNCERTAIN
    - RISK_ON  → Market strength, buyers in control → good time to SELL into strength
    - RISK_OFF → Market weakness, sellers in control → wait to BUY at dips
    - UNCERTAIN → Mixed signals, reduce position size, be cautious
    """

    def __init__(self, market_summary: dict):
        self.market = market_summary
        self.stocks = market_summary.get("stock", {}).get("detail", []) if isinstance(market_summary.get("stock"), dict) else market_summary.get("stocks", [])
        self.overall = market_summary.get("overall", {})
        self.sectors = market_summary.get("sector", {}).get("detail", []) if isinstance(market_summary.get("sector"), dict) else market_summary.get("sectors", [])

    # ── Market Breadth ────────────────────────────────────────────────

    def get_breadth(self) -> dict:
        """
        Market breadth = ratio of gainers to losers.
        >0.6 gainers → bullish day
        <0.4 gainers → bearish day
        """
        gainers = 0
        losers = 0
        unchanged = 0

        for stock in self.stocks:
            try:
                # API uses 'c' for point change, 'lp' for last price
                # compute pct from those; fallback to 'pc' if available
                pc = stock.get("pc", None)
                if pc is not None:
                    pct = float(pc or 0)
                else:
                    c = float(stock.get("c", 0) or 0)
                    lp = float(stock.get("lp", 0) or 0)
                    prev = lp - c
                    pct = (c / prev * 100) if prev != 0 else 0
                if pct > 0.5:
                    gainers += 1
                elif pct < -0.5:
                    losers += 1
                else:
                    unchanged += 1
            except (ValueError, TypeError):
                pass

        total = gainers + losers + unchanged
        if total == 0:
            return {"gainers": 0, "losers": 0, "ratio": 0.5, "mood": "UNKNOWN"}

        ratio = gainers / total
        if ratio >= 0.65:
            mood = "STRONGLY BULLISH"
        elif ratio >= 0.55:
            mood = "BULLISH"
        elif ratio >= 0.45:
            mood = "NEUTRAL"
        elif ratio >= 0.35:
            mood = "BEARISH"
        else:
            mood = "STRONGLY BEARISH"

        return {
            "gainers": gainers,
            "losers": losers,
            "unchanged": unchanged,
            "ratio": round(ratio, 3),
            "mood": mood,
        }

    # ── Sector Analysis ───────────────────────────────────────────────

    def get_insurance_sector(self) -> dict:
        """Find insurance sector performance specifically (ALICL is insurance)."""
        for sector in self.sectors:
            name = str(sector.get("n", "") or sector.get("name", "")).lower()
            if "insurance" in name or "life" in name:
                try:
                    change = float(sector.get("pc", 0) or sector.get("change", 0) or 0)
                    return {
                        "name": sector.get("n", "Insurance"),
                        "change_pct": round(change, 2),
                        "sentiment": "BULLISH" if change > 0 else ("BEARISH" if change < -1 else "NEUTRAL"),
                    }
                except (ValueError, TypeError):
                    pass

        # If sector not found, estimate from insurance stocks
        insurance_stocks = ["ALICL", "LICN", "NLIC", "PLICL", "SLICL", "GLICL", "JLIC", "MLICL"]
        changes = []
        for stock in self.stocks:
            symbol = str(stock.get("s", "") or "").upper()
            if symbol in insurance_stocks:
                try:
                    pc = stock.get("pc", None)
                    if pc is not None:
                        pct = float(pc or 0)
                    else:
                        c = float(stock.get("c", 0) or 0)
                        lp = float(stock.get("lp", 0) or 0)
                        prev = lp - c
                        pct = (c / prev * 100) if prev != 0 else 0
                    changes.append(pct)
                except (ValueError, TypeError):
                    pass

        if changes:
            avg_change = sum(changes) / len(changes)
            return {
                "name": "Life Insurance (estimated)",
                "change_pct": round(avg_change, 2),
                "sentiment": "BULLISH" if avg_change > 0 else ("BEARISH" if avg_change < -1 else "NEUTRAL"),
            }

        return {"name": "Insurance", "change_pct": 0, "sentiment": "UNKNOWN"}

    def get_top_movers(self, n: int = 5) -> dict:
        """Get top gainers and losers for context."""
        valid = []
        for stock in self.stocks:
            try:
                pc = stock.get("pc", None)
                if pc is not None:
                    pct = float(pc or 0)
                else:
                    c = float(stock.get("c", 0) or 0)
                    lp = float(stock.get("lp", 0) or 0)
                    prev = lp - c
                    pct = (c / prev * 100) if prev != 0 else 0
                symbol = str(stock.get("s", ""))
                if symbol:
                    valid.append({"symbol": symbol, "change": pct})
            except (ValueError, TypeError):
                pass

        valid.sort(key=lambda x: x["change"], reverse=True)
        return {
            "top_gainers": valid[:n],
            "top_losers": valid[-n:][::-1],
        }

    # ── Volatility Regime ─────────────────────────────────────────────

    def get_volatility_regime(self, breadth: dict) -> str:
        """
        Detect volatility regime from market conditions.

        High volatility = political events, uncertainty
        Low volatility = stable trending market
        """
        ratio = breadth.get("ratio", 0.5)
        gainers = breadth.get("gainers", 0)
        losers = breadth.get("losers", 0)

        # Very lopsided day = high volatility event
        extreme = abs(ratio - 0.5)
        if extreme > 0.25:
            return "HIGH"
        elif extreme > 0.15:
            return "ELEVATED"
        else:
            return "NORMAL"

    # ── Macro Score ───────────────────────────────────────────────────

    def get_macro_score(self) -> dict:
        """
        Compute a macro score (-100 to +100):
        - Positive = market is bullish → good to SELL into strength
        - Negative = market is bearish → wait for dip, then BUY
        - Near zero = mixed signals

        Returns score + recommended stance.
        """
        breadth = self.get_breadth()
        insurance = self.get_insurance_sector()
        movers = self.get_top_movers(5)
        volatility = self.get_volatility_regime(breadth)

        # Score from breadth (±50 max)
        # ratio=0.5 → 0, ratio=1.0 → +50, ratio=0.0 → -50
        breadth_score = (breadth["ratio"] - 0.5) * 100

        # Score from insurance sector (±30 max)
        ins_change = insurance.get("change_pct", 0)
        insurance_score = max(-30, min(30, ins_change * 5))

        # Volatility modifier
        vol_modifier = {
            "HIGH": 1.2,      # Amplify signal (strong trend)
            "ELEVATED": 1.1,
            "NORMAL": 1.0,
        }.get(volatility, 1.0)

        raw_score = (breadth_score + insurance_score) * vol_modifier
        macro_score = max(-100, min(100, raw_score))

        # Determine regime
        if macro_score >= 25:
            regime = "RISK_ON"
            stance = "Market is BULLISH today - good opportunity to SELL into strength"
        elif macro_score <= -25:
            regime = "RISK_OFF"
            stance = "Market is BEARISH today - wait, DO NOT buy yet, look for lower entry"
        else:
            regime = "UNCERTAIN"
            stance = "Mixed signals - political uncertainty causing choppy action, be cautious"

        return {
            "score": round(macro_score, 1),
            "regime": regime,
            "stance": stance,
            "breadth": breadth,
            "insurance_sector": insurance,
            "volatility": volatility,
            "top_movers": movers,
        }

    # ── Decision Bias ─────────────────────────────────────────────────

    def get_decision_bias(self, macro_score: float, stock_pnl_pct: float) -> dict:
        """
        Given macro score + your current P&L, recommend SELL FIRST or BUY FIRST.

        Logic (user's strategy: SELL first, then buy dip):
        - If price popped (stock up today) AND market is bullish → SELL NOW
        - If price is down AND market bearish → WAIT, do NOT buy yet
        - If political uncertainty is high → REDUCE EXPOSURE, sell into any strength
        """
        # Political event bias: Nepal political change = volatility = sell into strength
        POLITICAL_UNCERTAINTY = True  # Hardcoded based on current Nepal political situation

        bias = "NEUTRAL"
        urgency = "LOW"
        action = "HOLD"
        reason = ""

        if POLITICAL_UNCERTAINTY:
            if macro_score >= 20:
                # Market up + political uncertainty = SELL NOW before reversal
                bias = "SELL_FIRST"
                urgency = "HIGH"
                action = "SELL"
                reason = (
                    "Nepal political change creates uncertainty. Market is up today - "
                    "SELL into this strength before potential reversal. "
                    "Plan to rebuy at lower levels after sentiment settles."
                )
            elif macro_score >= 0:
                # Flat/slightly up + political uncertainty = SELL PARTIAL
                bias = "SELL_PARTIAL"
                urgency = "MEDIUM"
                action = "SELL"
                reason = (
                    "Political uncertainty active. Sell a portion now to reduce exposure. "
                    "Keep some position in case market rallies further on political euphoria."
                )
            else:
                # Market down + political uncertainty = WAIT (don't buy the dip yet)
                bias = "WAIT"
                urgency = "LOW"
                action = "HOLD"
                reason = (
                    "Market is down and political situation is uncertain. "
                    "DO NOT buy yet - political uncertainty can push prices lower. "
                    "Wait for clarity before entering."
                )
        else:
            if macro_score >= 30:
                bias = "SELL_FIRST"
                urgency = "MEDIUM"
                action = "SELL"
                reason = "Market is strongly bullish - good exit opportunity."
            elif macro_score <= -30:
                bias = "BUY_PARTIAL"
                urgency = "MEDIUM"
                action = "BUY"
                reason = "Market oversold - consider partial buy for averaging."
            else:
                bias = "HOLD"
                urgency = "LOW"
                action = "HOLD"
                reason = "Mixed signals - no clear edge. Hold current position."

        return {
            "bias": bias,
            "urgency": urgency,
            "action": action,
            "reason": reason,
            "political_uncertainty": POLITICAL_UNCERTAINTY,
        }
