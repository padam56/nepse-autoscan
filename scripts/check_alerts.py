#!/usr/bin/env python3
"""
Monitor cron job status and view recent alerts.
Usage: python check_alerts.py
"""

import os
import json
from datetime import datetime

def check_cron_status():
    """Check if cron job is installed."""
    result = os.popen("crontab -l | grep NEPSE").read()
    if result:
        print("[OK] NEPSE Cron Job: INSTALLED")
        print(f"  {result.strip()}")
    else:
        print("[WARN] NEPSE Cron Job: NOT INSTALLED")

def check_recent_alerts():
    """Show recent email alerts sent."""
    log_path = "data/analysis_log.json"
    if not os.path.exists(log_path):
        print("\n[INFO] No alerts sent yet")
        return

    try:
        with open(log_path) as f:
            logs = json.load(f)

        print(f"\n[OK] Recent Alerts ({len(logs)} total):")
        for log in logs[-5:]:  # Last 5
            timestamp = log.get('timestamp', 'N/A')
            signal = log.get('signals', {}).get('action', 'N/A')
            score = log.get('signals', {}).get('composite_score', 'N/A')
            print(f"  {timestamp}: {signal} (Score: {score})")
    except Exception as e:
        print(f"\n[ERROR] Error reading logs: {e}")

def check_cron_logs():
    """Show cron execution logs."""
    log_path = "data/cron.log"
    if not os.path.exists(log_path):
        print("\n[INFO] No cron logs yet (will appear after first run)")
        return

    with open(log_path) as f:
        lines = f.readlines()

    print(f"\n[OK] Cron Execution Logs (last 10 lines):")
    for line in lines[-10:]:
        print(f"  {line.rstrip()}")

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  NEPSE ALERT SYSTEM STATUS")
    print("="*50 + "\n")

    check_cron_status()
    check_recent_alerts()
    check_cron_logs()

    print("\n" + "="*50)
    print("Next scheduled alert: 10:30 AM Nepal Time (weekdays)")
    print("="*50 + "\n")
