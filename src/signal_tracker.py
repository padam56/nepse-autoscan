"""
Signal Performance Tracker - Logs daily scanner picks and measures accuracy.

Tracks every signal the daily scanner generates, then evaluates whether
the predicted direction was correct after 5 trading days.
"""

import fcntl
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


class SignalTracker:
    """Logs daily picks and tracks their 5-day forward returns."""

    TRACKER_FILE = ROOT / "data" / "signal_log.json"

    def __init__(self):
        os.makedirs(self.TRACKER_FILE.parent, exist_ok=True)

    # ── Persistence helpers ───────────────────────────────────────

    def _read_log(self) -> list[dict]:
        """Read the signal log from disk."""
        if not self.TRACKER_FILE.exists():
            return []
        try:
            with open(self.TRACKER_FILE, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                data = json.load(f)
                fcntl.flock(f, fcntl.LOCK_UN)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, IOError):
            return []

    def _write_log(self, records: list[dict]) -> None:
        """Atomically write the signal log to disk.

        Writes to a temporary file first, then renames so readers never
        see a half-written file.
        """
        os.makedirs(self.TRACKER_FILE.parent, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=self.TRACKER_FILE.parent, suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                json.dump(records, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
                fcntl.flock(f, fcntl.LOCK_UN)
            os.replace(tmp_path, self.TRACKER_FILE)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ── Core API ──────────────────────────────────────────────────

    def log_signals(self, date: str, picks: list[dict]) -> int:
        """Save today's picks into the signal log.

        Each pick dict should contain:
            symbol, signal, score, ta, ml, gru, kelly_pct

        An optional ``price_at_signal`` key is also accepted.  If the
        caller does not supply it the field is stored as ``None`` so that
        ``evaluate_pending`` can fill it in later from the history data.

        Returns the number of new entries appended (duplicates by
        date+symbol are skipped).
        """
        records = self._read_log()

        existing_keys = {
            (r["date"], r["symbol"]) for r in records
        }

        added = 0
        for pick in picks:
            symbol = pick.get("symbol", "").upper()
            if not symbol:
                continue
            key = (date, symbol)
            if key in existing_keys:
                continue

            records.append({
                "date": date,
                "symbol": symbol,
                "signal": pick.get("signal", "HOLD"),
                "score": pick.get("score"),
                "ta": pick.get("ta"),
                "ml": pick.get("ml"),
                "gru": pick.get("gru"),
                "kelly_pct": pick.get("kelly_pct"),
                "price_at_signal": pick.get("price_at_signal"),
                "price_after_5d": None,
                "return_5d_pct": None,
                "hit": None,
                "evaluated_at": None,
            })
            existing_keys.add(key)
            added += 1

        if added:
            self._write_log(records)
        return added

    def evaluate_pending(self, histories: dict) -> int:
        """Evaluate picks that are at least 5 trading days old.

        Args:
            histories: ``{symbol: [{'date': 'YYYY-MM-DD', 'close': float}, ...]}``
                       Each list should be sorted ascending by date.  The
                       ``close`` key may alternatively be named ``ltp`` or
                       ``price``.

        Returns:
            Number of entries newly evaluated.
        """
        records = self._read_log()
        today = datetime.now().date()
        evaluated = 0

        for rec in records:
            # Skip already-evaluated entries
            if rec.get("hit") is not None:
                continue

            signal_date = self._parse_date(rec["date"])
            if signal_date is None:
                continue

            # Need at least 5 trading days (roughly 7 calendar days)
            if (today - signal_date).days < 7:
                continue

            symbol = rec["symbol"]
            price_series = histories.get(symbol)
            if not price_series:
                continue

            price_at_signal = self._find_price(price_series, signal_date)
            if price_at_signal is None:
                # Try the signal-date price already stored on the record
                price_at_signal = rec.get("price_at_signal")
            if price_at_signal is None or price_at_signal == 0:
                continue

            # Find price ~5 trading days later
            price_after = self._find_price_after_n_trading_days(
                price_series, signal_date, n=5
            )
            if price_after is None:
                continue

            return_pct = round(
                (price_after - price_at_signal) / price_at_signal * 100, 2
            )

            signal_type = (rec.get("signal") or "").upper()
            if signal_type in ("BUY", "STRONG BUY"):
                hit = return_pct > 0
            elif signal_type in ("SELL", "STRONG SELL"):
                hit = return_pct < 0
            else:
                # HOLD -- count as hit if absolute move < 2%
                hit = abs(return_pct) < 2.0

            rec["price_at_signal"] = round(price_at_signal, 2)
            rec["price_after_5d"] = round(price_after, 2)
            rec["return_5d_pct"] = return_pct
            rec["hit"] = hit
            rec["evaluated_at"] = today.isoformat()
            evaluated += 1

        if evaluated:
            self._write_log(records)
        return evaluated

    def get_stats(self, lookback_days: int = 30) -> dict:
        """Rolling performance metrics over the last ``lookback_days``."""
        cutoff = (
            datetime.now().date() - timedelta(days=lookback_days)
        ).isoformat()

        records = self._read_log()
        recent = [
            r for r in records if r.get("date", "") >= cutoff
        ]

        total = len(recent)
        evald = [r for r in recent if r.get("hit") is not None]
        hits = [r for r in evald if r["hit"]]

        hit_rate = (len(hits) / len(evald) * 100) if evald else 0.0

        returns = [
            r["return_5d_pct"] for r in evald
            if r.get("return_5d_pct") is not None
        ]
        avg_return = (
            round(sum(returns) / len(returns), 2) if returns else 0.0
        )

        best = max(evald, key=lambda r: r.get("return_5d_pct", -999), default=None)
        worst = min(evald, key=lambda r: r.get("return_5d_pct", 999), default=None)

        # Breakdown by signal type
        by_signal: dict[str, dict] = {}
        for r in evald:
            sig = r.get("signal", "UNKNOWN")
            bucket = by_signal.setdefault(sig, {"count": 0, "hits": 0, "returns": []})
            bucket["count"] += 1
            if r["hit"]:
                bucket["hits"] += 1
            if r.get("return_5d_pct") is not None:
                bucket["returns"].append(r["return_5d_pct"])

        by_signal_clean = {}
        for sig, b in by_signal.items():
            by_signal_clean[sig] = {
                "count": b["count"],
                "hit_rate": round(b["hits"] / b["count"] * 100, 1) if b["count"] else 0.0,
                "avg_return": (
                    round(sum(b["returns"]) / len(b["returns"]), 2)
                    if b["returns"] else 0.0
                ),
            }

        return {
            "total_signals": total,
            "evaluated": len(evald),
            "hit_rate": round(hit_rate, 1),
            "avg_return_pct": avg_return,
            "best_pick": (
                f"{best['symbol']} ({best['signal']}) +{best['return_5d_pct']}%"
                if best else "N/A"
            ),
            "worst_pick": (
                f"{worst['symbol']} ({worst['signal']}) {worst['return_5d_pct']}%"
                if worst else "N/A"
            ),
            "by_signal": by_signal_clean,
        }

    def summary_text(self, lookback_days: int = 30) -> str:
        """Human-readable summary for email or console output."""
        stats = self.get_stats(lookback_days)

        lines = [
            "",
            " Signal Performance Tracker ".center(60, "="),
            f"  Period          : last {lookback_days} days",
            f"  Total signals   : {stats['total_signals']}",
            f"  Evaluated       : {stats['evaluated']}",
            f"  Hit rate        : {stats['hit_rate']}%",
            f"  Avg 5d return   : {stats['avg_return_pct']}%",
            f"  Best pick       : {stats['best_pick']}",
            f"  Worst pick      : {stats['worst_pick']}",
        ]

        if stats["by_signal"]:
            lines.append("")
            lines.append(" Breakdown by Signal Type ".center(60, "-"))
            lines.append(
                f"  {'Signal':<14} {'Count':>6} {'Hit%':>7} {'Avg Ret':>9}"
            )
            lines.append("  " + "-" * 38)
            for sig, b in sorted(stats["by_signal"].items()):
                lines.append(
                    f"  {sig:<14} {b['count']:>6} {b['hit_rate']:>6.1f}% {b['avg_return']:>8.2f}%"
                )

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    # ── Private helpers ───────────────────────────────────────────

    @staticmethod
    def _parse_date(date_str: str) -> Optional["datetime.date"]:
        """Parse a YYYY-MM-DD string into a date object."""
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _close_price(row: dict) -> Optional[float]:
        """Extract closing price from a history row, trying common key names."""
        for key in ("close", "ltp", "price", "Close", "LTP", "Price"):
            val = row.get(key)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    continue
        return None

    @staticmethod
    def _row_date(row: dict) -> Optional[str]:
        """Extract date string from a history row."""
        for key in ("date", "Date", "businessDate", "tradingDate"):
            val = row.get(key)
            if val is not None:
                # Handle datetime strings with time component
                return str(val)[:10]
        return None

    def _find_price(
        self, series: list[dict], target_date: "datetime.date"
    ) -> Optional[float]:
        """Find the closing price on or nearest before ``target_date``."""
        target_str = target_date.isoformat()
        best_price = None
        best_date = None

        for row in series:
            row_date_str = self._row_date(row)
            if row_date_str is None:
                continue
            if row_date_str > target_str:
                continue
            if best_date is None or row_date_str >= best_date:
                price = self._close_price(row)
                if price is not None:
                    best_price = price
                    best_date = row_date_str
        return best_price

    def _find_price_after_n_trading_days(
        self, series: list[dict], signal_date: "datetime.date", n: int = 5
    ) -> Optional[float]:
        """Find the closing price ``n`` trading days after ``signal_date``.

        Trading days are counted as distinct dates present in the series
        that fall strictly after ``signal_date``.
        """
        signal_str = signal_date.isoformat()

        # Collect unique future dates with prices
        future: dict[str, float] = {}
        for row in series:
            row_date_str = self._row_date(row)
            if row_date_str is None or row_date_str <= signal_str:
                continue
            price = self._close_price(row)
            if price is not None:
                future[row_date_str] = price

        if not future:
            return None

        sorted_dates = sorted(future.keys())
        # Use the n-th trading day if available, otherwise the latest we have
        # (only if we have at least n days)
        if len(sorted_dates) >= n:
            return future[sorted_dates[n - 1]]
        return None
