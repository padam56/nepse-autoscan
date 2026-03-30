"""Shared pytest fixtures for NEPSE test suite."""
import sys
import os
import pytest

# Ensure project root is on sys.path so imports work without installation.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture
def synthetic_ohlcv_day():
    """
    Return a single-day OHLCV snapshot dict matching the JSON format
    produced by advanced_screener.save_daily_snapshot and consumed by
    parallel_train.get_remaining_symbols.

    Structure mirrors data/price_history/<date>.json files:
        {
            "date": "2026-03-20",
            "stocks": {
                "ALICL": {"lp": 550, "pc": 1.2, "h": 560, "l": 540,
                           "op": 545, "t": 3200000, "q": 5800},
                ...
            }
        }
    """
    return {
        "date": "2026-03-20",
        "stocks": {
            "ALICL": {
                "lp": 550.0, "pc": 1.20, "h": 560.0, "l": 540.0,
                "op": 545.0, "t": 3_200_000, "q": 5800,
            },
            "NABIL": {
                "lp": 1100.0, "pc": 0.85, "h": 1120.0, "l": 1090.0,
                "op": 1095.0, "t": 12_500_000, "q": 11400,
            },
            "NLIC": {
                "lp": 740.0, "pc": -0.40, "h": 750.0, "l": 735.0,
                "op": 745.0, "t": 1_800_000, "q": 2430,
            },
            "BARUN": {
                "lp": 395.0, "pc": 2.50, "h": 400.0, "l": 385.0,
                "op": 388.0, "t": 950_000, "q": 2400,
            },
            "UPPER": {
                "lp": 200.0, "pc": 0.30, "h": 205.0, "l": 198.0,
                "op": 199.0, "t": 400_000, "q": 2000,
            },
        },
    }


@pytest.fixture
def stock_records():
    """
    Return a list of raw stock dicts (MeroLagani-style) suitable for
    compute_momentum_score and related feature-computation functions.

    Each dict uses the short keys the screener expects:
        s  = symbol, lp = last price, pc = percent change,
        h  = high, l = low, op = open, t = turnover (NPR), q = quantity.
    """
    return [
        {
            "s": "NABIL",
            "lp": 1100, "pc": 1.5, "h": 1120, "l": 1080,
            "op": 1090, "t": 15_000_000, "q": 13600,
        },
        {
            "s": "ALICL",
            "lp": 550, "pc": 2.0, "h": 560, "l": 540,
            "op": 545, "t": 3_200_000, "q": 5800,
        },
        {
            "s": "BARUN",
            "lp": 395, "pc": 0.8, "h": 400, "l": 385,
            "op": 390, "t": 950_000, "q": 2400,
        },
        {
            # Penny stock -- should be filtered out by screener
            "s": "PENNY",
            "lp": 30, "pc": 3.0, "h": 32, "l": 28,
            "op": 29, "t": 50_000, "q": 1700,
        },
        {
            # Upper-circuit stock -- should be filtered out
            "s": "PUMP",
            "lp": 500, "pc": 9.8, "h": 500, "l": 460,
            "op": 460, "t": 8_000_000, "q": 16000,
        },
    ]
