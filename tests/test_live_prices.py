"""Tests for src/live_prices.py -- number parsing and price fetching."""
import json
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from live_prices import _parse_num, fetch_live_prices


class TestParseNum:
    """Test _parse_num with various real-world inputs."""

    def test_plain_integer(self):
        assert _parse_num("1234") == 1234.0

    def test_with_commas(self):
        assert _parse_num("1,234.5") == 1234.5

    def test_large_number_with_commas(self):
        assert _parse_num("12,345,678.90") == 12345678.90

    def test_negative_value(self):
        assert _parse_num("-0.45") == -0.45

    def test_unicode_minus(self):
        # Sharesansar sometimes uses Unicode minus (U+2212)
        assert _parse_num("\u22120.45") == -0.45

    def test_double_dash(self):
        assert _parse_num("--") == 0.0

    def test_single_dash(self):
        assert _parse_num("-") == 0.0

    def test_empty_string(self):
        assert _parse_num("") == 0.0

    def test_whitespace(self):
        assert _parse_num("  ") == 0.0

    def test_padded_number(self):
        assert _parse_num("  550.00  ") == 550.0

    def test_zero(self):
        assert _parse_num("0") == 0.0

    def test_non_numeric_garbage(self):
        assert _parse_num("N/A") == 0.0


class TestFetchLivePrices:
    """Test fetch_live_prices with mocked HTTP responses."""

    SAMPLE_HTML = """
    <html><body>
    <table class="table">
      <tr><th>SN</th><th>Symbol</th><th>LTP</th><th>Change</th><th>%Change</th>
          <th>Open</th><th>High</th><th>Low</th><th>Qty</th><th>Turnover</th></tr>
      <tr><td>1</td><td>NABIL</td><td>1,100.00</td><td>10</td><td>0.92</td>
          <td>1,090</td><td>1,120</td><td>1,085</td><td>11,400</td><td>12,500,000</td></tr>
      <tr><td>2</td><td>ALICL</td><td>550.00</td><td>-5</td><td>-0.90</td>
          <td>555</td><td>560</td><td>545</td><td>5,800</td><td>3,200,000</td></tr>
    </table>
    </body></html>
    """

    @patch("live_prices.CACHE_FILE")
    @patch("live_prices.requests.get")
    def test_returns_dict(self, mock_get, mock_cache_path):
        mock_cache_path.exists.return_value = False
        mock_cache_path.parent.mkdir = MagicMock()
        mock_cache_path.with_suffix.return_value = MagicMock()

        mock_resp = MagicMock()
        mock_resp.text = self.SAMPLE_HTML
        mock_get.return_value = mock_resp

        prices = fetch_live_prices()

        assert isinstance(prices, dict)
        assert "NABIL" in prices
        assert "ALICL" in prices
        assert prices["NABIL"]["lp"] == 1100.0
        assert prices["ALICL"]["pc"] == -0.90

    @patch("live_prices.CACHE_FILE")
    @patch("live_prices.requests.get")
    def test_empty_table_returns_fallback(self, mock_get, mock_cache_path):
        mock_cache_path.exists.return_value = False

        mock_resp = MagicMock()
        mock_resp.text = "<html><body><table class='table'><tr><th>SN</th></tr></table></body></html>"
        mock_get.return_value = mock_resp

        prices = fetch_live_prices()
        assert isinstance(prices, dict)

    @patch("live_prices.CACHE_FILE")
    @patch("live_prices.requests.get")
    def test_network_error_returns_dict(self, mock_get, mock_cache_path):
        mock_cache_path.exists.return_value = False
        mock_get.side_effect = ConnectionError("no network")

        prices = fetch_live_prices()
        assert isinstance(prices, dict)
