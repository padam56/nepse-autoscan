"""Data quality gate -- validates price data before pipeline execution."""

from datetime import datetime, timedelta


class DataQualityGate:
    """Validates price data before pipeline execution."""

    # ── Thresholds ────────────────────────────────────────────────
    MIN_STOCKS_WARN = 200
    MIN_STOCKS_HARD = 100
    MAX_ZERO_PRICE_HARD = 50
    MAX_STALE_DAYS_WARN = 2
    MAX_STALE_DAYS_HARD = 5
    EXTREME_MOVE_PCT = 10.0
    EXTREME_MOVE_WARN_COUNT = 50
    ZERO_VOLUME_RATIO = 0.50

    # ── Public API ────────────────────────────────────────────────

    def check_all(self, histories: dict) -> tuple[bool, list[str]]:
        """Run all checks. Returns (pass: bool, warnings: list[str])."""
        warnings: list[str] = []

        checks = [
            self._check_stock_count,
            self._check_zero_prices,
            self._check_extreme_moves,
            self._check_stale_data,
            self._check_duplicate_dates,
            self._check_volume_anomalies,
        ]

        for check in checks:
            warnings.extend(check(histories))

        hard_fail = self._has_hard_fail(histories, warnings)

        for w in warnings:
            print(f"[DATA] {w}")

        if hard_fail:
            print("[DATA] HARD FAIL -- pipeline should not proceed.")
        elif warnings:
            print(f"[DATA] Passed with {len(warnings)} warning(s).")
        else:
            print("[DATA] All checks passed.")

        return (not hard_fail, warnings)

    # ── Individual Checks ─────────────────────────────────────────

    def _check_stock_count(self, histories: dict) -> list[str]:
        """Warn if fewer than 200 stocks (normal is 310+)."""
        count = len(histories)
        warnings = []
        if count < self.MIN_STOCKS_HARD:
            warnings.append(
                f"Stock count critically low: {count} (hard fail threshold: {self.MIN_STOCKS_HARD})"
            )
        elif count < self.MIN_STOCKS_WARN:
            warnings.append(
                f"Stock count below expected: {count} (expected 310+)"
            )
        return warnings

    def _check_zero_prices(self, histories: dict) -> list[str]:
        """Flag stocks with zero or negative close prices."""
        bad_stocks = []
        for symbol, records in histories.items():
            if not records:
                continue
            latest = records[-1] if records else None
            if latest and _get_close(latest) <= 0:
                bad_stocks.append(symbol)

        warnings = []
        if bad_stocks:
            count = len(bad_stocks)
            sample = ", ".join(bad_stocks[:10])
            suffix = f" (and {count - 10} more)" if count > 10 else ""
            warnings.append(
                f"{count} stock(s) with zero/negative close price: {sample}{suffix}"
            )
        return warnings

    def _check_extreme_moves(self, histories: dict) -> list[str]:
        """Flag stocks that moved >10% in a day (NEPSE circuit breaker is 10%)."""
        extreme_stocks = []
        for symbol, records in histories.items():
            if len(records) < 2:
                continue
            latest = records[-1]
            previous = records[-2]
            curr_close = _get_close(latest)
            prev_close = _get_close(previous)
            if prev_close <= 0:
                continue
            pct = abs((curr_close - prev_close) / prev_close) * 100
            if pct > self.EXTREME_MOVE_PCT:
                extreme_stocks.append((symbol, pct))

        warnings = []
        if extreme_stocks:
            count = len(extreme_stocks)
            sample = ", ".join(
                f"{s} ({p:.1f}%)" for s, p in extreme_stocks[:10]
            )
            suffix = f" (and {count - 10} more)" if count > 10 else ""
            msg = f"{count} stock(s) moved >{self.EXTREME_MOVE_PCT}%: {sample}{suffix}"
            if count > self.EXTREME_MOVE_WARN_COUNT:
                msg += " -- excessive count suggests bad data"
            warnings.append(msg)
        return warnings

    def _check_stale_data(self, histories: dict) -> list[str]:
        """Warn if the most recent date in price_history is >2 trading days old."""
        most_recent = _find_most_recent_date(histories)
        if most_recent is None:
            return ["Could not determine most recent data date."]

        today = datetime.now().date()
        delta_days = _trading_days_between(most_recent, today)

        warnings = []
        if delta_days > self.MAX_STALE_DAYS_HARD:
            warnings.append(
                f"Data is critically stale: most recent date is {most_recent} "
                f"(~{delta_days} trading days old, hard fail threshold: {self.MAX_STALE_DAYS_HARD})"
            )
        elif delta_days > self.MAX_STALE_DAYS_WARN:
            warnings.append(
                f"Data may be stale: most recent date is {most_recent} "
                f"(~{delta_days} trading days old)"
            )
        return warnings

    def _check_duplicate_dates(self, histories: dict) -> list[str]:
        """Check for duplicate date entries per stock."""
        dup_stocks = []
        for symbol, records in histories.items():
            dates = [r.get("date", "") for r in records]
            if len(dates) != len(set(dates)):
                dup_count = len(dates) - len(set(dates))
                dup_stocks.append((symbol, dup_count))

        warnings = []
        if dup_stocks:
            count = len(dup_stocks)
            sample = ", ".join(
                f"{s} ({n} dups)" for s, n in dup_stocks[:10]
            )
            suffix = f" (and {count - 10} more)" if count > 10 else ""
            warnings.append(
                f"{count} stock(s) with duplicate dates: {sample}{suffix}"
            )
        return warnings

    def _check_volume_anomalies(self, histories: dict) -> list[str]:
        """Flag if >50% of stocks have zero volume (exchange likely closed)."""
        if not histories:
            return []

        zero_vol_count = 0
        total = 0
        for symbol, records in histories.items():
            if not records:
                continue
            total += 1
            latest = records[-1]
            if latest.get("volume", latest.get("q", 0)) == 0:
                zero_vol_count += 1

        warnings = []
        if total > 0 and (zero_vol_count / total) > self.ZERO_VOLUME_RATIO:
            pct = (zero_vol_count / total) * 100
            warnings.append(
                f"{zero_vol_count}/{total} stocks ({pct:.0f}%) have zero volume "
                f"on latest day -- exchange may have been closed or data is stale"
            )
        return warnings

    # ── Hard Fail Logic ───────────────────────────────────────────

    def _has_hard_fail(self, histories: dict, warnings: list[str]) -> bool:
        """Determine if any condition warrants a hard pipeline block."""
        # Fewer than 100 stocks
        if len(histories) < self.MIN_STOCKS_HARD:
            return True

        # More than 50 stocks with zero/negative close
        zero_count = 0
        for records in histories.values():
            if records and _get_close(records[-1]) <= 0:
                zero_count += 1
        if zero_count > self.MAX_ZERO_PRICE_HARD:
            return True

        # Most recent data >5 trading days old
        most_recent = _find_most_recent_date(histories)
        if most_recent is not None:
            today = datetime.now().date()
            if _trading_days_between(most_recent, today) > self.MAX_STALE_DAYS_HARD:
                return True

        return False


# ── Helper Functions ──────────────────────────────────────────────


def _get_close(record: dict) -> float:
    """Extract close price from a record, defaulting to 0."""
    try:
        return float(record.get("lp", record.get("close", 0)))
    except (TypeError, ValueError):
        return 0.0


def _find_most_recent_date(histories: dict):
    """Find the most recent date across all stocks. Returns date or None."""
    latest = None
    for records in histories.values():
        if not records:
            continue
        date_str = records[-1].get("date", "")
        parsed = _parse_date(date_str)
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    return latest


def _parse_date(date_str: str):
    """Parse a date string, trying common formats. Returns date or None."""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _trading_days_between(start, end) -> int:
    """Approximate trading days between two dates (excludes Sat/Sun).
    NEPSE trades Sun-Thu, but this simple weekday count is close enough
    for a staleness check."""
    if start > end:
        return 0
    count = 0
    current = start + timedelta(days=1)
    while current <= end:
        # NEPSE week: Sun(6) Mon(0) Tue(1) Wed(2) Thu(3) are trading days
        # Fri(4) and Sat(5) are off
        if current.weekday() not in (4, 5):  # Friday, Saturday
            count += 1
        current += timedelta(days=1)
    return count
