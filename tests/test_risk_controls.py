"""Unit tests for src/risk_controls.py -- apply_sector_cap and apply_drawdown_brake."""

import sys
import os

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.risk_controls import apply_sector_cap, apply_drawdown_brake


# ── Helpers ───────────────────────────────────────────────────────


def _make_picks(symbols: list[str]) -> list[dict]:
    """Create pick dicts with sequential scores (higher = better rank)."""
    return [
        {"symbol": s, "score": 100 - i}
        for i, s in enumerate(symbols)
    ]


# ── apply_sector_cap tests ───────────────────────────────────────


class TestApplySectorCap:

    def test_excess_banking_picks_capped(self):
        """10 picks, 6 from banking -> only 3 banking picks kept."""
        symbols = [f"BANK{i}" for i in range(6)] + [f"INS{i}" for i in range(3)] + ["HYDRO0"]
        picks = _make_picks(symbols)
        sectors = {f"BANK{i}": "Banking" for i in range(6)}
        sectors.update({f"INS{i}": "Insurance" for i in range(3)})
        sectors["HYDRO0"] = "Hydropower"

        result = apply_sector_cap(picks, sectors, max_per_sector=3)

        banking_in_result = [p for p in result if sectors[p["symbol"]] == "Banking"]
        assert len(banking_in_result) == 3
        # 3 banking + 3 insurance + 1 hydro = 7 total
        assert len(result) == 7

    def test_all_different_sectors_unchanged(self):
        """10 picks each from a unique sector -> all 10 kept."""
        symbols = [f"SYM{i}" for i in range(10)]
        picks = _make_picks(symbols)
        sectors = {f"SYM{i}": f"Sector{i}" for i in range(10)}

        result = apply_sector_cap(picks, sectors, max_per_sector=3)

        assert len(result) == 10

    def test_max_per_sector_one(self):
        """max_per_sector=1 keeps at most one per sector."""
        symbols = ["A", "B", "C", "D", "E"]
        picks = _make_picks(symbols)
        sectors = {"A": "X", "B": "X", "C": "Y", "D": "Y", "E": "Z"}

        result = apply_sector_cap(picks, sectors, max_per_sector=1)

        sector_list = [sectors[p["symbol"]] for p in result]
        assert len(sector_list) == len(set(sector_list))
        assert len(result) == 3  # one from X, one from Y, one from Z

    def test_preserves_ranking_order(self):
        """Filtered list maintains original ranking (best first)."""
        symbols = ["TOP", "MID1", "MID2", "LOW"]
        picks = _make_picks(symbols)
        sectors = {"TOP": "A", "MID1": "A", "MID2": "A", "LOW": "B"}

        result = apply_sector_cap(picks, sectors, max_per_sector=2)

        result_symbols = [p["symbol"] for p in result]
        # TOP and MID1 kept from sector A (first two), LOW from sector B
        assert result_symbols == ["TOP", "MID1", "LOW"]
        # Scores descending
        scores = [p["score"] for p in result]
        assert scores == sorted(scores, reverse=True)


# ── apply_drawdown_brake tests ───────────────────────────────────


class TestApplyDrawdownBrake:

    def test_hit_rate_above_50_no_change(self):
        """hit_rate >= 50% -> no reduction."""
        stats = {"evaluated": 20, "hit_rate": 60.0, "avg_return_pct": 1.0}
        assert apply_drawdown_brake(10.0, stats) == 10.0

    def test_hit_rate_40_to_50_reduces_30_pct(self):
        """hit_rate 40-50% -> 30% reduction (scale=0.70)."""
        stats = {"evaluated": 20, "hit_rate": 45.0, "avg_return_pct": 0.0}
        result = apply_drawdown_brake(10.0, stats)
        assert result == pytest.approx(7.0)

    def test_hit_rate_below_30_reduces_75_pct(self):
        """hit_rate < 30% -> 75% reduction (scale=0.25)."""
        stats = {"evaluated": 20, "hit_rate": 20.0, "avg_return_pct": 0.0}
        result = apply_drawdown_brake(10.0, stats)
        assert result == pytest.approx(2.5)

    def test_few_evaluated_signals_no_change(self):
        """< 10 evaluated signals -> no adjustment regardless of hit_rate."""
        stats = {"evaluated": 5, "hit_rate": 10.0, "avg_return_pct": -5.0}
        assert apply_drawdown_brake(10.0, stats) == 10.0

    def test_negative_avg_return_additional_cut(self):
        """avg_return < -2% -> additional 25% cut on top of hit-rate scaling."""
        stats = {"evaluated": 20, "hit_rate": 45.0, "avg_return_pct": -3.0}
        # scale=0.70, then *0.75 for negative returns
        expected = 10.0 * 0.70 * 0.75  # 5.25
        result = apply_drawdown_brake(10.0, stats)
        assert result == pytest.approx(expected)

    def test_result_never_below_one(self):
        """Even with worst stats, result is clamped to 1.0%."""
        stats = {"evaluated": 100, "hit_rate": 5.0, "avg_return_pct": -10.0}
        # scale=0.25, then *0.75 -> 0.1875 * kelly
        result = apply_drawdown_brake(2.0, stats)
        # 2.0 * 0.25 * 0.75 = 0.375 -> clamped to 1.0
        assert result == 1.0

    def test_hit_rate_30_to_40_reduces_50_pct(self):
        """hit_rate 30-40% -> 50% reduction (scale=0.50)."""
        stats = {"evaluated": 20, "hit_rate": 35.0, "avg_return_pct": 0.0}
        result = apply_drawdown_brake(10.0, stats)
        assert result == pytest.approx(5.0)
