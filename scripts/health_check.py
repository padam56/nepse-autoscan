#!/usr/bin/env python3
"""
Daily health check for NEPSE system.

Runs at 11 PM NPT. Alerts on Telegram if anything is wrong:
- Crontab empty
- Scanner log not updated today
- Dashboard HTML stale (>24h)
- Git push failed (PERFORMANCE.md not updated today)
- Paper portfolio hasn't been updated today
"""
import os
import subprocess
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NPT = timezone(timedelta(hours=5, minutes=45))
TODAY = datetime.now(NPT).strftime("%Y-%m-%d")


def _age_hours(path: Path) -> float:
    """Return age in hours of a file."""
    if not path.exists():
        return float("inf")
    age = datetime.now().timestamp() - path.stat().st_mtime
    return age / 3600


def check_crontab():
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if result.returncode != 0:
            return False, "Crontab is empty"
        lines = [l for l in result.stdout.splitlines()
                 if l and not l.startswith("#") and not l.startswith(("SHELL=", "PYTHONPATH=", "HOME="))]
        if len(lines) < 5:
            return False, f"Only {len(lines)} cron entries (expected 10+)"
        return True, f"{len(lines)} entries"
    except Exception as e:
        return False, f"Error: {e}"


def check_scanner():
    log = ROOT / "logs" / "scanner.log"
    age = _age_hours(log)
    weekday = datetime.now(NPT).weekday()  # Mon=0, Sun=6
    # Nepal trading: Sun(6) to Thu(3) -- if today is Fri/Sat, skip check
    if weekday in (4, 5):  # Fri, Sat
        return True, f"Weekend (age {age:.0f}h OK)"
    if age > 24:
        return False, f"Scanner log {age:.1f}h old (last trading day)"
    return True, f"Scanner log {age:.1f}h old"


def check_dashboard():
    html = ROOT / "docs" / "index.html"
    age = _age_hours(html)
    weekday = datetime.now(NPT).weekday()
    if weekday in (4, 5):
        return True, f"Weekend (age {age:.0f}h OK)"
    if age > 24:
        return False, f"Dashboard {age:.1f}h old"
    return True, f"Dashboard {age:.1f}h old"


def check_paper_portfolio():
    pf = ROOT / "data" / "paper_portfolio.json"
    if not pf.exists():
        return False, "paper_portfolio.json missing"
    try:
        data = json.loads(pf.read_text())
        eq_curve = data.get("equity_curve", [])
        if not eq_curve:
            return False, "Equity curve empty"
        last_date = eq_curve[-1].get("date", "unknown")
        weekday = datetime.now(NPT).weekday()
        if weekday in (4, 5):
            return True, f"Last: {last_date} (weekend)"
        if last_date != TODAY:
            # Allow 1 day lag for market close
            return False, f"Paper portfolio last updated {last_date}, today is {TODAY}"
        return True, f"Last: {last_date}"
    except Exception as e:
        return False, f"Parse error: {e}"


def check_git_push():
    os.chdir(str(ROOT))
    try:
        # Check if last commit was today
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ci"],
            capture_output=True, text=True
        )
        last_commit = result.stdout.strip()[:10]
        weekday = datetime.now(NPT).weekday()
        if weekday in (4, 5):
            return True, f"Last push: {last_commit} (weekend)"
        if last_commit != TODAY:
            return False, f"Last git push: {last_commit}, today is {TODAY}"
        return True, f"Last push: {last_commit}"
    except Exception as e:
        return False, f"Error: {e}"


def send_telegram(msg: str) -> bool:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat:
            return False
        import urllib.request
        import urllib.parse
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg, "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data, method="POST"
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"Telegram failed: {e}")
        return False


def main():
    checks = {
        "Crontab": check_crontab(),
        "Scanner": check_scanner(),
        "Dashboard": check_dashboard(),
        "Paper Portfolio": check_paper_portfolio(),
        "Git Push": check_git_push(),
    }

    failed = [(name, reason) for name, (ok, reason) in checks.items() if not ok]

    print(f"=== NEPSE Health Check {TODAY} {datetime.now(NPT).strftime('%H:%M')} NPT ===")
    for name, (ok, reason) in checks.items():
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {name}: {reason}")

    if failed:
        msg = f"*NEPSE Health Check FAILED* ({datetime.now(NPT).strftime('%H:%M')} NPT)\n\n"
        for name, reason in failed:
            msg += f"- {name}: {reason}\n"
        msg += "\nCheck logs: `tail logs/*.log`"
        send_telegram(msg)
        sys.exit(1)
    else:
        print("\nAll systems healthy")


if __name__ == "__main__":
    main()
