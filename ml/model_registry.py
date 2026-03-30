"""
Model Registry -- Version tracking, A/B comparison, and rollback for ML models.

Stores metadata in data/model_registry.json.
Keeps model files versioned in data/models/archive/.

Integration points (do not modify these files -- document only):
  1. gru_predictor.py::train_symbol_fast() -- after saving model, call
     registry.register('gru', symbol, metrics, model_path)
  2. daily_scanner.py -- after GRU scoring, call
     registry.record_prediction() for each stock
  3. signal_tracker.py::evaluate_pending() -- when outcomes are known,
     update predictions with actual values via
     registry.update_actuals()
"""

import fcntl
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


class ModelRegistry:
    """Track model versions, training metrics, and live performance.

    Stores metadata in data/model_registry.json.
    Keeps model files versioned in data/models/archive/.
    """

    REGISTRY_FILE = ROOT / "data" / "model_registry.json"
    ARCHIVE_DIR = ROOT / "data" / "models" / "archive"

    # Rollback threshold: trigger if current accuracy is worse by this margin
    ROLLBACK_MARGIN = 0.05
    # Minimum predictions required before rollback recommendation kicks in
    MIN_PREDICTIONS_FOR_ROLLBACK = 10

    def __init__(self):
        os.makedirs(self.REGISTRY_FILE.parent, exist_ok=True)
        os.makedirs(self.ARCHIVE_DIR, exist_ok=True)

    # ── Persistence helpers (mirroring signal_tracker.py patterns) ──

    def _read_registry(self) -> dict:
        """Read the registry JSON from disk."""
        if not self.REGISTRY_FILE.exists():
            return {"models": {}, "last_cleanup": None}
        try:
            with open(self.REGISTRY_FILE, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                data = json.load(f)
                fcntl.flock(f, fcntl.LOCK_UN)
            if not isinstance(data, dict) or "models" not in data:
                return {"models": {}, "last_cleanup": None}
            return data
        except (json.JSONDecodeError, IOError):
            return {"models": {}, "last_cleanup": None}

    def _write_registry(self, data: dict) -> None:
        """Atomically write the registry to disk.

        Writes to a temporary file first, then renames so readers never
        see a half-written file.
        """
        os.makedirs(self.REGISTRY_FILE.parent, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=self.REGISTRY_FILE.parent, suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                json.dump(data, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
                fcntl.flock(f, fcntl.LOCK_UN)
            os.replace(tmp_path, self.REGISTRY_FILE)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def _model_key(model_type: str, symbol: str) -> str:
        """Build the registry dict key, e.g. 'gru_NABIL'."""
        return f"{model_type}_{symbol}"

    # ── Core API ────────────────────────────────────────────────────

    def register(
        self,
        model_type: str,
        symbol: str,
        metrics: dict,
        model_path: Path,
    ) -> str:
        """Register a newly trained model.

        Args:
            model_type: 'gru', 'transformer', 'xgb_lgb'
            symbol: stock symbol or 'ensemble' for cross-sectional
            metrics: {val_acc, test_acc, dir_acc, precision, ...}
            model_path: path to the saved model file

        Returns:
            version_id  (e.g. 'gru_NABIL_v3_20260328')

        Side-effects:
            - Archives the *previous* active version to data/models/archive/
            - Increments version counter
            - Records training timestamp and metrics
        """
        model_path = Path(model_path)
        registry = self._read_registry()
        key = self._model_key(model_type, symbol)
        today_str = datetime.now().strftime("%Y%m%d")

        entry = registry["models"].setdefault(
            key, {"versions": [], "current_version": 0}
        )

        # Archive previous active version (move its model file to archive/)
        for ver in entry["versions"]:
            if ver["status"] == "active":
                ver["status"] = "archived"
                prev_path = Path(ver["model_path"])
                if prev_path.is_absolute():
                    src = prev_path
                else:
                    src = ROOT / prev_path
                if src.exists() and src != model_path:
                    safe_sym = symbol.replace("/", "_")
                    archive_name = f"{safe_sym}_{model_type}_v{ver['version']}.pt"
                    dest = self.ARCHIVE_DIR / archive_name
                    shutil.copy2(str(src), str(dest))
                    ver["model_path"] = str(
                        dest.relative_to(ROOT)
                    )

        # Determine new version number
        new_version = entry["current_version"] + 1
        version_id = f"{model_type}_{symbol}_v{new_version}_{today_str}"

        # Store model_path relative to ROOT for portability
        try:
            rel_path = str(model_path.relative_to(ROOT))
        except ValueError:
            rel_path = str(model_path)

        version_record = {
            "version": new_version,
            "version_id": version_id,
            "trained_at": datetime.now().isoformat(),
            "metrics": metrics,
            "model_path": rel_path,
            "predictions": [],
            "live_accuracy": None,
            "status": "active",
        }

        entry["versions"].append(version_record)
        entry["current_version"] = new_version

        self._write_registry(registry)
        return version_id

    def get_active_version(self, model_type: str, symbol: str) -> Optional[dict]:
        """Get currently active model version info.

        Returns the version record dict, or None if no model is registered.
        """
        registry = self._read_registry()
        key = self._model_key(model_type, symbol)
        entry = registry["models"].get(key)
        if not entry:
            return None
        for ver in reversed(entry["versions"]):
            if ver["status"] == "active":
                return ver
        return None

    def get_history(
        self, model_type: str, symbol: str, last_n: int = 10
    ) -> list:
        """Get training history for a model.

        Returns a list of version records (most recent last), limited to
        the last ``last_n`` entries.
        """
        registry = self._read_registry()
        key = self._model_key(model_type, symbol)
        entry = registry["models"].get(key)
        if not entry:
            return []
        versions = entry["versions"]
        return versions[-last_n:]

    def record_prediction(
        self,
        model_type: str,
        symbol: str,
        date: str,
        predicted: int,
        actual: Optional[int] = None,
    ) -> None:
        """Log a prediction for the currently active version.

        Args:
            model_type: 'gru', 'transformer', 'xgb_lgb'
            symbol: stock symbol
            date: prediction date (YYYY-MM-DD)
            predicted: predicted class (0-4)
            actual: actual class, filled in later by signal tracker
        """
        registry = self._read_registry()
        key = self._model_key(model_type, symbol)
        entry = registry["models"].get(key)
        if not entry:
            return

        # Find active version
        active = None
        for ver in entry["versions"]:
            if ver["status"] == "active":
                active = ver
                break
        if active is None:
            return

        # Avoid duplicate predictions for the same date
        existing_dates = {p["date"] for p in active["predictions"]}
        if date in existing_dates:
            return

        correct = None
        if actual is not None:
            correct = predicted == actual

        active["predictions"].append(
            {
                "date": date,
                "predicted": predicted,
                "actual": actual,
                "correct": correct,
            }
        )

        # Recompute live accuracy
        self._recompute_live_accuracy(active)
        self._write_registry(registry)

    def update_actuals(
        self,
        model_type: str,
        symbol: str,
        date: str,
        actual: int,
    ) -> None:
        """Fill in the actual outcome for a previously recorded prediction.

        Called by signal_tracker.evaluate_pending() once outcomes are known.
        """
        registry = self._read_registry()
        key = self._model_key(model_type, symbol)
        entry = registry["models"].get(key)
        if not entry:
            return

        changed = False
        for ver in entry["versions"]:
            for pred in ver["predictions"]:
                if pred["date"] == date and pred["actual"] is None:
                    pred["actual"] = actual
                    pred["correct"] = pred["predicted"] == actual
                    changed = True
            if changed:
                self._recompute_live_accuracy(ver)

        if changed:
            self._write_registry(registry)

    def evaluate_versions(self, model_type: str, symbol: str) -> dict:
        """Compare performance of current vs previous version.

        Returns:
            {
                'current_version': 'v3',
                'current_live_acc': 0.62,
                'previous_version': 'v2',
                'previous_live_acc': 0.58,
                'improvement': +0.04,
                'n_predictions': 25,
                'recommendation': 'KEEP' or 'ROLLBACK'
            }
        """
        registry = self._read_registry()
        key = self._model_key(model_type, symbol)
        entry = registry["models"].get(key)
        if not entry or len(entry["versions"]) < 2:
            return {
                "current_version": None,
                "current_live_acc": None,
                "previous_version": None,
                "previous_live_acc": None,
                "improvement": None,
                "n_predictions": 0,
                "recommendation": "INSUFFICIENT_DATA",
            }

        current = entry["versions"][-1]
        previous = entry["versions"][-2]

        cur_acc = current.get("live_accuracy")
        prev_acc = previous.get("live_accuracy")

        evaluated_preds = [
            p for p in current["predictions"] if p.get("correct") is not None
        ]
        n_preds = len(evaluated_preds)

        improvement = None
        recommendation = "INSUFFICIENT_DATA"
        if cur_acc is not None and prev_acc is not None:
            improvement = round(cur_acc - prev_acc, 4)
            if n_preds >= self.MIN_PREDICTIONS_FOR_ROLLBACK:
                if cur_acc < prev_acc - self.ROLLBACK_MARGIN:
                    recommendation = "ROLLBACK"
                else:
                    recommendation = "KEEP"

        return {
            "current_version": f"v{current['version']}",
            "current_live_acc": cur_acc,
            "previous_version": f"v{previous['version']}",
            "previous_live_acc": prev_acc,
            "improvement": improvement,
            "n_predictions": n_preds,
            "recommendation": recommendation,
        }

    def suggest_rollback(self, model_type: str, symbol: str) -> bool:
        """Return True if current model is significantly worse than previous.

        Threshold: current accuracy < previous - 5% AND n_predictions >= 10.
        """
        result = self.evaluate_versions(model_type, symbol)
        return result["recommendation"] == "ROLLBACK"

    def cleanup_old_versions(self, keep_last_n: int = 3) -> int:
        """Delete archived models older than keep_last_n versions per symbol.

        Returns the number of archive files deleted.
        """
        registry = self._read_registry()
        deleted = 0

        for key, entry in registry["models"].items():
            versions = entry["versions"]
            if len(versions) <= keep_last_n:
                continue

            # Versions to remove: everything before the last keep_last_n
            to_remove = versions[: -keep_last_n]
            for ver in to_remove:
                # Delete archived model file from disk
                mp = ver.get("model_path", "")
                if mp:
                    full_path = Path(mp)
                    if not full_path.is_absolute():
                        full_path = ROOT / mp
                    if full_path.exists() and self.ARCHIVE_DIR in full_path.parents:
                        try:
                            full_path.unlink()
                            deleted += 1
                        except OSError:
                            pass

            # Keep only the last keep_last_n versions in the registry
            entry["versions"] = versions[-keep_last_n:]

        registry["last_cleanup"] = datetime.now().date().isoformat()
        self._write_registry(registry)
        return deleted

    def summary(self) -> str:
        """Formatted summary of all registered models."""
        registry = self._read_registry()
        models = registry.get("models", {})

        if not models:
            return "Model Registry: empty -- no models registered."

        lines = [
            "",
            " Model Registry Summary ".center(70, "="),
            f"  {'Key':<25} {'Ver':>4} {'Status':<9} {'Val Acc':>8} "
            f"{'Live Acc':>9} {'Preds':>6}",
            "  " + "-" * 64,
        ]

        for key in sorted(models.keys()):
            entry = models[key]
            for ver in entry["versions"]:
                val_acc = ver["metrics"].get("val_acc")
                val_str = f"{val_acc:.3f}" if val_acc is not None else "   N/A"
                live_acc = ver.get("live_accuracy")
                live_str = f"{live_acc:.3f}" if live_acc is not None else "    N/A"
                n_preds = len(ver.get("predictions", []))
                lines.append(
                    f"  {key:<25} v{ver['version']:>3} {ver['status']:<9} "
                    f"{val_str:>8} {live_str:>9} {n_preds:>6}"
                )

        # Rollback recommendations
        rollback_candidates = []
        for key in models:
            parts = key.split("_", 1)
            if len(parts) == 2:
                mtype, sym = parts
                ev = self.evaluate_versions(mtype, sym)
                if ev["recommendation"] == "ROLLBACK":
                    rollback_candidates.append(
                        f"  [!] {key}: current v{ev['current_version']} "
                        f"({ev['current_live_acc']:.3f}) worse than "
                        f"v{ev['previous_version']} "
                        f"({ev['previous_live_acc']:.3f})"
                    )

        if rollback_candidates:
            lines.append("")
            lines.append(" Rollback Recommendations ".center(70, "-"))
            lines.extend(rollback_candidates)

        lines.append("")
        lines.append("=" * 70)
        return "\n".join(lines)

    # ── Private helpers ─────────────────────────────────────────────

    @staticmethod
    def _recompute_live_accuracy(version_record: dict) -> None:
        """Recompute live_accuracy from the predictions list."""
        evaluated = [
            p for p in version_record["predictions"]
            if p.get("correct") is not None
        ]
        if evaluated:
            correct = sum(1 for p in evaluated if p["correct"])
            version_record["live_accuracy"] = round(
                correct / len(evaluated), 4
            )
        else:
            version_record["live_accuracy"] = None
