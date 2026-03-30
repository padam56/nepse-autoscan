"""
Unit tests for utility and data modules.

Covers:
  - scripts.parallel_train.chunk_list
  - scrapers.advanced_screener.compute_momentum_score (volume_ratio clamping,
    division-by-zero safety, normal computation)

All tests are self-contained: no I/O, no network.
"""
import sys
import os
import pytest
from unittest.mock import patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.parallel_train import chunk_list


# ---------------------------------------------------------------------------
# chunk_list
# ---------------------------------------------------------------------------

class TestChunkList:
    """Tests for parallel_train.chunk_list."""

    def test_even_split(self):
        """10 items into 5 chunks -- each chunk should have exactly 2 items."""
        items = list(range(10))
        chunks = chunk_list(items, 5)
        assert len(chunks) == 5
        assert all(len(c) == 2 for c in chunks)
        # All original items must be present, in order.
        assert [x for c in chunks for x in c] == items

    def test_uneven_split(self):
        """7 items into 3 chunks -- sizes should be [3, 2, 2]."""
        items = list(range(7))
        chunks = chunk_list(items, 3)
        assert len(chunks) == 3
        sizes = [len(c) for c in chunks]
        assert sizes == [3, 2, 2]
        assert [x for c in chunks for x in c] == items

    def test_more_chunks_than_items(self):
        """2 items into 5 chunks -- 2 non-empty + 3 empty chunks."""
        items = [1, 2]
        chunks = chunk_list(items, 5)
        assert len(chunks) == 5
        non_empty = [c for c in chunks if len(c) > 0]
        assert len(non_empty) == 2
        assert [x for c in chunks for x in c] == items

    def test_empty_list(self):
        """Empty list into N chunks -- all chunks should be empty."""
        chunks = chunk_list([], 4)
        assert len(chunks) == 4
        assert all(len(c) == 0 for c in chunks)

    def test_single_chunk(self):
        """All items into 1 chunk -- output should wrap the full list."""
        items = list(range(5))
        chunks = chunk_list(items, 1)
        assert len(chunks) == 1
        assert chunks[0] == items


# ---------------------------------------------------------------------------
# compute_momentum_score
# ---------------------------------------------------------------------------

# We need to patch the PORTFOLIO import inside advanced_screener so that
# tests do not depend on the real portfolio config, and also patch
# SECTOR_MAP so no external state leaks in.

_FAKE_PORTFOLIO = {"ALICL": {"shares": 100, "wacc": 500, "total_cost": 50000}}
_FAKE_SECTOR_MAP = {"NABIL": "Banking", "ALICL": "Life Insurance", "BARUN": "Hydropower"}


def _import_compute():
    """Import compute_momentum_score with external dependencies patched."""
    with patch.dict("sys.modules", {}):
        pass  # force fresh resolution if needed
    # Patch at the module level where the names are looked up.
    with patch("scrapers.advanced_screener.PORTFOLIO", _FAKE_PORTFOLIO), \
         patch("scrapers.advanced_screener.SECTOR_MAP", _FAKE_SECTOR_MAP):
        from scrapers.advanced_screener import compute_momentum_score
        return compute_momentum_score


class TestComputeMomentumScore:
    """Tests for advanced_screener.compute_momentum_score."""

    # ------------------------------------------------------------------
    # volume_ratio clamping
    # ------------------------------------------------------------------
    def test_volume_ratio_clamped_to_100(self):
        """When today's turnover vastly exceeds historical average the
        volume_ratio field must be clamped to 100."""
        compute = _import_compute()
        stock = {
            "s": "NABIL", "lp": 1100, "pc": 1.5,
            "h": 1120, "l": 1080, "op": 1090,
            "t": 500_000_000, "q": 400000,  # extreme turnover
        }
        # Provide history with tiny average turnover to force a huge ratio.
        history = {
            "NABIL": [
                {"date": "2026-03-19", "lp": 1090, "t": 100},
                {"date": "2026-03-18", "lp": 1085, "t": 100},
                {"date": "2026-03-17", "lp": 1080, "t": 100},
            ]
        }
        with patch("scrapers.advanced_screener.PORTFOLIO", _FAKE_PORTFOLIO), \
             patch("scrapers.advanced_screener.SECTOR_MAP", _FAKE_SECTOR_MAP):
            result = compute(stock, history)

        assert result is not None
        assert result["volume_ratio"] <= 100.0

    # ------------------------------------------------------------------
    # Division-by-zero safety when all turnovers are zero
    # ------------------------------------------------------------------
    def test_zero_turnover_no_crash(self):
        """compute_momentum_score must not crash when today's turnover
        and all historical turnovers are zero.  The stock will likely be
        filtered out (t < 200_000 rule), so we just verify no exception."""
        compute = _import_compute()
        stock = {
            "s": "NABIL", "lp": 1100, "pc": 1.0,
            "h": 1110, "l": 1090, "op": 1095,
            "t": 0, "q": 0,
        }
        history = {
            "NABIL": [
                {"date": "2026-03-19", "lp": 1090, "t": 0},
                {"date": "2026-03-18", "lp": 1085, "t": 0},
                {"date": "2026-03-17", "lp": 1080, "t": 0},
            ]
        }
        # Should not raise -- it may return None (filtered as illiquid).
        with patch("scrapers.advanced_screener.PORTFOLIO", _FAKE_PORTFOLIO), \
             patch("scrapers.advanced_screener.SECTOR_MAP", _FAKE_SECTOR_MAP):
            result = compute(stock, history)

        # The stock has t=0 which is < 200_000, so it should be filtered.
        assert result is None

    # ------------------------------------------------------------------
    # Normal momentum score computation
    # ------------------------------------------------------------------
    def test_normal_momentum_score(self):
        """A healthy stock with moderate gain, good turnover, and closing
        near day high should produce a positive composite score with the
        expected structure."""
        compute = _import_compute()
        stock = {
            "s": "NABIL", "lp": 1115, "pc": 2.0,
            "h": 1120, "l": 1080, "op": 1090,
            "t": 15_000_000, "q": 13600,
        }
        history = {
            "NABIL": [
                {"date": "2026-03-19", "lp": 1100, "t": 10_000_000},
                {"date": "2026-03-18", "lp": 1090, "t": 9_000_000},
                {"date": "2026-03-17", "lp": 1080, "t": 11_000_000},
            ]
        }
        with patch("scrapers.advanced_screener.PORTFOLIO", _FAKE_PORTFOLIO), \
             patch("scrapers.advanced_screener.SECTOR_MAP", _FAKE_SECTOR_MAP):
            result = compute(stock, history)

        assert result is not None
        # Structure checks
        assert result["symbol"] == "NABIL"
        assert "composite_score" in result
        assert "opportunity_score" in result
        assert "volume_ratio" in result
        assert "price_position" in result
        assert "reasons" in result
        assert isinstance(result["reasons"], list)
        assert "targets" in result
        for key in ("entry", "tp1", "tp2", "sl", "risk_reward"):
            assert key in result["targets"]

        # The stock is not in portfolio, closing near high, healthy gain --
        # composite score should be solidly positive.
        assert result["composite_score"] > 0

        # Price position: lp=1115 in range [1080, 1120] -> (1115-1080)/40 = 0.875
        assert result["price_position"] == pytest.approx(0.875, abs=0.01)

        # volume_ratio should be reasonable, not clamped
        assert 0 < result["volume_ratio"] < 100

    def test_portfolio_stock_penalised(self):
        """A stock present in PORTFOLIO should have its score reduced by
        the -30 portfolio overlap penalty."""
        compute = _import_compute()
        stock = {
            "s": "ALICL", "lp": 550, "pc": 2.0,
            "h": 560, "l": 540, "op": 545,
            "t": 3_200_000, "q": 5800,
        }
        with patch("scrapers.advanced_screener.PORTFOLIO", _FAKE_PORTFOLIO), \
             patch("scrapers.advanced_screener.SECTOR_MAP", _FAKE_SECTOR_MAP):
            result = compute(stock, history=None)

        assert result is not None
        assert result["in_portfolio"] is True
        # Run the same stock as if it were NOT in portfolio and compare.
        stock2 = dict(stock, s="BARUN")
        stock2["lp"] = 550
        stock2["h"] = 560
        stock2["l"] = 540
        stock2["op"] = 545
        stock2["t"] = 3_200_000
        stock2["pc"] = 2.0
        with patch("scrapers.advanced_screener.PORTFOLIO", _FAKE_PORTFOLIO), \
             patch("scrapers.advanced_screener.SECTOR_MAP", _FAKE_SECTOR_MAP):
            result2 = compute(stock2, history=None)

        assert result2 is not None
        assert result2["in_portfolio"] is False
        # The portfolio stock should score exactly 30 points less.
        assert result["composite_score"] == pytest.approx(
            result2["composite_score"] - 30, abs=0.1
        )

    def test_penny_stock_filtered(self):
        """Stocks with lp < 50 should be filtered out (return None)."""
        compute = _import_compute()
        stock = {
            "s": "CHEAP", "lp": 30, "pc": 2.0,
            "h": 32, "l": 28, "op": 29,
            "t": 1_000_000, "q": 30000,
        }
        with patch("scrapers.advanced_screener.PORTFOLIO", _FAKE_PORTFOLIO), \
             patch("scrapers.advanced_screener.SECTOR_MAP", _FAKE_SECTOR_MAP):
            result = compute(stock, history=None)

        assert result is None

    def test_upper_circuit_filtered(self):
        """Stocks at upper circuit (pc >= 9.5) should be filtered out."""
        compute = _import_compute()
        stock = {
            "s": "PUMPED", "lp": 500, "pc": 9.8,
            "h": 500, "l": 460, "op": 460,
            "t": 8_000_000, "q": 16000,
        }
        with patch("scrapers.advanced_screener.PORTFOLIO", _FAKE_PORTFOLIO), \
             patch("scrapers.advanced_screener.SECTOR_MAP", _FAKE_SECTOR_MAP):
            result = compute(stock, history=None)

        assert result is None
