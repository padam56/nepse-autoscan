"""Unit tests for src/signals.py -- SignalGenerator class."""

import sys
import os

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.signals import SignalGenerator


# ── Helpers ───────────────────────────────────────────────────────


def _base_ta_results(**overrides):
    """Return a minimal ta_results dict with sane defaults.

    All sub-dicts can be overridden via keyword arguments.
    """
    defaults = {
        "price": {"close": 500, "open": 498, "high": 505, "low": 495, "volume": 20000},
        "rsi": 50.0,
        "macd": {"macd": 1.5, "signal": 1.0, "histogram": 0.5},
        "bollinger": {"position": 0.5, "upper": 520, "mid": 500, "lower": 480, "width": 0.08},
        "volume": {"ratio": 1.1, "current": 20000, "avg_20d": 18000},
        "support_resistance": {
            "current_price": 500,
            "support_levels": [490, 480],
            "resistance_levels": [510, 520],
            "pivot_point": 500,
            "pivot_r1": 510,
            "pivot_r2": 520,
            "pivot_s1": 490,
            "pivot_s2": 480,
        },
        "moving_averages": {
            "SMA_20": 498, "SMA_50": 495, "SMA_120": 490, "SMA_200": 485,
            "EMA_9": 499, "EMA_21": 497, "EMA_50": 494,
        },
        "trend": {
            "overall_trend": "BULLISH",
            "trend_score": 0.4,
            "ma_signals": ["BULLISH", "BULLISH", "BULLISH"],
            "cross_signal": "GOLDEN_CROSS (Bullish)",
            "roc_5d": 1.2,
            "roc_20d": 3.5,
            "hh_hl_ratio": 0.65,
        },
        "atr": {"atr": 8.0, "atr_pct": 1.6},
    }
    defaults.update(overrides)
    return defaults


# ── Support/Resistance Signal Tests ──────────────────────────────


class TestSRSignal:
    def test_current_price_zero_no_crash(self):
        """When current_price == 0, _sr_signal should not crash (division by zero)."""
        ta = _base_ta_results(
            support_resistance={
                "current_price": 0,
                "support_levels": [490],
                "resistance_levels": [510],
                "pivot_point": 500,
                "pivot_r1": 510,
                "pivot_r2": 520,
                "pivot_s1": 490,
                "pivot_s2": 480,
            }
        )
        sg = SignalGenerator(ta)
        result = sg._sr_signal()
        # Should return a score of 0 with a "NO DATA" label, not raise
        assert result["score"] == 0
        assert "NO DATA" in result["label"]

    def test_no_support_no_resistance(self):
        """When both support and resistance lists are empty, should return NO DATA."""
        ta = _base_ta_results(
            support_resistance={
                "current_price": 500,
                "support_levels": [],
                "resistance_levels": [],
                "pivot_point": 500,
                "pivot_r1": 510,
                "pivot_r2": 520,
                "pivot_s1": 490,
                "pivot_s2": 480,
            }
        )
        sg = SignalGenerator(ta)
        result = sg._sr_signal()
        assert result["score"] == 0
        assert "NO DATA" in result["label"]


# ── Moving Average Signal Tests ──────────────────────────────────


class TestMASignal:
    def test_all_nan_values_handled(self):
        """When all MA values are NaN, _ma_signal should return NO DATA."""
        ta = _base_ta_results(
            moving_averages={
                "SMA_20": float("nan"),
                "SMA_50": float("nan"),
                "EMA_9": float("nan"),
            }
        )
        sg = SignalGenerator(ta)
        result = sg._ma_signal()
        assert result["score"] == 0
        assert "NO DATA" in result["label"]

    def test_partial_nan_values(self):
        """When some MAs are NaN, should only count the valid ones."""
        ta = _base_ta_results(
            moving_averages={
                "SMA_20": 490,
                "SMA_50": float("nan"),
                "EMA_9": 495,
            }
        )
        ta["price"]["close"] = 500  # above both valid MAs
        sg = SignalGenerator(ta)
        result = sg._ma_signal()
        # Price 500 > both valid MAs (490, 495), so 2/2 -> ratio=1.0 -> score=100
        assert result["score"] == 100
        assert "BULLISH" in result["label"]


# ── Normal Signal Generation Tests ───────────────────────────────


class TestGenerateAll:
    def test_normal_signal_generation(self):
        """generate_all() should complete and return expected structure."""
        ta = _base_ta_results()
        sg = SignalGenerator(ta)
        result = sg.generate_all()

        assert "composite_score" in result
        assert "action" in result
        assert "signals" in result
        assert "risk_level" in result
        assert "key_levels" in result

        assert result["action"] in ("STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL")
        assert -100 <= result["composite_score"] <= 100

    def test_strong_buy_signal(self):
        """With strongly bullish inputs, action should be BUY or STRONG BUY."""
        ta = _base_ta_results(
            rsi=28.0,  # oversold
            macd={"macd": 5.0, "signal": 2.0, "histogram": 3.0},
            bollinger={"position": 0.05, "upper": 520, "mid": 500, "lower": 480, "width": 0.08},
            trend={"overall_trend": "BULLISH", "trend_score": 0.8,
                   "ma_signals": ["BULLISH"] * 3, "cross_signal": "GOLDEN_CROSS",
                   "roc_5d": 3.0, "roc_20d": 8.0, "hh_hl_ratio": 0.7},
        )
        sg = SignalGenerator(ta)
        result = sg.generate_all()
        assert result["action"] in ("STRONG BUY", "BUY")
        assert result["composite_score"] > 0

    def test_strong_sell_signal(self):
        """With strongly bearish inputs, action should be SELL or STRONG SELL."""
        ta = _base_ta_results(
            rsi=78.0,  # overbought
            macd={"macd": -5.0, "signal": -2.0, "histogram": -3.0},
            bollinger={"position": 0.95, "upper": 520, "mid": 500, "lower": 480, "width": 0.08},
            trend={"overall_trend": "BEARISH", "trend_score": -0.8,
                   "ma_signals": ["BEARISH"] * 3, "cross_signal": "DEATH_CROSS",
                   "roc_5d": -3.0, "roc_20d": -8.0, "hh_hl_ratio": 0.3},
            moving_averages={
                "SMA_20": 510, "SMA_50": 515, "SMA_120": 520, "SMA_200": 525,
                "EMA_9": 508, "EMA_21": 512, "EMA_50": 518,
            },
        )
        sg = SignalGenerator(ta)
        result = sg.generate_all()
        assert result["action"] in ("STRONG SELL", "SELL")
        assert result["composite_score"] < 0

    def test_all_signal_categories_present(self):
        """All seven signal categories should appear in output."""
        ta = _base_ta_results()
        sg = SignalGenerator(ta)
        result = sg.generate_all()
        expected_keys = {"trend", "rsi", "macd", "bollinger", "volume",
                         "support_resistance", "moving_averages"}
        assert expected_keys == set(result["signals"].keys())

    def test_risk_assessment_fields(self):
        """Risk level should contain expected keys."""
        ta = _base_ta_results()
        sg = SignalGenerator(ta)
        result = sg.generate_all()
        risk = result["risk_level"]
        assert "volatility_risk" in risk
        assert "bollinger_squeeze" in risk
        assert "volume_conviction" in risk

    def test_key_action_levels_fields(self):
        """Key levels should contain buy/sell zones and stop loss."""
        ta = _base_ta_results()
        sg = SignalGenerator(ta)
        result = sg.generate_all()
        levels = result["key_levels"]
        for key in ["buy_zone", "strong_buy", "sell_zone", "strong_sell",
                     "stop_loss", "trailing_stop"]:
            assert key in levels, f"Missing key '{key}' in key_levels"


# ── RSI Signal Edge Cases ────────────────────────────────────────


class TestRSISignal:
    def test_nan_rsi_handled(self):
        """When RSI is NaN, _rsi_signal should return NO DATA."""
        ta = _base_ta_results(rsi=float("nan"))
        sg = SignalGenerator(ta)
        result = sg._rsi_signal()
        assert result["score"] == 0
        assert "NO DATA" in result["label"]


# ── MACD Signal Edge Cases ───────────────────────────────────────


class TestMACDSignal:
    def test_nan_macd_handled(self):
        """When MACD values are NaN, _macd_signal should return NO DATA."""
        ta = _base_ta_results(
            macd={"macd": float("nan"), "signal": float("nan"), "histogram": float("nan")}
        )
        sg = SignalGenerator(ta)
        result = sg._macd_signal()
        assert result["score"] == 0
        assert "NO DATA" in result["label"]
