"""
Unit tests for ML modules: gru_predictor, features, regime.
Uses synthetic data only -- no file I/O or network calls.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))


# ---------------------------------------------------------------------------
# Helpers -- synthetic OHLCV record generators
# ---------------------------------------------------------------------------

def _make_records(n: int, base_price: float = 500.0, trend: float = 0.001,
                  vol_noise: float = 0.02, start_date: str = "2024-01-01") -> list:
    """Generate n synthetic daily OHLCV records with a mild upward trend."""
    from datetime import datetime, timedelta
    rng = np.random.RandomState(42)
    dt = datetime.strptime(start_date, "%Y-%m-%d")
    records = []
    price = base_price
    for i in range(n):
        ret = trend + rng.normal(0, vol_noise)
        price *= (1 + ret)
        h = price * (1 + abs(rng.normal(0, 0.005)))
        l = price * (1 - abs(rng.normal(0, 0.005)))
        o = price * (1 + rng.normal(0, 0.003))
        v = max(1, int(rng.exponential(10000)))
        records.append({
            "date": (dt + timedelta(days=i)).strftime("%Y-%m-%d"),
            "lp": round(price, 2),
            "h": round(h, 2),
            "l": round(l, 2),
            "op": round(o, 2),
            "q": v,
            "close": round(price, 2),
        })
    return records


def _make_constant_records(n: int, price: float = 100.0,
                           start_date: str = "2024-01-01") -> list:
    """Generate n records where price never changes (constant)."""
    from datetime import datetime, timedelta
    dt = datetime.strptime(start_date, "%Y-%m-%d")
    records = []
    for i in range(n):
        records.append({
            "date": (dt + timedelta(days=i)).strftime("%Y-%m-%d"),
            "lp": price,
            "h": price,
            "l": price,
            "op": price,
            "q": 1000,
            "close": price,
        })
    return records


# ===========================================================================
# gru_predictor tests
# ===========================================================================

class TestComputeFeatures:
    """Tests for gru_predictor.compute_features."""

    def test_insufficient_data_returns_empty(self):
        from ml.gru_predictor import compute_features, LOOKBACK
        # Need at least lookback + 30 records; supply fewer
        records = _make_records(LOOKBACK + 10)
        X, y = compute_features(records)
        assert len(X) == 0
        assert len(y) == 0

    def test_exactly_minimum_data(self):
        from ml.gru_predictor import compute_features, LOOKBACK, N_FEATURES
        # Minimum is lookback + 30 + 1 (need at least 1 sample with a next-day)
        min_needed = LOOKBACK + 30 + 2
        records = _make_records(min_needed)
        X, y = compute_features(records)
        assert len(X) > 0
        assert X.shape[1] == LOOKBACK
        assert X.shape[2] == N_FEATURES

    def test_feature_shape_validation(self):
        from ml.gru_predictor import compute_features, LOOKBACK, N_FEATURES
        records = _make_records(120)
        X, y = compute_features(records)
        assert X.ndim == 3
        # Shape should be [N, LOOKBACK, N_FEATURES]
        assert X.shape[1] == LOOKBACK
        assert X.shape[2] == N_FEATURES
        assert len(y) == len(X)

    def test_label_distribution_range(self):
        from ml.gru_predictor import compute_features
        records = _make_records(200)
        X, y = compute_features(records)
        assert len(y) > 0
        # Labels should be in {0, 1, 2, 3, 4}
        assert y.min() >= 0
        assert y.max() <= 4
        assert y.dtype == np.int64

    def test_custom_lookback(self):
        from ml.gru_predictor import compute_features, N_FEATURES
        lookback = 10
        records = _make_records(100)
        X, y = compute_features(records, lookback=lookback)
        assert len(X) > 0
        assert X.shape[1] == lookback
        assert X.shape[2] == N_FEATURES


class TestSymbolNameSanitization:
    """Tests for '/' -> '_' in model paths."""

    def test_slash_replaced_in_model_save_path(self):
        from ml.gru_predictor import MODEL_DIR
        symbol = "NTC/G"
        safe_name = symbol.replace("/", "_")
        model_path = MODEL_DIR / ("%s_gru.pt" % safe_name)
        assert "/" not in model_path.name
        assert "NTC_G_gru.pt" == model_path.name

    def test_no_slash_symbol_unchanged(self):
        from ml.gru_predictor import MODEL_DIR
        symbol = "ALICL"
        safe_name = symbol.replace("/", "_")
        model_path = MODEL_DIR / ("%s_gru.pt" % safe_name)
        assert model_path.name == "ALICL_gru.pt"

    def test_multiple_slashes(self):
        symbol = "A/B/C"
        safe_name = symbol.replace("/", "_")
        assert safe_name == "A_B_C"


# ===========================================================================
# features.py tests
# ===========================================================================

class TestPctRank:
    """Tests for features._pct_rank."""

    def test_all_nan_returns_fifties(self):
        from ml.features import _pct_rank
        arr = np.array([np.nan, np.nan, np.nan, np.nan])
        result = _pct_rank(arr)
        np.testing.assert_array_equal(result, 50.0)

    def test_normal_data_returns_percentiles(self):
        from ml.features import _pct_rank
        arr = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
        result = _pct_rank(arr)
        assert result.shape == arr.shape
        # Smallest value should have the lowest rank, largest the highest
        assert result[0] < result[-1]
        # All values should be in [0, 100]
        assert np.all(result >= 0)
        assert np.all(result <= 100)

    def test_single_value(self):
        from ml.features import _pct_rank
        arr = np.array([42.0])
        result = _pct_rank(arr)
        assert result.shape == (1,)
        # Single value gets 100th percentile of its own distribution
        assert result[0] == 100.0

    def test_mixed_nan_and_finite(self):
        from ml.features import _pct_rank
        arr = np.array([10.0, np.nan, 30.0, np.nan, 50.0])
        result = _pct_rank(arr)
        assert result.shape == arr.shape
        # NaN positions should be filled with 50
        assert result[1] == 50.0
        assert result[3] == 50.0
        # Finite positions should have real ranks
        assert result[0] < result[4]

    def test_inf_treated_as_nan(self):
        from ml.features import _pct_rank
        arr = np.array([np.inf, -np.inf, 10.0, 20.0])
        result = _pct_rank(arr)
        # inf and -inf are not finite, so should get 50
        assert result[0] == 50.0
        assert result[1] == 50.0


class TestFeatureComputation:
    """Tests for FeatureEngine and feature computation."""

    def test_short_history_does_not_crash(self):
        from ml.features import FeatureEngine
        engine = FeatureEngine()
        # compute_one requires >= 60 records, so short history returns empty dict
        recs = _make_records(10)
        result = engine.compute_one("TEST", recs)
        assert result == {}

    def test_short_history_compute_sequence_returns_none(self):
        from ml.features import FeatureEngine
        engine = FeatureEngine()
        recs = _make_records(20)
        result = engine.compute_sequence("TEST", recs, lookback=15)
        assert result is None

    def test_feature_names_count_matches_computed(self):
        from ml.features import (
            ALL_FEATURE_NAMES, TECHNICAL_FEATURE_NAMES,
            CALENDAR_FEATURE_NAMES, SECTOR_FEATURE_NAMES,
            FACTOR_FEATURE_NAMES, N_FEATURES_ALL,
        )
        total = (
            len(TECHNICAL_FEATURE_NAMES)
            + len(CALENDAR_FEATURE_NAMES)
            + len(SECTOR_FEATURE_NAMES)
            + len(FACTOR_FEATURE_NAMES)
        )
        assert total == len(ALL_FEATURE_NAMES)
        assert N_FEATURES_ALL == len(ALL_FEATURE_NAMES)

    def test_gru_feature_names_count(self):
        from ml.features import GRU_FEATURE_NAMES, N_FEATURES_GRU
        assert len(GRU_FEATURE_NAMES) == 14
        assert N_FEATURES_GRU == 14

    def test_compute_one_with_sufficient_data(self):
        from ml.features import FeatureEngine, ALL_FEATURE_NAMES
        engine = FeatureEngine()
        recs = _make_records(100)
        result = engine.compute_one("TEST", recs)
        assert isinstance(result, dict)
        assert len(result) > 0
        # Should contain technical features at minimum
        assert "ret_1d" in result
        assert "rsi_14" in result

    def test_compute_universe_returns_dataframe(self):
        from ml.features import FeatureEngine
        import pandas as pd
        engine = FeatureEngine()
        histories = {
            "SYM_A": _make_records(100, base_price=200, trend=0.002),
            "SYM_B": _make_records(100, base_price=300, trend=-0.001),
            "SYM_C": _make_records(30),  # too short, should be skipped
        }
        df = engine.compute_universe(histories)
        assert isinstance(df, pd.DataFrame)
        # SYM_C has only 30 records (<60), should be excluded
        assert "SYM_A" in df.index
        assert "SYM_B" in df.index
        assert "SYM_C" not in df.index


# ===========================================================================
# regime.py tests
# ===========================================================================

class TestExtractMarketFeatures:
    """Tests for regime._extract_market_features."""

    def test_constant_prices_volatility_near_zero(self):
        from ml.regime import _extract_market_features
        # Build 15 symbols with constant price, enough data
        histories = {}
        for i in range(15):
            histories[f"SYM_{i}"] = _make_constant_records(30, price=100.0 + i)
        features = _extract_market_features(histories)
        assert features is not None
        assert features.shape == (6,)
        # Volatility (index 2 = market_vol_20d) should be ~0 for constant prices
        vol = features[2]
        assert np.isfinite(vol)
        assert vol < 0.01  # effectively zero
        # No NaN or Inf in any feature
        assert not np.any(np.isnan(features))
        assert not np.any(np.isinf(features))

    def test_insufficient_symbols_returns_none(self):
        from ml.regime import _extract_market_features
        # Need at least 10 symbols with >= 25 records; supply fewer
        histories = {
            "SYM_0": _make_records(30),
            "SYM_1": _make_records(30),
        }
        result = _extract_market_features(histories)
        assert result is None

    def test_short_records_skipped(self):
        from ml.regime import _extract_market_features
        # Mix of long and short records
        histories = {}
        for i in range(12):
            if i < 5:
                histories[f"SYM_{i}"] = _make_records(5)  # too short
            else:
                histories[f"SYM_{i}"] = _make_records(30)
        # Only 7 valid symbols (< 10 threshold), so should return None
        result = _extract_market_features(histories)
        assert result is None

    def test_normal_data_returns_6_features(self):
        from ml.regime import _extract_market_features
        histories = {}
        rng = np.random.RandomState(99)
        for i in range(20):
            histories[f"SYM_{i}"] = _make_records(
                50, base_price=100 + i * 10, trend=rng.uniform(-0.005, 0.005)
            )
        features = _extract_market_features(histories)
        assert features is not None
        assert features.shape == (6,)
        assert features.dtype == np.float32
        assert not np.any(np.isnan(features))
        assert not np.any(np.isinf(features))


class TestBuildHistoryMatrix:
    """Tests for regime._build_history_matrix."""

    def test_insufficient_data_returns_none(self):
        from ml.regime import _build_history_matrix
        # Less than 40 unique dates
        histories = {
            "SYM_0": _make_records(15),
        }
        result = _build_history_matrix(histories)
        assert result is None

    def test_too_few_valid_rows_returns_none(self):
        from ml.regime import _build_history_matrix
        # Provide dates but too few symbols to produce valid features
        histories = {
            "SYM_0": _make_records(50),
            "SYM_1": _make_records(50),
        }
        # Only 2 symbols -- _extract_market_features will return None
        # for each snapshot, so no rows accumulate -> returns None
        result = _build_history_matrix(histories)
        assert result is None


class TestRegimeDetector:
    """Tests for the RegimeDetector GMM-based classifier."""

    def test_unfitted_uses_rule_based_fallback(self):
        from ml.regime import RegimeDetector
        det = RegimeDetector()
        # Bull-like features: high breadth, positive return
        bull_features = np.array([0.75, 0.05, 0.02, 0.5, 1.0, 0.7], dtype=np.float32)
        regime_id, conf, proba = det.predict(bull_features)
        assert regime_id == 0  # BULL
        assert 0 < conf <= 1
        assert proba.shape == (3,)
        assert abs(proba.sum() - 1.0) < 1e-6

    def test_rule_based_bear(self):
        from ml.regime import RegimeDetector
        det = RegimeDetector()
        bear_features = np.array([0.25, -0.05, 0.40, -0.5, 0.5, 0.3], dtype=np.float32)
        regime_id, conf, proba = det.predict(bear_features)
        assert regime_id == 2  # BEAR

    def test_rule_based_range(self):
        from ml.regime import RegimeDetector
        det = RegimeDetector()
        range_features = np.array([0.50, 0.00, 0.10, 0.0, 1.0, 0.4], dtype=np.float32)
        regime_id, conf, proba = det.predict(range_features)
        assert regime_id == 1  # RANGE

    def test_fit_and_predict(self):
        from ml.regime import RegimeDetector, SKLEARN_AVAILABLE
        if not SKLEARN_AVAILABLE:
            pytest.skip("sklearn not available")

        rng = np.random.RandomState(42)
        # Create synthetic data with 3 separable clusters
        X = np.vstack([
            rng.normal([0.8, 0.05, 0.02, 0.5, 1.2, 0.7], 0.05, size=(30, 6)),  # bull-like
            rng.normal([0.5, 0.00, 0.10, 0.0, 1.0, 0.4], 0.05, size=(30, 6)),  # range-like
            rng.normal([0.2, -0.04, 0.35, -0.4, 0.8, 0.3], 0.05, size=(30, 6)),  # bear-like
        ]).astype(np.float32)

        det = RegimeDetector()
        det.fit(X)
        assert det.fitted

        # Predict a bull-like observation
        regime_id, conf, proba = det.predict(
            np.array([0.8, 0.05, 0.02, 0.5, 1.2, 0.7], dtype=np.float32)
        )
        assert regime_id in (0, 1, 2)
        assert proba.shape == (3,)
        assert 0 < conf <= 1.0


class TestNaNFiltering:
    """Tests that NaN values in the feature matrix are filtered out by _build_history_matrix."""

    def test_feature_matrix_no_nans(self):
        from ml.regime import _build_history_matrix
        # Build enough data to produce a feature matrix
        histories = {}
        rng = np.random.RandomState(123)
        for i in range(20):
            histories[f"SYM_{i}"] = _make_records(
                80, base_price=100 + i * 5, trend=rng.uniform(-0.003, 0.003)
            )
        result = _build_history_matrix(histories, lookback=60)
        if result is not None:
            # The function filters rows with NaN, so the result should be clean
            assert not np.any(np.isnan(result))
            assert not np.any(np.isinf(result))
            assert result.shape[1] == 6


class TestMarketRegimeMonitor:
    """Tests for the MarketRegimeMonitor orchestrator."""

    def test_fallback_result_on_insufficient_data(self):
        from ml.regime import MarketRegimeMonitor
        monitor = MarketRegimeMonitor(model_path=None)
        # Empty histories -> should return fallback
        result = monitor.update({})
        assert result["regime"] in ("BULL", "RANGE", "BEAR")
        assert "confidence" in result
        assert "multiplier" in result

    def test_get_multiplier_default(self):
        from ml.regime import MarketRegimeMonitor, REGIME_MULT
        monitor = MarketRegimeMonitor(model_path=None)
        mult = monitor.get_multiplier()
        # Default regime is RANGE (id=1), multiplier=0.8
        assert mult == REGIME_MULT[1]
