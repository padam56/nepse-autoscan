#!/usr/bin/env python3
"""
weight_optimizer.py -- Learn optimal ensemble weights from historical signal performance.

Reads the signal_log.json produced by SignalTracker, then uses scipy.optimize.minimize
(SLSQP) to find weights [w_ml, w_gru, w_ta] that maximize rank correlation (Spearman)
between the weighted ensemble score and actual 5-day forward returns.
"""

import json
from pathlib import Path
from typing import Dict

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_LOG_PATH = ROOT / "data" / "signal_log.json"

# Defaults used when insufficient data is available
DEFAULT_WEIGHTS = {"w_ml": 0.45, "w_gru": 0.30, "w_ta": 0.25}
MIN_EVALUATED_SIGNALS = 50


def _load_evaluated_signals(signal_log_path: Path) -> list:
    """Load signals that have been evaluated (have actual 5d returns)."""
    if not signal_log_path.exists():
        return []
    try:
        with open(signal_log_path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

    if not isinstance(data, list):
        return []

    evaluated = []
    for rec in data:
        # Must have been evaluated and have all three component scores
        if rec.get("return_5d_pct") is None:
            continue
        ta = rec.get("ta")
        ml = rec.get("ml")
        gru = rec.get("gru")
        if ta is None or ml is None:
            continue
        # GRU may be missing for some signals; skip those
        if gru is None:
            continue
        try:
            evaluated.append({
                "ta": float(ta),
                "ml": float(ml),
                "gru": float(gru),
                "return_5d": float(rec["return_5d_pct"]),
            })
        except (ValueError, TypeError):
            continue

    return evaluated


def optimize_ensemble_weights(signal_log_path: Path = SIGNAL_LOG_PATH) -> Dict[str, float]:
    """Learn optimal ensemble weights from historical signal performance.

    Reads signal_log.json (from signal tracker).
    For each evaluated signal, we have: ta, ml, gru scores and actual 5d return.

    Use scipy.optimize.minimize to find weights [w_ml, w_gru, w_ta] that maximize
    rank correlation (Spearman) between weighted_score and actual_return.

    Constraints: weights sum to 1.0, each weight >= 0.05

    Returns: {'w_ml': float, 'w_gru': float, 'w_ta': float}
    Falls back to default {0.45, 0.30, 0.25} if insufficient data (< 50 evaluated signals).
    """
    signals = _load_evaluated_signals(signal_log_path)

    if len(signals) < MIN_EVALUATED_SIGNALS:
        return dict(DEFAULT_WEIGHTS)

    from scipy.optimize import minimize
    from scipy.stats import spearmanr

    # Build arrays: columns are [ml, gru, ta]
    scores = np.array([[s["ml"], s["gru"], s["ta"]] for s in signals])
    returns = np.array([s["return_5d"] for s in signals])

    def neg_spearman(w):
        """Negative Spearman correlation (we minimize, so negate)."""
        weighted = scores @ w
        corr, _ = spearmanr(weighted, returns)
        if np.isnan(corr):
            return 0.0  # no correlation
        return -corr

    # Constraints: weights sum to 1.0
    constraints = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    # Bounds: each weight >= 0.05
    bounds = [(0.05, 1.0)] * 3

    # Initial guess: default weights
    x0 = np.array([DEFAULT_WEIGHTS["w_ml"], DEFAULT_WEIGHTS["w_gru"], DEFAULT_WEIGHTS["w_ta"]])

    result = minimize(
        neg_spearman,
        x0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 200, "ftol": 1e-8},
    )

    if result.success:
        w_ml, w_gru, w_ta = result.x
        # Round to 3 decimal places for cleanliness
        return {
            "w_ml": round(float(w_ml), 3),
            "w_gru": round(float(w_gru), 3),
            "w_ta": round(float(w_ta), 3),
        }
    else:
        # Optimization did not converge; use defaults
        return dict(DEFAULT_WEIGHTS)


if __name__ == "__main__":
    weights = optimize_ensemble_weights()
    print("Ensemble weights:", weights)
