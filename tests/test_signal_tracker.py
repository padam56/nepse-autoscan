"""Unit tests for src/signal_tracker.py -- SignalTracker class."""

import sys
import os
import json

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.signal_tracker import SignalTracker


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    """Create a SignalTracker whose TRACKER_FILE points to tmp_path."""
    tracker_file = tmp_path / "signal_log.json"
    monkeypatch.setattr(SignalTracker, "TRACKER_FILE", tracker_file)
    return SignalTracker()


# ── log_signals tests ────────────────────────────────────────────


class TestLogSignals:

    def test_creates_file_and_logs_entries(self, tracker):
        """log_signals creates the file and writes entries."""
        picks = [
            {"symbol": "NABIL", "signal": "BUY", "score": 85},
            {"symbol": "NICA", "signal": "SELL", "score": 70},
        ]
        added = tracker.log_signals("2026-03-20", picks)

        assert added == 2
        assert tracker.TRACKER_FILE.exists()

        data = json.loads(tracker.TRACKER_FILE.read_text())
        assert len(data) == 2
        assert data[0]["symbol"] == "NABIL"
        assert data[1]["symbol"] == "NICA"

    def test_no_duplicate_same_date_symbol(self, tracker):
        """Calling log_signals twice with the same date+symbol skips duplicates."""
        picks = [{"symbol": "NABIL", "signal": "BUY", "score": 85}]
        added1 = tracker.log_signals("2026-03-20", picks)
        added2 = tracker.log_signals("2026-03-20", picks)

        assert added1 == 1
        assert added2 == 0

        data = json.loads(tracker.TRACKER_FILE.read_text())
        assert len(data) == 1


# ── get_stats tests ──────────────────────────────────────────────


class TestGetStats:

    def test_no_data_returns_zeros(self, tracker):
        """get_stats on empty log returns zero counts."""
        stats = tracker.get_stats()

        assert stats["total_signals"] == 0
        assert stats["evaluated"] == 0
        assert stats["hit_rate"] == 0.0
        assert stats["avg_return_pct"] == 0.0

    def test_evaluated_signals_correct_hit_rate(self, tracker):
        """get_stats computes correct hit_rate from evaluated records."""
        # Manually write records with known evaluated outcomes
        records = [
            {
                "date": "2026-03-20",
                "symbol": "NABIL",
                "signal": "BUY",
                "score": 85,
                "ta": None,
                "ml": None,
                "gru": None,
                "kelly_pct": None,
                "price_at_signal": 1000.0,
                "price_after_5d": 1050.0,
                "return_5d_pct": 5.0,
                "hit": True,
                "evaluated_at": "2026-03-27",
            },
            {
                "date": "2026-03-20",
                "symbol": "NICA",
                "signal": "BUY",
                "score": 70,
                "ta": None,
                "ml": None,
                "gru": None,
                "kelly_pct": None,
                "price_at_signal": 500.0,
                "price_after_5d": 480.0,
                "return_5d_pct": -4.0,
                "hit": False,
                "evaluated_at": "2026-03-27",
            },
        ]
        tracker.TRACKER_FILE.write_text(json.dumps(records))

        stats = tracker.get_stats(lookback_days=30)

        assert stats["evaluated"] == 2
        assert stats["hit_rate"] == 50.0
        assert stats["avg_return_pct"] == 0.5  # (5.0 + -4.0) / 2


# ── summary_text tests ──────────────────────────────────────────


class TestSummaryText:

    def test_returns_non_empty_string(self, tracker):
        """summary_text returns a non-empty string even with no data."""
        text = tracker.summary_text()

        assert isinstance(text, str)
        assert len(text) > 0
        assert "Signal Performance Tracker" in text
