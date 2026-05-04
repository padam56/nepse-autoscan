"""
src/email_throttle.py -- Daily email/telegram rate limiter.

Prevents spam by capping sends per category per day.

Policy: ONE email + ONE telegram per NEPSE trading day. No exceptions
unless category="critical" (system errors only).

Categories:
    morning_scan     -> 1/day (the daily quality email)
    telegram_daily   -> 1/day (the daily quality telegram message)
    critical         -> unlimited (system errors -- e.g., crontab wiped)

All other categories (afternoon_exits, telegram_intraday, weekly_recap)
are deprecated and capped at 0 per day. Their cron entries are removed.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

NPT = timezone(timedelta(hours=5, minutes=45))
STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "email_throttle.json"


def _load() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# Deprecated categories -- HARD BLOCKED regardless of caller's max_per_day.
# This is defense-in-depth: even if some code path forgets to remove a send call,
# we won't spam the user.
DEPRECATED = {
    "afternoon_exits",
    "telegram_intraday",
    "telegram_alert",
    "weekly_recap",
    "telegram_morning_scan",
    "telegram_afternoon",
}


def allow(category: str, max_per_day: int = 1, dedupe_key: str = "") -> bool:
    """Check and record whether a send is allowed today.

    Args:
        category: bucket name. Allowed values: 'morning_scan', 'telegram_daily',
                  'critical'. Anything in DEPRECATED is rejected silently.
        max_per_day: cap for this category (NPT day). Hard-capped at 1 unless
                     category="critical".
        dedupe_key: optional payload hash — if the same key was sent today,
                    return False even if under the cap.

    Returns True if sending is allowed (and records the send).
    """
    if category in DEPRECATED:
        return False  # spam protection -- these channels are off

    # Enforce hard 1/day cap for non-critical channels
    if category != "critical":
        max_per_day = min(max_per_day, 1)

    today = datetime.now(NPT).strftime("%Y-%m-%d")
    state = _load()
    bucket = state.setdefault(category, {})

    # Reset counter if it's a new NPT day
    if bucket.get("date") != today:
        bucket.clear()
        bucket["date"] = today
        bucket["count"] = 0
        bucket["keys"] = []

    if dedupe_key and dedupe_key in bucket.get("keys", []):
        return False  # duplicate content today — skip

    if bucket.get("count", 0) >= max_per_day:
        return False  # cap reached

    bucket["count"] = bucket.get("count", 0) + 1
    if dedupe_key:
        bucket.setdefault("keys", []).append(dedupe_key)

    _save(state)
    return True


def status() -> dict:
    """Return today's send counts per category."""
    today = datetime.now(NPT).strftime("%Y-%m-%d")
    state = _load()
    return {
        cat: (b.get("count", 0) if b.get("date") == today else 0)
        for cat, b in state.items()
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(status(), indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "reset":
        STATE_FILE.unlink(missing_ok=True)
        print("Throttle state reset.")
    else:
        print("Usage: python3 email_throttle.py [status|reset]")
