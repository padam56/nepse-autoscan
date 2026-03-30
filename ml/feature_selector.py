"""
ml/feature_selector.py — Automatic feature selection for NEPSE ensemble model.

Identifies and removes noisy features that dilute signal:
  1. Drops features with near-zero variance (constant across stocks)
  2. Drops features with >50% NaN/Inf
  3. Drops highly correlated redundant features (keeps the one with higher importance)
  4. Ranks by XGB/LGB feature importance and prunes bottom performers
  5. Saves selected feature list for reproducible daily scans

Usage:
    from ml.feature_selector import FeatureSelector
    selector = FeatureSelector()
    selected = selector.select(X_train, y_train, feature_names, top_k=40)
    # selected = ['ret_5d', 'rsi_14', ...] (pruned list)
"""

import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
SELECTED_FEATURES_PATH = ROOT / "data" / "selected_features.json"


class FeatureSelector:
    """Select the most predictive features, drop noise."""

    def __init__(self, min_variance: float = 0.01, max_corr: float = 0.95,
                 max_nan_frac: float = 0.5):
        self.min_variance = min_variance
        self.max_corr = max_corr
        self.max_nan_frac = max_nan_frac

    def select(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: List[str],
        top_k: int = 40,
    ) -> List[str]:
        """Run all selection steps. Returns ordered list of selected feature names."""

        n_samples, n_features = X.shape
        assert len(feature_names) == n_features

        mask = np.ones(n_features, dtype=bool)
        names = list(feature_names)

        # Step 1: Drop high-NaN features
        nan_fracs = np.isnan(X).mean(axis=0)
        nan_drop = nan_fracs > self.max_nan_frac
        if nan_drop.any():
            dropped = [names[i] for i in range(n_features) if nan_drop[i]]
            logger.info("[FEAT] Dropping %d features with >%.0f%% NaN: %s" % (
                len(dropped), self.max_nan_frac * 100, ", ".join(dropped)))
            mask &= ~nan_drop

        # Step 2: Drop near-zero variance
        # Replace NaN with column mean for variance calculation
        X_clean = X.copy()
        col_means = np.nanmean(X_clean, axis=0)
        for j in range(n_features):
            nan_mask = np.isnan(X_clean[:, j])
            X_clean[nan_mask, j] = col_means[j] if np.isfinite(col_means[j]) else 0.0

        variances = np.nanvar(X_clean, axis=0)
        low_var = variances < self.min_variance
        low_var_drop = low_var & mask
        if low_var_drop.any():
            dropped = [names[i] for i in range(n_features) if low_var_drop[i]]
            logger.info("[FEAT] Dropping %d near-zero variance features: %s" % (
                len(dropped), ", ".join(dropped)))
            mask &= ~low_var

        # Step 3: Drop highly correlated features (keep one with higher variance)
        active_idx = np.where(mask)[0]
        if len(active_idx) > 2:
            X_active = X_clean[:, active_idx]
            # Correlation matrix
            with np.errstate(divide='ignore', invalid='ignore'):
                corr = np.corrcoef(X_active, rowvar=False)
                corr = np.nan_to_num(corr, nan=0.0)

            to_drop = set()
            for i in range(len(active_idx)):
                if i in to_drop:
                    continue
                for j in range(i + 1, len(active_idx)):
                    if j in to_drop:
                        continue
                    if abs(corr[i, j]) > self.max_corr:
                        # Drop the one with lower variance
                        idx_i = active_idx[i]
                        idx_j = active_idx[j]
                        if variances[idx_i] >= variances[idx_j]:
                            to_drop.add(j)
                            logger.info("[FEAT] Dropping %s (r=%.2f with %s)" % (
                                names[idx_j], corr[i, j], names[idx_i]))
                        else:
                            to_drop.add(i)
                            logger.info("[FEAT] Dropping %s (r=%.2f with %s)" % (
                                names[idx_i], corr[i, j], names[idx_j]))
                            break

            for idx in to_drop:
                mask[active_idx[idx]] = False

        # Step 4: Rank by importance using a lightweight model
        active_idx = np.where(mask)[0]
        if len(active_idx) > top_k:
            importances = self._compute_importance(X_clean[:, active_idx], y)
            ranked = np.argsort(importances)[::-1]
            keep_set = set(ranked[:top_k])
            for rank_pos, local_idx in enumerate(ranked):
                if rank_pos >= top_k:
                    feat_name = names[active_idx[local_idx]]
                    mask[active_idx[local_idx]] = False
                    logger.info("[FEAT] Pruning low-importance: %s (rank %d, imp=%.4f)" % (
                        feat_name, rank_pos + 1, importances[local_idx]))

        selected = [names[i] for i in range(n_features) if mask[i]]
        logger.info("[FEAT] Selected %d / %d features" % (len(selected), n_features))

        return selected

    def _compute_importance(self, X: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Compute feature importance using permutation importance with a fast model."""
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.inspection import permutation_importance

            # Quick RF for importance ranking (not for prediction)
            rf = RandomForestClassifier(
                n_estimators=50, max_depth=8, random_state=42, n_jobs=-1)

            # Handle any remaining NaN
            X_safe = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
            rf.fit(X_safe, y)

            # Permutation importance is more reliable than Gini importance
            result = permutation_importance(
                rf, X_safe, y, n_repeats=5, random_state=42, n_jobs=-1)

            return result.importances_mean

        except ImportError:
            # Fallback: use variance as proxy
            logger.warning("[FEAT] sklearn not available, using variance as importance proxy")
            return np.nanvar(X, axis=0)

    def save_selected(self, selected: List[str]) -> None:
        """Save selected feature list for reproducible scans."""
        SELECTED_FEATURES_PATH.write_text(json.dumps({
            "selected_features": selected,
            "n_selected": len(selected),
        }, indent=2))
        logger.info("[FEAT] Saved %d features to %s" % (len(selected), SELECTED_FEATURES_PATH))

    @staticmethod
    def load_selected() -> Optional[List[str]]:
        """Load previously saved feature selection. Returns None if not found."""
        if not SELECTED_FEATURES_PATH.exists():
            return None
        try:
            data = json.loads(SELECTED_FEATURES_PATH.read_text())
            return data.get("selected_features")
        except Exception:
            return None
