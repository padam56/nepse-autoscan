"""
ml/regime.py — NEPSE Market Regime Detection
═════════════════════════════════════════════
Detects one of three market regimes for the Nepal Stock Exchange:

    BULL  (0) — Post-NRB rate-cut rallies, strong breadth, trending market
    RANGE (1) — Pre-election consolidation, low ADX, sideways action
    BEAR  (2) — Budget-shock sell-offs, declining breadth, rising volatility

Primary:   Gaussian Mixture Model (sklearn) — 3 components on 6 market features
Secondary: Hidden Markov Model (hmmlearn)   — optional, captures regime transitions
Fallback:  Rule-based classifier            — no external ML deps required

Regime signal multipliers:
    BULL  → 1.2×  (lean in)
    RANGE → 0.8×  (be selective)
    BEAR  → 0.4×  (stay cautious)

Walk-forward: models are re-fit on a rolling 252-trading-day window.
"""

from __future__ import annotations

import logging
import pickle
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")

# ── Optional imports ──────────────────────────────────────────────────────────
try:
    from sklearn.mixture import GaussianMixture
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    from hmmlearn.hmm import GaussianHMM
    HMM_AVAILABLE = True
except ImportError:
    HMM_AVAILABLE = False

# ── Constants ─────────────────────────────────────────────────────────────────
REGIME_NAMES = {0: "BULL", 1: "RANGE", 2: "BEAR"}
REGIME_MULT  = {0: 1.2,    1: 0.8,     2: 0.4}
REGIME_IDS   = {"BULL": 0, "RANGE": 1, "BEAR": 2}

# Feature names for regime model
REGIME_FEATURES = [
    "breadth",          # % stocks with positive 20d return (0–1)
    "market_ret_20d",   # median 20d return across universe
    "market_vol_20d",   # median 20d annualized volatility
    "adv_dec_ratio",    # (advancing − declining) / total
    "vol_ratio",        # aggregate volume vs 20d avg
    "trend_strength",   # median ADX across universe
]


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET FEATURE EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_market_features(histories: Dict[str, list]) -> Optional[np.ndarray]:
    """
    Compute 6 market-level features from the cross-sectional universe.
    Returns a 1D array of shape (6,) representing the current market state.
    """
    rets_20d = []
    vols_20d = []
    vol_ratios = []

    for sym, recs in histories.items():
        if len(recs) < 25:
            continue
        closes = np.array([
            float(r.get("lp", r.get("close", 0)) or 0)
            for r in recs
        ])
        closes = closes[closes > 0]
        if len(closes) < 22:
            continue

        vols = np.array([float(r.get("q", 1) or 1) for r in recs[-25:]])

        ret_20d  = closes[-1] / closes[-21] - 1
        vol_20d  = closes[-20:].std() / (closes[-20:].mean() + 1e-8) * np.sqrt(252)
        vol_ma20 = vols[-20:].mean()
        vol_curr = vols[-1]

        rets_20d.append(float(ret_20d))
        vols_20d.append(float(vol_20d))
        vol_ratios.append(float(vol_curr / (vol_ma20 + 1e-6)))

    if len(rets_20d) < 10:
        return None

    rets_arr = np.array(rets_20d)
    breadth        = float(np.mean(rets_arr > 0))
    market_ret_20d = float(np.median(rets_arr))
    market_vol_20d = float(np.median(vols_20d))
    adv_dec        = float(np.mean(rets_arr > 0) - np.mean(rets_arr < 0))
    vol_ratio      = float(np.median(vol_ratios))
    trend_strength = _estimate_trend_strength(histories)

    return np.array([
        breadth, market_ret_20d, market_vol_20d,
        adv_dec, vol_ratio, trend_strength,
    ], dtype=np.float32)


def _estimate_trend_strength(histories: Dict[str, list]) -> float:
    """Estimate average ADX proxy (EMA slope strength) across universe."""
    strengths = []
    for sym, recs in histories.items():
        if len(recs) < 30:
            continue
        closes = np.array([float(r.get("lp", r.get("close", 0)) or 0) for r in recs[-30:]])
        closes = closes[closes > 0]
        if len(closes) < 20:
            continue
        # Simple trend strength: R² of linear regression on last 20 prices
        x = np.arange(len(closes[-20:]))
        y = closes[-20:]
        if y.std() < 1e-6:
            continue
        r = np.corrcoef(x, y)[0, 1]
        strengths.append(abs(r))  # 0 = flat, 1 = strong trend
    return float(np.median(strengths)) if strengths else 0.3


def _build_history_matrix(
    histories: Dict[str, list],
    lookback: int = 252,
) -> Optional[np.ndarray]:
    """
    Build a (T, 6) matrix of market features over the last ``lookback`` days.
    Used to fit the GMM/HMM on historical regime patterns.
    """
    # Find all unique dates
    all_dates = sorted(set(
        r["date"]
        for recs in histories.values()
        for r in recs
    ))
    if len(all_dates) < 40:
        return None

    sample_dates = all_dates[-lookback:]
    rows = []

    for d_str in sample_dates[20:]:  # need 20 warm-up days
        snap = {
            sym: [r for r in recs if r["date"] <= d_str]
            for sym, recs in histories.items()
            if sum(1 for r in recs if r["date"] <= d_str) >= 22
        }
        if len(snap) < 15:
            continue
        feat = _extract_market_features(snap)
        if feat is not None and not np.any(np.isnan(feat)):
            rows.append(feat)

    if len(rows) < 20:
        return None

    return np.array(rows, dtype=np.float32)


# ═══════════════════════════════════════════════════════════════════════════════
# GMM REGIME DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class RegimeDetector:
    """
    Gaussian Mixture Model based regime detector.

    3 Gaussian components are fitted to market feature vectors.
    Components are then labeled BULL/RANGE/BEAR by sorting on
    'breadth' (component 0 mean).

    After fitting, the detector assigns semantic labels to GMM components
    by inspecting the breadth feature:
        highest breadth mean  → BULL
        lowest  breadth mean  → BEAR
        middle               → RANGE
    """

    def __init__(self, n_components: int = 3, random_state: int = 42):
        self.n_components  = n_components
        self.random_state  = random_state
        self._gmm: Optional[GaussianMixture] = None
        self._scaler: Optional[object] = None
        self._label_map: Dict[int, int] = {0: 0, 1: 1, 2: 2}  # gmm_idx → regime_id
        self.fitted = False

    def fit(self, X: np.ndarray) -> "RegimeDetector":
        """Fit GMM on (T, 6) feature matrix."""
        if not SKLEARN_AVAILABLE:
            logger.warning("sklearn not available — rule-based fallback will be used")
            return self

        from sklearn.mixture import GaussianMixture
        from sklearn.preprocessing import StandardScaler

        self._scaler = StandardScaler()
        Xs = self._scaler.fit_transform(X)

        self._gmm = GaussianMixture(
            n_components=self.n_components,
            covariance_type="full",
            random_state=self.random_state,
            n_init=5,
            max_iter=200,
        )
        self._gmm.fit(Xs)

        # Map GMM components to BULL/RANGE/BEAR by breadth feature (idx 0)
        breadths = self._gmm.means_[:, 0]   # breadth is feature 0
        order    = np.argsort(breadths)      # lowest breadth → BEAR
        # order[0] = lowest breadth = BEAR(2), order[2] = highest = BULL(0)
        self._label_map = {
            int(order[2]): 0,   # highest breadth → BULL
            int(order[1]): 1,   # middle          → RANGE
            int(order[0]): 2,   # lowest          → BEAR
        }
        self.fitted = True
        return self

    def predict(self, x: np.ndarray) -> Tuple[int, float, np.ndarray]:
        """
        Predict regime for a single feature vector x of shape (6,).

        Returns:
            regime_id   : int  (0=BULL, 1=RANGE, 2=BEAR)
            confidence  : float 0–1
            proba       : ndarray [p_bull, p_range, p_bear]
        """
        if not self.fitted or self._gmm is None:
            return self._rule_based(x)

        xs = self._scaler.transform(x.reshape(1, -1))
        raw_probs = self._gmm.predict_proba(xs)[0]   # shape (3,)

        # Remap to BULL/RANGE/BEAR order
        proba = np.zeros(3, dtype=float)
        for gmm_idx, regime_id in self._label_map.items():
            proba[regime_id] = raw_probs[gmm_idx]

        regime_id  = int(np.argmax(proba))
        confidence = float(proba[regime_id])
        return regime_id, confidence, proba

    def _rule_based(self, x: np.ndarray) -> Tuple[int, float, np.ndarray]:
        """Simple rule-based fallback when sklearn unavailable."""
        breadth        = float(x[0]) if len(x) > 0 else 0.5
        market_ret_20d = float(x[1]) if len(x) > 1 else 0.0
        vol_20d        = float(x[2]) if len(x) > 2 else 0.02

        if breadth > 0.60 and market_ret_20d > 0.02:
            regime, conf = 0, 0.75   # BULL
        elif breadth < 0.40 or market_ret_20d < -0.02 or vol_20d > 0.35:
            regime, conf = 2, 0.75   # BEAR
        else:
            regime, conf = 1, 0.65   # RANGE

        proba = np.zeros(3)
        proba[regime] = conf
        proba[(regime + 1) % 3] = (1 - conf) / 2
        proba[(regime + 2) % 3] = (1 - conf) / 2
        return regime, conf, proba


# ═══════════════════════════════════════════════════════════════════════════════
# HMM REGIME DETECTOR (optional)
# ═══════════════════════════════════════════════════════════════════════════════

class HMMRegimeDetector:
    """
    Hidden Markov Model regime detector (requires hmmlearn).
    Captures regime transition dynamics (e.g., slow exit from Bear).
    Used as an ensemble member alongside GMM.
    """

    def __init__(self, n_states: int = 3, random_state: int = 42):
        self.n_states    = n_states
        self.random_state = random_state
        self._hmm: Optional[object] = None
        self._scaler: Optional[object] = None
        self._label_map: Dict[int, int] = {}
        self.fitted = False

    def fit(self, X: np.ndarray) -> "HMMRegimeDetector":
        if not HMM_AVAILABLE or not SKLEARN_AVAILABLE:
            return self

        from hmmlearn.hmm import GaussianHMM
        from sklearn.preprocessing import StandardScaler

        self._scaler = StandardScaler()
        Xs = self._scaler.fit_transform(X)

        np.random.seed(self.random_state)
        self._hmm = GaussianHMM(
            n_components=self.n_states,
            covariance_type="full",
            n_iter=200,
            random_state=self.random_state,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._hmm.fit(Xs)

        # Label states by mean breadth
        means_breadth = self._hmm.means_[:, 0]
        order = np.argsort(means_breadth)
        self._label_map = {
            int(order[2]): 0,   # BULL
            int(order[1]): 1,   # RANGE
            int(order[0]): 2,   # BEAR
        }
        self.fitted = True
        return self

    def predict(self, X_seq: np.ndarray) -> Tuple[int, float]:
        """
        Predict regime using Viterbi decoding on a sequence of observations.

        Args:
            X_seq: (T, 6) array of recent market features

        Returns: (regime_id, confidence)
        """
        if not self.fitted or self._hmm is None:
            return 1, 0.5   # default RANGE

        Xs = self._scaler.transform(X_seq)
        states = self._hmm.predict(Xs)
        last_state = int(states[-1])
        regime_id = self._label_map.get(last_state, 1)

        # Confidence from state occupancy in last 5 steps
        recent = states[-5:]
        conf = float(np.mean(recent == last_state))
        return regime_id, conf


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET REGIME MONITOR (orchestrator)
# ═══════════════════════════════════════════════════════════════════════════════

class MarketRegimeMonitor:
    """
    High-level orchestrator that:
      1. Extracts market features from universe histories
      2. Fits (or loads) GMM/HMM models
      3. Returns current regime + confidence + signal multiplier

    Usage:
        monitor = MarketRegimeMonitor(model_path)
        result  = monitor.update(histories)
        # result = {regime, confidence, multiplier, features, proba}
    """

    def __init__(self, model_path: Optional[Path] = None):
        self.model_path   = Path(model_path) if model_path else None
        self._gmm_det     = RegimeDetector()
        self._hmm_det     = HMMRegimeDetector()
        self._last_regime = "RANGE"
        self._last_conf   = 0.5
        self._history: List[dict] = []
        self._loaded = False

        if self.model_path and self.model_path.exists():
            self._load()

    def update(self, histories: Dict[str, list]) -> dict:
        """
        Update regime estimate from current universe.

        Args:
            histories: {symbol: [records]} from load_all_history()

        Returns:
            dict with keys: regime, confidence, multiplier, proba, features
        """
        # Extract current market features
        features = _extract_market_features(histories)
        if features is None:
            return self._fallback_result()

        # Fit / re-fit if not loaded or stale (re-fit weekly)
        if not self._gmm_det.fitted:
            self._fit_models(histories)

        # GMM prediction
        gmm_id, gmm_conf, gmm_proba = self._gmm_det.predict(features)

        # HMM prediction (if available)
        hmm_id, hmm_conf = 1, 0.5
        if self._hmm_det.fitted:
            X_matrix = _build_history_matrix(histories, lookback=60)
            if X_matrix is not None and len(X_matrix) >= 5:
                hmm_id, hmm_conf = self._hmm_det.predict(X_matrix)

        # Ensemble GMM + HMM (70/30 if HMM available, else pure GMM)
        if self._hmm_det.fitted and hmm_conf > 0.3:
            votes = np.zeros(3)
            votes[gmm_id] += 0.70 * gmm_conf
            votes[hmm_id] += 0.30 * hmm_conf
            regime_id  = int(np.argmax(votes))
            confidence = float(votes[regime_id] / (votes.sum() + 1e-6))
        else:
            regime_id  = gmm_id
            confidence = gmm_conf

        regime_name = REGIME_NAMES.get(regime_id, "RANGE")
        multiplier  = REGIME_MULT.get(regime_id, 1.0)

        # Smooth regime changes (avoid flipping every day)
        if regime_name != self._last_regime and confidence < 0.65:
            # Keep previous regime if not confident enough
            regime_name = self._last_regime
            multiplier  = REGIME_MULT.get(REGIME_IDS.get(regime_name, 1), 1.0)
            confidence  = self._last_conf * 0.85  # decay

        self._last_regime = regime_name
        self._last_conf   = confidence

        # Log to history
        self._history.append({
            "regime": regime_name,
            "confidence": confidence,
            "features": features.tolist(),
        })
        if len(self._history) > 252:
            self._history = self._history[-252:]

        # Persist model state
        if self.model_path:
            self._save()

        return {
            "regime":      regime_name,
            "confidence":  round(confidence, 4),
            "multiplier":  multiplier,
            "proba":       {
                "BULL":  round(float(gmm_proba[0]), 3),
                "RANGE": round(float(gmm_proba[1]), 3),
                "BEAR":  round(float(gmm_proba[2]), 3),
            },
            "features": {
                name: round(float(v), 4)
                for name, v in zip(REGIME_FEATURES, features)
            },
        }

    def get_multiplier(self) -> float:
        """Quick access to the current regime signal multiplier."""
        return REGIME_MULT.get(REGIME_IDS.get(self._last_regime, 1), 1.0)

    def _fit_models(self, histories: Dict[str, list]) -> None:
        """Fit GMM and HMM on historical market features."""
        logger.info("[REGIME] Fitting models on historical data...")
        X = _build_history_matrix(histories, lookback=252)
        if X is None or len(X) < 30:
            logger.warning("[REGIME] Not enough data for model fitting")
            return

        # Verify features are valid before fitting
        if np.any(np.isnan(X)) or np.any(np.isinf(X)):
            logger.warning("[REGIME] Feature matrix contains NaN/Inf, skipping fit")
            return

        self._gmm_det.fit(X)
        logger.info(f"[REGIME] GMM fitted on {len(X)} observations")

        if HMM_AVAILABLE:
            self._hmm_det.fit(X)
            logger.info("[REGIME] HMM fitted")

    def _fallback_result(self) -> dict:
        return {
            "regime":     self._last_regime,
            "confidence": self._last_conf,
            "multiplier": REGIME_MULT.get(REGIME_IDS.get(self._last_regime, 1), 1.0),
            "proba":      {"BULL": 0.33, "RANGE": 0.34, "BEAR": 0.33},
            "features":   {},
        }

    def _save(self) -> None:
        if self.model_path is None:
            return
        try:
            state = {
                "gmm":          self._gmm_det,
                "hmm":          self._hmm_det,
                "last_regime":  self._last_regime,
                "last_conf":    self._last_conf,
                "history":      self._history[-50:],
            }
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.model_path, "wb") as f:
                pickle.dump(state, f)
        except Exception as e:
            logger.warning(f"[REGIME] Save failed: {e}")

    def _load(self) -> None:
        try:
            with open(self.model_path, "rb") as f:
                state = pickle.load(f)
            self._gmm_det     = state.get("gmm",         RegimeDetector())
            self._hmm_det     = state.get("hmm",         HMMRegimeDetector())
            self._last_regime = state.get("last_regime", "RANGE")
            self._last_conf   = state.get("last_conf",   0.5)
            self._history     = state.get("history",     [])
            self._loaded = True
            logger.info(f"[REGIME] Loaded model state, last regime: {self._last_regime}")
        except Exception as e:
            logger.warning(f"[REGIME] Load failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE USAGE
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json
    from pathlib import Path

    ROOT = Path(__file__).parent.parent
    HISTORY_DIR = ROOT / "data" / "price_history"
    MODEL_PATH  = ROOT / "data" / "models" / "regime.pkl"

    print("Loading histories...")
    histories = {}
    for f in sorted(HISTORY_DIR.glob("*.json")):
        try:
            day = json.loads(f.read_text())
            d   = day["date"]
            for sym, data in day.get("stocks", {}).items():
                if sym not in histories:
                    histories[sym] = []
                histories[sym].append({"date": d, **data})
        except Exception:
            continue

    print(f"Loaded {len(histories)} stocks")

    monitor = MarketRegimeMonitor(MODEL_PATH)
    result  = monitor.update(histories)

    print(f"\nMarket Regime: {result['regime']}  (confidence {result['confidence']:.0%})")
    print(f"Signal multiplier: {result['multiplier']}×")
    print(f"Probabilities: BULL={result['proba']['BULL']:.0%}  "
          f"RANGE={result['proba']['RANGE']:.0%}  "
          f"BEAR={result['proba']['BEAR']:.0%}")
    print(f"\nMarket features:")
    for k, v in result["features"].items():
        print(f"  {k:<20} {v:+.4f}")
