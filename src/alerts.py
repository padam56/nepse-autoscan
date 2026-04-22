"""
Email Alert System - Sends buy/sell notifications based on signal analysis.

Supports Gmail, Outlook, and custom SMTP servers.
"""

import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

NPT = timezone(timedelta(hours=5, minutes=45))


class AlertSystem:
    """Send email alerts for trading signals."""

    def __init__(self):
        self.smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        self.email = os.environ.get("ALERT_EMAIL", "")
        self.password = os.environ.get("ALERT_PASSWORD", "")  # App password for Gmail
        self.recipient = os.environ.get("ALERT_RECIPIENT", self.email)

    @property
    def is_configured(self) -> bool:
        return bool(self.email and self.password)

    def send_alert(self, subject: str, body_html: str, body_text: str = ""):
        """Send an email alert."""
        if not self.is_configured:
            print("[!] Email not configured. Set ALERT_EMAIL and ALERT_PASSWORD env vars.")
            print(f"[*] Alert subject: {subject}")
            print(f"[*] Would have sent to: {self.recipient}")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.email
        msg["To"] = self.recipient

        if body_text:
            msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(body_html, "html"))

        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.email, self.password)
                server.sendmail(self.email, self.recipient, msg.as_string())
            print(f"[+] Alert sent to {self.recipient}: {subject}")
            return True
        except Exception as e:
            print(f"[!] Failed to send alert: {e}")
            return False

    # ── Pre-built Alert Templates ──────────────────────────────

    def send_signal_alert(self, symbol: str, signals: dict, position: dict, ta_summary: dict):
        """Send a comprehensive trading signal alert."""
        action = signals.get("action", "HOLD")
        score = signals.get("composite_score", 0)
        timestamp = datetime.now(NPT).strftime("%Y-%m-%d %H:%M")

        # Determine urgency
        if "STRONG" in action:
            urgency = "URGENT"
            color = "#d32f2f" if "SELL" in action else "#2e7d32"
        elif action in ("BUY", "SELL"):
            urgency = "ACTION"
            color = "#f57c00" if "SELL" in action else "#1565c0"
        else:
            urgency = "INFO"
            color = "#616161"

        subject = f"[{urgency}] NEPSE {symbol}: {action} (Score: {score}) - {timestamp}"

        # Build HTML email
        price = ta_summary.get("price", {})
        sr = ta_summary.get("support_resistance", {})
        levels = signals.get("key_levels", {})
        risk = signals.get("risk_level", {})

        pnl = position.get("unrealized_pnl", 0)
        pnl_pct = position.get("unrealized_pnl_pct", 0)
        pnl_color = "#2e7d32" if pnl >= 0 else "#d32f2f"

        signals_rows = ""
        for name, sig in signals.get("signals", {}).items():
            sig_score = sig.get("score", 0)
            sig_color = "#2e7d32" if sig_score > 0 else "#d32f2f" if sig_score < 0 else "#616161"
            signals_rows += f"""
            <tr>
                <td style="padding:6px;border-bottom:1px solid #eee;">{name.upper()}</td>
                <td style="padding:6px;border-bottom:1px solid #eee;color:{sig_color};font-weight:bold;">{sig_score}</td>
                <td style="padding:6px;border-bottom:1px solid #eee;">{sig.get('label', '')}</td>
            </tr>"""

        body_html = f"""
        <html>
        <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
            <div style="background:{color};color:white;padding:20px;border-radius:8px 8px 0 0;">
                <h1 style="margin:0;font-size:24px;">{action}</h1>
                <p style="margin:5px 0 0;font-size:16px;">{symbol} | Score: {score}/100 | {timestamp}</p>
            </div>

            <div style="padding:20px;background:#f5f5f5;">
                <h2 style="color:#333;margin-top:0;">Position Summary</h2>
                <table style="width:100%;border-collapse:collapse;">
                    <tr><td style="padding:4px;">Current Price:</td><td style="font-weight:bold;">NPR {price.get('close', 'N/A')}</td></tr>
                    <tr><td style="padding:4px;">Your WACC:</td><td>NPR {position.get('wacc', 'N/A')}</td></tr>
                    <tr><td style="padding:4px;">Shares:</td><td>{position.get('shares', 'N/A'):,}</td></tr>
                    <tr><td style="padding:4px;">P&L:</td><td style="color:{pnl_color};font-weight:bold;">NPR {pnl:+,.2f} ({pnl_pct:+.2f}%)</td></tr>
                    <tr><td style="padding:4px;">Break-even:</td><td>NPR {position.get('breakeven_price', 'N/A')}</td></tr>
                </table>

                <h2 style="color:#333;">Signal Breakdown</h2>
                <table style="width:100%;border-collapse:collapse;">
                    <tr style="background:#e0e0e0;">
                        <th style="padding:8px;text-align:left;">Indicator</th>
                        <th style="padding:8px;text-align:left;">Score</th>
                        <th style="padding:8px;text-align:left;">Signal</th>
                    </tr>
                    {signals_rows}
                </table>

                <h2 style="color:#333;">Key Action Levels</h2>
                <table style="width:100%;border-collapse:collapse;">
                    <tr><td style="padding:4px;color:#2e7d32;">Buy Zone:</td><td style="font-weight:bold;">NPR {levels.get('buy_zone', 'N/A')}</td></tr>
                    <tr><td style="padding:4px;color:#2e7d32;">Strong Buy:</td><td style="font-weight:bold;">NPR {levels.get('strong_buy', 'N/A')}</td></tr>
                    <tr><td style="padding:4px;color:#d32f2f;">Sell Zone:</td><td style="font-weight:bold;">NPR {levels.get('sell_zone', 'N/A')}</td></tr>
                    <tr><td style="padding:4px;color:#d32f2f;">Stop Loss:</td><td style="font-weight:bold;">NPR {levels.get('stop_loss', 'N/A')}</td></tr>
                </table>

                <h2 style="color:#333;">Support & Resistance</h2>
                <p>Supports: {', '.join(f'NPR {s}' for s in sr.get('support_levels', [])[:3])}</p>
                <p>Resistances: {', '.join(f'NPR {r}' for r in sr.get('resistance_levels', [])[:3])}</p>

                <h2 style="color:#333;">Risk</h2>
                <p>Volatility: {risk.get('volatility_risk', 'N/A')} | ATR: {risk.get('atr_pct', 'N/A')}% | Volume: {risk.get('volume_conviction', 'N/A')}</p>
                {'<p style="color:#d32f2f;font-weight:bold;">BOLLINGER SQUEEZE DETECTED - Breakout Imminent!</p>' if risk.get('bollinger_squeeze') else ''}
            </div>

            <div style="padding:15px;background:#e0e0e0;border-radius:0 0 8px 8px;font-size:12px;color:#666;">
                NEPSE Stock Analyzer | Auto-generated alert | Not financial advice
            </div>
        </body>
        </html>
        """

        body_text = f"""
{action} - {symbol} (Score: {score}/100)
Generated: {timestamp}

Price: NPR {price.get('close', 'N/A')}
P&L: NPR {pnl:+,.2f} ({pnl_pct:+.2f}%)

Buy Zone: NPR {levels.get('buy_zone', 'N/A')}
Sell Zone: NPR {levels.get('sell_zone', 'N/A')}
Stop Loss: NPR {levels.get('stop_loss', 'N/A')}
        """

        return self.send_alert(subject, body_html, body_text)

    def send_price_alert(self, symbol: str, current_price: float, alert_type: str, target_price: float):
        """Send a simple price level alert."""
        subject = f"[PRICE ALERT] {symbol}: {alert_type} at NPR {current_price}"
        body_html = f"""
        <html><body style="font-family:Arial,sans-serif;">
            <h2>{alert_type}</h2>
            <p><strong>{symbol}</strong> hit NPR {current_price} (target was NPR {target_price})</p>
            <p>Time: {datetime.now(NPT).strftime('%Y-%m-%d %H:%M')}</p>
        </body></html>
        """
        return self.send_alert(subject, body_html)
