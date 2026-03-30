# INTRADAY PROFIT-TAKING GUIDE - Real-Time Trading (11 AM - 3 PM)

## Overview

The Intraday Trader automatically monitors ALICL price **every minute** during market hours (11:00 AM - 3:00 PM NPT) and sends **instant email alerts** when your stock reaches profitable exit points.

**Perfect for:** Taking 3-4 profits per month by selling small portions at strategic price levels.

---

## How It Works

### The Setup
- **Runs on:** Your PC (must be open during market hours)
- **Monitors:** Real-time ALICL price from MeroLagani API
- **Checks:** Every 60 seconds (can be customized)
- **Sends alerts:** Instantly when profit targets hit
- **Trading days:** Sunday - Thursday, 11 AM - 3 PM NPT only

### Profit Target Tiers

The system automatically triggers SELL signals at these profit points:

| Profit Level | Trigger | Qty to Sell | Urgency | When to Use |
|---|---|---|---|---|
| **+1%** | Price gains 1% from WACC | 15% of holdings | LOW | Conservative, safe profit |
| **+2%** | Price gains 2% from WACC | 25% of holdings | MEDIUM | Moderate, reasonable profit |
| **+3%** | Price gains 3% from WACC | 40% of holdings | HIGH | Aggressive, good opportunity |
| **+5%** | Price gains 5% from WACC | 50% of holdings | EXTREME | Excellent, rare opportunity |

**Example:**
```
Your WACC: NPR 549.87
Current price: NPR 565

Profit: (565 - 549.87) / 549.87 = +2.74%
→ Triggers +2% signal (MEDIUM urgency)
→ Sell 25% of your shares (2,011 shares)
→ Proceeds: NPR 10,55,465
```

---

## Starting Intraday Monitoring

### Option 1: Simple - Default Settings (60 sec checks)
```bash
python intraday_monitor.py
```

This runs continuously from now until 3:00 PM, checking price every 60 seconds.

**What you'll see:**
```
[11:05] MARKET OPEN - ALICL opened at NPR 512.50
[11:06] ALICL: NPR 512.60 | P&L: -6.78% | High: 512.60 | Low: 512.50
[11:07] ALICL: NPR 513.20 | P&L: -6.66% | High: 513.20 | Low: 512.50 | SIGNAL: MEDIUM - CONSIDER SELLING
```

### Option 2: Quick Check - Is Market Open?
```bash
python intraday_monitor.py --check
```

**Output:**
```
[14:35] Wednesday
 MARKET IS OPEN - Monitoring active

Current ALICL Price: NPR 525.00
Your WACC: NPR 549.87
Current P&L: -4.52%
```

Use this to verify market status before starting monitoring.

### Option 3: Custom Interval - Check Every N Seconds
```bash
python intraday_monitor.py --interval 30
```

Checks price every 30 seconds instead of 60 (faster response, more API calls).

### Option 4: From daily_run.py
```bash
python daily_run.py --intraday
```

Same as `python intraday_monitor.py` - starts monitoring with default 60-second interval.

---

## Reading the Signals

### Signal Format in Terminal

```
[14:22] ALICL: NPR 561.50 | P&L: +2.10% | High: 565.20 | Low: 559.00 | SIGNAL: MEDIUM - CONSIDER SELLING
```

Breaking it down:
- `[14:22]` = Current time (2:22 PM)
- `NPR 561.50` = Current price
- `+2.10%` = Your profit percentage
- `High: 565.20` = Highest price today
- `Low: 559.00` = Lowest price today
- ` SIGNAL: MEDIUM` = Alert urgency level

### Signal Urgency Levels

| Urgency | Meaning | Action |
|---|---|---|
| **LOW** | +1% profit | Optional - safe but small gain |
| **MEDIUM** | +2% profit | Consider selling - good return |
| **HIGH** | +3% profit + pullback | Sell soon - excellent opportunity |
| **EXTREME** | +5% profit | **SELL NOW** - rare opportunity |

---

## When You Get an Email Alert

### Email Contents

You'll receive an email that looks like:

```
Subject: INTRADAY SELL SIGNAL: HIGH @ NPR 565.00

ALICL INTRADAY SELL SIGNAL - HIGH

Current Price: NPR 565.00
Your WACC: NPR 549.87
Profit: NPR 15.13 per share (+2.75%)

Suggested Action:
 SELL 2,011 shares @ NPR 565.00
 Proceeds: NPR 11,36,215

Reason: EXCELLENT: +2.75% gain, price pulled back from high

DECISION:
 This signal is INTRADAY - valid only during market hours
 If you agree with the price, execute the SELL order immediately
 After selling, report: python trade_report.py SELL 2011 565
```

### Steps to Execute the Trade

1. **Read the email** - Understand the current profit and suggested quantity
2. **Open your TMS portal** - Log into your broker's trading system
3. **Place SELL order:**
 - Symbol: `ALICL`
 - Quantity: `2,011` (as suggested in email)
 - Price: `565.00` (match the email price or sell at market if price has risen)
 - Click PLACE ORDER
4. **Order executes** - If someone buys at that price, your shares are sold
5. **Report to system:**
 ```bash
 python trade_report.py SELL 2011 565
 ```
 (Use the actual quantity and price you sold at)

6. **System updates automatically:**
 - Your WACC recalculates
 - Realized P&L is recorded
 - Position is updated
 - Next email will show your new position

---

## Real Example: Intraday Trading Session

### Morning Setup
```
Your position: 8,046 shares @ WACC 549.87
Today's plan: Watch for +2-3% gain and sell portion
```

### 11:30 AM - Market Opens
```
Email from cron job shows:
Signal: HOLD (strong resistance at 560)
Price: NPR 551.00
Your P&L: +0.22%
```

### 1:15 PM - First Profit
```
[13:15] ALICL: NPR 563.50 | P&L: +2.45% | High: 565.20 | Low: 551.00 | SIGNAL: MEDIUM - CONSIDER SELLING

 You receive email alert
You open TMS and sell 2,000 shares @ NPR 563.50
You report: python trade_report.py SELL 2000 563.50

System shows:
 Shares: 8,046 → 6,046
 Proceeds: NPR 11,27,000
 Realized loss: -NPR 16,740 (on sold batch)
```

### 2:45 PM - Price Dips, Ready to Buy
```
[14:45] ALICL: NPR 555.00 | P&L: +0.92% | High: 565.20 | Low: 551.00

Email from 10:30 AM said:
Signal: BUY (Score: 42/100)

You decide: "Good entry point, I'll buy again"
You buy 2,500 shares @ NPR 555 = NPR 13,87,500

You report: python trade_report.py BUY 2500 555

System shows:
 Shares: 6,046 → 8,546
 New WACC: NPR 547.80 (down from 549.87!)
 Break-even improved: +0.22% → closer to break-even
```

### 3:15 PM - Market Closes
```
End of day:
- Sold 2,000 @ 563.50 (recovered NPR 11.27L)
- Bought 2,500 @ 555 (deployed NPR 13.87L)
- Current position: 8,546 shares
- Improved WACC from 549.87 → 547.80
- Ready for next day!
```

---

## Advanced: Customization & Tips

### Tip 1: Run Monitoring in Background

If you want to keep your terminal free, you can run it in the background:

**On Linux/Mac:**
```bash
nohup python intraday_monitor.py > intraday.log 2>&1 &
```

This runs monitoring even if you close the terminal. Check logs with:
```bash
tail -f intraday.log
```

### Tip 2: Faster Response Time

For 30-second checks instead of 60:
```bash
python intraday_monitor.py --interval 30
```

**Trade-off:** More responsive signals, but higher API usage (240 calls/day vs 120/day)

### Tip 3: Monitor Multiple Sessions

If ALICL is illiquid and you miss the morning signals, you can run monitoring again in the afternoon:
```bash
python intraday_monitor.py # 11 AM - 3 PM
# Market closes at 3 PM
python intraday_monitor.py # Run again tomorrow
```

### Tip 4: Combine with Daily Signals

**Morning (10:30 AM):** Get daily email with technical analysis signal
**During day (11 AM - 3 PM):** Run intraday monitoring for real-time profits
**Evening:** Review trades and update portfolio

```bash
# Daily analysis (sent via cron at 10:30 AM)
# ← Auto received in email

# Intraday monitoring (you start manually)
python intraday_monitor.py

# Report trades when you execute them
python trade_report.py SELL 2000 565
python trade_report.py BUY 2500 555
```

---

## What NOT to Do

### DON'T Ignore the Signal
```
 SIGNAL: HIGH arrives, but price looks like it might go higher
You wait... price drops to 559 (below signal price)
→ You missed the sale
→ Now you're back to -2% loss

 DO: Trust the signal - take profits when offered
```

### DON'T Trade Without Reporting
```
 You sell 2,000 shares on TMS
 You forget to report: python trade_report.py SELL 2000 565
→ System still thinks you have 8,046 shares
→ Next email shows wrong P&L and recommendations
→ Your decisions are based on old data

 DO: Always report immediately after trading
```

### DON'T Hold for "Perfect Price"
```
Signal at +2.5%, you think "let me wait for +3%"
Price drops to +0.5%, signal cancelled
→ Now you're underwater again

 DO: Take profits when signals appear
→ You can buy back in later if price dips
```

### DON'T Close the Monitoring Window
```
 You start monitoring, then close the terminal
→ Monitoring stops
→ You miss all signals

 DO: Keep the terminal open during market hours
→ Or use nohup to run in background
```

---

## Debugging & Troubleshooting

### Problem: "Error fetching price, retrying..."
```
[13:15] Error fetching price, retrying...
```

**Cause:** API temporarily unavailable
**Solution:** System will retry automatically. If persistent, check internet connection.

### Problem: "MARKET CLOSED" message
```
[09:45] MARKET CLOSED - Waiting for market to open at 11:00 AM
```

**Cause:** It's before 11 AM or after 3 PM, or it's Friday/weekend
**Solution:** Run monitoring again during 11 AM - 3 PM Sun-Thu

### Problem: No email alerts received
```
Signal triggered but no email arrives
```

**Checklist:**
1. Check spam folder (especially Gmail spam/promotions)
2. Verify `.env` has correct `ALERT_EMAIL` and `ALERT_PASSWORD`
3. Verify email settings in `src/alerts.py`
4. Test: `python trade_report.py` (should show current position)

### Problem: Price seems wrong
```
[13:15] ALICL: NPR 565.00 | P&L: +2.75%
But TMS shows price is 560
```

**Cause:** Data lag between MeroLagani API and your TMS broker
**Solution:** Use MeroLagani price as reference, trade at TMS price available to you

---

## Summary: Intraday Trading Workflow

```
11:00 AM → Market opens
 python intraday_monitor.py (starts monitoring)

During day (every minute)
 Checks price
 Tracks high/low
 Watches for +1%, +2%, +3%, +5% profit targets

Signal triggered (e.g., +2.5% gain)
 Terminal shows: SIGNAL: MEDIUM
 Email arrives: "SELL 2,000 shares @ NPR 565"
 You execute on TMS

After trade
 Report: python trade_report.py SELL 2000 565
 System updates WACC and P&L
 Continue monitoring (might rebuy on dip)

3:00 PM → Market closes
 Monitoring stops automatically

Evening → Review trades
 python trade_report.py (view all trades)
 Plan for next day
```

---

## Real Money Example: One Week

### Day 1 (Monday)
- Start: 8,046 shares @ 549.87
- Monitoring: 11 AM - 3 PM
- Signal at +2.1%: Sell 2,000 @ 562
- **Result:** Proceeds NPR 11,24,000 | Realized loss: -NPR 16,000

### Day 2 (Tuesday)
- Start: 6,046 shares
- Monitoring: No strong signals
- **Result:** Hold

### Day 3 (Wednesday)
- Start: 6,046 shares
- Monitoring: Price dips
- Signal at +0.5% recovery: Buy 2,500 @ 552
- **Result:** Invested NPR 13,80,000 | New WACC: 547.90

### Day 4 (Thursday)
- Start: 8,546 shares @ 547.90
- Monitoring: Nice bounce
- Signal at +3.2%: Sell 3,400 @ 568
- **Result:** Proceeds NPR 19,31,200 | Realized profit: +NPR 68,700

### Week Summary
```
Trades executed: 3 (2 sells, 1 buy)
Capital deployed: +NPR 13.8L (buy)
Capital recovered: -NPR 30.55L (sales)
Net proceeds: +NPR 16.75L in cash
Realized P&L: +NPR 52,700
Shares left: 5,146 (core position preserved)
Break-even improved: 549.87 → 546.50

By trading actively 3 times, you:
 Made NPR 52K profit
 Recovered NPR 16.75L in cash
 Improved your WACC
 Still have shares for future recovery
```

---

## Bottom Line

**Intraday trading gives you:**

 **Real-time signals** - Know exactly when to sell for profit
 **Automated checks** - No need to stare at chart all day
 **Email alerts** - Get instant notification on your phone
 **Systematic approach** - Follow clear profit tiers (+1%, +2%, +3%, +5%)
 **3-4 trades/month** - Easy to achieve with daily opportunities
 **Recovery acceleration** - Build trading profits to fund recovery

**Start monitoring:**
```bash
python intraday_monitor.py
```

**That's it! System handles the rest.**
