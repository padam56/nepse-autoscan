"""Configuration for NEPSE stock analysis toolkit."""

# ── Your Position (canonical source: portfolio/config.py) ─────
try:
    from portfolio.config import PORTFOLIO
except ImportError:
    PORTFOLIO = {
        "ALICL": {"shares": 8046, "wacc": 549.87, "total_cost": 4_424_248.16},
    }

# ── Data Sources ───────────────────────────────────────────────
MEROLAGANI_BASE = "https://www.merolagani.com"
MEROLAGANI_COMPANY = f"{MEROLAGANI_BASE}/CompanyDetail.aspx"
MEROLAGANI_FLOORSHEET = f"{MEROLAGANI_BASE}/handlers/FloorsheetHandler.ashx"
MEROLAGANI_PRICE_HISTORY = f"{MEROLAGANI_BASE}/handlers/TechnicalChartHandler.ashx"

NEPSEALPHA_API = "https://nepsealpha.com/api"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": MEROLAGANI_BASE,
}

# ── Technical Analysis Parameters ──────────────────────────────
TA_CONFIG = {
    "sma_periods": [20, 50, 120, 200],
    "ema_periods": [9, 21, 50],
    "rsi_period": 14,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "bb_period": 20,
    "bb_std": 2,
    "atr_period": 14,
    "volume_ma_period": 20,
    "support_resistance_window": 20,
    "pivot_lookback": 5,
}

# ── Output ─────────────────────────────────────────────────────
DATA_DIR = "data"
REPORTS_DIR = "reports"
