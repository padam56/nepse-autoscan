"""Unit tests for src/data_quality.py -- DataQualityGate class."""

import sys
import os
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data_quality import DataQualityGate


# ── Helpers ───────────────────────────────────────────────────────


def _today_str() -> str:
    return datetime.now().date().isoformat()


def _make_histories(n_stocks: int, close: float = 500.0, volume: int = 10000) -> dict:
    """Create synthetic histories dict with n_stocks, each having 2 records."""
    today = _today_str()
    yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
    histories = {}
    for i in range(n_stocks):
        sym = f"SYM{i:04d}"
        histories[sym] = [
            {"date": today, "close": close, "volume": volume},
            {"date": yesterday, "close": close * 0.99, "volume": volume},
        ]
    return histories


# ── Tests ─────────────────────────────────────────────────────────


class TestDataQualityGate:

    def test_normal_data_passes(self):
        """310 stocks with valid data passes all checks."""
        gate = DataQualityGate()
        histories = _make_histories(310)

        passed, warnings = gate.check_all(histories)

        assert passed is True

    def test_very_few_stocks_fails(self):
        """Fewer than 100 stocks triggers a hard fail."""
        gate = DataQualityGate()
        histories = _make_histories(50)

        passed, warnings = gate.check_all(histories)

        assert passed is False
        matching = [w for w in warnings if "critically low" in w.lower()]
        assert len(matching) >= 1

    def test_zero_prices_detected(self):
        """Stocks with close=0 are flagged in warnings."""
        gate = DataQualityGate()
        # 200 normal + 5 zero-price stocks (above hard count, below hard fail of 50)
        histories = _make_histories(200)
        for i in range(5):
            sym = f"ZERO{i}"
            histories[sym] = [
                {"date": _today_str(), "close": 0, "volume": 100},
            ]

        passed, warnings = gate.check_all(histories)

        zero_warnings = [w for w in warnings if "zero" in w.lower() and "price" in w.lower()]
        assert len(zero_warnings) >= 1

    def test_stale_data_warned(self):
        """Data from 10+ days ago triggers a stale data warning."""
        gate = DataQualityGate()
        old_date = (datetime.now().date() - timedelta(days=15)).isoformat()
        older_date = (datetime.now().date() - timedelta(days=16)).isoformat()
        histories = {}
        for i in range(200):
            sym = f"SYM{i:04d}"
            histories[sym] = [
                {"date": old_date, "close": 500.0, "volume": 10000},
                {"date": older_date, "close": 495.0, "volume": 10000},
            ]

        passed, warnings = gate.check_all(histories)

        stale_warnings = [w for w in warnings if "stale" in w.lower()]
        assert len(stale_warnings) >= 1

    def test_zero_volume_anomaly_detected(self):
        """More than 50% zero-volume stocks triggers a volume anomaly warning."""
        gate = DataQualityGate()
        # All stocks with zero volume
        histories = _make_histories(200, volume=0)

        passed, warnings = gate.check_all(histories)

        vol_warnings = [w for w in warnings if "zero volume" in w.lower()]
        assert len(vol_warnings) >= 1
