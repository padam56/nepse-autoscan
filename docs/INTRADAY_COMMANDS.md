# INTRADAY TRADING - Quick Command Reference

## Start Intraday Monitoring

### Standard (60-second checks)
```bash
python intraday_monitor.py
```
Monitors ALICL price every 60 seconds during market hours (11 AM - 3 PM NPT). Checks only Sun-Thu. Automatically stops at 3 PM.

### Fast Mode (30-second checks)
```bash
python intraday_monitor.py --interval 30
```
Same as above but checks every 30 seconds (faster alerts, more API calls).

### From daily_run.py
```bash
python daily_run.py --intraday
```
Alias for `python intraday_monitor.py`

### Quick Market Status Check
```bash
python intraday_monitor.py --check
```
Shows:
- Is market currently open? (YES/NO)
- Current ALICL price
- Your WACC
- Current P&L %
- Does NOT start monitoring, just shows status

---

## After You Trade

### Record a SELL (when you sell shares)
```bash
python trade_report.py SELL 2000 565
```
- `SELL` = You sold shares
- `2000` = Number of shares sold
- `565` = Price per share (NPR)

**System will:**
- Record the trade with timestamp
- Calculate realized P&L
- Update your WACC
- Show new position summary

### Record a BUY (when you buy shares)
```bash
python trade_report.py BUY 2500 555
```
- `BUY` = You bought shares
- `2500` = Number of shares bought
- `555` = Price per share (NPR)

**System will:**
- Record the trade with timestamp
- Recalculate your WACC (usually lower)
- Update share count
- Show new break-even price

### Add Notes to Trade (optional)
```bash
python trade_report.py SELL 2000 565 "profit taking on MEDIUM signal"
python trade_report.py BUY 2500 555 "averaging down on dip"
```
Notes are saved and appear in your trade history.

---

## View Your Position & History

### Current Position Summary
```bash
python trade_report.py
```
or
```bash
python trade_report.py view
```

Shows:
- Current shares held
- WACC (weighted average cost)
- Total invested
- Number of trades executed
- Realized P&L

### All Trades (historical)
```bash
python trade_report.py view
```

Lists every trade you've made:
```
1. BUY 2000 @ 488 (2026-03-26) - averaging down on BUY signal
2. SELL 3000 @ 520 (2026-03-31) - profit taking on SELL signal
...
```

### Get Smart Recommendations
```bash
python trade_report.py recommend 565
```

At current price NPR 565, system shows:
- Your profit/loss %
- Recommended sell strategies
- How much to sell
- Expected proceeds
- Rebuying strategy after sale

---

## Real-World Example: Your Trading Day

### Morning (10:30 AM)
```
 Daily email arrives with:
 Signal: BUY (Score: 38/100)
 Price: NPR 551.00
 Your P&L: -0.22%
```

### Around 11:00 AM
```bash
# Start intraday monitoring
python intraday_monitor.py

[11:05] MARKET OPEN - ALICL opened at NPR 512.50
[11:06] ALICL: NPR 563.20 | P&L: +2.40% | High: 563.20 | Low: 551.00
[11:06] SIGNAL: MEDIUM - CONSIDER SELLING
```

### Email Alert Arrives
```
Subject: INTRADAY SELL SIGNAL: MEDIUM @ NPR 563.20
...
Suggested Action:
 SELL 2,011 shares @ NPR 563.20
 Proceeds: NPR 11,35,655
```

### You Execute Trade
```
1. Open TMS portal
2. Click SELL order
3. Symbol: ALICL
4. Quantity: 2000 (you decide to sell 2000 instead of 2011)
5. Price: 563.20
6. Click PLACE ORDER
7. Order fills immediately
```

### You Report to System
```bash
python trade_report.py SELL 2000 563.20 "profit taking on MEDIUM signal"
```

Output:
```
============================================================
 TRADE RECORDED: SELL 2000 @ NPR 563.20
============================================================
 Total value: NPR 11,26,400
 Notes: profit taking on MEDIUM signal

 Updated Position:
 Shares: 6,046
 WACC: NPR 549.87 (unchanged on sale)
 Total cost: NPR 3,325,858.42
 Current P&L: NPR 84,231
============================================================
```

### Price Dips Later (2:15 PM)
```bash
# Monitoring still running
[14:15] ALICL: NPR 555.00 | P&L: +0.92% | High: 563.20 | Low: 551.00

# Daily email said BUY signal, price looks good
# You decide to buy more
```

### You Buy Again
```bash
python trade_report.py BUY 2500 555 "averaging down on dip"
```

Output:
```
============================================================
 TRADE RECORDED: BUY 2500 @ NPR 555.00
============================================================
 Total value: NPR 13,87,500
 Notes: averaging down on dip

 Updated Position:
 Shares: 8,546
 WACC: NPR 547.80 ← Down from 549.87!
 Total cost: NPR 4,681,341.68
 Current P&L: NPR 58,980
============================================================
```

### Review at End of Day
```bash
python trade_report.py view
```

Shows:
```
ALICL POSITION
============================================================
 Shares: 8,546
 WACC (break-even): NPR 547.80
 Total cost: NPR 4,681,341.68
 Trades: 2
 Realized P&L: NPR -4,091.32
============================================================

ALICL TRADE HISTORY
============================================================

1. SELL 2000 @ NPR 563.20
 Value: NPR 11,26,400
 Date: 2026-03-26T14:15:32.123456
 Notes: profit taking on MEDIUM signal

2. BUY 2500 @ NPR 555.00
 Value: NPR 13,87,500
 Date: 2026-03-26T14:42:18.456789
 Notes: averaging down on dip
```

---

## Monitoring While Away

### Background Monitoring (Linux/Mac)
```bash
# Start monitoring in background
nohup python intraday_monitor.py > intraday.log 2>&1 &

# Check the log later
tail -f intraday.log

# Find the process ID
ps aux | grep intraday_monitor.py

# Kill monitoring when done (after 3 PM)
kill <process_id>
```

### Background Monitoring (Windows)
```bash
# Start in minimized terminal
start /min python intraday_monitor.py

# Or run with pythonw (no console window)
pythonw intraday_monitor.py
```

---

## Troubleshooting

### Error: "Could not fetch price"
```bash
# Check internet connection
ping 8.8.8.8

# Quick check if API is working
python daily_run.py --quick
```

### Error: "Market is closed"
```bash
# Check market hours (11 AM - 3 PM Sun-Thu only)
python intraday_monitor.py --check

# If Friday/weekend, wait for next trading day
```

### Error: "Trade already sent"
```
[14:15] SIGNAL: MEDIUM (already sent 10 minutes ago)
```
System prevents duplicate alerts for same urgency level in one day.

### No Email Received
1. Check spam folder
2. Verify `.env` file has correct email
3. Test email manually: check if cron is running (`python check_alerts.py`)

---

## Daily Workflow Summary

```
 Every Trading Day:

10:30 AM
 Auto-email arrives (daily signal)
 Read signal and current price
 Decide if you want to trade

11:00 AM - 3:00 PM
 python intraday_monitor.py
 Monitor real-time prices
 Alerts arrive when profit targets hit
 Execute trades on TMS
 Report each trade: python trade_report.py SELL/BUY qty price

3:00 PM+
 Review: python trade_report.py view
 Check updated position and WACC
 Plan for next day
```

---

## Target-Based Strategy

Once you know your next target price, use this:

### Know You Want to Sell at NPR 565?
```bash
# Get recommendations for that price
python trade_report.py recommend 565

# Output shows:
# How much to sell at 565
# Expected proceeds
# Profit amount
# What to do after selling
```

### Know You Want to Buy at NPR 555?
```bash
# Just wait for price to hit 555
# When it does, intraday monitor will show it
[14:15] ALICL: NPR 555.00 | P&L: +0.92%

# Then execute:
python trade_report.py BUY 2500 555
```

---

## One-Time Setup

### First Time: Install Email Notifications
```bash
# Edit .env file
nano .env

# Make sure it has:
ALERT_EMAIL=tpadamjung@gmail.com
ALERT_PASSWORD=dkdagdzomwrvoynd (no spaces!)

# Save and exit (Ctrl+X, then Y, then Enter)
```

### First Time: Verify Initial Position
```bash
python trade_report.py

# Should show:
# Shares: 8,046
# WACC: 549.87
# Total cost: 4,424,248.16
```

---

## Quick Stats After 1 Month of Trading

Example: Following daily signals for 1 month (20 trading days)

```
Trades executed: 8-12 (average 2-3 per week)

Example results:
 Sell 2,000 @ 565 → Recovered NPR 11.3L
 Buy 2,500 @ 555 → Deploy NPR 13.9L
 Sell 3,000 @ 580 → Recovered NPR 17.4L
 Buy 1,500 @ 540 → Deploy NPR 8.1L
 Sell 2,500 @ 575 → Recovered NPR 14.4L
 Buy 2,000 @ 550 → Deploy NPR 11.0L

Final Position:
 Shares: 8,046 (same as start)
 WACC: 545 (down from 549.87)
 Realized P&L: +NPR 150,000
 Break-even: Only +0.8% away (was +12.68%)

SUCCESS: Made money AND improved break-even!
```

---

## That's it!

Start monitoring during market hours, trade when signals appear, and let the system handle everything else.

```bash
python intraday_monitor.py
```

 Go make your profits!
