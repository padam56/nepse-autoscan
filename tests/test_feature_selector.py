"""Tests for ml/feature_selector.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "ml"))

import numpy as np
import pytest
from ml.feature_selector import FeatureSelector


@pytest.fixture
def rng():
    return np.random.RandomState(42)


@pytest.fixture
def sample_data(rng):
    """50 samples, 10 features, 5 classes."""
    X = rng.randn(50, 10)
    y = rng.randint(0, 5, 50)
    names = ["f_%d" % i for i in range(10)]
    return X, y, names


class TestDropNaN:
    def test_high_nan_feature_dropped(self, rng):
        X = rng.randn(100, 5)
        X[:, 3] = np.nan  # 100% NaN
        X[:60, 4] = np.nan  # 60% NaN
        y = rng.randint(0, 3, 100)
        names = ["a", "b", "c", "nan_col", "mostly_nan"]

        sel = FeatureSelector(max_nan_frac=0.5)
        selected = sel.select(X, y, names, top_k=10)

        assert "nan_col" not in selected
        assert "mostly_nan" not in selected
        assert "a" in selected

    def test_low_nan_feature_kept(self, rng):
        X = rng.randn(100, 3)
        X[:10, 2] = np.nan  # 10% NaN — under threshold
        y = rng.randint(0, 3, 100)
        names = ["a", "b", "slight_nan"]

        sel = FeatureSelector(max_nan_frac=0.5)
        selected = sel.select(X, y, names, top_k=10)

        assert "slight_nan" in selected


class TestDropLowVariance:
    def test_constant_feature_dropped(self, rng):
        X = rng.randn(100, 4)
        X[:, 2] = 5.0  # constant
        y = rng.randint(0, 3, 100)
        names = ["a", "b", "constant", "d"]

        sel = FeatureSelector(min_variance=0.01)
        selected = sel.select(X, y, names, top_k=10)

        assert "constant" not in selected
        assert "a" in selected


class TestDropCorrelated:
    def test_highly_correlated_pair_one_dropped(self, rng):
        X = rng.randn(100, 4)
        X[:, 1] = X[:, 0] + rng.randn(100) * 0.01  # r > 0.99
        y = rng.randint(0, 3, 100)
        names = ["original", "clone", "c", "d"]

        sel = FeatureSelector(max_corr=0.95)
        selected = sel.select(X, y, names, top_k=10)

        # One of the pair should be dropped
        has_original = "original" in selected
        has_clone = "clone" in selected
        assert not (has_original and has_clone), "Both correlated features kept"
        assert has_original or has_clone, "Both correlated features dropped"


class TestTopK:
    def test_top_k_limits_output(self, sample_data):
        X, y, names = sample_data
        sel = FeatureSelector()
        selected = sel.select(X, y, names, top_k=5)

        assert len(selected) <= 5

    def test_top_k_larger_than_features_keeps_all_valid(self, sample_data):
        X, y, names = sample_data
        sel = FeatureSelector()
        selected = sel.select(X, y, names, top_k=100)

        # Should keep all non-dropped features (at most 10)
        assert len(selected) <= 10
        assert len(selected) >= 5  # most features should survive


class TestSaveLoad:
    def test_save_and_load(self, tmp_path, sample_data, monkeypatch):
        import ml.feature_selector as fs
        monkeypatch.setattr(fs, "SELECTED_FEATURES_PATH", tmp_path / "sel.json")

        X, y, names = sample_data
        sel = FeatureSelector()
        selected = sel.select(X, y, names, top_k=5)
        sel.save_selected(selected)

        loaded = FeatureSelector.load_selected()
        assert loaded == selected

    def test_load_missing_returns_none(self, tmp_path, monkeypatch):
        import ml.feature_selector as fs
        monkeypatch.setattr(fs, "SELECTED_FEATURES_PATH", tmp_path / "nope.json")

        assert FeatureSelector.load_selected() is None
