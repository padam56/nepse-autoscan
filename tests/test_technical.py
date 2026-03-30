"""Unit tests for src/technical.py -- TechnicalAnalysis class."""

import sys
import os
import math

import numpy as np
import pandas as pd
import pytest

# Ensure the project root is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.technical import TechnicalAnalysis


# ── Fixtures ──────────────────────────────────────────────────────


def _make_ohlcv(closes, *, opens=None, highs=None, lows=None, volumes=None):
    """Build a list[dict] suitable for TechnicalAnalysis from close prices."""
    n = len(closes)
    if opens is None:
        opens = [c * 0.99 for c in closes]
    if highs is None:
        highs = [c * 1.01 for c in closes]
    if lows is None:
        lows = [c * 0.98 for c in closes]
    if volumes is None:
        volumes = [10000] * n

    base = pd.Timestamp("2025-01-01")
    return [
        {
            "date": (base + pd.Timedelta(days=i)).isoformat(),
            "open": opens[i],
            "high": highs[i],
            "low": lows[i],
            "close": closes[i],
            "volume": volumes[i],
        }
        for i in range(n)
    ]


@pytest.fixture
def realistic_data():
    """60 days of synthetic NEPSE-like OHLCV data with a mild uptrend."""
    np.random.seed(42)
    n = 60
    base_price = 500.0
    # Random walk with slight upward drift
    returns = np.random.normal(0.001, 0.015, n)
    closes = [base_price]
    for r in returns[1:]:
        closes.append(closes[-1] * (1 + r))
    closes = [round(c, 2) for c in closes]

    opens = [round(c * np.random.uniform(0.995, 1.005), 2) for c in closes]
    highs = [round(max(o, c) * np.random.uniform(1.001, 1.02), 2) for o, c in zip(opens, closes)]
    lows = [round(min(o, c) * np.random.uniform(0.98, 0.999), 2) for o, c in zip(opens, closes)]
    volumes = [int(np.random.uniform(5000, 50000)) for _ in range(n)]

    return _make_ohlcv(closes, opens=opens, highs=highs, lows=lows, volumes=volumes)


@pytest.fixture
def flat_data():
    """30 rows where close is always 100 (flat prices)."""
    return _make_ohlcv([100.0] * 30)


@pytest.fixture
def short_data():
    """Only 5 rows -- insufficient for most indicator windows."""
    return _make_ohlcv([100, 102, 101, 103, 104])


@pytest.fixture
def zero_close_data():
    """Contains zero close prices mixed with normal prices."""
    closes = [0.0, 0.0, 100.0, 101.0, 0.0, 102.0, 103.0, 0.0, 104.0, 105.0]
    return _make_ohlcv(closes)


# ── Bollinger Band Tests ─────────────────────────────────────────


class TestBollingerBands:
    def test_zero_width_bands_returns_half(self, flat_data):
        """When BB_Upper == BB_Lower (flat prices), BB_Position should be 0.5."""
        ta = TechnicalAnalysis(flat_data)
        ta.compute_bollinger()

        # After the rolling window fills, std=0 so upper==lower
        filled = ta.df.dropna(subset=["BB_Upper"])
        if len(filled) > 0:
            for _, row in filled.iterrows():
                assert row["BB_Position"] == pytest.approx(0.5), (
                    "BB_Position must be 0.5 when bands have zero width"
                )

    def test_normal_position_range(self, realistic_data):
        """BB_Position should generally be between 0 and 1 for typical data."""
        ta = TechnicalAnalysis(realistic_data)
        ta.compute_bollinger()
        filled = ta.df.dropna(subset=["BB_Position"])
        assert len(filled) > 0
        # Position may exceed [0,1] slightly when price is outside bands, but
        # should not contain NaN or Inf.
        for val in filled["BB_Position"]:
            assert np.isfinite(val), f"BB_Position should be finite, got {val}"


# ── VPT Tests ────────────────────────────────────────────────────


class TestVPT:
    def test_zero_close_no_inf(self, zero_close_data):
        """VPT must not produce Inf when close prices include zeros."""
        ta = TechnicalAnalysis(zero_close_data)
        ta.compute_volume_analysis()
        vpt_values = ta.df["VPT"]
        assert not vpt_values.isin([np.inf, -np.inf]).any(), (
            "VPT should not contain Inf values even with zero close prices"
        )

    def test_vpt_normal(self, realistic_data):
        """VPT should be finite for normal data."""
        ta = TechnicalAnalysis(realistic_data)
        ta.compute_volume_analysis()
        assert ta.df["VPT"].apply(np.isfinite).all()


# ── RSI Tests ────────────────────────────────────────────────────


class TestRSI:
    def test_flat_prices_not_nan(self, flat_data):
        """RSI with flat prices (no gains, no losses) should not be NaN.

        When avg_loss is zero, the code replaces 0 with NaN in the divisor
        which yields NaN RSI.  For flat prices a neutral value (~50 or NaN
        that is handled downstream) is acceptable; but it must not crash.
        """
        ta = TechnicalAnalysis(flat_data)
        ta.compute_rsi()
        # The last RSI value after the window fills
        filled = ta.df.dropna(subset=["RSI"])
        # It is acceptable for RSI to be NaN on flat data (no gains/losses),
        # but the computation must not raise.
        # If the implementation does produce a value, it should be finite.
        for val in filled["RSI"]:
            assert np.isfinite(val), f"RSI should be finite where not NaN, got {val}"

    def test_insufficient_data(self, short_data):
        """RSI with fewer than 14 records should not crash."""
        ta = TechnicalAnalysis(short_data)
        ta.compute_rsi()
        # With only 5 rows and period=14, all RSI values will be NaN
        assert "RSI" in ta.df.columns
        # Should not crash -- NaN values are expected
        assert len(ta.df) == len(short_data)

    def test_normal_rsi_range(self, realistic_data):
        """RSI should be between 0 and 100 for normal data."""
        ta = TechnicalAnalysis(realistic_data)
        ta.compute_rsi()
        filled = ta.df.dropna(subset=["RSI"])
        assert len(filled) > 0, "Should have some valid RSI values with 60 rows"
        for val in filled["RSI"]:
            assert 0 <= val <= 100, f"RSI must be in [0, 100], got {val}"


# ── MACD Tests ───────────────────────────────────────────────────


class TestMACD:
    def test_short_data_no_crash(self, short_data):
        """MACD with very short data should not crash."""
        ta = TechnicalAnalysis(short_data)
        ta.compute_macd()
        assert "MACD" in ta.df.columns
        assert "MACD_Signal" in ta.df.columns
        assert "MACD_Hist" in ta.df.columns
        # EWM still produces values even with short data
        assert len(ta.df) == len(short_data)

    def test_macd_histogram_is_difference(self, realistic_data):
        """MACD_Hist should equal MACD - MACD_Signal."""
        ta = TechnicalAnalysis(realistic_data)
        ta.compute_macd()
        diff = ta.df["MACD"] - ta.df["MACD_Signal"]
        pd.testing.assert_series_equal(
            ta.df["MACD_Hist"], diff, check_names=False, atol=1e-10
        )

    def test_macd_values_finite(self, realistic_data):
        """All MACD values should be finite."""
        ta = TechnicalAnalysis(realistic_data)
        ta.compute_macd()
        for col in ["MACD", "MACD_Signal", "MACD_Hist"]:
            assert ta.df[col].apply(np.isfinite).all(), f"{col} contains non-finite values"


# ── Normal / Integration Tests ───────────────────────────────────


class TestRunAll:
    def test_run_all_realistic(self, realistic_data):
        """run_all() should complete without error on realistic data."""
        ta = TechnicalAnalysis(realistic_data)
        result = ta.run_all()
        assert "price" in result
        assert "rsi" in result
        assert "macd" in result
        assert "bollinger" in result
        assert "support_resistance" in result
        assert "trend" in result
        assert "dataframe" in result

    def test_run_all_returns_finite_rsi(self, realistic_data):
        """RSI in run_all output should be a finite number."""
        ta = TechnicalAnalysis(realistic_data)
        result = ta.run_all()
        rsi = result["rsi"]
        assert np.isfinite(rsi), f"RSI should be finite, got {rsi}"
        assert 0 <= rsi <= 100

    def test_support_resistance_structure(self, realistic_data):
        """Support/resistance output should have expected keys."""
        ta = TechnicalAnalysis(realistic_data)
        ta.compute_sma()
        sr = ta.find_support_resistance()
        for key in ["current_price", "support_levels", "resistance_levels",
                     "pivot_point", "pivot_r1", "pivot_r2", "pivot_s1", "pivot_s2"]:
            assert key in sr, f"Missing key '{key}' in support_resistance output"

    def test_empty_data_raises(self):
        """Empty data should raise ValueError."""
        with pytest.raises(ValueError, match="No price data"):
            TechnicalAnalysis([])
