"""
ml/xgb_lgbm.py — XGBoost + LightGBM Cross-Sectional Ensemble
═════════════════════════════════════════════════════════════════
Cross-sectional model that predicts 5-day forward return quintile
(0=worst 20%, 4=best 20%) for each stock vs the universe.

Architecture:
  - XGBoost  (GPU: device='cuda', tree_method='hist')
  - LightGBM (GPU: device='gpu')
  - Ensemble: weighted softmax average (XGB=55%, LGB=45%)
  - Calibration: isotonic regression on held-out validation set

Input:  55-feature vector per stock (from ml/features.py ALL_FEATURE_NAMES)
Output: 5-class probability distribution, score = P(Q4) - P(Q0) → [0, 1]

Training protocol:
  - Time-ordered 85/15 split (no shuffling to prevent future leakage)
  - Walk-forward: sample every 5 trading days, 200d rolling window
  - Early stopping: 50 rounds on validation mlogloss
"""

from __future__ import annotations

import logging
import pickle
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor, Future
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, "/scratch/C00621463/pypackages")
logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")

# ── Optional ML imports ───────────────────────────────────────────────────────
try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False
    logger.warning("XGBoost not available")

try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    LGB_AVAILABLE = False
    logger.warning("LightGBM not available")

try:
    from sklearn.isotonic import IsotonicRegression
    from sklearn.calibration import CalibratedClassifierCV
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

# ── Constants ─────────────────────────────────────────────────────────────────
XGB_PARAMS = {
    "objective":        "multi:softprob",
    "num_class":        5,
    "device":           "cuda",
    "tree_method":      "hist",
    "max_depth":        6,
    "learning_rate":    0.05,
    "n_estimators":     500,
    "subsample":        0.8,
    "colsample_bytree": 0.7,
    "min_child_weight": 5,
    "gamma":            0.1,
    "reg_alpha":        0.1,
    "reg_lambda":       1.0,
    "eval_metric":      "mlogloss",
    "verbosity":        0,
}

LGB_PARAMS = {
    "objective":        "multiclass",
    "num_class":        5,
    "device":           "gpu",
    "num_leaves":       63,
    "max_depth":        6,
    "learning_rate":    0.05,
    "n_estimators":     500,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.8,
    "bagging_freq":     1,
    "min_data_in_leaf": 20,
    "lambda_l1":        0.1,
    "lambda_l2":        1.0,
    "metric":           "multi_logloss",
    "verbose":          -1,
    "force_col_wise":   True,
}

ENSEMBLE_WEIGHTS = {"xgb": 0.55, "lgb": 0.45}
EARLY_STOPPING   = 50


# ═══════════════════════════════════════════════════════════════════════════════
# PARALLEL TRAINING WORKER FUNCTIONS (module-level for pickling)
# ═══════════════════════════════════════════════════════════════════════════════

def _train_xgb_worker(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
) -> dict:
    """Train XGBoost in a subprocess. Limits GPU to ~50% memory via env var."""
    import os
    os.environ["CUDA_MEM_FRACTION"] = "0.5"
    os.environ["CUDA_VISIBLE_DEVICES"] = os.environ.get("CUDA_VISIBLE_DEVICES", "0")

    try:
        import xgboost as xgb
    except ImportError:
        return {"error": "XGBoost not available"}

    try:
        params = {**XGB_PARAMS}
        dtrain = xgb.DMatrix(X_train, label=y_train)
        dval   = xgb.DMatrix(X_val,   label=y_val)

        booster = xgb.train(
            params,
            dtrain,
            num_boost_round=XGB_PARAMS["n_estimators"],
            evals=[(dtrain, "train"), (dval, "val")],
            early_stopping_rounds=EARLY_STOPPING,
            verbose_eval=50,
        )
        best_iter = getattr(booster, "best_iteration", None) or getattr(booster, "best_ntree_limit", 0)

        booster.set_param({"device": "cpu"})
        preds = booster.predict(dval, iteration_range=(0, best_iter) if best_iter else None).reshape(-1, 5)
        pred_cls  = preds.argmax(axis=1)
        val_acc   = float((pred_cls == y_val).mean())
        dir_acc   = float(((pred_cls >= 3) == (y_val >= 3)).mean())
        top_prec  = float(np.mean(y_val[pred_cls == 4] == 4)) if (pred_cls == 4).any() else 0.0

        return {
            "booster": booster,
            "best_iter": best_iter,
            "preds": preds,
            "metrics": {
                "xgb_val_acc":  round(val_acc, 4),
                "xgb_dir_acc":  round(dir_acc, 4),
                "xgb_top_prec": round(top_prec, 4),
                "xgb_trees":    best_iter,
            },
        }
    except Exception as e:
        # CPU fallback
        try:
            params_cpu = {**XGB_PARAMS, "device": "cpu"}
            dtrain = xgb.DMatrix(X_train, label=y_train)
            dval   = xgb.DMatrix(X_val,   label=y_val)
            booster = xgb.train(
                params_cpu, dtrain,
                num_boost_round=200,
                evals=[(dval, "val")],
                early_stopping_rounds=EARLY_STOPPING,
                verbose_eval=50,
            )
            return {"booster": booster, "best_iter": 0, "preds": None, "metrics": {}, "cpu_fallback": True}
        except Exception as e2:
            return {"error": f"GPU: {e} | CPU: {e2}"}


def _train_lgb_worker(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    feature_names: list,
) -> dict:
    """Train LightGBM in a subprocess. Limits GPU to ~50% memory."""
    import os
    os.environ["CUDA_MEM_FRACTION"] = "0.5"
    os.environ["CUDA_VISIBLE_DEVICES"] = os.environ.get("CUDA_VISIBLE_DEVICES", "0")

    try:
        import lightgbm as lgb
    except ImportError:
        return {"error": "LightGBM not available"}

    try:
        lgb_train = lgb.Dataset(X_train, label=y_train,
                                feature_name=list(map(str, feature_names)))
        lgb_val   = lgb.Dataset(X_val,   label=y_val, reference=lgb_train)

        callbacks = [
            lgb.early_stopping(EARLY_STOPPING, verbose=False),
            lgb.log_evaluation(50),
        ]
        model = lgb.train(
            LGB_PARAMS,
            lgb_train,
            num_boost_round=LGB_PARAMS["n_estimators"],
            valid_sets=[lgb_val],
            callbacks=callbacks,
        )

        preds = model.predict(X_val)
        pred_cls  = preds.argmax(axis=1)
        val_acc   = float((pred_cls == y_val).mean())
        dir_acc   = float(((pred_cls >= 3) == (y_val >= 3)).mean())

        return {
            "model": model,
            "best_iter": model.best_iteration,
            "preds": preds,
            "metrics": {
                "lgb_val_acc":  round(val_acc, 4),
                "lgb_dir_acc":  round(dir_acc, 4),
                "lgb_trees":    model.best_iteration,
            },
        }
    except Exception as e:
        # CPU fallback
        try:
            params_cpu = {**LGB_PARAMS, "device": "cpu"}
            lgb_train = lgb.Dataset(X_train, label=y_train,
                                    feature_name=list(map(str, feature_names)))
            lgb_val_ds = lgb.Dataset(X_val, label=y_val, reference=lgb_train)
            callbacks = [
                lgb.early_stopping(EARLY_STOPPING, verbose=False),
                lgb.log_evaluation(50),
            ]
            model = lgb.train(params_cpu, lgb_train,
                              num_boost_round=200,
                              valid_sets=[lgb_val_ds],
                              callbacks=callbacks)
            return {"model": model, "best_iter": 0, "preds": None, "metrics": {}, "cpu_fallback": True}
        except Exception as e2:
            return {"error": f"GPU: {e} | CPU: {e2}"}


# ═══════════════════════════════════════════════════════════════════════════════
# TRAINING DATA BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_training_data(
    histories: Dict[str, list],
    feature_engine,
    sample_every: int = 5,
) -> Tuple[np.ndarray, np.ndarray, List[str], List[str]]:
    """
    Build cross-sectional training dataset.

    For each sampled date:
      1. Compute 55 features for every stock (point-in-time safe)
      2. Look up 5-day forward returns
      3. Label by cross-sectional quintile (0–4)

    Returns:
        X       : (N, 55) float32 feature matrix
        y       : (N,)    int32  quintile labels
        symbols : (N,)    symbol list
        dates   : (N,)    date string list
    """
    all_X:   List[np.ndarray] = []
    all_y:   List[int]        = []
    all_sym: List[str]        = []
    all_dt:  List[str]        = []

    # Collect all dates
    sorted_dates = sorted(set(
        r["date"]
        for recs in histories.values()
        for r in recs
    ))

    # Sample every `sample_every` trading days, skip first 60 for warm-up
    sample_dates = sorted_dates[60::sample_every]

    total = len(sample_dates)
    for i, d_str in enumerate(sample_dates):
        if i % 20 == 0:
            logger.info(f"[TRAIN] Processing date {i+1}/{total}: {d_str}")

        # Universe snapshot at d_str
        snap = {}
        for sym, recs in histories.items():
            day_recs = [r for r in recs if r["date"] <= d_str]
            if len(day_recs) >= 60:
                snap[sym] = day_recs

        if len(snap) < 20:
            continue

        # Features
        try:
            feat_df = feature_engine.compute_universe(snap)
        except Exception as e:
            logger.debug(f"Feature error at {d_str}: {e}")
            continue

        if feat_df.empty:
            continue

        # 5-day forward returns
        future_rets: Dict[str, float] = {}
        for sym in feat_df.index:
            recs = histories.get(sym, [])
            future_recs = [r for r in recs if r["date"] > d_str]
            cur_recs    = [r for r in recs if r["date"] <= d_str]
            if len(future_recs) < 5 or not cur_recs:
                continue
            c_now = float(cur_recs[-1].get("lp", cur_recs[-1].get("close", 0)) or 0)
            c_fut = float(future_recs[4].get("lp", future_recs[4].get("close", 0)) or 0)
            if c_now > 0 and c_fut > 0:
                future_rets[sym] = c_fut / c_now - 1

        syms_ok = [s for s in feat_df.index if s in future_rets]
        if len(syms_ok) < 10:
            continue

        # Cross-sectional quintile labels
        rets_arr = np.array([future_rets[s] for s in syms_ok])
        breaks   = np.quantile(rets_arr, [0.2, 0.4, 0.6, 0.8])
        labels   = np.digitize(rets_arr, breaks).astype(np.int32)   # 0–4

        for j, sym in enumerate(syms_ok):
            if sym not in feat_df.index:
                continue
            row = feat_df.loc[sym].values.astype(np.float32)
            row = np.nan_to_num(row, nan=0.0, posinf=1.0, neginf=-1.0)
            all_X.append(row)
            all_y.append(int(labels[j]))
            all_sym.append(sym)
            all_dt.append(d_str)

    if not all_X:
        return np.empty((0, 0)), np.empty(0), [], []

    return (
        np.stack(all_X, axis=0),
        np.array(all_y, dtype=np.int32),
        all_sym,
        all_dt,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ENSEMBLE MODEL
# ═══════════════════════════════════════════════════════════════════════════════

class EnsembleModel:
    """
    XGBoost + LightGBM cross-sectional ensemble.

    Train:
        model = EnsembleModel()
        model.fit(X_train, y_train, X_val, y_val)
        model.save(path)

    Infer:
        model = EnsembleModel.load(path)
        scores = model.predict_scores(X)  # {0..N-1: float 0-1}
    """

    def __init__(self):
        self._xgb_booster = None
        self._lgb_model   = None
        self._calibrators: Dict[str, object] = {}
        self.feature_names: List[str] = []
        self.fitted = False
        self.best_iter: Dict[str, int] = {}
        self.metrics:   Dict[str, float] = {}

    def fit(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val:   np.ndarray,
        y_val:   np.ndarray,
        feature_names: Optional[List[str]] = None,
    ) -> "EnsembleModel":
        """Train XGBoost and LightGBM in parallel with early stopping.

        Both models are dispatched to separate processes via
        ProcessPoolExecutor so they train concurrently.  Each worker
        restricts itself to ~50 % GPU memory via CUDA_MEM_FRACTION.
        """
        self.feature_names = feature_names or list(range(X_train.shape[1]))
        n_tr, n_val = len(X_train), len(X_val)
        logger.info(f"[ENSEMBLE] Training on {n_tr:,} samples, val on {n_val:,}...")
        logger.info("[ENSEMBLE] Dispatching XGB + LGB training in parallel...")

        # ── Launch both training jobs in parallel ─────────────────────────────
        futures: Dict[str, Future] = {}
        with ProcessPoolExecutor(max_workers=2) as pool:
            if XGB_AVAILABLE:
                futures["xgb"] = pool.submit(
                    _train_xgb_worker, X_train, y_train, X_val, y_val,
                )
            if LGB_AVAILABLE:
                futures["lgb"] = pool.submit(
                    _train_lgb_worker, X_train, y_train, X_val, y_val,
                    list(self.feature_names),
                )

        # ── Collect XGBoost results ───────────────────────────────────────────
        if "xgb" in futures:
            xgb_res = futures["xgb"].result()
            if "error" in xgb_res:
                logger.error(f"[XGB] Training failed: {xgb_res['error']}")
            else:
                self._xgb_booster = xgb_res["booster"]
                self.best_iter["xgb"] = xgb_res["best_iter"]
                self.metrics.update(xgb_res.get("metrics", {}))
                if xgb_res.get("cpu_fallback"):
                    logger.info("[XGB] Trained on CPU fallback")
                else:
                    m = xgb_res["metrics"]
                    logger.info(
                        f"[XGB] Val acc={m.get('xgb_val_acc', 0):.4f}  "
                        f"Dir acc={m.get('xgb_dir_acc', 0):.4f}  "
                        f"Top-Q prec={m.get('xgb_top_prec', 0):.4f}  "
                        f"({xgb_res['best_iter']} trees)"
                    )
                # Calibrate on val set
                if SKLEARN_AVAILABLE and xgb_res.get("preds") is not None:
                    self._calibrate("xgb", xgb_res["preds"], y_val)

        # ── Collect LightGBM results ──────────────────────────────────────────
        if "lgb" in futures:
            lgb_res = futures["lgb"].result()
            if "error" in lgb_res:
                logger.error(f"[LGB] Training failed: {lgb_res['error']}")
            else:
                self._lgb_model = lgb_res["model"]
                self.best_iter["lgb"] = lgb_res["best_iter"]
                self.metrics.update(lgb_res.get("metrics", {}))
                if lgb_res.get("cpu_fallback"):
                    logger.info("[LGB] Trained on CPU fallback")
                else:
                    m = lgb_res["metrics"]
                    logger.info(
                        f"[LGB] Val acc={m.get('lgb_val_acc', 0):.4f}  "
                        f"Dir acc={m.get('lgb_dir_acc', 0):.4f}  "
                        f"({lgb_res['best_iter']} trees)"
                    )
                if SKLEARN_AVAILABLE and lgb_res.get("preds") is not None:
                    self._calibrate("lgb", lgb_res["preds"], y_val)

        self.fitted = (self._xgb_booster is not None or self._lgb_model is not None)

        if self.fitted:
            logger.info(f"[ENSEMBLE] Training complete. Metrics: {self.metrics}")
        else:
            logger.error("[ENSEMBLE] No model was successfully trained!")

        return self

    def _calibrate(self, name: str, probs: np.ndarray, y_true: np.ndarray) -> None:
        """Fit isotonic calibrator per class."""
        calibrators = {}
        for cls in range(5):
            binary_y = (y_true == cls).astype(float)
            if binary_y.sum() < 5:
                continue
            iso = IsotonicRegression(out_of_bounds="clip")
            iso.fit(probs[:, cls], binary_y)
            calibrators[cls] = iso
        self._calibrators[name] = calibrators

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """
        Predict class probabilities for input X of shape (N, n_features).
        Returns (N, 5) array of probabilities.
        """
        if not self.fitted:
            return np.ones((len(X), 5)) / 5.0

        probs_list = []

        if self._xgb_booster is not None and XGB_AVAILABLE:
            try:
                dmat = xgb.DMatrix(X)
                p = self._xgb_booster.predict(
                    dmat,
                    ntree_limit=self.best_iter.get("xgb", 0)
                ).reshape(-1, 5)
                # Apply calibration if available
                if "xgb" in self._calibrators:
                    p = self._apply_calibration("xgb", p)
                probs_list.append(("xgb", p, ENSEMBLE_WEIGHTS["xgb"]))
            except Exception as e:
                logger.warning(f"[XGB] Inference error: {e}")

        if self._lgb_model is not None and LGB_AVAILABLE:
            try:
                p = self._lgb_model.predict(X)
                if "lgb" in self._calibrators:
                    p = self._apply_calibration("lgb", p)
                probs_list.append(("lgb", p, ENSEMBLE_WEIGHTS["lgb"]))
            except Exception as e:
                logger.warning(f"[LGB] Inference error: {e}")

        if not probs_list:
            return np.ones((len(X), 5)) / 5.0

        if len(probs_list) == 1:
            _, probs, _ = probs_list[0]
        else:
            total_w = sum(w for _, _, w in probs_list)
            probs = sum(p * (w / total_w) for _, p, w in probs_list)

        # Normalize rows
        row_sums = probs.sum(axis=1, keepdims=True)
        probs = np.where(row_sums > 0, probs / row_sums, 1.0 / 5.0)
        return probs

    def _apply_calibration(self, name: str, probs: np.ndarray) -> np.ndarray:
        """Apply per-class isotonic calibration."""
        calibrated = probs.copy()
        cals = self._calibrators.get(name, {})
        for cls, cal in cals.items():
            calibrated[:, cls] = cal.predict(probs[:, cls])
        # Re-normalize
        row_sums = calibrated.sum(axis=1, keepdims=True)
        return np.where(row_sums > 0, calibrated / row_sums, 1.0 / 5.0)

    def predict_scores(self, X: np.ndarray) -> np.ndarray:
        """
        Returns a 1D score array of shape (N,), values in [0, 1].
        Score = (P(Q4) - P(Q0) + 1) / 2
        """
        probs = self.predict_proba(X)
        raw   = probs[:, 4] - probs[:, 0]
        return np.clip((raw + 1.0) / 2.0, 0.0, 1.0)

    def feature_importance(self, top_n: int = 20) -> List[Tuple[str, float]]:
        """Return top-N important features as (name, importance) pairs."""
        importances: Dict[str, float] = {}

        if self._xgb_booster is not None:
            try:
                scores = self._xgb_booster.get_score(importance_type="gain")
                for fname, imp in scores.items():
                    importances[fname] = importances.get(fname, 0) + imp * 0.55
            except Exception:
                pass

        if self._lgb_model is not None:
            try:
                feat_names = self._lgb_model.feature_name()
                imps = self._lgb_model.feature_importance(importance_type="gain")
                for fn, imp in zip(feat_names, imps):
                    importances[fn] = importances.get(fn, 0) + float(imp) * 0.45
            except Exception:
                pass

        if not importances:
            return []

        total = sum(importances.values()) + 1e-9
        ranked = sorted(
            [(k, v / total) for k, v in importances.items()],
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:top_n]

    def save(self, path: Path) -> None:
        """Serialize model to disk."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "xgb_booster":   self._xgb_booster,
            "lgb_model":     self._lgb_model,
            "calibrators":   self._calibrators,
            "feature_names": self.feature_names,
            "best_iter":     self.best_iter,
            "metrics":       self.metrics,
        }
        with open(path, "wb") as f:
            pickle.dump(payload, f)
        logger.info(f"[ENSEMBLE] Model saved → {path}")

    @classmethod
    def load(cls, path: Path) -> "EnsembleModel":
        """Load model from disk."""
        model = cls()
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Model not found: {path}")
        with open(path, "rb") as f:
            payload = pickle.load(f)

        # Support both new dict format and legacy bare XGBBooster
        if isinstance(payload, dict):
            model._xgb_booster   = payload.get("xgb_booster")
            model._lgb_model     = payload.get("lgb_model")
            model._calibrators   = payload.get("calibrators", {})
            model.feature_names  = payload.get("feature_names", [])
            model.best_iter      = payload.get("best_iter", {})
            model.metrics        = payload.get("metrics", {})
            # Legacy: models dict with 'xgb' / 'lgb' keys
            if "models" in payload:
                model._xgb_booster = payload["models"].get("xgb")
                model._lgb_model   = payload["models"].get("lgb")
        else:
            # Legacy: raw XGBBooster
            model._xgb_booster = payload

        model.fitted = (model._xgb_booster is not None or model._lgb_model is not None)
        logger.info(f"[ENSEMBLE] Loaded from {path}")
        return model


# ═══════════════════════════════════════════════════════════════════════════════
# HIGH-LEVEL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def score_universe(
    feat_map: Dict[str, dict],
    model: EnsembleModel,
    feature_names: Optional[List[str]] = None,
) -> Dict[str, float]:
    """
    Score all stocks in feat_map.

    Args:
        feat_map:      {symbol: feature_dict}
        model:         fitted EnsembleModel
        feature_names: ordered list of feature keys (defaults to model.feature_names)

    Returns:
        {symbol: score 0-1}  where 1 = strongest buy signal
    """
    if not feat_map or not model.fitted:
        return {}

    names   = feature_names or model.feature_names
    symbols = list(feat_map.keys())
    rows    = []

    for sym in symbols:
        f = feat_map[sym]
        if names:
            row = np.array([f.get(str(k), 0.0) for k in names], dtype=np.float32)
        else:
            row = np.array(list(f.values()), dtype=np.float32)
        row = np.nan_to_num(row, nan=0.0, posinf=1.0, neginf=-1.0)
        rows.append(row)

    X      = np.stack(rows, axis=0)
    scores = model.predict_scores(X)

    return {sym: float(sc) for sym, sc in zip(symbols, scores)}


def train_and_save(
    histories: Dict[str, list],
    feature_engine,
    save_path: Path,
) -> Optional[EnsembleModel]:
    """
    Full training pipeline: build data → train → save.
    Returns fitted EnsembleModel or None on failure.
    """
    from features import ALL_FEATURE_NAMES
    from feature_selector import FeatureSelector

    logger.info("[ENSEMBLE] Building training data...")
    X, y, syms, dates = build_training_data(histories, feature_engine)

    if len(X) < 500:
        logger.error(f"[ENSEMBLE] Only {len(X)} samples — need ≥ 500")
        return None

    # Time-ordered 85/15 split
    split    = int(len(X) * 0.85)
    X_tr, X_val = X[:split], X[split:]
    y_tr, y_val = y[:split], y[split:]

    # Feature selection: prune noisy features
    all_names = list(ALL_FEATURE_NAMES)
    selector = FeatureSelector()
    selected = selector.select(X_tr, y_tr, all_names, top_k=40)
    selector.save_selected(selected)

    # Filter columns to selected features only
    sel_idx = [all_names.index(f) for f in selected if f in all_names]
    X_tr  = X_tr[:, sel_idx]
    X_val = X_val[:, sel_idx]
    selected_names = [all_names[i] for i in sel_idx]
    logger.info("[ENSEMBLE] Training with %d / %d features" % (len(selected_names), len(all_names)))

    model = EnsembleModel()
    model.fit(X_tr, y_tr, X_val, y_val, feature_names=selected_names)
    model.save(save_path)

    # Print feature importance
    top_feats = model.feature_importance(top_n=10)
    if top_feats:
        logger.info("[ENSEMBLE] Top features:")
        for fn, imp in top_feats:
            logger.info(f"  {fn:<30} {imp:.4f}")

    return model


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE USAGE
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json
    from pathlib import Path

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    ROOT        = Path(__file__).parent.parent
    HISTORY_DIR = ROOT / "data" / "price_history"
    SECTORS     = ROOT / "data" / "sectors.json"
    CALENDAR    = ROOT / "data" / "calendar.json"
    MODEL_PATH  = ROOT / "data" / "models" / "xgb_scanner.pkl"

    sys.path.insert(0, str(ROOT / "ml"))
    from features import FeatureEngine

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
    engine = FeatureEngine(SECTORS, CALENDAR)
    model  = train_and_save(histories, engine, MODEL_PATH)

    if model:
        print(f"\nTraining complete. Metrics: {model.metrics}")
