"""Tests for ml/transformer_predictor.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pytest


class TestTransformerModel:
    def test_model_builds(self):
        import torch
        from ml.transformer_predictor import build_transformer_model, TRANSFORMER_CONFIG
        model = build_transformer_model(TRANSFORMER_CONFIG)
        assert model is not None

    def test_output_shape(self):
        import torch
        from ml.transformer_predictor import build_transformer_model, TRANSFORMER_CONFIG
        model = build_transformer_model(TRANSFORMER_CONFIG)
        x = torch.randn(4, 15, 14)
        out = model(x)
        assert out.shape == (4, 5)

    def test_output_finite(self):
        import torch
        from ml.transformer_predictor import build_transformer_model, TRANSFORMER_CONFIG
        model = build_transformer_model(TRANSFORMER_CONFIG)
        x = torch.randn(2, 15, 14)
        out = model(x)
        assert torch.isfinite(out).all()

    def test_softmax_sums_to_one(self):
        import torch
        from ml.transformer_predictor import build_transformer_model, TRANSFORMER_CONFIG
        model = build_transformer_model(TRANSFORMER_CONFIG)
        x = torch.randn(3, 15, 14)
        out = model(x)
        probs = torch.softmax(out, dim=1)
        sums = probs.sum(dim=1)
        assert torch.allclose(sums, torch.ones(3), atol=1e-5)

    def test_different_batch_sizes(self):
        import torch
        from ml.transformer_predictor import build_transformer_model, TRANSFORMER_CONFIG
        model = build_transformer_model(TRANSFORMER_CONFIG)
        for bs in [1, 8, 32]:
            x = torch.randn(bs, 15, 14)
            out = model(x)
            assert out.shape == (bs, 5)

    def test_param_count_reasonable(self):
        import torch
        from ml.transformer_predictor import build_transformer_model, TRANSFORMER_CONFIG
        model = build_transformer_model(TRANSFORMER_CONFIG)
        n_params = sum(p.numel() for p in model.parameters())
        # Should be between 50K and 500K for this small model
        assert 50_000 < n_params < 500_000


class TestSentimentScoring:
    def test_bullish_headline(self):
        from scrapers.sentiment_analyzer import NEPSESentimentAnalyzer
        sa = NEPSESentimentAnalyzer()
        score = sa.score_sentiment("NABIL bank reports record profit and declares dividend")
        assert score > 0

    def test_bearish_headline(self):
        from scrapers.sentiment_analyzer import NEPSESentimentAnalyzer
        sa = NEPSESentimentAnalyzer()
        score = sa.score_sentiment("NRB suspends banking license, penalty imposed for fraud")
        assert score < 0

    def test_neutral_headline(self):
        from scrapers.sentiment_analyzer import NEPSESentimentAnalyzer
        sa = NEPSESentimentAnalyzer()
        score = sa.score_sentiment("Annual general meeting scheduled for next week")
        assert -0.3 <= score <= 0.3

    def test_symbol_extraction(self):
        from scrapers.sentiment_analyzer import NEPSESentimentAnalyzer
        sa = NEPSESentimentAnalyzer()
        symbols = sa.extract_symbols("NABIL and HBL report strong quarterly earnings")
        assert "NABIL" in symbols
        assert "HBL" in symbols

    def test_company_alias_extraction(self):
        from scrapers.sentiment_analyzer import NEPSESentimentAnalyzer
        sa = NEPSESentimentAnalyzer()
        symbols = sa.extract_symbols("Nepal Life Insurance posts record premium collection")
        assert "NLIC" in symbols or "NLICL" in symbols

    def test_score_range(self):
        from scrapers.sentiment_analyzer import NEPSESentimentAnalyzer
        sa = NEPSESentimentAnalyzer()
        for headline in ["good news", "bad crash", "normal day", "massive profit surge boom rally"]:
            score = sa.score_sentiment(headline)
            assert -1.0 <= score <= 1.0


class TestModelRegistry:
    def test_init_creates_files(self, tmp_path, monkeypatch):
        import ml.model_registry as mr
        monkeypatch.setattr(mr, "ROOT", tmp_path)
        mr.ModelRegistry.REGISTRY_FILE = tmp_path / "data" / "model_registry.json"
        mr.ModelRegistry.ARCHIVE_DIR = tmp_path / "data" / "models" / "archive"

        reg = mr.ModelRegistry()
        assert (tmp_path / "data" / "models" / "archive").exists()

    def test_register_and_get(self, tmp_path, monkeypatch):
        import ml.model_registry as mr
        monkeypatch.setattr(mr, "ROOT", tmp_path)
        mr.ModelRegistry.REGISTRY_FILE = tmp_path / "data" / "model_registry.json"
        mr.ModelRegistry.ARCHIVE_DIR = tmp_path / "data" / "models" / "archive"

        # Create a fake model file
        model_dir = tmp_path / "data" / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        model_path = model_dir / "TEST_gru.pt"
        model_path.write_text("fake model")

        reg = mr.ModelRegistry()
        vid = reg.register("gru", "TEST", {"val_acc": 0.5, "test_acc": 0.4}, model_path)

        assert "gru_TEST" in vid
        active = reg.get_active_version("gru", "TEST")
        assert active is not None
        assert active["metrics"]["val_acc"] == 0.5

    def test_summary_not_empty_after_register(self, tmp_path, monkeypatch):
        import ml.model_registry as mr
        monkeypatch.setattr(mr, "ROOT", tmp_path)
        mr.ModelRegistry.REGISTRY_FILE = tmp_path / "data" / "model_registry.json"
        mr.ModelRegistry.ARCHIVE_DIR = tmp_path / "data" / "models" / "archive"

        model_dir = tmp_path / "data" / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        model_path = model_dir / "X_gru.pt"
        model_path.write_text("fake")

        reg = mr.ModelRegistry()
        reg.register("gru", "X", {"val_acc": 0.6}, model_path)
        s = reg.summary()
        assert "X" in s
        assert len(s) > 20
