# Email Alert Setup for NEPSE Analyzer

## For Gmail (Recommended)

### Step 1: Enable 2-Step Verification
1. Go to: https://myaccount.google.com/security
2. Click "2-Step Verification"
3. Follow the setup wizard

### Step 2: Generate App Password
1. Go to: https://myaccount.google.com/apppasswords
2. Select "Mail" and "Windows Computer" (or your device)
3. Google will generate a **16-character password with spaces**: `xxxx xxxx xxxx xxxx`
4. **IMPORTANT**: Copy the password WITHOUT the spaces

### Step 3: Update .env File
Edit `/home/C00621463/Documents/NEPSE/.env` and replace:

```
ALERT_PASSWORD=YOUR_16_CHAR_PASSWORD_NO_SPACES
```

For example, if Google gives you: `dkda gdzo mwrv oynd`
Use in .env: `dkdagdzomwrvoynd`

### Step 4: Test It
```bash
cd /home/C00621463/Documents/NEPSE
source venv/bin/activate
python daily_run.py --quick   # Should not show email error
```

---

## For Other Email Providers

### Outlook/Hotmail
```
SMTP_SERVER=smtp-mail.outlook.com
SMTP_PORT=587
ALERT_EMAIL=your@outlook.com
ALERT_PASSWORD=your_password
```

### Yahoo Mail
```
SMTP_SERVER=smtp.mail.yahoo.com
SMTP_PORT=587
ALERT_EMAIL=your@yahoo.com
ALERT_PASSWORD=your_app_password
```

---

## Cron Setup (After Email Works)

Once email is configured:
```bash
python daily_run.py --install-cron
# Then: crontab -e
# Paste the lines shown
```

This will run analysis automatically at **10:30 AM Nepal Time** every trading day.

---

## Daily Email Preview

You'll receive emails like this:

**Subject:** `[ACTION] NEPSE ALICL: BUY (Score: 31.5) - 2026-03-26 10:47`

**Content:**
- Current price & your P&L
- Signal strength & indicators
- Buy/Sell/Stop-Loss levels
- Support & Resistance zones
- Risk assessment

---

## Troubleshooting

**Error: "Username and Password not accepted"**
- Check that the app password is exactly 16 characters
- Remove ALL spaces from the password
- Make sure you're using an app password, not your regular Gmail password

**Error: "SMTP connection failed"**
- Check internet connection
- Verify SMTP_SERVER and SMTP_PORT are correct

**Email received but looks plain**
- Your email client might not support HTML. That's fine - the data is still there.

---

## Support

Once working, you'll get analysis emails every day at 10:30 AM Nepal Time (Sun-Thu, NEPSE trading days only).

Check logs: `tail -f data/cron.log` (after cron is set up)
