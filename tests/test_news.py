"""Tests for llm/news_intelligence.py -- formatting functions."""
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "llm"))

from news_intelligence import format_telegram_alert, format_email_html


SAMPLE_ANALYSIS = {
    "market_outlook": "BULLISH",
    "impact_score": 6,
    "summary": "Banking sector earnings beat expectations across the board.",
    "key_events": [
        {
            "impact": "POSITIVE",
            "explanation": "NRB eases CCD ratio requirement",
            "affected_stocks": ["NABIL", "HBL", "NICA"],
        },
        {
            "impact": "NEGATIVE",
            "explanation": "Hydropower output drops due to low rainfall",
            "affected_stocks": ["UPPER", "NHPC"],
        },
    ],
    "action_items": [
        "Consider adding banking positions on dips",
        "Reduce hydro exposure until monsoon",
    ],
    "stocks_to_watch": ["NABIL", "NICA"],
    "stocks_to_avoid": ["UPPER"],
}


class TestFormatTelegramAlert:
    """Test Telegram alert formatting."""

    def test_contains_outlook(self):
        msg = format_telegram_alert(SAMPLE_ANALYSIS)
        assert "BULLISH" in msg

    def test_contains_impact_score(self):
        msg = format_telegram_alert(SAMPLE_ANALYSIS)
        assert "+6" in msg or "6" in msg

    def test_contains_summary(self):
        msg = format_telegram_alert(SAMPLE_ANALYSIS)
        assert "Banking sector earnings" in msg

    def test_contains_key_events(self):
        msg = format_telegram_alert(SAMPLE_ANALYSIS)
        assert "NRB eases CCD" in msg
        assert "NABIL" in msg

    def test_contains_action_items(self):
        msg = format_telegram_alert(SAMPLE_ANALYSIS)
        assert "banking positions" in msg

    def test_contains_watch_and_avoid(self):
        msg = format_telegram_alert(SAMPLE_ANALYSIS)
        assert "NICA" in msg
        assert "UPPER" in msg

    def test_returns_string(self):
        msg = format_telegram_alert(SAMPLE_ANALYSIS)
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_minimal_analysis(self):
        """Handles analysis dict with missing optional fields."""
        minimal = {"market_outlook": "NEUTRAL", "impact_score": 0, "summary": "Quiet day."}
        msg = format_telegram_alert(minimal)
        assert "NEUTRAL" in msg
        assert "Quiet day" in msg

    def test_empty_analysis(self):
        """Handles completely empty dict without crashing."""
        msg = format_telegram_alert({})
        assert isinstance(msg, str)


class TestFormatEmailHtml:
    """Test email HTML formatting."""

    def test_returns_string(self):
        html = format_email_html(SAMPLE_ANALYSIS)
        assert isinstance(html, str)

    def test_contains_html_tags(self):
        html = format_email_html(SAMPLE_ANALYSIS)
        assert "<table" in html
        assert "</table>" in html

    def test_contains_outlook_text(self):
        html = format_email_html(SAMPLE_ANALYSIS)
        assert "BULLISH" in html

    def test_contains_impact_score(self):
        html = format_email_html(SAMPLE_ANALYSIS)
        assert "+6" in html

    def test_contains_summary(self):
        html = format_email_html(SAMPLE_ANALYSIS)
        assert "Banking sector earnings" in html

    def test_contains_event_stocks(self):
        html = format_email_html(SAMPLE_ANALYSIS)
        assert "NABIL" in html

    def test_contains_action_items(self):
        html = format_email_html(SAMPLE_ANALYSIS)
        assert "banking positions" in html

    def test_bullish_color(self):
        html = format_email_html(SAMPLE_ANALYSIS)
        assert "#00e475" in html

    def test_bearish_color(self):
        bearish = {**SAMPLE_ANALYSIS, "market_outlook": "BEARISH"}
        html = format_email_html(bearish)
        assert "#ff5252" in html

    def test_minimal_analysis(self):
        minimal = {"market_outlook": "NEUTRAL", "impact_score": 0, "summary": "Nothing happened."}
        html = format_email_html(minimal)
        assert "<table" in html
        assert "Nothing happened" in html

    def test_empty_analysis(self):
        html = format_email_html({})
        assert isinstance(html, str)
        assert "<table" in html
