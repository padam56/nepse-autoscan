# QUICK START - Your Complete NEPSE Trading System

## What You Have Right Now

A complete automated trading system with:
- **Daily signal emails** - Arrives 10:30 AM Nepal Time every trading day
- **Intraday real-time monitoring** - Instant profit signals during market hours
- **Automatic position tracking** - WACC recalculates after each trade
- **Smart recommendations** - Tells you exactly when/how much to buy/sell

---

## Your First 3 Days

### Day 1: Verify Everything Works
```bash
# Test: Quick market check
python3 intraday_monitor.py --check
```

### Day 2: Get First Daily Signal
Email arrives at 10:30 AM Nepal Time with BUY/SELL/HOLD signal

### Day 3: Execute First Trade
Start monitoring, trade during market hours, report to system

---

## Key Commands (Ready to Copy)

```bash
# Information
python3 intraday_monitor.py --check # Is market open?
python3 trade_report.py # Your current position
python3 trade_report.py view # Your trade history

# Monitoring
python3 intraday_monitor.py # Start real-time monitoring

# Trading (when you execute)
python3 trade_report.py SELL 2000 565 # Report a sale
python3 trade_report.py BUY 2500 555 # Report a purchase
```

---

## Your Daily Routine

1. **Morning (10:30 AM Nepal):** Read daily email signal
2. **Late night (11 PM - 4 AM your time):** Run `python3 intraday_monitor.py`
3. **When alert arrives:** Trade on TMS portal
4. **After trade:** Report: `python3 trade_report.py SELL/BUY qty price`
5. **Evening:** Review: `python3 trade_report.py view`

---

## Important: Timezone (Already Fixed!)

- You're in Louisiana, market is in Nepal
- **When to monitor:** Thursday 11 PM - Friday 4 AM your time
- System automatically checks Nepal Time - no configuration needed!

Details: See `TIMEZONE_GUIDE.md`

---

## Right Now

Test system:
```bash
python3 intraday_monitor.py --check
```

Then wait for tomorrow's 10:30 AM email (Nepal Time).

That's it! System handles everything else.
