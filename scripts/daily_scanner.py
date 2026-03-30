#!/usr/bin/env python3
"""
scripts/daily_scanner.py — NEPSE Ultra-Tier Ensemble Scanner
═══════════════════════════════════════════════════════════════════════════════

Pipeline (runs daily at 10:30 AM NPT after market open):

  [1] Load price history  →  310+ stocks, 120+ days each
  [2] Feature engineering →  56 features (technical + calendar + sector + factor)
  [3] Market regime        →  BULL / RANGE / BEAR (GMM-based)
  [4] XGBoost + LightGBM  →  Cross-sectional 5-day forward return quintile
  [5] GRU predictions      →  Per-stock deep learning scores
  [6] Technical scoring    →  Momentum / trend / volume / oscillator signals
  [7] Ensemble ranking     →  Weighted combination → Top 10 picks
  [8] Kelly sizing         →  Position size per pick (fraction of capital)
  [9] LLM reasoning        →  Qwen2.5-14B qualitative rationale (top 6)
 [10] Portfolio status     →  P&L, unrealized gains, recovery targets
 [11] Email report         →  Concise HTML to configured address

Usage:
  python scripts/daily_scanner.py               # full run + email
  python scripts/daily_scanner.py --print       # no email, print to console
  python scripts/daily_scanner.py --train-xgb   # retrain XGBoost + LightGBM
  python scripts/daily_scanner.py --train-gru   # launch GRU training in bg
  python scripts/daily_scanner.py --backtest    # walk-forward backtest report

Author:  NEPSE ML System
License: MIT
"""

from __future__ import annotations
import argparse
import concurrent.futures
import json
import math
import os
import pickle
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT    = Path(__file__).parent.parent
SCRATCH = Path("/scratch/C00621463/pypackages")
if SCRATCH.exists():
    sys.path.insert(0, str(SCRATCH))
sys.path.insert(0, str(ROOT / "ml"))
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(line_buffering=True)

import numpy as np

# ── Lazy ML imports (graceful degradation) ───────────────────────────────────
try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False

try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    LGB_AVAILABLE = False

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

# ── Internal modules ──────────────────────────────────────────────────────────
try:
    from features import FeatureEngine, ALL_FEATURE_NAMES, GRU_FEATURE_NAMES
    FEATURES_AVAILABLE = True
except ImportError:
    FEATURES_AVAILABLE = False

try:
    from regime import MarketRegimeMonitor
    REGIME_AVAILABLE = True
except ImportError:
    REGIME_AVAILABLE = False

try:
    from xgb_lgbm import EnsembleModel
    ENSEMBLE_AVAILABLE = True
except ImportError:
    ENSEMBLE_AVAILABLE = False

from risk_controls import apply_sector_cap, apply_drawdown_brake

try:
    from signal_tracker import SignalTracker
    SIGNAL_TRACKER_AVAILABLE = True
except ImportError:
    SIGNAL_TRACKER_AVAILABLE = False

try:
    from weight_optimizer import optimize_ensemble_weights
    WEIGHT_OPTIMIZER_AVAILABLE = True
except ImportError:
    WEIGHT_OPTIMIZER_AVAILABLE = False

# ── Configuration ─────────────────────────────────────────────────────────────
HISTORY_DIR   = ROOT / "data" / "price_history"
MODEL_DIR     = ROOT / "data" / "models"
SECTORS_FILE  = ROOT / "data" / "sectors.json"
CALENDAR_FILE = ROOT / "data" / "calendar.json"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

XGB_MODEL_PATH = MODEL_DIR / "xgb_scanner.pkl"
GRU_MODEL_DIR  = MODEL_DIR / "gru"
REGIME_MODEL   = MODEL_DIR / "regime.pkl"

MIN_DAYS    = 120
MIN_VOLUME  = 5_000
MIN_PRICE   = 50      # Exclude mutual funds, debentures, penny stocks
MAX_PRICE   = 1500    # Exclude expensive stocks (less accessible for retail)
MAX_IPO_GAIN = 150    # % gain from first-seen price — flag as IPO pump if exceeded
NEW_LISTING_DAYS = 90 # Stocks with < N days of data get a penalty
TOP_N       = 10
LOOKBACK    = 15     # GRU sequence length
COMMISSION  = 0.005  # 0.5% per side (NEPSE)

# Ensemble weights (XGB+LGB, GRU, TA) — must sum to 1.0
# Try to load learned weights from historical signal performance
_DEFAULT_W_ENSEMBLE = 0.45
_DEFAULT_W_GRU      = 0.30
_DEFAULT_W_TA       = 0.25

if WEIGHT_OPTIMIZER_AVAILABLE:
    try:
        _learned = optimize_ensemble_weights()
        W_ENSEMBLE = _learned.get("w_ml", _DEFAULT_W_ENSEMBLE)
        W_GRU      = _learned.get("w_gru", _DEFAULT_W_GRU)
        W_TA       = _learned.get("w_ta", _DEFAULT_W_TA)
    except Exception:
        W_ENSEMBLE = _DEFAULT_W_ENSEMBLE
        W_GRU      = _DEFAULT_W_GRU
        W_TA       = _DEFAULT_W_TA
else:
    W_ENSEMBLE = _DEFAULT_W_ENSEMBLE
    W_GRU      = _DEFAULT_W_GRU
    W_TA       = _DEFAULT_W_TA

# Kelly fraction cap (never risk more than 15% per trade)
MAX_KELLY   = 0.15

# ── Portfolio (update via trade_report.py or manually) ───────────────────────
PORTFOLIO: Dict[str, dict] = {
    "ALICL": {"shares": 8046,  "wacc": 549.87},
    "TTL":   {"shares": 368,   "wacc": 922.92},
    "NLIC":  {"shares": 273,   "wacc": 746.84},
    "BPCL":  {"shares": 200,   "wacc": 535.18},
    "BARUN": {"shares": 400,   "wacc": 391.41},
}

# ── Email config (from environment / .env) ────────────────────────────────────
EMAIL_FROM     = os.getenv("EMAIL_FROM",    "")
EMAIL_TO       = os.getenv("EMAIL_TO",      "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
SMTP_HOST      = os.getenv("SMTP_HOST",     "smtp.gmail.com")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_all_history(min_days: int = MIN_DAYS) -> Dict[str, list]:
    """Load {symbol: [records]} for all stocks with ≥ min_days of data."""
    stock_records: Dict[str, list] = {}
    for f in sorted(HISTORY_DIR.glob("*.json")):
        try:
            day = json.loads(f.read_text())
            d   = day["date"]
            for sym, data in day.get("stocks", {}).items():
                if sym not in stock_records:
                    stock_records[sym] = []
                stock_records[sym].append({"date": d, **data})
        except Exception:
            continue

    result = {}
    for sym, recs in stock_records.items():
        recs_sorted = sorted(recs, key=lambda x: x["date"])
        if len(recs_sorted) >= min_days:
            result[sym] = recs_sorted
    return result


def get_arrays(recs: list) -> Tuple[np.ndarray, ...]:
    """Extract (close, high, low, open, volume) numpy arrays from record list."""
    c = np.array([float(r.get("lp", r.get("close", 0))) for r in recs])
    h = np.array([float(r.get("h",  c[i])) for i, r in enumerate(recs)])
    l = np.array([float(r.get("l",  c[i])) for i, r in enumerate(recs)])
    o = np.array([float(r.get("op", c[i])) for i, r in enumerate(recs)])
    v = np.array([float(r.get("q",  1))    for r in recs])
    c = np.where((c <= 0) | ~np.isfinite(c), np.nan, c)
    c = np.where(np.isnan(c), np.nanmedian(c) if np.any(~np.isnan(c)) else 100.0, c)
    h = np.maximum(h, c)
    l = np.minimum(l, c)
    v = np.where(v <= 0, 1.0, v)
    return c, h, l, o, v


def _load_excluded_symbols() -> set:
    """Return set of symbols to exclude: mutual funds, debentures, bonds, funds."""
    excluded = set()
    try:
        data = json.loads(Path(SECTORS_FILE).read_text())
        sym_sec = data.get("symbol_to_sector", {})
        for s, sec in sym_sec.items():
            if "mutual" in sec.lower() or "fund" in sec.lower():
                excluded.add(s)
    except Exception:
        pass
    return excluded


def _is_debenture_or_fund(symbol: str) -> bool:
    """Check if a symbol looks like a debenture, bond, or fund by name pattern."""
    import re
    sym = symbol.upper()
    # Debentures: end with D followed by 2 digits (CBLD88, ICFCD83, NBLD87, etc.)
    if re.search(r'D\d{2,4}$', sym):
        return True
    # Bonds: end with BD or BLD followed by digits
    if re.search(r'B[L]?D\d{2,4}$', sym):
        return True
    # Funds: contain MF, GF, SF, EF, LF, or end with F followed by digit
    if re.search(r'(MF|GF|SF|EF|LF|BF|PF)\d*$', sym):
        return True
    # Promoter shares: end with PO
    if sym.endswith('PO'):
        return True
    # Named funds: NMB50, NICBF, etc.
    fund_patterns = ['NMB50', 'NICBF', 'NIBSF', 'NIBLGF', 'NIBLSTF',
                     'NICSF', 'NICGF', 'MBLEF', 'NMBMF', 'NMBHF',
                     'KEF', 'SEF', 'PSF', 'NFS', 'LVF']
    for pat in fund_patterns:
        if sym.startswith(pat):
            return True
    return False


MUTUAL_FUNDS = _load_excluded_symbols()


def apply_liquidity_filter(histories: Dict[str, list]) -> Dict[str, list]:
    """Remove stocks below minimum volume/price, exclude mutual funds and IPO pumps."""
    result = {}
    ipo_flagged = []
    for sym, recs in histories.items():
        if sym in MUTUAL_FUNDS or _is_debenture_or_fund(sym):
            continue
        c, _, _, _, v = get_arrays(recs)
        # Skip low-price (mutual funds, debentures) and high-price stocks
        if c[-1] < MIN_PRICE or c[-1] > MAX_PRICE:
            continue
        if v[-20:].mean() < MIN_VOLUME:
            continue
        # Flag newly listed stocks with extreme gains from listing price
        n_days = len(recs)
        if n_days < NEW_LISTING_DAYS:
            first_valid = next((p for p in c if p > 0), c[-1])
            gain_from_listing = (c[-1] / first_valid - 1) * 100 if first_valid > 0 else 0
            if gain_from_listing > MAX_IPO_GAIN:
                ipo_flagged.append((sym, n_days, gain_from_listing))
                continue  # Exclude IPO pumps from picks
        result[sym] = recs
    if ipo_flagged:
        names = ", ".join(f"{s} (+{g:.0f}% in {d}d)" for s, d, g in ipo_flagged)
        print(f"  [IPO FILTER] Excluded {len(ipo_flagged)} newly listed stocks with extreme gains: {names}")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# TECHNICAL SCORING (standalone, no ML dependency)
# ═══════════════════════════════════════════════════════════════════════════════

def _rsi(c: np.ndarray, p: int = 14) -> float:
    delta = np.diff(c)
    gain  = np.where(delta > 0, delta, 0.0)
    loss  = np.where(delta < 0, -delta, 0.0)
    def rmean(x, n):
        if len(x) < n: return 50.0
        return x[-n:].mean()
    ag, al = rmean(gain, p), rmean(loss, p)
    if al == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def _ema(c: np.ndarray, span: int) -> np.ndarray:
    a = 2.0 / (span + 1)
    r = np.empty_like(c, dtype=float)
    r[0] = c[0]
    for i in range(1, len(c)):
        r[i] = a * c[i] + (1 - a) * r[i - 1]
    return r


def compute_ta_score(recs: list) -> Tuple[float, str, List[str]]:
    """
    Technical analysis score (0–100).

    Components:
      - Momentum  30%  (RSI position, short/mid-term returns)
      - Trend     25%  (EMA alignment, price vs SMA200, MACD)
      - Volume    20%  (vol ratio, volume trend)
      - Structure 25%  (BB position, Williams %R, distance from 52w high)

    Returns:
      score    : float 0–100
      signal   : "STRONG BUY" / "BUY" / "NEUTRAL" / "SELL" / "STRONG SELL"
      reasons  : list of human-readable bullet points
    """
    c, h, l, o, v = get_arrays(recs)
    n = len(c)
    reasons: List[str] = []

    # ── Momentum ──────────────────────────────────────────────────────────────
    rsi = _rsi(c, 14)
    ret5  = (c[-1] / c[-6]  - 1) * 100 if n > 5  else 0.0
    ret20 = (c[-1] / c[-21] - 1) * 100 if n > 20 else 0.0

    # RSI: 40–65 is ideal entry zone (not overbought, momentum confirmed)
    if   rsi >= 65: rsi_s = 60
    elif rsi >= 50: rsi_s = 80
    elif rsi >= 40: rsi_s = 65
    elif rsi >= 30: rsi_s = 45
    else:           rsi_s = 20

    ret5_s  = min(100, max(0, 50 + ret5  * 5))
    ret20_s = min(100, max(0, 50 + ret20 * 2))
    mom_score = 0.4 * rsi_s + 0.35 * ret5_s + 0.25 * ret20_s

    if rsi >= 55: reasons.append(f"RSI bullish at {rsi:.0f}")
    if ret5 > 2:  reasons.append(f"Up {ret5:.1f}% this week")

    # ── Trend ─────────────────────────────────────────────────────────────────
    ema8  = _ema(c, 8)[-1]
    ema21 = _ema(c, 21)[-1]
    ema55 = _ema(c, 55)[-1] if n > 55 else ema21
    sma200 = c[-200:].mean() if n >= 200 else c.mean()

    ema_aligned = int(ema8 > ema21 > ema55)
    above_200   = int(c[-1] > sma200)
    price_200_dev = (c[-1] / sma200 - 1) * 100

    # MACD
    macd = _ema(c, 12) - _ema(c, 26)
    macd_sig = _ema(macd, 9)
    macd_bull = int(macd[-1] > macd_sig[-1])

    trend_score = (40 * ema_aligned + 30 * above_200 + 30 * macd_bull)

    if ema_aligned:  reasons.append("EMA8 > EMA21 > EMA55 (aligned)")
    if above_200:    reasons.append(f"Above SMA200 (+{price_200_dev:.1f}%)")
    if macd_bull:    reasons.append("MACD bullish crossover")

    # ── Volume ────────────────────────────────────────────────────────────────
    vol_ma20 = v[-20:].mean()
    vol_ma5  = v[-5:].mean()
    vol_ratio = v[-1] / vol_ma20  if vol_ma20 > 0 else 1.0
    vol_trend = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1.0

    vol_r_s  = min(100, max(0, 50 + (vol_ratio  - 1) * 50))
    vol_t_s  = min(100, max(0, 50 + (vol_trend  - 1) * 50))
    vol_score = 0.6 * vol_r_s + 0.4 * vol_t_s

    if vol_ratio > 1.5:   reasons.append(f"Volume surge {vol_ratio:.1f}x avg")
    elif vol_ratio > 1.2: reasons.append(f"Volume above avg ({vol_ratio:.1f}x)")

    # ── Structure ─────────────────────────────────────────────────────────────
    # Bollinger band position
    bb_mid = c[-20:].mean()
    bb_std = c[-20:].std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_range = bb_upper - bb_lower
    bb_pos = (c[-1] - bb_lower) / bb_range if bb_range > 0 else 0.5

    # Williams %R
    hh = h[-14:].max() if n >= 14 else h.max()
    ll = l[-14:].min() if n >= 14 else l.min()
    wr = ((hh - c[-1]) / (hh - ll) * -100) if hh != ll else -50

    # 52-week proximity
    high_52w = h[-min(n, 252):].max()
    dist_52w = (c[-1] / high_52w - 1) * 100   # ≤ 0

    bb_s   = min(100, max(0, bb_pos * 100))
    wr_s   = min(100, max(0, (wr + 100)))       # wr in -100..0 → 0..100
    h52_s  = min(100, max(0, 100 + dist_52w * 2))  # near high = high score

    struct_score = 0.35 * bb_s + 0.30 * wr_s + 0.35 * h52_s

    if bb_pos > 0.6:    reasons.append(f"Above BB midline (pos={bb_pos:.2f})")
    if dist_52w > -5:   reasons.append(f"Near 52-week high ({dist_52w:.1f}%)")

    # ── Final ─────────────────────────────────────────────────────────────────
    total = 0.30 * mom_score + 0.25 * trend_score + 0.20 * vol_score + 0.25 * struct_score

    if   total >= 75: signal = "STRONG BUY"
    elif total >= 60: signal = "BUY"
    elif total >= 45: signal = "NEUTRAL"
    elif total >= 30: signal = "SELL"
    else:             signal = "STRONG SELL"

    return round(total, 1), signal, reasons[:4]  # cap at 4 reasons


# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-SECTIONAL RANKING
# ═══════════════════════════════════════════════════════════════════════════════

RANK_FEATURES = [
    "ret_1d", "ret_5d", "ret_20d", "ret_60d",
    "rsi_14", "macd_cross", "bb_pos", "vol_ratio", "vol_trend",
    "momentum_10", "momentum_20", "atr_pct", "adx_14",
    "dist_52w_high", "obv_slope", "ema_cross",
    "fac_vol_z7", "fac_near_52h", "fac_mom_12_1",
]


def cross_sectional_rank(feat_map: Dict[str, dict]) -> Dict[str, dict]:
    """
    For each feature in RANK_FEATURES, compute percentile rank (0–100)
    across the universe. Returns {symbol: {feat: rank}}.
    """
    from scipy import stats as sp_stats

    symbols = list(feat_map.keys())
    ranked  = {s: {} for s in symbols}

    for feat in RANK_FEATURES:
        vals = np.array([feat_map[s].get(feat, 0.0) for s in symbols], dtype=float)
        for i, s in enumerate(symbols):
            ranked[s][feat] = float(sp_stats.percentileofscore(vals, vals[i], kind="rank"))

    return ranked


# ═══════════════════════════════════════════════════════════════════════════════
# XGBoost + LightGBM SCORING
# ═══════════════════════════════════════════════════════════════════════════════

def train_ensemble(
    histories: Dict[str, list],
    feature_engine: "FeatureEngine",
    save_path: Path = XGB_MODEL_PATH,
) -> Optional[object]:
    """Train XGBoost + LightGBM ensemble on full history."""
    if not XGB_AVAILABLE:
        print("[ENSEMBLE] XGBoost not available — skipping training")
        return None

    print("[ENSEMBLE] Building training dataset...")

    all_rows   = []
    all_labels = []
    all_syms   = []
    all_dates  = []

    # Build feature matrix (cross-sectional, rolling 20-day windows)
    sorted_dates = sorted(set(
        r["date"] for recs in histories.values() for r in recs
    ))

    # Sample every 5 trading days to avoid autocorrelation
    sample_dates = sorted_dates[60::5]

    for d_idx, d_str in enumerate(sample_dates):
        # Snap histories to this date
        snap = {}
        for sym, recs in histories.items():
            day_recs = [r for r in recs if r["date"] <= d_str]
            if len(day_recs) >= 60:
                snap[sym] = day_recs

        if len(snap) < 20:
            continue

        # Compute features for this date
        try:
            feat_df = feature_engine.compute_universe(snap)
        except Exception as e:
            continue

        if feat_df.empty:
            continue

        # Compute 5d forward return labels (cross-sectional quintile)
        future_rets = {}
        for sym in feat_df.index:
            recs = histories.get(sym, [])
            t_recs = [r for r in recs if r["date"] <= d_str]
            idx = len(t_recs) - 1
            # Find record 5 days later
            future_5d_recs = [r for r in recs if r["date"] > d_str]
            if len(future_5d_recs) < 5:
                continue
            c_now  = float(t_recs[-1].get("lp", t_recs[-1].get("close", 0)) or 0)
            c_fut  = float(future_5d_recs[4].get("lp", future_5d_recs[4].get("close", 0)) or 0)
            if c_now > 0 and c_fut > 0:
                future_rets[sym] = c_fut / c_now - 1

        if len(future_rets) < 10:
            continue

        # Quintile labels (0–4, cross-sectional)
        syms_with_rets = [s for s in feat_df.index if s in future_rets]
        if len(syms_with_rets) < 10:
            continue

        rets = np.array([future_rets[s] for s in syms_with_rets])
        quantiles = np.quantile(rets, [0.2, 0.4, 0.6, 0.8])
        labels = np.digitize(rets, quantiles)  # 0–4

        for i, sym in enumerate(syms_with_rets):
            if sym not in feat_df.index:
                continue
            row = feat_df.loc[sym].values.astype(float)
            if not np.all(np.isfinite(row)):
                row = np.nan_to_num(row, nan=0.0, posinf=1.0, neginf=-1.0)
            all_rows.append(row)
            all_labels.append(int(labels[i]))
            all_syms.append(sym)
            all_dates.append(d_str)

    if len(all_rows) < 500:
        print(f"[ENSEMBLE] Only {len(all_rows)} samples — need ≥ 500, skipping")
        return None

    X = np.array(all_rows,   dtype=np.float32)
    y = np.array(all_labels, dtype=np.int32)

    # Time-ordered 80/20 split
    split = int(len(X) * 0.85)
    X_tr, X_val = X[:split], X[split:]
    y_tr, y_val = y[:split], y[split:]

    print(f"[ENSEMBLE] Training on {len(X_tr):,} samples, val on {len(X_val):,}...")

    models = {}

    # ── XGBoost ──────────────────────────────────────────────────────────────
    if XGB_AVAILABLE:
        try:
            dtrain = xgb.DMatrix(X_tr, label=y_tr)
            dval   = xgb.DMatrix(X_val, label=y_val)
            params = {
                "objective":        "multi:softprob",
                "num_class":        5,
                "device":           "cuda",
                "tree_method":      "hist",
                "max_depth":        6,
                "learning_rate":    0.05,
                "subsample":        0.8,
                "colsample_bytree": 0.7,
                "min_child_weight": 5,
                "gamma":            0.1,
                "reg_alpha":        0.1,
                "reg_lambda":       1.0,
                "eval_metric":      "mlogloss",
                "verbosity":        0,
            }
            evals = [(dtrain, "train"), (dval, "val")]
            booster = xgb.train(
                params, dtrain,
                num_boost_round=500,
                evals=evals,
                early_stopping_rounds=50,
                verbose_eval=25,
            )
            # Metrics
            xgb_preds = booster.predict(dval).reshape(-1, 5).argmax(axis=1)
            xgb_acc   = (xgb_preds == y_val).mean()
            dir_acc   = ((xgb_preds >= 3) == (y_val >= 3)).mean()
            print(f"[XGB] Val acc={xgb_acc:.4f}  Dir acc={dir_acc:.4f}  ({booster.best_ntree_limit} trees)")
            models["xgb"] = booster
        except Exception as e:
            print(f"[XGB] Training failed: {e}")

    # ── LightGBM ─────────────────────────────────────────────────────────────
    if LGB_AVAILABLE:
        try:
            lgb_train = lgb.Dataset(X_tr, label=y_tr)
            lgb_val   = lgb.Dataset(X_val, label=y_val, reference=lgb_train)
            lgb_params = {
                "objective":         "multiclass",
                "num_class":         5,
                "device":            "gpu",
                "num_leaves":        63,
                "max_depth":         6,
                "learning_rate":     0.05,
                "feature_fraction":  0.7,
                "bagging_fraction":  0.8,
                "bagging_freq":      1,
                "min_data_in_leaf":  20,
                "lambda_l1":         0.1,
                "lambda_l2":         1.0,
                "metric":            "multi_logloss",
                "verbose":           -1,
                "force_col_wise":    True,
            }
            lgb_model = lgb.train(
                lgb_params,
                lgb_train,
                num_boost_round=500,
                valid_sets=[lgb_val],
                callbacks=[
                    lgb.early_stopping(50, verbose=False),
                    lgb.log_evaluation(25),
                ],
            )
            lgb_preds = lgb_model.predict(X_val).argmax(axis=1)
            lgb_acc   = (lgb_preds == y_val).mean()
            print(f"[LGB] Val acc={lgb_acc:.4f}  ({lgb_model.best_iteration} trees)")
            models["lgb"] = lgb_model
        except Exception as e:
            print(f"[LGB] Training failed: {e}")

    if not models:
        return None

    payload = {"models": models, "feature_names": ALL_FEATURE_NAMES if FEATURES_AVAILABLE else list(range(X.shape[1]))}
    with open(save_path, "wb") as f:
        pickle.dump(payload, f)
    print(f"[ENSEMBLE] Model saved: {save_path}")
    return payload


def ensemble_predict_all(
    feat_map: Dict[str, dict],
    model_payload: Optional[dict],
) -> Dict[str, float]:
    """
    Run XGB+LGB ensemble over all stocks.
    Returns {symbol: score 0–1} where 1 = strongest buy signal.
    Score = P(quintile=4) - P(quintile=0), rescaled to [0,1].
    """
    if model_payload is None or not feat_map:
        return {}

    models       = model_payload.get("models", {})
    feat_names   = model_payload.get("feature_names", ALL_FEATURE_NAMES if FEATURES_AVAILABLE else [])
    symbols      = list(feat_map.keys())

    # Build feature matrix
    rows = []
    valid_syms = []
    for sym in symbols:
        f = feat_map[sym]
        if FEATURES_AVAILABLE:
            row = np.array([f.get(k, 0.0) for k in feat_names], dtype=np.float32)
        else:
            row = np.array(list(f.values()), dtype=np.float32)
        row = np.nan_to_num(row, nan=0.0, posinf=1.0, neginf=-1.0)
        rows.append(row)
        valid_syms.append(sym)

    if not rows:
        return {}

    X = np.array(rows, dtype=np.float32)
    probs_list = []

    # XGBoost
    if "xgb" in models and XGB_AVAILABLE:
        try:
            booster = models["xgb"]
            booster.set_param({"device": "cpu"})
            dmat = xgb.DMatrix(X)
            p = booster.predict(dmat).reshape(-1, 5)
            probs_list.append(("xgb", p, 0.55))
        except Exception as e:
            print(f"[XGB] Inference error: {e}")

    # LightGBM
    if "lgb" in models and LGB_AVAILABLE:
        try:
            p = models["lgb"].predict(X)
            probs_list.append(("lgb", p, 0.45))
        except Exception as e:
            print(f"[LGB] Inference error: {e}")

    if not probs_list:
        return {}

    # Weighted ensemble
    if len(probs_list) == 1:
        _, probs, _ = probs_list[0]
    else:
        total_w = sum(w for _, _, w in probs_list)
        probs = sum(p * w / total_w for _, p, w in probs_list)

    # Score = P(Q4) - P(Q0), scaled to [0, 1]
    raw_scores = probs[:, 4] - probs[:, 0]   # range: approx -1 to +1
    scores = (raw_scores + 1.0) / 2.0         # scale to [0, 1]

    result = {}
    for i, sym in enumerate(valid_syms):
        result[sym] = float(np.clip(scores[i], 0.0, 1.0))

    print(f"[ENSEMBLE] {len(result)} scores computed")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# GRU SCORING
# ═══════════════════════════════════════════════════════════════════════════════

def gru_predict_all(
    histories: Dict[str, list],
    feature_engine: Optional["FeatureEngine"] = None,
) -> Dict[str, float]:
    """
    Load GRU per-stock models and score each stock.
    Returns {symbol: score 0–1}.
    """
    if not TORCH_AVAILABLE or not GRU_MODEL_DIR.exists():
        return {}

    try:
        sys.path.insert(0, str(ROOT / "ml"))
        from gru_predictor import GRUPredictor
    except ImportError:
        return {}

    scores = {}
    model_files = list(GRU_MODEL_DIR.glob("*_gru.pt"))
    if not model_files:
        return {}

    print(f"[GRU] Scoring {len(model_files)} trained models...")

    def _score_gru(model_file):
        sym = model_file.stem.replace('_gru', '')  # filename = SYMBOL_gru.pt
        if sym not in histories:
            return None
        try:
            payload = torch.load(model_file, map_location="cpu", weights_only=False)

            predictor: GRUPredictor = payload.get("model")
            if predictor is None:
                return None

            recs = histories[sym]
            if feature_engine is not None and FEATURES_AVAILABLE:
                seq = feature_engine.compute_sequence(sym, recs, lookback=LOOKBACK)
            else:
                seq = _build_gru_sequence_fallback(recs, lookback=LOOKBACK)

            if seq is None or seq.shape[0] < LOOKBACK:
                return None

            # Determine device from model parameters and move tensor there
            device = next(predictor.parameters()).device
            seq_t = torch.FloatTensor(seq).unsqueeze(0).to(device)

            predictor.eval()
            with torch.no_grad():
                logits = predictor(seq_t)
                probs  = torch.softmax(logits, dim=-1).cpu().numpy()[0]

            # Score = P(Q3) + P(Q4) — probability of top-40%
            score = float(probs[3] + probs[4])
            return sym, np.clip(score, 0.0, 1.0)
        except Exception:
            return None

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for result in pool.map(_score_gru, model_files):
                if result is not None:
                    scores[result[0]] = result[1]
    except Exception as e:
        print(f"[GRU] ThreadPool error: {e}")

    print(f"[GRU] {len(scores)} scores")
    return scores


def _build_gru_sequence_fallback(recs: list, lookback: int = 15) -> Optional[np.ndarray]:
    """Build a simple 14-feature sequence without FeatureEngine (fallback)."""
    if len(recs) < lookback + 20:
        return None

    c, h, l, o, v = get_arrays(recs)
    n = len(c)

    def safe_div(a, b):
        return a / b if b > 0 else 0.0

    seq = []
    for t in range(n - lookback, n):
        ct = c[: t + 1]
        vt = v[: t + 1]
        ht = h[: t + 1]
        lt = l[: t + 1]
        nt = len(ct)

        # 14 features matching GRU_FEATURE_NAMES
        ret_1d  = safe_div(ct[-1] - ct[-2], ct[-2])  if nt > 1  else 0.0
        ret_5d  = safe_div(ct[-1] - ct[-6], ct[-6])  if nt > 5  else 0.0
        ret_20d = safe_div(ct[-1] - ct[-21], ct[-21]) if nt > 20 else 0.0

        delta = np.diff(ct[-15:]) if nt >= 15 else np.diff(ct)
        gain  = delta[delta > 0].mean() if any(delta > 0) else 0.0
        loss  = -delta[delta < 0].mean() if any(delta < 0) else 1e-6
        rsi = 100 - 100 / (1 + gain / loss) if loss > 0 else 50.0

        ema12 = _ema(ct, 12)[-1]
        ema26 = _ema(ct, 26)[-1] if nt > 26 else ema12
        ema8  = _ema(ct, 8)[-1]
        ema21 = _ema(ct, 21)[-1] if nt > 21 else ema8
        macd_cross = safe_div(ema12 - ema26, ct[-1])
        ema_cross  = safe_div(ema8 - ema21,  ema21)

        bb_mid = ct[-20:].mean() if nt >= 20 else ct.mean()
        bb_std = ct[-20:].std()  if nt >= 20 else ct.std()
        bb_pos = safe_div(ct[-1] - (bb_mid - 2*bb_std), 4*bb_std) if bb_std > 0 else 0.5

        vm20 = vt[-20:].mean() if nt >= 20 else vt.mean()
        vm5  = vt[-5:].mean()  if nt >= 5  else vt[-1]
        vol_ratio = safe_div(vt[-1], vm20)
        vol_trend = safe_div(vm5,    vm20)

        tr = np.maximum(ht[-14:] - lt[-14:], np.abs(np.diff(ct[-15:]))) if nt >= 15 else np.array([ct[-1] * 0.01])
        atr_pct = safe_div(tr.mean(), ct[-1])

        mom10 = safe_div(ct[-1] - ct[-11], ct[-11]) if nt > 10 else 0.0
        mom20 = safe_div(ct[-1] - ct[-21], ct[-21]) if nt > 20 else 0.0

        h52  = ht[-min(nt, 252):].max()
        dist = safe_div(ct[-1] - h52, h52) if h52 > 0 else 0.0

        adx = 25.0  # placeholder

        row = np.array([
            ret_1d, ret_5d, ret_20d,
            rsi / 100, macd_cross, bb_pos,
            vol_ratio, vol_trend,
            atr_pct, ema_cross,
            mom10, mom20,
            dist, adx / 100,
        ], dtype=np.float32)
        row = np.nan_to_num(row, nan=0.0, posinf=1.0, neginf=-1.0)
        row = np.clip(row, -5.0, 5.0)
        seq.append(row)

    return np.stack(seq, axis=0) if seq else None


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE PRICES
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_live_prices() -> Dict[str, dict]:
    """
    Fetch real-time prices from Sharesansar live trading page.
    Returns {symbol: {price, change_pct, volume, lp, pc, h, l, op, q, t}}.
    """
    try:
        from src.live_prices import fetch_live_prices as _sharesansar_fetch
        raw = _sharesansar_fetch()
        # Add "price" and "change_pct" keys expected by portfolio_status / paper_trader
        live = {}
        for sym, d in raw.items():
            entry = dict(d)
            entry["price"] = float(d.get("lp", 0))
            entry["change_pct"] = float(d.get("pc", 0))
            entry["volume"] = int(d.get("q", 0))
            live[sym] = entry
        print(f"[LIVE] Fetched {len(live)} stocks from Sharesansar")
        return live
    except Exception as e:
        print(f"[LIVE] Price fetch failed: {e}")
        return {}


def save_daily_prices(live: Dict[str, dict], today: str) -> None:
    """Save today's prices to data/price_history/YYYY-MM-DD.json."""
    out = HISTORY_DIR / f"{today}.json"
    if out.exists():
        return  # already saved today
    if not live:
        return
    stocks = {sym: {k: v for k, v in d.items() if k in ("lp","pc","h","l","op","q","t")}
              for sym, d in live.items()}
    out.write_text(json.dumps({"date": today, "stocks": stocks}))
    print(f"[DATA] Saved {len(stocks)} stocks → {out.name}")


# ═══════════════════════════════════════════════════════════════════════════════
# KELLY POSITION SIZING
# ═══════════════════════════════════════════════════════════════════════════════

def kelly_size(
    score: float,
    win_rate: float = 0.55,
    win_loss_ratio: float = 1.5,
    max_fraction: float = MAX_KELLY,
) -> float:
    """
    Half-Kelly criterion for position sizing.

    Kelly fraction = (W * b - L) / b
    where W = win probability, L = loss probability, b = win/loss ratio.

    We use half-Kelly for conservatism and cap at max_fraction.
    Score (0–1) scales the base Kelly: higher score = more conviction.
    """
    L = 1.0 - win_rate
    b = win_loss_ratio
    full_kelly = max(0.0, (win_rate * b - L) / b)
    half_kelly = full_kelly * 0.5 * score  # scale by conviction
    return min(half_kelly, max_fraction)


# ═══════════════════════════════════════════════════════════════════════════════
# LLM REASONING
# ═══════════════════════════════════════════════════════════════════════════════

def llm_reason(candidates: List[dict], regime: str = "UNKNOWN") -> Dict[str, str]:
    """
    Use local Qwen2.5-14B via Ollama for qualitative rationale on top candidates.
    Returns {symbol: rationale_text}.
    """
    try:
        import urllib.request as urlreq

        def _call_llm(c):
            sym     = c["symbol"]
            score   = c.get("score", 0)
            signal  = c.get("signal", "?")
            rsi     = c.get("rsi", 50)
            ret5    = c.get("ret_5d", 0) * 100
            reasons = c.get("reasons", [])

            prompt = (
                f"You are a senior quantitative analyst at a top Nepal-focused fund. "
                f"Analyze this NEPSE stock for a short-term trade (1–5 days):\n"
                f"Symbol: {sym}\n"
                f"Signal: {signal} (score={score:.1f}/100)\n"
                f"RSI: {rsi:.0f}, 5d return: {ret5:.1f}%\n"
                f"Key signals: {'; '.join(reasons)}\n"
                f"Market regime: {regime}\n\n"
                f"Give a 2-sentence rationale. Be specific, concise, and actionable. "
                f"Mention any key risk. No fluff."
            )

            payload_bytes = json.dumps({
                "model": "qwen2.5:14b",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 100},
            }).encode()

            req = urlreq.Request(
                "http://localhost:11434/api/generate",
                data=payload_bytes,
                headers={"Content-Type": "application/json"},
            )
            with urlreq.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                return sym, result.get("response", "").strip()

        rationales = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_call_llm, c): c for c in candidates[:6]}
            for future in concurrent.futures.as_completed(futures):
                try:
                    sym, text = future.result()
                    rationales[sym] = text
                except Exception:
                    pass

        return rationales
    except Exception as e:
        print(f"[LLM] Reasoning failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO STATUS
# ═══════════════════════════════════════════════════════════════════════════════

def portfolio_status(live_prices: Dict[str, dict]) -> dict:
    """
    Compute P&L for all portfolio positions.
    Returns summary dict with total unrealized P&L, individual positions.
    """
    positions = []
    total_cost  = 0.0
    total_value = 0.0

    for sym, pos in PORTFOLIO.items():
        shares = pos["shares"]
        wacc   = pos["wacc"]
        cost   = shares * wacc

        price_data = live_prices.get(sym, {})
        price = price_data.get("price", 0.0)

        if price > 0:
            value = shares * price
            pnl   = value - cost
            pct   = (price / wacc - 1) * 100
        else:
            value = cost
            pnl   = 0.0
            pct   = 0.0

        total_cost  += cost
        total_value += value

        positions.append({
            "symbol":    sym,
            "shares":    shares,
            "wacc":      wacc,
            "price":     price,
            "value":     value,
            "pnl":       pnl,
            "pnl_pct":   pct,
            "cost":      cost,
        })

    total_pnl = total_value - total_cost
    total_pct = (total_value / total_cost - 1) * 100 if total_cost > 0 else 0.0

    return {
        "positions":   positions,
        "total_cost":  total_cost,
        "total_value": total_value,
        "total_pnl":   total_pnl,
        "total_pct":   total_pct,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ENSEMBLE RANKING
# ═══════════════════════════════════════════════════════════════════════════════

def ensemble_rank(
    ta_scores:       Dict[str, Tuple[float, str, list]],
    ml_scores:       Dict[str, float],
    gru_scores:      Dict[str, float],
    ranks:           Dict[str, dict],
    regime_mult:     float = 1.0,
    top_n:           int   = TOP_N,
) -> List[dict]:
    """
    Combine TA + ML ensemble + GRU into a final ranking.

    Weights: ML=45%, GRU=30%, TA=25%
    Each component is normalized to [0, 1] before weighting.
    Regime multiplier scales signal conviction.
    """
    all_syms = set(ta_scores.keys())

    results = []
    for sym in all_syms:
        ta_raw, signal, reasons = ta_scores.get(sym, (50, "NEUTRAL", []))
        ta_norm  = ta_raw / 100.0
        ml_norm  = ml_scores.get(sym,  0.5)
        gru_norm = gru_scores.get(sym, 0.5)

        # Weighted combination (normalize weights to available components)
        if gru_scores:
            score = W_ENSEMBLE * ml_norm + W_GRU * gru_norm + W_TA * ta_norm
        else:
            # No GRU: rebalance weights
            score = 0.60 * ml_norm + 0.40 * ta_norm

        score = float(np.clip(score * regime_mult, 0.0, 1.0))

        # Cross-sectional rank bonus (Goldman-style factor score)
        r = ranks.get(sym, {})
        rank_score = np.mean([
            r.get("momentum_10",   50),
            r.get("momentum_20",   50),
            r.get("vol_ratio",     50),
            r.get("ret_5d",        50),
            r.get("fac_near_52h",  50),
        ]) / 100.0
        # Blend rank_score into final (10%)
        final_score = 0.90 * score + 0.10 * rank_score

        # Kelly sizing
        kelly_frac = kelly_size(final_score)

        results.append({
            "symbol":       sym,
            "score":        round(final_score * 100, 1),
            "signal":       signal,
            "ta_score":     round(ta_raw, 1),
            "ml_score":     round(ml_norm * 100, 1),
            "gru_score":    round(gru_norm * 100, 1) if sym in gru_scores else None,
            "kelly_pct":    round(kelly_frac * 100, 1),
            "reasons":      reasons,
        })

    # Sort descending, take top N
    results.sort(key=lambda x: x["score"], reverse=True)

    # Filter out STRONG SELL / SELL signals from top picks
    buy_results = [r for r in results if r["signal"] not in ("STRONG SELL", "SELL")]
    top = buy_results[:top_n]

    # If not enough buys, fill with remaining
    if len(top) < top_n:
        extras = [r for r in results if r not in top]
        top.extend(extras[: top_n - len(top)])

    return top[:top_n]


# ═══════════════════════════════════════════════════════════════════════════════
# WALK-FORWARD BACKTEST
# ═══════════════════════════════════════════════════════════════════════════════

def walk_forward_backtest(histories: Dict[str, list]) -> dict:
    """
    Simple walk-forward backtest of the ensemble strategy.

    Assumes we pick Top-5 stocks every Friday (or last trading day of week),
    hold for 5 trading days (1 week), pay 0.5% commission each side.

    Returns performance metrics.
    """
    print("[BACKTEST] Running walk-forward simulation...")

    sorted_dates = sorted(set(
        r["date"] for recs in histories.values() for r in recs
    ))

    weekly_dates = sorted_dates[200::5]   # every 5 trading days
    portfolio_value = 100.0               # normalized to 100
    trades = []
    equity_curve = [(sorted_dates[200], 100.0)]

    for i, d_str in enumerate(weekly_dates[:-1]):
        next_d = weekly_dates[i + 1]

        # Get universe snapshot at d_str
        snap = {}
        for sym, recs in histories.items():
            day_recs = [r for r in recs if r["date"] <= d_str]
            if len(day_recs) >= 60:
                snap[sym] = day_recs

        if len(snap) < 10:
            continue

        # Quick TA scoring
        ta_raw = {}
        for sym, recs in snap.items():
            try:
                sc, sig, _ = compute_ta_score(recs)
                ta_raw[sym] = (sc, sig, [])
            except Exception:
                ta_raw[sym] = (50, "NEUTRAL", [])

        # Pick top 5 by TA score
        top5 = sorted(ta_raw.keys(), key=lambda s: ta_raw[s][0], reverse=True)[:5]

        # Compute forward returns
        week_rets = []
        for sym in top5:
            recs = histories.get(sym, [])
            cur_recs  = [r for r in recs if r["date"] <= d_str]
            next_recs = [r for r in recs if r["date"] <= next_d]
            if len(cur_recs) < 1 or len(next_recs) < 1:
                continue
            c_now = float(cur_recs[-1].get("lp", cur_recs[-1].get("close", 0)) or 0)
            c_fut = float(next_recs[-1].get("lp", next_recs[-1].get("close", 0)) or 0)
            if c_now > 0 and c_fut > 0:
                ret = (c_fut / c_now - 1) - 2 * COMMISSION  # round-trip cost
                week_rets.append(ret)

        if week_rets:
            avg_ret = np.mean(week_rets)
            portfolio_value *= (1 + avg_ret)
            trades.append({"date": d_str, "return": avg_ret, "picks": top5})
            equity_curve.append((next_d, round(portfolio_value, 2)))

    if not trades:
        return {"error": "not enough data"}

    returns = np.array([t["return"] for t in trades])
    total_return = (portfolio_value - 100) / 100
    win_rate     = (returns > 0).mean()
    sharpe       = returns.mean() / (returns.std() + 1e-6) * np.sqrt(52)  # annualized

    # Max drawdown
    equity = np.array([e[1] for e in equity_curve])
    peak   = np.maximum.accumulate(equity)
    dd     = (equity - peak) / peak
    max_dd = float(dd.min())

    print(f"[BACKTEST] Total return: {total_return*100:.1f}%  Sharpe: {sharpe:.2f}  "
          f"Win rate: {win_rate*100:.0f}%  Max DD: {max_dd*100:.1f}%")

    return {
        "total_return": round(total_return * 100, 2),
        "sharpe":       round(sharpe, 3),
        "win_rate":     round(win_rate * 100, 1),
        "max_drawdown": round(max_dd * 100, 2),
        "n_trades":     len(trades),
        "equity_curve": equity_curve[-20:],   # last 20 data points
    }


# ═══════════════════════════════════════════════════════════════════════════════
# EMAIL REPORT
# ═══════════════════════════════════════════════════════════════════════════════

def build_html_email(
    top_picks:    List[dict],
    portfolio:    dict,
    live:         Dict[str, dict],
    regime:       str,
    regime_conf:  float,
    rationales:   Dict[str, str],
    backtest:     Optional[dict] = None,
    scan_date:    Optional[str]  = None,
    paper_html:          str = "",    # paper trading card
    tracker_summary:     str = "",    # signal tracker stats
    ai_picks_html:       str = "",    # AI top 3 picks
    portfolio_advice_html: str = "",  # AI portfolio advisor
    corp_events_html:      str = "",  # corporate events warnings
) -> str:
    """Generate a clean, concise HTML email report."""
    if scan_date is None:
        scan_date = date.today().strftime("%Y-%m-%d")

    # Signal color map
    sig_colors = {
        "STRONG BUY": "#00c853",
        "BUY":        "#69f0ae",
        "NEUTRAL":    "#ffd740",
        "SELL":       "#ff6d00",
        "STRONG SELL":"#d50000",
    }
    regime_colors = {"BULL": "#00c853", "RANGE": "#ffd740", "BEAR": "#d50000"}

    rows_html = ""
    for i, p in enumerate(top_picks, 1):
        sym     = p["symbol"]
        score   = p["score"]
        signal  = p["signal"]
        ta_s    = p["ta_score"]
        ml_s    = p["ml_score"]
        gru_s   = p.get("gru_score")
        kelly   = p["kelly_pct"]
        reasons = p.get("reasons", [])
        rat     = rationales.get(sym, "")

        live_d  = live.get(sym, {})
        lp      = live_d.get("price", "–")
        pct_c   = live_d.get("change_pct", "–")

        gru_cell = f"{gru_s:.0f}" if gru_s is not None else "–"
        color    = sig_colors.get(signal, "#888")
        reasons_str = " · ".join(reasons) if reasons else "–"

        rat_html = f'<div class="pick-rationale">{rat}</div>' if rat else ""
        rows_html += f"""
          <div class="pick-card">
            <div class="pick-header">
              <span class="pick-sym">{i}. {sym}</span>
              <span class="pick-badge" style="background:{color}">{signal}</span>
              <span class="pick-score">{score:.0f}</span>
            </div>
            <div class="pick-metrics">
              TA {ta_s:.0f} &middot; ML {ml_s:.0f} &middot; GRU {gru_cell} &middot; Kelly {kelly:.1f}%
            </div>
            <div class="pick-reasons">{reasons_str}</div>
            {rat_html}
          </div>
        """

    # Portfolio table
    port_rows = ""
    for pos in portfolio.get("positions", []):
        pnl_cls = "pnl-pos" if pos["pnl"] >= 0 else "pnl-neg"
        pnl_arrow = "&#x25B2;" if pos["pnl"] >= 0 else "&#x25BC;"
        live_p = live.get(pos["symbol"], {}).get("price", "–")
        port_rows += f"""
        <tr>
          <td style="padding:6px 8px;font-weight:600;font-size:13px;">{pos["symbol"]}<br><span style="font-weight:400;font-size:11px;color:#888;">{pos["shares"]:,} @ {pos["wacc"]:.0f}</span></td>
          <td style="padding:6px 8px;text-align:right;font-size:13px;">{live_p}</td>
          <td style="padding:6px 8px;text-align:right;font-size:13px;" class="{pnl_cls}">{pnl_arrow} {pos["pnl_pct"]:+.1f}%<br><span style="font-size:11px;">Rs {pos["pnl"]:,.0f}</span></td>
        </tr>
        """

    total_pct = portfolio.get("total_pct", 0)
    total_pnl = portfolio.get("total_pnl", 0)
    total_col = "#c62828" if total_pnl < 0 else "#1b5e20"

    regime_color = regime_colors.get(regime, "#888")

    bt_html = ""
    if backtest and "total_return" in backtest:
        bt_html = f"""
        <h3 style="color:#333;margin-top:24px">Walk-Forward Backtest (TA-only, {backtest["n_trades"]} trades)</h3>
        <table style="font-family:Arial;font-size:13px;border-collapse:collapse">
          <tr><td style="padding:4px 12px">Total Return</td><td style="font-weight:bold">{backtest["total_return"]:+.1f}%</td></tr>
          <tr><td style="padding:4px 12px">Sharpe Ratio</td><td style="font-weight:bold">{backtest["sharpe"]:.2f}</td></tr>
          <tr><td style="padding:4px 12px">Win Rate</td><td style="font-weight:bold">{backtest["win_rate"]:.0f}%</td></tr>
          <tr><td style="padding:4px 12px">Max Drawdown</td><td style="font-weight:bold;color:#c62828">{backtest["max_drawdown"]:.1f}%</td></tr>
        </table>
        """

    # Regime emoji and description
    _regime_info = {
        "BULL": ("&#x1F7E2;", "Bullish — strong breadth, risk on"),
        "RANGE": ("&#x1F7E1;", "Ranging — mixed signals, selective"),
        "BEAR": ("&#x1F534;", "Bearish — defensive, reduce exposure"),
    }
    regime_dot, regime_desc = _regime_info.get(regime, ("&#x26AA;", "Unknown"))

    n_picks = len(top_picks)
    avg_score = sum(p["score"] for p in top_picks) / n_picks if n_picks else 0
    max_kelly = max((p["kelly_pct"] for p in top_picks), default=0)

    # Build tracker HTML outside f-string to avoid backslash limitation
    if tracker_summary:
        _ts_escaped = tracker_summary.replace("<", "&lt;").replace(">", "&gt;")
        _tracker_html = (
            "<div class='card'><div class='section'>"
            "<h2>Signal Performance Tracker (30d)</h2>"
            '<pre style="font-size:12px;line-height:1.5;color:#333">'
            + _ts_escaped
            + "</pre></div></div>"
        )
    else:
        _tracker_html = ""

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; background:#f0f2f5; margin:0; padding:0; }}
        .wrap {{ max-width:600px; margin:0 auto; }}
        .card {{ background:#fff; border-radius:12px; box-shadow:0 1px 6px rgba(0,0,0,0.1); padding:0; margin-bottom:12px; overflow:hidden; }}
        .header {{ background: linear-gradient(135deg, #1a237e 0%, #283593 50%, #3949ab 100%); color:#fff; padding:20px 16px; }}
        .header h1 {{ margin:0; font-size:18px; font-weight:600; letter-spacing:0.3px; }}
        .header .sub {{ color:#b3c0ff; font-size:12px; margin-top:4px; }}
        .section {{ padding:12px 16px; }}
        .section h2 {{ font-size:15px; color:#333; margin:0 0 12px 0; font-weight:600; }}
        table {{ width:100%; border-collapse:collapse; }}
        .pnl-pos {{ color:#1b5e20; font-weight:600; }}
        .pnl-neg {{ color:#c62828; font-weight:600; }}
        .port-total {{ font-weight:700; background:#f5f6fa; }}
        .footer {{ text-align:center; padding:16px; color:#999; font-size:11px; line-height:1.6; }}
        .pick-card {{ background:#fafbff; border:1px solid #e8eaf6; border-radius:8px; padding:12px; margin-bottom:8px; }}
        .pick-header {{ margin-bottom:6px; }}
        .pick-sym {{ font-weight:700; font-size:16px; color:#1a237e; }}
        .pick-badge {{ display:inline-block; padding:2px 8px; border-radius:10px; font-size:10px; font-weight:600; color:#fff; vertical-align:middle; margin-left:6px; }}
        .pick-score {{ float:right; font-size:20px; font-weight:700; color:#1a237e; }}
        .pick-metrics {{ font-size:12px; color:#666; margin:4px 0; }}
        .pick-reasons {{ font-size:11px; color:#888; margin-top:4px; }}
        .pick-rationale {{ font-size:12px; color:#555; font-style:italic; margin-top:6px; padding-top:6px; border-top:1px solid #e8eaf6; line-height:1.4; }}
      </style>
    </head>
    <body>
    <div class="wrap">

      <!-- Header -->
      <div class="card">
        <div class="header">
          <h1>NEPSE AutoScan &mdash; {scan_date}</h1>
          <div class="sub">Automated ML Stock Scanner &middot; Daily Report</div>
        </div>

        <!-- Summary stats (table-based, 2x2 grid for mobile) -->
        <table style="width:100%;border-collapse:collapse;">
          <tr>
            <td style="width:50%;text-align:center;padding:12px 8px;border-right:1px solid #eee;border-bottom:1px solid #eee;">
              <div style="font-size:18px;font-weight:700;color:#1a237e;">{regime_dot} {regime}</div>
              <div style="font-size:10px;color:#888;text-transform:uppercase;margin-top:2px;">Regime ({regime_conf:.0%})</div>
            </td>
            <td style="width:50%;text-align:center;padding:12px 8px;border-bottom:1px solid #eee;">
              <div style="font-size:18px;font-weight:700;color:#1a237e;">{n_picks}</div>
              <div style="font-size:10px;color:#888;text-transform:uppercase;margin-top:2px;">Top Picks</div>
            </td>
          </tr>
          <tr>
            <td style="text-align:center;padding:12px 8px;border-right:1px solid #eee;">
              <div style="font-size:18px;font-weight:700;color:#1a237e;">{avg_score:.0f}</div>
              <div style="font-size:10px;color:#888;text-transform:uppercase;margin-top:2px;">Avg Score</div>
            </td>
            <td style="text-align:center;padding:12px 8px;">
              <div style="font-size:18px;font-weight:700;color:#1a237e;">{max_kelly:.1f}%</div>
              <div style="font-size:10px;color:#888;text-transform:uppercase;margin-top:2px;">Max Kelly</div>
            </td>
          </tr>
        </table>

        <div style="padding:8px 16px;background:#f8f9ff;font-size:12px;color:#666;border-bottom:1px solid #eee">
          {regime_desc}
        </div>
      </div>

      <!-- Top Picks (card-based for mobile) -->
      <div class="card">
        <div class="section">
          <h2>Top {TOP_N} Picks</h2>
          {rows_html}
        </div>
      </div>

      <!-- AI Top 3 Picks -->
      {ai_picks_html}

      <!-- Portfolio -->
      <div class="card">
        <div class="section">
          <h2>Portfolio Status</h2>
          <table>
            <tr style="background:#f5f6fa;">
              <th style="padding:6px 8px;text-align:left;font-size:11px;color:#555;">Stock</th>
              <th style="padding:6px 8px;text-align:right;font-size:11px;color:#555;">LTP</th>
              <th style="padding:6px 8px;text-align:right;font-size:11px;color:#555;">P&amp;L</th>
            </tr>
            {port_rows}
            <tr class="port-total">
              <td style="padding:8px;font-weight:700;">Total</td>
              <td style="padding:8px;text-align:right;"></td>
              <td style="padding:8px;text-align:right;font-weight:700;color:{total_col};">{total_pct:+.1f}% (Rs {total_pnl:,.0f})</td>
            </tr>
          </table>
        </div>
      </div>

      <!-- Corporate Events Warnings -->
      {corp_events_html}

      <!-- AI Portfolio Advisor -->
      {portfolio_advice_html}

      <!-- Paper Trading -->
      {paper_html}

      <!-- Signal Tracker -->
      {_tracker_html}

      {bt_html}

      <!-- Footer -->
      <div class="footer">
        &#x26A1; Generated by <strong>NEPSE AutoScan</strong><br>
        {datetime.now().strftime("%Y-%m-%d %H:%M")} NPT &middot;
        XGBoost + LightGBM + GRU + TA &middot; Regime: {regime}<br>
        <span style="color:#bbb">Models retrained daily &middot; Signal tracking active</span>
      </div>

    </div>
    </body>
    </html>
    """


def send_email(subject: str, html_body: str) -> bool:
    """Send HTML email via configured SMTP."""
    if not EMAIL_FROM or not EMAIL_TO or not EMAIL_PASSWORD:
        print("[EMAIL] Credentials not configured — skipping")
        return False
    try:
        import smtplib
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print(f"[EMAIL] Sent to {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"[EMAIL] Failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def run_scanner(
    send_email_flag: bool = True,
    train_xgb_flag:  bool = False,
    train_gru_flag:  bool = False,
    run_backtest:    bool = False,
):
    t0 = time.time()
    today = date.today().strftime("%Y-%m-%d")
    print(f"\n{'='*65}")
    print(f"  NEPSE ENSEMBLE SCANNER — {today}")
    print(f"{'='*65}\n")

    # ── Step 1: Load data ─────────────────────────────────────────────────────
    print("[1/9] Loading price history...")
    histories = load_all_history()
    print(f"  {len(histories)} stocks loaded (≥{MIN_DAYS} days)")
    histories = apply_liquidity_filter(histories)
    print(f"  {len(histories)} stocks after liquidity/price/fund filter")

    # ── Step 1b: Data quality gate ────────────────────────────────────────────
    try:
        from data_quality import DataQualityGate
        dq = DataQualityGate()
        passed, warnings = dq.check_all(histories)
        if not passed:
            print("[ABORT] Data quality check failed:")
            for w in warnings:
                print("  " + w)
            return
        if warnings:
            print("[WARN] Data quality issues:")
            for w in warnings:
                print("  " + w)
    except ImportError:
        print("[DATA] DataQualityGate not available -- skipping quality checks")
    except Exception as e:
        print(f"[DATA] Quality check error: {e} -- continuing anyway")

    # ── Step 2: Feature engineering ───────────────────────────────────────────
    print("[2/9] Computing features...")
    engine = None
    N_FEATURES_ALL_VAL = len(ALL_FEATURE_NAMES) if FEATURES_AVAILABLE else 5
    if FEATURES_AVAILABLE:
        engine = FeatureEngine(SECTORS_FILE, CALENDAR_FILE)
        feat_df = engine.compute_universe(histories)
        feat_map = feat_df.to_dict(orient="index")
        print(f"  {len(feat_map)} stocks passed liquidity filter ({N_FEATURES_ALL_VAL} features)")
    else:
        # Fallback: basic feature dict from existing logic
        feat_map = {}
        for sym, recs in histories.items():
            c, _, _, _, v = get_arrays(recs)
            if v[-20:].mean() < MIN_VOLUME or c[-1] < MIN_PRICE or c[-1] > MAX_PRICE:
                continue
            n = len(c)
            feat_map[sym] = {
                "ret_1d":      (c[-1]/c[-2]-1)  if n > 1  else 0.0,
                "ret_5d":      (c[-1]/c[-6]-1)  if n > 5  else 0.0,
                "ret_20d":     (c[-1]/c[-21]-1) if n > 20 else 0.0,
                "vol_ratio":   float(v[-1] / (v[-20:].mean() + 1e-6)),
                "vol_trend":   float(v[-5:].mean() / (v[-20:].mean() + 1e-6)),
            }
        print(f"  {len(feat_map)} stocks (basic features — install ml/features.py for full set)")

    # ── Step 3: Market regime ──────────────────────────────────────────────────
    print("[3/9] Detecting market regime...")
    regime      = "RANGE"
    regime_conf = 0.5
    regime_mult = 1.0
    if REGIME_AVAILABLE:
        try:
            monitor = MarketRegimeMonitor(REGIME_MODEL)
            regime_result = monitor.update(histories)
            regime      = regime_result.get("regime",     "RANGE")
            regime_conf = regime_result.get("confidence", 0.5)
            regime_mult = regime_result.get("multiplier", 1.0)
            print(f"  Regime: {regime} (conf={regime_conf:.0%}, mult={regime_mult:.1f}x)")
        except Exception as e:
            print(f"  Regime detection failed: {e}")
    else:
        # Simple rule-based fallback
        all_rets = [
            (recs[-1].get("lp", 0) / recs[-21].get("lp", recs[-1].get("lp", 1)) - 1)
            for recs in histories.values() if len(recs) > 20
        ]
        breadth = np.mean([r > 0 for r in all_rets]) if all_rets else 0.5
        market_ret = float(np.median(all_rets)) if all_rets else 0.0
        if breadth > 0.60 and market_ret > 0.02:
            regime, regime_conf, regime_mult = "BULL",  0.7, 1.2
        elif breadth < 0.40 or market_ret < -0.02:
            regime, regime_conf, regime_mult = "BEAR",  0.7, 0.4
        else:
            regime, regime_conf, regime_mult = "RANGE", 0.6, 0.8
        print(f"  Regime: {regime} (breadth={breadth:.0%}, market={market_ret*100:.1f}%)")

    # ── Step 4: ML ensemble training (optional) ────────────────────────────────
    if train_xgb_flag:
        print("[4/9] Training XGBoost + LightGBM ensemble...")
        if FEATURES_AVAILABLE:
            model_payload = train_ensemble(histories, engine, XGB_MODEL_PATH)
        else:
            print("  Skipping — ml/features.py not available")
            model_payload = None
    else:
        print("[4/9] Loading ensemble model...")
        model_payload = None
        if XGB_MODEL_PATH.exists():
            try:
                with open(XGB_MODEL_PATH, "rb") as f:
                    model_payload = pickle.load(f)
                # Handle legacy format (just a booster)
                if not isinstance(model_payload, dict):
                    model_payload = {"models": {"xgb": model_payload}, "feature_names": []}
                n_models = len(model_payload.get("models", {}))
                print(f"  Loaded ({n_models} model(s))")
            except Exception as e:
                print(f"  Load failed: {e}")
        else:
            print("  No model found — use --train-xgb to train")

    # ── Step 5: ML scoring ────────────────────────────────────────────────────
    print("[5/9] Scoring with ML ensemble...")
    ml_scores = ensemble_predict_all(feat_map, model_payload)

    # ── Step 6: GRU scoring ───────────────────────────────────────────────────
    print("[6/9] GRU predictions...")
    feat_engine_for_gru = engine if FEATURES_AVAILABLE else None
    gru_scores = gru_predict_all(histories, feat_engine_for_gru)

    if train_gru_flag:
        print("  Launching GRU training in background...")
        gru_train_script = ROOT / "ml" / "gru_predictor.py"
        if gru_train_script.exists():
            import subprocess
            subprocess.Popen(
                [sys.executable, str(gru_train_script), "--train"],
                stdout=open(ROOT / "data" / "models" / "gru_train.log", "w"),
                stderr=subprocess.STDOUT,
            )
            print("  GRU training launched (see data/models/gru_train.log)")

    # ── Step 7: Technical analysis ────────────────────────────────────────────
    print("[7/9] Technical analysis scoring...")
    ta_scores: Dict[str, Tuple[float, str, list]] = {}

    def _score_ta(sym_recs):
        sym, recs = sym_recs
        try:
            sc, sig, reasons = compute_ta_score(recs)
            return sym, (sc, sig, reasons)
        except Exception:
            return sym, (50.0, "NEUTRAL", [])

    ta_items = [(sym, recs) for sym, recs in histories.items() if sym in feat_map]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for sym, result in pool.map(_score_ta, ta_items):
                ta_scores[sym] = result
    except Exception as e:
        print(f"[TA] ThreadPool error: {e}")

    # ── Step 7b: Cross-sectional ranking ──────────────────────────────────────
    ranks = cross_sectional_rank(feat_map)

    # ── Step 8: Ensemble ranking + Kelly sizing ────────────────────────────────
    print("[8/9] Ensemble ranking + Kelly sizing...")
    # Request extra picks so sector cap filtering still yields TOP_N
    top_picks = ensemble_rank(ta_scores, ml_scores, gru_scores, ranks, regime_mult, TOP_N * 3)

    # ── Sector diversification cap ───────────────────────────────────────────
    try:
        _sec_data = json.loads(Path(SECTORS_FILE).read_text())
        _sym_to_sector = _sec_data.get("symbol_to_sector", {})
    except Exception:
        _sym_to_sector = {}
    top_picks = apply_sector_cap(top_picks, _sym_to_sector, max_per_sector=3)
    top_picks = top_picks[:TOP_N]

    # ── Corporate events filter (exclude book closure within 3 trading days) ─
    corp_events_warnings = []
    try:
        sys.path.insert(0, str(ROOT / "scrapers"))
        from corporate_events import CorporateEventScraper
        _event_scraper = CorporateEventScraper()
        _exclusions = _event_scraper.get_exclusion_list(days_threshold=5)
        if _exclusions:
            _before = len(top_picks)
            top_picks = [p for p in top_picks if p["symbol"] not in _exclusions]
            _removed = _before - len(top_picks)
            if _removed:
                print(f"  Removed {_removed} picks near book closure: {', '.join(_exclusions)}")
        corp_events_warnings = _event_scraper.format_warnings(top_picks)
        if corp_events_warnings:
            print("  Corporate event warnings:")
            for w in corp_events_warnings:
                print(f"    {w}")
    except ImportError:
        print("[EVENTS] CorporateEventScraper not available -- skipping")
    except Exception as e:
        print(f"[EVENTS] Error checking corporate events: {e}")

    # ── Drawdown circuit breaker (scale Kelly sizing) ────────────────────────
    if SIGNAL_TRACKER_AVAILABLE:
        try:
            _tracker_stats = SignalTracker().get_stats(lookback_days=30)
            for p in top_picks:
                p["kelly_pct"] = round(
                    apply_drawdown_brake(p["kelly_pct"], _tracker_stats), 1
                )
        except Exception:
            pass  # signal tracker unavailable or empty -- keep original Kelly

    # ── Signal tracking ─────────────────────────────────────────────────────
    tracker_stats = {}
    tracker_summary = ""
    try:
        from signal_tracker import SignalTracker
        tracker = SignalTracker()
        tracker.log_signals(today, [{
            'symbol': p['symbol'], 'signal': p['signal'], 'score': p['score'],
            'ta': p['ta_score'], 'ml': p['ml_score'],
            'gru': p.get('gru_score'), 'kelly_pct': p['kelly_pct'],
        } for p in top_picks])
        tracker.evaluate_pending(histories)
        tracker_stats = tracker.get_stats(30)
        tracker_summary = tracker.summary_text(30)
    except ImportError:
        print("[TRACKER] SignalTracker not available -- skipping")
    except Exception as e:
        print(f"[TRACKER] Error: {e}")

    # Enrich with per-stock feature data
    for p in top_picks:
        sym  = p["symbol"]
        feat = feat_map.get(sym, {})
        recs = histories.get(sym, [])
        c, _, _, _, v = get_arrays(recs) if recs else (np.array([0]), *([np.array([0])] * 4))
        p["rsi"]    = feat.get("rsi_14",  _rsi(c, 14) if len(c) > 14 else 50)
        p["ret_5d"] = feat.get("ret_5d",  0.0)
        p["price"]  = float(c[-1]) if len(c) > 0 else 0.0

    # ── Step 8b: Live prices + save to history ────────────────────────────────
    live = fetch_live_prices()
    save_daily_prices(live, today)
    for p in top_picks:
        ld = live.get(p["symbol"], {})
        if ld.get("price"):
            p["price"]     = ld["price"]
            p["change_pct"] = ld.get("change_pct", 0.0)

    # ── Paper trading ────────────────────────────────────────────────────────
    paper_html = ""
    try:
        from paper_trader import PaperTrader
        pt = PaperTrader()
        # Build {symbol: price} dict for PaperTrader.process_signals
        _live_price_map = {sym: d.get("price", 0) for sym, d in live.items()}
        pt.process_signals(today, top_picks, _live_price_map)
        paper_html = pt.summary_html()
    except ImportError:
        print("[PAPER] PaperTrader not available -- skipping")
    except Exception as e:
        print("[PAPER] Error: %s" % e)
        paper_html = ""

    # ── News Intelligence ──────────────────────────────────────────────────
    news_html = ""
    try:
        from llm.news_intelligence import run_news_analysis, format_email_html
        news_analysis = run_news_analysis(regime=regime, send_telegram=True)
        if news_analysis:
            news_html = format_email_html(news_analysis)
    except Exception as e:
        print("[NEWS] Skipped: %s" % e)

    # ── Step 9: LLM reasoning (Claude first, Qwen fallback) ─────────────────
    print("[9/9] LLM reasoning...")
    rationales = {}
    try:
        from llm.claude_analyst import generate_rationales
        rationales = generate_rationales(top_picks, regime)
        if rationales:
            print("  Claude Sonnet 4.6: %d rationales" % len(rationales))
    except Exception as e:
        print("  Claude unavailable: %s" % e)

    # Fall back to Qwen for any missing rationales
    if len(rationales) < min(6, len(top_picks)):
        qwen_rationales = llm_reason(top_picks, regime)
        for sym, text in qwen_rationales.items():
            if sym not in rationales:
                rationales[sym] = text
        if qwen_rationales:
            print("  Qwen fallback: %d rationales" % len(qwen_rationales))

    # ── AI Top 3 Picks (RSI 45-65 screener) ─────────────────────────────────
    ai_picks_html = ""
    try:
        ai_candidates = []
        for sym, recs in histories.items():
            if sym not in feat_map:
                continue
            c, h, l, o, v = get_arrays(recs)
            if len(c) < 55:
                continue
            rsi = _rsi(c, 14)
            ema8 = _ema(c, 8)[-1]
            ema21 = _ema(c, 21)[-1]
            ema55 = _ema(c, 55)[-1]
            # RSI 45-65, EMA aligned, moderate momentum
            if 45 <= rsi <= 65 and ema8 > ema21 > ema55:
                ret5 = (c[-1] / c[-6] - 1) * 100 if len(c) > 5 else 0
                vol_ratio = float(v[-1] / (v[-20:].mean() + 1e-6))
                # Moderate momentum: 5d return between 0% and 8%
                if 0 < ret5 < 8 and vol_ratio > 0.8:
                    score_val = ta_scores.get(sym, (50, "NEUTRAL", []))[0]
                    ai_candidates.append({
                        "symbol": sym,
                        "rsi": round(rsi, 1),
                        "ret5": round(ret5, 1),
                        "vol_ratio": round(vol_ratio, 1),
                        "ta_score": score_val,
                        "price": float(c[-1]),
                    })
        # Sort by TA score, take top 3
        ai_candidates.sort(key=lambda x: x["ta_score"], reverse=True)
        ai_top3 = ai_candidates[:3]

        if ai_top3:
            rows = ""
            for i, pick in enumerate(ai_top3, 1):
                rows += (
                    f"<tr>"
                    f"<td style='text-align:center;font-weight:700;color:#1a237e'>{i}</td>"
                    f"<td style='font-weight:700'>{pick['symbol']}</td>"
                    f"<td style='text-align:center'>{pick['rsi']:.0f}</td>"
                    f"<td style='text-align:center'>{pick['ret5']:+.1f}%</td>"
                    f"<td style='text-align:center'>{pick['vol_ratio']:.1f}x</td>"
                    f"<td style='text-align:center;font-weight:600'>{pick['ta_score']:.0f}</td>"
                    f"</tr>"
                )
            ai_picks_html = f"""
            <div class="card">
              <div class="section">
                <h2>AI Top 3 -- Ideal Entry Zone (RSI 45-65, EMA Aligned)</h2>
                <p style="font-size:12px;color:#666;margin:0 0 8px 0">
                  Stocks with moderate RSI, aligned EMAs, and positive momentum -- optimal entry conditions.
                </p>
                <table>
                  <tr><th style="text-align:center">#</th><th>Symbol</th>
                      <th style="text-align:center">RSI</th>
                      <th style="text-align:center">5d Ret</th>
                      <th style="text-align:center">Vol Ratio</th>
                      <th style="text-align:center">TA Score</th></tr>
                  {rows}
                </table>
              </div>
            </div>
            """
    except Exception as e:
        print(f"[AI-PICKS] Error generating AI top 3: {e}")
        ai_picks_html = ""

    # ── AI Portfolio Advisor (LLM) ────────────────────────────────────────────
    portfolio_advice_html = ""
    try:
        sys.path.insert(0, str(ROOT / "llm"))
        from multi_agent import agent_portfolio_advisor

        # Build portfolio list for the advisor
        _port_list = []
        for sym, pos_data in PORTFOLIO.items():
            ld = live.get(sym, {})
            ltp = ld.get("price", 0)
            wacc = pos_data["wacc"]
            shares = pos_data["shares"]
            pnl_pct = ((ltp / wacc) - 1) * 100 if wacc > 0 and ltp > 0 else 0
            _port_list.append({
                "symbol": sym,
                "shares": shares,
                "wacc": wacc,
                "ltp": ltp,
                "pnl_pct": round(pnl_pct, 2),
                "current_value": round(ltp * shares, 2) if ltp > 0 else 0,
                "total_cost": round(wacc * shares, 2),
            })

        # Build summary strings for the advisor
        _picks_str = ", ".join(
            f"{p['symbol']}({p['signal']}, score={p['score']:.0f})"
            for p in top_picks[:6]
        )
        advice_text = agent_portfolio_advisor(
            _port_list, regime, _picks_str, _picks_str, ""
        )
        if advice_text:
            # Convert plain text to HTML (preserve line breaks)
            advice_escaped = (
                advice_text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br>")
            )
            portfolio_advice_html = f"""
            <div class="card">
              <div class="section">
                <h2>AI Portfolio Advisor</h2>
                <div style="font-size:13px;line-height:1.6;color:#333">{advice_escaped}</div>
              </div>
            </div>
            """
    except ImportError:
        print("[ADVISOR] LLM multi_agent not available -- skipping portfolio advice")
    except Exception as e:
        print(f"[ADVISOR] Error: {e}")
        portfolio_advice_html = ""

    # ── Walk-forward backtest (optional) ──────────────────────────────────────
    bt_result = None
    if run_backtest:
        bt_result = walk_forward_backtest(histories)

    # ── Portfolio status ───────────────────────────────────────────────────────
    port = portfolio_status(live)

    # ── Print / Email ─────────────────────────────────────────────────────────
    elapsed = time.time() - t0
    print(f"\n{'─'*65}")
    print(f"  Market Regime: {regime} (confidence {regime_conf:.0%})")
    print(f"  Universe: {len(feat_map)} stocks analyzed  |  {len(ml_scores)} ML scores  |  {len(gru_scores)} GRU scores")
    print(f"{'─'*65}")
    print(f"\n  TOP {TOP_N} PICKS  ({today})\n")

    for i, p in enumerate(top_picks, 1):
        gru_str = f"GRU={p['gru_score']:.0f}  " if p.get("gru_score") else ""
        rat = rationales.get(p["symbol"], "")
        rat_preview = (rat[:80] + "…") if len(rat) > 80 else rat
        print(f"  {i:2d}. {p['symbol']:<8}  Score={p['score']:.0f}/100  {p['signal']:<12}  "
              f"TA={p['ta_score']:.0f}  ML={p['ml_score']:.0f}  {gru_str}"
              f"Kelly={p['kelly_pct']:.1f}%")
        if p["reasons"]:
            print(f"        {' · '.join(p['reasons'][:3])}")
        if rat_preview:
            print(f"        [AI] {rat_preview}")

    print(f"\n  PORTFOLIO STATUS")
    for pos in port["positions"]:
        arrow = "▲" if pos["pnl"] >= 0 else "▼"
        print(f"  {pos['symbol']:<8}  {pos['shares']:>5} shares @ {pos['wacc']:.0f}  "
              f"{arrow} {pos['pnl_pct']:+.1f}%  Rs {pos['pnl']:+,.0f}")
    print(f"  {'─'*50}")
    print(f"  Total P&L: Rs {port['total_pnl']:+,.0f}  ({port['total_pct']:+.1f}%)")
    print(f"\n  Scan completed in {elapsed:.1f}s")

    # Build corporate events warning HTML
    _corp_html = ""
    if corp_events_warnings:
        _warn_rows = "".join(
            f"<li style='margin:4px 0;color:#e65100'>{w}</li>"
            for w in corp_events_warnings
        )
        _corp_html = f"""
        <div class="card">
          <div class="section">
            <h2>Corporate Events -- Upcoming</h2>
            <ul style="margin:0;padding-left:20px;font-size:13px">{_warn_rows}</ul>
          </div>
        </div>
        """

    # Build and send email
    html = build_html_email(
        top_picks, port, live, regime, regime_conf,
        rationales, bt_result, today,
        paper_html=paper_html,
        tracker_summary=tracker_summary,
        ai_picks_html=ai_picks_html,
        portfolio_advice_html=portfolio_advice_html,
        corp_events_html=_corp_html,
    )

    if send_email_flag:
        # Smart subject: top pick, regime, strong buy count
        strong_buys = [p for p in top_picks if p.get("signal") == "STRONG BUY"]
        n_strong = len(strong_buys)
        top_sym = top_picks[0]["symbol"] if top_picks else "N/A"
        date_short = datetime.strptime(today, "%Y-%m-%d").strftime("%b %d") if today else today

        if n_strong > 0:
            subject = f"NEPSE | {regime} | {top_sym} leads {n_strong} STRONG BUY picks | {date_short}"
        elif top_picks:
            top_signal = top_picks[0].get("signal", "BUY")
            subject = f"NEPSE | {regime} | {top_sym} ({top_signal}) + {len(top_picks) - 1} more | {date_short}"
        else:
            subject = f"NEPSE | {regime} | No actionable picks | {date_short}"

        send_email(subject, html)
    else:
        # Save HTML locally for review
        out_path = ROOT / "reports" / f"scanner_{today}.html"
        out_path.parent.mkdir(exist_ok=True)
        out_path.write_text(html)
        print(f"  Report saved: {out_path}")

    return top_picks


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="NEPSE Ensemble Scanner")
    parser.add_argument("--print",      action="store_true",  help="Print only, no email")
    parser.add_argument("--train-xgb",  action="store_true",  help="Retrain XGBoost+LGB ensemble")
    parser.add_argument("--train-gru",  action="store_true",  help="Launch GRU training in bg")
    parser.add_argument("--backtest",   action="store_true",  help="Run walk-forward backtest")
    args = parser.parse_args()

    run_scanner(
        send_email_flag = not args.print,
        train_xgb_flag  = args.train_xgb,
        train_gru_flag  = args.train_gru,
        run_backtest    = args.backtest,
    )


if __name__ == "__main__":
    main()
