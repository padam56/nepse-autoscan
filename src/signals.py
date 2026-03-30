"""
Signal Generator - Generates buy/sell/hold signals based on technical analysis.

Combines multiple indicators into a weighted scoring system for actionable trading signals.
"""

import numpy as np


class SignalGenerator:
    """Generates trading signals from technical analysis results."""

    # Weight each signal category (total = 1.0)
    WEIGHTS = {
        "trend": 0.20,
        "rsi": 0.15,
        "macd": 0.15,
        "bollinger": 0.10,
        "volume": 0.10,
        "support_resistance": 0.15,
        "moving_averages": 0.15,
    }

    def __init__(self, ta_results: dict):
        self.ta = ta_results
        self.signals = {}
        self.composite_score = 0.0

    def generate_all(self) -> dict:
        """Run all signal generators and compute composite score."""
        self.signals["trend"] = self._trend_signal()
        self.signals["rsi"] = self._rsi_signal()
        self.signals["macd"] = self._macd_signal()
        self.signals["bollinger"] = self._bollinger_signal()
        self.signals["volume"] = self._volume_signal()
        self.signals["support_resistance"] = self._sr_signal()
        self.signals["moving_averages"] = self._ma_signal()

        # Composite score: weighted average of all signals (-100 to +100)
        total = 0
        for key, signal in self.signals.items():
            weight = self.WEIGHTS.get(key, 0)
            total += signal["score"] * weight
        self.composite_score = round(total, 1)

        # Overall recommendation
        if self.composite_score >= 40:
            action = "STRONG BUY"
        elif self.composite_score >= 15:
            action = "BUY"
        elif self.composite_score >= -15:
            action = "HOLD"
        elif self.composite_score >= -40:
            action = "SELL"
        else:
            action = "STRONG SELL"

        return {
            "composite_score": self.composite_score,
            "action": action,
            "signals": self.signals,
            "risk_level": self._assess_risk(),
            "key_levels": self._key_action_levels(),
        }

    # ── Individual Signal Generators ───────────────────────────

    def _trend_signal(self) -> dict:
        trend = self.ta.get("trend", {})
        score = trend.get("trend_score", 0) * 100  # -100 to +100
        return {
            "score": round(score),
            "label": trend.get("overall_trend", "UNKNOWN"),
            "detail": f"Trend score: {trend.get('trend_score', 0)}, "
                      f"Cross: {trend.get('cross_signal', 'N/A')}, "
                      f"5D ROC: {trend.get('roc_5d', 0)}%, "
                      f"20D ROC: {trend.get('roc_20d', 0)}%",
        }

    def _rsi_signal(self) -> dict:
        rsi = self.ta.get("rsi", 50)
        if np.isnan(rsi):
            return {"score": 0, "label": "NO DATA", "detail": "RSI unavailable"}

        if rsi < 30:
            score = 80  # Oversold = buy signal
            label = "OVERSOLD (Strong Buy)"
        elif rsi < 40:
            score = 40
            label = "APPROACHING OVERSOLD (Buy)"
        elif rsi < 60:
            score = 0
            label = "NEUTRAL"
        elif rsi < 70:
            score = -40
            label = "APPROACHING OVERBOUGHT (Sell)"
        else:
            score = -80
            label = "OVERBOUGHT (Strong Sell)"

        return {"score": score, "label": label, "detail": f"RSI(14) = {rsi}"}

    def _macd_signal(self) -> dict:
        macd = self.ta.get("macd", {})
        hist = macd.get("histogram", 0)
        macd_val = macd.get("macd", 0)
        signal_val = macd.get("signal", 0)

        if any(np.isnan(v) for v in [hist, macd_val, signal_val]):
            return {"score": 0, "label": "NO DATA", "detail": "MACD unavailable"}

        # Score based on histogram direction and magnitude
        if hist > 0 and macd_val > signal_val:
            score = min(70, hist * 10)
            label = "BULLISH (MACD above signal)"
        elif hist < 0 and macd_val < signal_val:
            score = max(-70, hist * 10)
            label = "BEARISH (MACD below signal)"
        else:
            score = 0
            label = "NEUTRAL (crossover zone)"

        # Check for crossover
        detail = f"MACD: {macd_val}, Signal: {signal_val}, Hist: {hist}"
        return {"score": round(score), "label": label, "detail": detail}

    def _bollinger_signal(self) -> dict:
        bb = self.ta.get("bollinger", {})
        position = bb.get("position", 0.5)

        if np.isnan(position):
            return {"score": 0, "label": "NO DATA", "detail": "Bollinger unavailable"}

        if position < 0.1:
            score = 70
            label = "NEAR LOWER BAND (Buy)"
        elif position < 0.3:
            score = 30
            label = "LOWER ZONE (Mild Buy)"
        elif position < 0.7:
            score = 0
            label = "MID BAND (Neutral)"
        elif position < 0.9:
            score = -30
            label = "UPPER ZONE (Mild Sell)"
        else:
            score = -70
            label = "NEAR UPPER BAND (Sell)"

        detail = (
            f"Position: {position:.2f} | "
            f"Upper: {bb.get('upper', 'N/A')}, Mid: {bb.get('mid', 'N/A')}, Lower: {bb.get('lower', 'N/A')}"
        )
        return {"score": score, "label": label, "detail": detail}

    def _volume_signal(self) -> dict:
        vol = self.ta.get("volume", {})
        ratio = vol.get("ratio", 1.0)

        if np.isnan(ratio):
            return {"score": 0, "label": "NO DATA", "detail": "Volume data unavailable"}

        price = self.ta.get("price", {})
        close = price.get("close", 0)
        open_p = price.get("open", 0)
        price_up = close > open_p

        # High volume + price up = bullish confirmation
        # High volume + price down = bearish confirmation
        if ratio > 1.5:
            score = 50 if price_up else -50
            label = "HIGH VOLUME " + ("BULLISH" if price_up else "BEARISH")
        elif ratio > 1.0:
            score = 20 if price_up else -20
            label = "ABOVE AVG VOLUME " + ("BULLISH" if price_up else "BEARISH")
        elif ratio > 0.5:
            score = 0
            label = "NORMAL VOLUME"
        else:
            score = -10  # Low volume = weak conviction
            label = "LOW VOLUME (Weak)"

        detail = f"Vol Ratio: {ratio:.2f}x avg | Current: {vol.get('current', 0):,} | 20D Avg: {vol.get('avg_20d', 0):,.0f}"
        return {"score": score, "label": label, "detail": detail}

    def _sr_signal(self) -> dict:
        sr = self.ta.get("support_resistance", {})
        current = sr.get("current_price", 0)
        supports = sr.get("support_levels", [])
        resistances = sr.get("resistance_levels", [])

        if not supports and not resistances:
            return {"score": 0, "label": "NO DATA", "detail": "S/R levels unavailable"}

        # How close to support vs resistance?
        nearest_support = supports[0] if supports else current * 0.9
        nearest_resistance = resistances[0] if resistances else current * 1.1

        if current == 0:
            return {"score": 0, "label": "NO DATA", "detail": "Current price is zero"}

        support_dist = (current - nearest_support) / current * 100
        resist_dist = (nearest_resistance - current) / current * 100

        # Closer to support = more bullish, closer to resistance = more bearish
        if support_dist < 2:
            score = 60
            label = "AT SUPPORT (Buy Zone)"
        elif support_dist < 5:
            score = 30
            label = "NEAR SUPPORT (Mild Buy)"
        elif resist_dist < 2:
            score = -60
            label = "AT RESISTANCE (Sell Zone)"
        elif resist_dist < 5:
            score = -30
            label = "NEAR RESISTANCE (Mild Sell)"
        else:
            score = 0
            label = "MID RANGE"

        detail = (
            f"Nearest Support: {nearest_support} ({support_dist:.1f}% away) | "
            f"Nearest Resistance: {nearest_resistance} ({resist_dist:.1f}% away)"
        )
        return {"score": score, "label": label, "detail": detail}

    def _ma_signal(self) -> dict:
        ma = self.ta.get("moving_averages", {})
        price = self.ta.get("price", {}).get("close", 0)

        above_count = 0
        total_count = 0
        for key, val in ma.items():
            try:
                if not np.isnan(val):
                    total_count += 1
                    if price > val:
                        above_count += 1
            except (TypeError, ValueError):
                continue

        if total_count == 0:
            return {"score": 0, "label": "NO DATA", "detail": "MA data unavailable"}

        ratio = above_count / total_count
        score = (ratio - 0.5) * 200  # Maps 0-1 to -100 to +100

        if ratio > 0.7:
            label = f"BULLISH (above {above_count}/{total_count} MAs)"
        elif ratio > 0.4:
            label = f"NEUTRAL (above {above_count}/{total_count} MAs)"
        else:
            label = f"BEARISH (above {above_count}/{total_count} MAs)"

        detail = " | ".join(f"{k}: {v}" for k, v in ma.items() if not np.isnan(v))
        return {"score": round(score), "label": label, "detail": detail}

    # ── Risk Assessment ────────────────────────────────────────

    def _assess_risk(self) -> dict:
        atr = self.ta.get("atr", {})
        bb = self.ta.get("bollinger", {})
        vol = self.ta.get("volume", {})

        atr_pct = atr.get("atr_pct", 0)
        bb_width = bb.get("width", 0)
        vol_ratio = vol.get("ratio", 1)

        # Volatility risk
        if atr_pct > 4:
            vol_risk = "HIGH"
        elif atr_pct > 2:
            vol_risk = "MEDIUM"
        else:
            vol_risk = "LOW"

        # Squeeze detection (low BB width = potential breakout)
        squeeze = bb_width < 0.05 if not np.isnan(bb_width) else False

        return {
            "volatility_risk": vol_risk,
            "atr_pct": round(atr_pct, 2) if not np.isnan(atr_pct) else "N/A",
            "bollinger_squeeze": squeeze,
            "volume_conviction": "STRONG" if vol_ratio > 1.5 else "MODERATE" if vol_ratio > 0.8 else "WEAK",
        }

    # ── Key Action Levels ──────────────────────────────────────

    def _key_action_levels(self) -> dict:
        sr = self.ta.get("support_resistance", {})
        atr = self.ta.get("atr", {}).get("atr", 0)
        current = sr.get("current_price", 0)

        supports = sr.get("support_levels", [])
        resistances = sr.get("resistance_levels", [])

        atr_val = atr if not np.isnan(atr) else current * 0.02

        return {
            "buy_zone": supports[0] if supports else round(current - atr_val * 1.5, 2),
            "strong_buy": supports[1] if len(supports) > 1 else round(current - atr_val * 3, 2),
            "sell_zone": resistances[0] if resistances else round(current + atr_val * 1.5, 2),
            "strong_sell": resistances[1] if len(resistances) > 1 else round(current + atr_val * 3, 2),
            "stop_loss": round(current - atr_val * 2, 2),
            "trailing_stop": round(current - atr_val * 1.5, 2),
        }
