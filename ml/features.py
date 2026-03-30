"""
ml/features.py — Comprehensive Feature Engineering for NEPSE

Generates 56+ features per stock across 5 categories:
  1. Technical     (32) — price/volume/momentum/volatility indicators
  2. Calendar      (12) — NEPSE-specific seasonal effects
  3. Sector        ( 6) — cross-sectional sector dynamics
  4. Factor model  ( 6) — Fama-French style equity factors

All features are designed to be:
  - Point-in-time safe (no future leakage)
  - Rank-normalized where appropriate (percentile 0–100 vs universe)
  - Named consistently for downstream model consumption

Usage:
    from ml.features import FeatureEngine
    engine = FeatureEngine(sectors_path, calendar_path)
    feat_df = engine.compute_universe(all_histories)   # DataFrame[symbol × feature]
    feat_seq = engine.compute_sequence(symbol, history, lookback=15)  # ndarray for GRU
"""

from __future__ import annotations
import json
import math
import warnings
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore", category=RuntimeWarning)

# ── Constants ─────────────────────────────────────────────────────────────────

SECTOR_CODES = {
    "COMMERCIAL_BANK": 0,
    "DEVELOPMENT_BANK": 1,
    "FINANCE": 2,
    "LIFE_INSURANCE": 3,
    "NONLIFE_INSURANCE": 4,
    "HYDROPOWER": 5,
    "MANUFACTURING": 6,
    "HOTEL_TOURISM": 7,
    "TRADING": 8,
    "MUTUAL_FUND": 9,
    "MICROFINANCE": 10,
    "OTHERS": 11,
}

# NEPSE-specific calendar: months where effects are strongest
# Nepali fiscal year: Shrawan (mid-Jul) → Ashadh (mid-Jul)
NRB_POLICY_MONTHS  = {1, 4, 7, 10}         # Quarterly monetary policy
BUDGET_MONTHS      = {5, 6}                  # May-Jun (budget presentation)
FY_END_MONTHS      = {6, 7}                  # Jun-Jul (fiscal year-end)
DASHAIN_MONTHS     = {10, 11}                # Oct-Nov
TIHAR_MONTHS       = {10, 11}                # Oct-Nov (overlaps Dashain)
NEPALI_NEW_YEAR    = {4}                     # April (Baisakh)
MONSOON_MONTHS     = {6, 7, 8}              # Jun-Aug (hydro peak)
WINTER_MONTHS      = {12, 1, 2}             # Dec-Feb (dry season)

FEATURE_NAMES: List[str] = []   # populated at bottom


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_div(a: np.ndarray, b: np.ndarray, fill: float = 0.0) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(b != 0, a / b, fill)
    return np.where(np.isfinite(r), r, fill)


def _pct_rank(arr: np.ndarray) -> np.ndarray:
    """Return percentile rank (0–100) for each element, NaN-safe."""
    finite = arr[np.isfinite(arr)]
    if len(finite) == 0:
        return np.full_like(arr, 50.0)
    ranks = np.array([
        stats.percentileofscore(finite, v, kind="rank") if np.isfinite(v) else 50.0
        for v in arr
    ])
    return ranks


def _rolling_mean(x: np.ndarray, w: int) -> np.ndarray:
    if len(x) < w:
        return np.full_like(x, np.nan)
    result = np.full_like(x, np.nan, dtype=float)
    cs = np.cumsum(x)
    result[w - 1:] = (cs[w - 1:] - np.concatenate([[0], cs[:-w]])[: len(cs) - w + 1]) / w
    return result


def _rolling_std(x: np.ndarray, w: int) -> np.ndarray:
    if len(x) < w:
        return np.full_like(x, np.nan)
    result = np.full_like(x, np.nan, dtype=float)
    for i in range(w - 1, len(x)):
        result[i] = x[i - w + 1 : i + 1].std(ddof=1)
    return result


def _ema(x: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1)
    result = np.empty_like(x, dtype=float)
    result[0] = x[0]
    for i in range(1, len(x)):
        result[i] = alpha * x[i] + (1 - alpha) * result[i - 1]
    return result


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = _rolling_mean(gain, period)
    avg_loss = _rolling_mean(loss, period)
    rs = _safe_div(avg_gain, avg_loss, fill=1.0)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return np.concatenate([[50.0], rsi])


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    tr = np.maximum(
        high - low,
        np.maximum(
            np.abs(high - np.concatenate([[close[0]], close[:-1]])),
            np.abs(low  - np.concatenate([[close[0]], close[:-1]])),
        ),
    )
    return _rolling_mean(tr, period)


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Average Directional Index."""
    n = len(close)
    if n < period * 2:
        return np.full(n, 25.0)
    prev_high = np.concatenate([[high[0]], high[:-1]])
    prev_low  = np.concatenate([[low[0]],  low[:-1]])
    prev_close = np.concatenate([[close[0]], close[:-1]])
    dm_plus  = np.where((high - prev_high) > (prev_low - low), np.maximum(high - prev_high, 0), 0)
    dm_minus = np.where((prev_low - low) > (high - prev_high), np.maximum(prev_low - low, 0), 0)
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    smooth_tr    = _rolling_mean(tr, period)
    smooth_plus  = _rolling_mean(dm_plus,  period)
    smooth_minus = _rolling_mean(dm_minus, period)
    di_plus  = 100 * _safe_div(smooth_plus,  smooth_tr)
    di_minus = 100 * _safe_div(smooth_minus, smooth_tr)
    di_diff  = np.abs(di_plus - di_minus)
    di_sum   = di_plus + di_minus
    dx = 100 * _safe_div(di_diff, di_sum)
    adx = _rolling_mean(dx, period)
    return np.where(np.isfinite(adx), adx, 25.0)


def _williams_r(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    result = np.full(n, -50.0)
    for i in range(period - 1, n):
        hh = high[i - period + 1 : i + 1].max()
        ll = low[i  - period + 1 : i + 1].min()
        if hh != ll:
            result[i] = (hh - close[i]) / (hh - ll) * -100.0
    return result


def _obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    sign = np.sign(np.diff(close))
    sign = np.concatenate([[0], sign])
    return np.cumsum(sign * volume)


def _cmf(high, low, close, volume, period: int = 20) -> np.ndarray:
    """Chaikin Money Flow."""
    hl_range = high - low
    mf_mult = _safe_div(((close - low) - (high - close)), hl_range)
    mf_vol  = mf_mult * volume
    cmf = np.full(len(close), 0.0)
    for i in range(period - 1, len(close)):
        vol_sum = volume[i - period + 1 : i + 1].sum()
        mfv_sum = mf_vol[i - period + 1 : i + 1].sum()
        if vol_sum > 0:
            cmf[i] = mfv_sum / vol_sum
    return cmf


def _macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[np.ndarray, np.ndarray]:
    ema_fast   = _ema(close, fast)
    ema_slow   = _ema(close, slow)
    macd_line  = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    return macd_line, signal_line


def _bollinger(close: np.ndarray, period: int = 20, num_std: float = 2.0):
    mid = _rolling_mean(close, period)
    std = _rolling_std(close, period)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


# ── Core feature computation ───────────────────────────────────────────────────

def _compute_technical(
    close: np.ndarray,
    high:  np.ndarray,
    low:   np.ndarray,
    open_: np.ndarray,
    volume: np.ndarray,
) -> dict:
    """Compute 32 technical features for the *last* bar."""
    n = len(close)

    def _last(arr):
        v = arr[-1]
        return float(v) if np.isfinite(v) else 0.0

    # ── Returns ──
    ret_1d  = (close[-1] / close[-2]  - 1) if n > 1  else 0.0
    ret_5d  = (close[-1] / close[-6]  - 1) if n > 5  else 0.0
    ret_20d = (close[-1] / close[-21] - 1) if n > 20 else 0.0
    ret_60d = (close[-1] / close[-61] - 1) if n > 60 else 0.0

    # ── RSI ──
    rsi_arr = _rsi(close, 14)
    rsi_14  = _last(rsi_arr)

    # ── MACD ──
    macd_line, macd_sig = _macd(close)
    macd_cross = _last(macd_line) - _last(macd_sig)   # positive = bullish

    # ── Bollinger ──
    bb_upper, bb_mid, bb_lower = _bollinger(close)
    bb_range = _last(bb_upper) - _last(bb_lower)
    bb_pos   = (_last(close) - _last(bb_lower)) / bb_range if bb_range > 0 else 0.5
    bb_width = bb_range / _last(bb_mid) if _last(bb_mid) > 0 else 0.0

    # ── Volume ──
    vol_ma20  = _rolling_mean(volume, 20)
    vol_ma5   = _rolling_mean(volume, 5)
    vol_ratio = _last(volume) / _last(vol_ma20) if _last(vol_ma20) > 0 else 1.0
    vol_trend = _last(vol_ma5) / _last(vol_ma20) if _last(vol_ma20) > 0 else 1.0

    # ── ATR ──
    atr_arr = _atr(high, low, close, 14)
    atr_pct = _last(atr_arr) / _last(close) if _last(close) > 0 else 0.0

    # ── ADX ──
    adx_arr = _adx(high, low, close, 14)
    adx_14  = _last(adx_arr)

    # ── Williams %R ──
    wr_arr    = _williams_r(high, low, close, 14)
    williams_r = _last(wr_arr)

    # ── Momentum (ROC) ──
    momentum_10 = (close[-1] / close[-11] - 1) if n > 10 else 0.0
    momentum_20 = (close[-1] / close[-21] - 1) if n > 20 else 0.0

    # ── EMA crossover ──
    ema8_arr  = _ema(close, 8)
    ema21_arr = _ema(close, 21)
    ema_cross = (_last(ema8_arr) / _last(ema21_arr) - 1) if _last(ema21_arr) > 0 else 0.0

    # ── VWAP deviation (approximate 20d VWAP) ──
    typical = (high + low + close) / 3.0
    tpv      = typical * volume
    vwap_20  = tpv[-20:].sum() / volume[-20:].sum() if volume[-20:].sum() > 0 else close[-1]
    vwap_dev = (close[-1] - vwap_20) / vwap_20 if vwap_20 > 0 else 0.0

    # ── 52-week high/low ──
    lookback_52w = min(n, 252)
    high_52w_val = high[-lookback_52w:].max()
    low_52w_val  = low[-lookback_52w:].min()
    dist_52w_high = (close[-1] / high_52w_val - 1) if high_52w_val > 0 else 0.0   # ≤ 0
    dist_52w_low  = (close[-1] / low_52w_val  - 1) if low_52w_val  > 0 else 0.0   # ≥ 0

    # ── Price acceleration (2nd derivative) ──
    if n > 3:
        d1 = close[-1] - close[-2]
        d2 = close[-2] - close[-3]
        price_accel = (d1 - d2) / close[-1] if close[-1] > 0 else 0.0
    else:
        price_accel = 0.0

    # ── Volume-price divergence ──
    # Positive price + negative vol trend = distribution (bearish)
    # Negative price + positive vol trend = accumulation (bullish)
    vp_div = (ret_5d * (1.0 - (vol_trend - 1.0)))  # simplified divergence signal

    # ── Circuit break frequency (±10% daily moves, NEPSE circuit) ──
    daily_rets = np.diff(close) / close[:-1]
    circuit_freq = float(np.mean(np.abs(daily_rets) >= 0.09)) if len(daily_rets) > 0 else 0.0

    # ── Max drawdown (60 days) ──
    c60 = close[-60:] if n >= 60 else close
    peak_60 = np.maximum.accumulate(c60)
    drawdowns = (c60 - peak_60) / peak_60
    max_dd_60 = float(drawdowns.min()) if len(drawdowns) > 0 else 0.0

    # ── Downside volatility (semi-deviation) ──
    neg_rets = daily_rets[daily_rets < 0]
    downside_vol = float(neg_rets.std()) if len(neg_rets) > 1 else 0.0

    # ── Skewness (20-day returns) ──
    rets_20 = np.diff(close[-21:]) / close[-21:-1] if n >= 21 else np.diff(close) / close[:-1]
    skew_20 = float(stats.skew(rets_20)) if len(rets_20) >= 3 else 0.0

    # ── OBV slope ──
    obv_arr = _obv(close, volume)
    obv_slope = float(np.polyfit(np.arange(5), obv_arr[-5:], 1)[0]) if n >= 5 else 0.0
    # normalize by avg volume
    avg_vol = float(volume.mean()) if volume.mean() > 0 else 1.0
    obv_slope_norm = obv_slope / avg_vol

    # ── CMF ──
    cmf_arr = _cmf(high, low, close, volume, 20)
    cmf_20  = _last(cmf_arr)

    # ── Close position in day's range ──
    hl = high[-1] - low[-1]
    close_range = (close[-1] - low[-1]) / hl if hl > 0 else 0.5

    # ── Average H-L spread % (20d) ──
    hl_spread = (high[-20:] - low[-20:]) / close[-20:]
    hl_spread_avg = float(hl_spread.mean()) if len(hl_spread) > 0 else 0.0

    # ── Gap frequency (gaps > 1%) ──
    open_close_prev = open_[1:] / close[:-1] - 1
    gap_freq = float(np.mean(np.abs(open_close_prev) > 0.01)) if len(open_close_prev) > 0 else 0.0

    # ── Price vs 200-day SMA ──
    sma200 = close[-200:].mean() if n >= 200 else close.mean()
    price_vs_sma200 = (close[-1] / sma200 - 1) if sma200 > 0 else 0.0

    return {
        # Returns
        "ret_1d":       float(np.clip(ret_1d,  -0.15, 0.15)),
        "ret_5d":       float(np.clip(ret_5d,  -0.30, 0.30)),
        "ret_20d":      float(np.clip(ret_20d, -0.50, 0.50)),
        "ret_60d":      float(np.clip(ret_60d, -0.70, 0.70)),
        # Oscillators
        "rsi_14":       float(np.clip(rsi_14, 0, 100)),
        "macd_cross":   float(np.clip(macd_cross / (close[-1] + 1e-6), -0.05, 0.05)),
        "bb_pos":       float(np.clip(bb_pos,   0.0, 1.0)),
        "bb_width":     float(np.clip(bb_width, 0.0, 0.3)),
        "williams_r":   float(np.clip(williams_r, -100, 0)),
        "cmf_20":       float(np.clip(cmf_20,    -1.0, 1.0)),
        # Momentum
        "momentum_10":  float(np.clip(momentum_10, -0.30, 0.30)),
        "momentum_20":  float(np.clip(momentum_20, -0.50, 0.50)),
        "ema_cross":    float(np.clip(ema_cross, -0.10, 0.10)),
        "price_accel":  float(np.clip(price_accel, -0.05, 0.05)),
        "price_vs_sma200": float(np.clip(price_vs_sma200, -0.5, 1.0)),
        # Trend strength
        "adx_14":       float(np.clip(adx_14, 0, 100)),
        # Volume
        "vol_ratio":    float(np.clip(vol_ratio,  0.0, 10.0)),
        "vol_trend":    float(np.clip(vol_trend,  0.0, 5.0)),
        "obv_slope":    float(np.clip(obv_slope_norm, -5.0, 5.0)),
        "vp_div":       float(np.clip(vp_div, -0.5, 0.5)),
        # Volatility
        "atr_pct":      float(np.clip(atr_pct,       0.0, 0.15)),
        "bb_width":     float(np.clip(bb_width,       0.0, 0.30)),
        "downside_vol": float(np.clip(downside_vol,   0.0, 0.10)),
        "skew_20":      float(np.clip(skew_20,       -3.0, 3.0)),
        "max_dd_60":    float(np.clip(max_dd_60,     -0.70, 0.0)),
        "circuit_freq": float(np.clip(circuit_freq,   0.0, 1.0)),
        # Structure
        "vwap_dev":     float(np.clip(vwap_dev,      -0.15, 0.15)),
        "dist_52w_high": float(np.clip(dist_52w_high, -0.8, 0.0)),
        "dist_52w_low":  float(np.clip(dist_52w_low,  0.0, 5.0)),
        "close_range":  float(np.clip(close_range,    0.0, 1.0)),
        "hl_spread_avg": float(np.clip(hl_spread_avg, 0.0, 0.15)),
        "gap_freq":     float(np.clip(gap_freq,        0.0, 1.0)),
    }


def _compute_calendar(ref_date: Optional[date] = None) -> dict:
    """12 calendar / seasonal features."""
    if ref_date is None:
        ref_date = date.today()
    m  = ref_date.month
    dw = ref_date.weekday()   # Mon=0 … Fri=4 (Sun not traded on NEPSE)
    # Map Python weekday to NEPSE weekday: Sun=0, Mon=1, Tue=2, Wed=3, Thu=4
    # Python: Mon=0, Sun=6 → nepse_dow = (dw + 1) % 7
    nepse_dow = (dw + 1) % 7
    woy = ref_date.isocalendar()[1]
    quarter = (m - 1) // 3 + 1

    return {
        "cal_dow":           float(nepse_dow),                          # 0–6 (Sun=0)
        "cal_month":         float(m),                                   # 1–12
        "cal_week":          float(woy),                                 # 1–53
        "cal_quarter":       float(quarter),                             # 1–4
        "cal_dashain":       float(m in DASHAIN_MONTHS),
        "cal_tihar":         float(m in TIHAR_MONTHS),
        "cal_budget":        float(m in BUDGET_MONTHS),
        "cal_fy_end":        float(m in FY_END_MONTHS),
        "cal_new_year":      float(m in NEPALI_NEW_YEAR),
        "cal_nrb":           float(m in NRB_POLICY_MONTHS),
        "cal_winter":        float(m in WINTER_MONTHS),
        "cal_monsoon":       float(m in MONSOON_MONTHS),
    }


def _compute_sector(
    symbol: str,
    close_5d_ret: float,
    close_20d_ret: float,
    sector_stats: dict,
    symbol_to_sector: dict,
) -> dict:
    """6 sector-relative features."""
    sector = symbol_to_sector.get(symbol, "OTHERS")
    sector_id = float(SECTOR_CODES.get(sector, 11))

    stats_5  = sector_stats.get(sector, {}).get("ret5",  0.0)
    stats_20 = sector_stats.get(sector, {}).get("ret20", 0.0)
    rank_5d  = sector_stats.get(sector, {}).get("rank_map", {}).get(symbol, 50.0)

    return {
        "sec_id":       sector_id / 11.0,          # normalized 0–1
        "sec_ret5":     float(np.clip(stats_5,  -0.30, 0.30)),
        "sec_ret20":    float(np.clip(stats_20, -0.50, 0.50)),
        "vs_sector5":   float(np.clip(close_5d_ret  - stats_5,  -0.20, 0.20)),
        "vs_sector20":  float(np.clip(close_20d_ret - stats_20, -0.30, 0.30)),
        "sec_rank5d":   float(rank_5d) / 100.0,    # normalized 0–1
    }


def _compute_factor(
    symbol: str,
    close: np.ndarray,
    volume: np.ndarray,
    factor_ranks: dict,
) -> dict:
    """6 Fama-French style factor features (cross-sectional rank-normalized)."""
    n = len(close)
    # Momentum (12-1): 12-month return minus last 1 month
    ret_12m = (close[-1] / close[-253] - 1) if n >= 253 else (close[-1] / close[0]  - 1)
    ret_1m  = (close[-1] / close[-22]  - 1) if n >= 22  else 0.0
    mom_12_1 = ret_12m - ret_1m

    # Low volatility rank (from pre-computed factor_ranks)
    low_vol_rank     = factor_ranks.get(symbol, {}).get("low_vol",     50.0) / 100.0
    near_52h_rank    = factor_ranks.get(symbol, {}).get("near_52w_h",  50.0) / 100.0
    price_vs_yr_avg  = factor_ranks.get(symbol, {}).get("p_vs_yr_avg", 50.0) / 100.0
    breakout_52w     = factor_ranks.get(symbol, {}).get("breakout_52w", 0.0)

    # Volume anomaly z-score (7 days)
    vol_7d     = volume[-7:]
    vol_mean   = float(volume[-30:].mean()) if n >= 30 else float(volume.mean())
    vol_std    = float(volume[-30:].std())  if n >= 30 else float(volume.std())
    vol_z      = (float(vol_7d.mean()) - vol_mean) / (vol_std + 1e-6)

    return {
        "fac_mom_12_1":   float(np.clip(mom_12_1,    -1.0, 2.0)),
        "fac_low_vol":    float(np.clip(low_vol_rank,  0.0, 1.0)),
        "fac_near_52h":   float(np.clip(near_52h_rank, 0.0, 1.0)),
        "fac_p_yr_avg":   float(np.clip(price_vs_yr_avg, 0.0, 1.0)),
        "fac_breakout":   float(np.clip(breakout_52w,  0.0, 1.0)),
        "fac_vol_z7":     float(np.clip(vol_z,        -4.0, 4.0)),
    }


# ── FeatureEngine ─────────────────────────────────────────────────────────────

class FeatureEngine:
    """
    Stateful feature engine.  Call ``compute_universe`` first (populates
    cross-sectional caches), then ``compute_one`` / ``compute_sequence``
    per stock.
    """

    def __init__(
        self,
        sectors_path: Optional[str | Path] = None,
        calendar_path: Optional[str | Path] = None,
    ):
        self.symbol_to_sector: Dict[str, str] = {}
        self.sector_to_symbols: Dict[str, List[str]] = {}
        self._sector_stats: Dict[str, dict] = {}
        self._factor_ranks: Dict[str, dict]  = {}

        if sectors_path and Path(sectors_path).exists():
            data = json.loads(Path(sectors_path).read_text())
            self.symbol_to_sector  = data.get("symbol_to_sector", {})
            self.sector_to_symbols = data.get("sectors", {})

    # ── Cross-sectional cache ──────────────────────────────────────────────────

    def _build_cross_sectional(
        self,
        histories: Dict[str, list],
    ) -> None:
        """Pre-compute sector stats and factor ranks across the universe."""
        # 1. Per-symbol raw metrics
        sym_ret5:  Dict[str, float] = {}
        sym_ret20: Dict[str, float] = {}
        sym_vol:   Dict[str, float] = {}
        sym_52h:   Dict[str, float] = {}
        sym_yr_avg: Dict[str, float] = {}

        for sym, recs in histories.items():
            c, _, _, _, v = _extract_arrays(recs)
            n = len(c)
            sym_ret5[sym]   = (c[-1] / c[-6] - 1) if n > 5  else 0.0
            sym_ret20[sym]  = (c[-1] / c[-21] - 1) if n > 20 else 0.0
            sym_vol[sym]    = float(c[-20:].std() / (c[-20:].mean() + 1e-6)) if n >= 20 else 0.05
            high_52w = c[-min(n, 252):].max()
            sym_52h[sym]    = c[-1] / high_52w if high_52w > 0 else 1.0
            sym_yr_avg[sym] = c[-1] / c[-min(n, 252):].mean() if c[-min(n, 252):].mean() > 0 else 1.0

        # 2. Sector-level stats
        self._sector_stats = {}
        for sector, syms in self.sector_to_symbols.items():
            in_uni = [s for s in syms if s in sym_ret5]
            if not in_uni:
                self._sector_stats[sector] = {"ret5": 0.0, "ret20": 0.0, "rank_map": {}}
                continue
            median_ret5  = float(np.median([sym_ret5[s]  for s in in_uni]))
            median_ret20 = float(np.median([sym_ret20[s] for s in in_uni]))
            rets_5 = [sym_ret5[s] for s in in_uni]
            rank_map = {
                s: float(stats.percentileofscore(rets_5, sym_ret5[s], kind="rank"))
                for s in in_uni
            }
            self._sector_stats[sector] = {
                "ret5":     median_ret5,
                "ret20":    median_ret20,
                "rank_map": rank_map,
            }

        # 3. Factor ranks (cross-sectional percentile)
        all_syms = list(sym_ret5.keys())
        vols     = np.array([sym_vol[s]    for s in all_syms])
        h52      = np.array([sym_52h[s]    for s in all_syms])
        yr_avg   = np.array([sym_yr_avg[s] for s in all_syms])

        low_vol_ranks    = _pct_rank(1.0 / (vols + 1e-6))  # low vol = high rank
        near_52h_ranks   = _pct_rank(h52)
        yr_avg_ranks     = _pct_rank(yr_avg)
        breakout_52w_arr = (h52 >= 0.97).astype(float)  # within 3% of 52w high

        self._factor_ranks = {}
        for i, s in enumerate(all_syms):
            self._factor_ranks[s] = {
                "low_vol":     float(low_vol_ranks[i]),
                "near_52w_h":  float(near_52h_ranks[i]),
                "p_vs_yr_avg": float(yr_avg_ranks[i]),
                "breakout_52w": float(breakout_52w_arr[i]),
            }

    # ── Public API ────────────────────────────────────────────────────────────

    def compute_universe(
        self,
        histories: Dict[str, list],
        ref_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """
        Compute all features for every stock in ``histories``.

        Returns a DataFrame with shape (n_stocks, n_features), index=symbol.
        Also stores cross-sectional caches for future calls.
        """
        self._build_cross_sectional(histories)
        cal = _compute_calendar(ref_date)

        from concurrent.futures import ThreadPoolExecutor

        def _compute_safe(args):
            sym, recs = args
            try:
                feat = self.compute_one(sym, recs, cal=cal)
                return (sym, feat) if feat else None
            except Exception:
                return None

        rows = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            for result in pool.map(_compute_safe, histories.items()):
                if result:
                    rows[result[0]] = result[1]

        df = pd.DataFrame(rows).T
        df.index.name = "symbol"
        return df

    def compute_one(
        self,
        symbol: str,
        recs: list,
        cal: Optional[dict] = None,
        ref_date: Optional[date] = None,
    ) -> dict:
        """
        Compute all features for a single stock.
        ``_build_cross_sectional`` must have been called first (or call
        ``compute_universe`` instead).
        """
        if len(recs) < 60:
            return {}
        c, h, l, o, v = _extract_arrays(recs)

        tech = _compute_technical(c, h, l, o, v)
        if cal is None:
            cal = _compute_calendar(ref_date)
        sect = _compute_sector(
            symbol,
            tech["ret_5d"],
            tech["ret_20d"],
            self._sector_stats,
            self.symbol_to_sector,
        )
        fac = _compute_factor(symbol, c, v, self._factor_ranks)

        return {**tech, **cal, **sect, **fac}

    def compute_sequence(
        self,
        symbol: str,
        recs: list,
        lookback: int = 15,
        gru_features: Optional[List[str]] = None,
    ) -> Optional[np.ndarray]:
        """
        Build a (lookback, n_gru_features) tensor for the GRU model.
        Uses a rolling window of point-in-time features over the last
        ``lookback + 60`` records.  No future leakage.

        Returns ndarray of shape (lookback, n_features) or None if not enough data.
        """
        if gru_features is None:
            gru_features = GRU_FEATURE_NAMES

        min_len = lookback + 60
        if len(recs) < min_len:
            return None

        # Use last (lookback + 60) records so we have warm-up history
        window = recs[-(lookback + 60):]
        seq = []
        for t in range(60, len(window)):
            sub_recs = window[:t + 1]
            feat = self.compute_one(symbol, sub_recs)
            if not feat:
                return None
            row = np.array([feat.get(k, 0.0) for k in gru_features], dtype=np.float32)
            seq.append(row)

        arr = np.stack(seq[-lookback:], axis=0)  # (lookback, n_features)
        # Clip extreme values and replace NaN/inf
        arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=-1.0)
        arr = np.clip(arr, -5.0, 5.0)
        return arr

    def rank_normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply cross-sectional rank normalization (percentile 0–1) to all
        numeric columns.  Non-numeric columns are dropped.
        """
        out = pd.DataFrame(index=df.index)
        for col in df.select_dtypes(include=[np.number]).columns:
            vals = df[col].values.astype(float)
            ranked = _pct_rank(vals)
            out[col] = ranked / 100.0
        return out


# ── Array extractor ───────────────────────────────────────────────────────────

def _extract_arrays(recs: list) -> Tuple[np.ndarray, ...]:
    """
    Convert list of dicts to (close, high, low, open, volume) arrays.
    Handles both 'lp'/'close' and missing H/L gracefully.
    """
    c = np.array([float(r.get("lp", r.get("close", 0))) for r in recs])
    h = np.array([float(r.get("h",  c[i]))               for i, r in enumerate(recs)])
    l = np.array([float(r.get("l",  c[i]))               for i, r in enumerate(recs)])
    o = np.array([float(r.get("op", c[i]))               for i, r in enumerate(recs)])
    v = np.array([float(r.get("q", 1))                   for r in recs])

    # Sanitize
    c = np.where((c <= 0) | ~np.isfinite(c), np.nan, c)
    med = np.nanmedian(c)
    c   = np.where(np.isnan(c), med if np.isfinite(med) else 100.0, c)
    h   = np.maximum(h, c)
    l   = np.minimum(l, c)
    v   = np.where(v <= 0, 1.0, v)

    return c, h, l, o, v


# ── Feature name lists ────────────────────────────────────────────────────────

TECHNICAL_FEATURE_NAMES = [
    "ret_1d", "ret_5d", "ret_20d", "ret_60d",
    "rsi_14", "macd_cross", "bb_pos", "bb_width", "williams_r", "cmf_20",
    "momentum_10", "momentum_20", "ema_cross", "price_accel", "price_vs_sma200",
    "adx_14",
    "vol_ratio", "vol_trend", "obv_slope", "vp_div",
    "atr_pct", "downside_vol", "skew_20", "max_dd_60", "circuit_freq",
    "vwap_dev", "dist_52w_high", "dist_52w_low",
    "close_range", "hl_spread_avg", "gap_freq",
]

CALENDAR_FEATURE_NAMES = [
    "cal_dow", "cal_month", "cal_week", "cal_quarter",
    "cal_dashain", "cal_tihar", "cal_budget", "cal_fy_end",
    "cal_new_year", "cal_nrb", "cal_winter", "cal_monsoon",
]

SECTOR_FEATURE_NAMES = [
    "sec_id", "sec_ret5", "sec_ret20",
    "vs_sector5", "vs_sector20", "sec_rank5d",
]

FACTOR_FEATURE_NAMES = [
    "fac_mom_12_1", "fac_low_vol", "fac_near_52h",
    "fac_p_yr_avg", "fac_breakout", "fac_vol_z7",
]

ALL_FEATURE_NAMES = (
    TECHNICAL_FEATURE_NAMES
    + CALENDAR_FEATURE_NAMES
    + SECTOR_FEATURE_NAMES
    + FACTOR_FEATURE_NAMES
)

# GRU uses a 14-feature subset (sequential, no cross-sectional leakage)
GRU_FEATURE_NAMES = [
    "ret_1d", "ret_5d", "ret_20d",
    "rsi_14", "macd_cross", "bb_pos",
    "vol_ratio", "vol_trend",
    "atr_pct", "ema_cross",
    "momentum_10", "momentum_20",
    "dist_52w_high", "adx_14",
]

N_FEATURES_ALL = len(ALL_FEATURE_NAMES)   # 55
N_FEATURES_GRU = len(GRU_FEATURE_NAMES)   # 14

assert N_FEATURES_GRU == 14, f"GRU expects 14 features, got {N_FEATURES_GRU}"
