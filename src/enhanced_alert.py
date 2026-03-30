"""
Enhanced Alert System - Sends decision-backbone-powered emails.

Format:
1. Decision Banner (SELL / BUY / HOLD + confidence)
2. Political/Macro Context
3. SELL LEVELS (your priority: sell first)
4. BUY-BACK LEVELS (where to re-enter after selling)
5. Technical indicators summary
6. Your position status
"""

import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime


class EnhancedAlert:
    """Sends the full decision-backbone email."""

    # Colors per action
    ACTION_COLORS = {
        "SELL": "#d32f2f",
        "STRONG_SELL": "#b71c1c",
        "SELL_PARTIAL": "#e64a19",
        "HOLD": "#f57c00",
        "BUY": "#388e3c",
        "STRONG_BUY": "#1b5e20",
    }

    def __init__(self):
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", 587))
        self.sender = os.getenv("ALERT_EMAIL", "")
        self.password = os.getenv("ALERT_PASSWORD", "")
        self.recipient = os.getenv("ALERT_EMAIL", "tpadamjung@gmail.com")

    def send(self, decision: dict) -> bool:
        """Send the full decision email."""
        subject = self._build_subject(decision)
        html = self._build_html(decision)
        text = self._build_text(decision)
        return self._send_email(subject, html, text)

    # ── Subject Line ──────────────────────────────────────────────────

    def _build_subject(self, d: dict) -> str:
        action = d["final_action"]
        price = d["position_context"]["price"]
        score = d["combined_score"]
        pnl = d["position_context"]["pnl_pct"]
        regime = d["macro_regime"]

        return (
            f"ALICL [{action}] NPR {price:,.0f} | "
            f"P&L {pnl:+.1f}% | Score {score:+.0f} | {regime}"
        )

    # ── HTML Email ────────────────────────────────────────────────────

    def _build_html(self, d: dict) -> str:
        action = d["final_action"]
        color = self.ACTION_COLORS.get(action, "#555")
        confidence = d["confidence"]
        score = d["combined_score"]
        tech_score = d["tech_score"]
        macro_score = d["macro_score"]
        regime = d["macro_regime"]
        bias = d["decision_bias"]
        political = bias.get("political_uncertainty", False)
        pos = d["position_context"]
        sell_levels = d["sell_levels"]
        buyback_levels = d["buyback_levels"]
        quantities = d["quantities"]
        macro = d["macro_detail"]
        breadth = macro["breadth"]
        insurance = macro["insurance_sector"]
        tech = d["tech_detail"]
        date_str = datetime.now().strftime("%Y-%m-%d %I:%M %p")

        # Build sell levels table rows
        sell_rows = ""
        for lvl in sell_levels:
            pnl_color = "#2e7d32" if lvl["pnl_at_this_price"] >= 0 else "#d32f2f"
            sell_rows += f"""
            <tr>
                <td style="padding:10px;border-bottom:1px solid #ddd;font-weight:bold;">{lvl['label']}</td>
                <td style="padding:10px;border-bottom:1px solid #ddd;font-size:16px;font-weight:bold;">NPR {lvl['price']:,.2f}</td>
                <td style="padding:10px;border-bottom:1px solid #ddd;color:{pnl_color};font-weight:bold;">{lvl['pnl_at_this_price']:+.1f}%</td>
                <td style="padding:10px;border-bottom:1px solid #ddd;">{lvl['qty_suggested']:,} shares</td>
                <td style="padding:10px;border-bottom:1px solid #ddd;font-size:12px;color:#666;">{lvl['reason']}</td>
            </tr>"""

        # Build buyback levels table rows
        buyback_rows = ""
        for lvl in buyback_levels:
            buyback_rows += f"""
            <tr>
                <td style="padding:10px;border-bottom:1px solid #ddd;font-weight:bold;">{lvl['label']}</td>
                <td style="padding:10px;border-bottom:1px solid #ddd;font-size:16px;font-weight:bold;">NPR {lvl['price']:,.2f}</td>
                <td style="padding:10px;border-bottom:1px solid #ddd;color:#1565c0;">{lvl['discount_from_now']}</td>
                <td style="padding:10px;border-bottom:1px solid #ddd;">{lvl['qty_suggested']:,} shares</td>
                <td style="padding:10px;border-bottom:1px solid #ddd;font-size:12px;color:#666;">{lvl['condition']}</td>
            </tr>"""

        # Tech signal rows
        ta_signals = tech.get("signals", {})
        ta_rows = ""
        for key, sig in ta_signals.items():
            label = sig.get("label", "")
            sig_score = sig.get("score", 0)
            sc = "#2e7d32" if sig_score > 0 else ("#d32f2f" if sig_score < 0 else "#888")
            ta_rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;text-transform:uppercase;font-size:12px;color:#666;">{key}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;color:{sc};">{label}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-size:12px;">{sig.get('detail', '')}</td>
            </tr>"""

        qty_sell_now = quantities.get("sell_now", 0)
        qty_keep = quantities.get("keep_core", pos["shares"])

        return f"""
<html>
<body style="font-family:Arial,sans-serif;max-width:680px;margin:0 auto;background:#f0f0f0;padding:10px;">

<!-- Header Banner -->
<div style="background:{color};color:white;padding:25px 20px;border-radius:10px 10px 0 0;">
  <div style="font-size:28px;font-weight:bold;letter-spacing:1px;">{action}</div>
  <div style="font-size:16px;margin-top:5px;">ALICL | NPR {pos['price']:,.2f} | Confidence: {confidence}%</div>
  <div style="font-size:13px;margin-top:5px;opacity:0.85;">Decision Score: {score:+.1f} &nbsp;|&nbsp; Tech: {tech_score:+.1f} &nbsp;|&nbsp; Macro: {macro_score:+.1f} &nbsp;|&nbsp; {date_str}</div>
</div>

<!-- Political Alert (if active) -->
{"" if not political else f'''
<div style="background:#ff6f00;color:white;padding:15px 20px;border-left:5px solid #e65100;">
  <strong>POLITICAL EVENT ACTIVE</strong><br>
  Nepal political change detected. Strategy: <strong>SELL INTO STRENGTH → WAIT → BUY THE DIP</strong><br>
  Political uncertainty makes markets volatile. Best practice: reduce exposure on any price pop.
</div>
'''}

<!-- Main Content -->
<div style="background:white;padding:20px;">

  <!-- Decision Rationale -->
  <div style="background:#fafafa;border-left:4px solid {color};padding:15px;margin-bottom:20px;border-radius:4px;">
    <strong style="font-size:15px;">WHY THIS SIGNAL</strong><br><br>
    <span style="font-size:14px;line-height:1.7;">{bias['reason']}</span>
  </div>

  <!-- Your Position -->
  <h3 style="color:#333;margin:0 0 10px;">YOUR POSITION</h3>
  <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
    <tr style="background:#f5f5f5;">
      <td style="padding:10px;border-bottom:1px solid #ddd;"><strong>Current Price</strong></td>
      <td style="padding:10px;border-bottom:1px solid #ddd;font-size:18px;font-weight:bold;">NPR {pos['price']:,.2f}</td>
    </tr>
    <tr>
      <td style="padding:10px;border-bottom:1px solid #ddd;"><strong>Your WACC</strong></td>
      <td style="padding:10px;border-bottom:1px solid #ddd;">NPR {pos['wacc']:,.2f}</td>
    </tr>
    <tr style="background:#f5f5f5;">
      <td style="padding:10px;border-bottom:1px solid #ddd;"><strong>Shares Held</strong></td>
      <td style="padding:10px;border-bottom:1px solid #ddd;">{pos['shares']:,}</td>
    </tr>
    <tr>
      <td style="padding:10px;border-bottom:1px solid #ddd;"><strong>Current P&L</strong></td>
      <td style="padding:10px;border-bottom:1px solid #ddd;font-weight:bold;color:{'#d32f2f' if pos['pnl_pct'] < 0 else '#2e7d32'};">{pos['pnl_pct']:+.2f}% (NPR {(pos['price'] - pos['wacc']) * pos['shares']:+,.2f})</td>
    </tr>
    <tr style="background:#f5f5f5;">
      <td style="padding:10px;"><strong>Distance to Break-even</strong></td>
      <td style="padding:10px;">{'AT BREAK-EVEN' if pos['pnl_pct'] >= 0 else f"Need +{abs(pos['pnl_pct']):.2f}% → NPR {pos['wacc']:,.2f}"}</td>
    </tr>
  </table>

  <!-- SELL LEVELS (Priority 1) -->
  <h3 style="color:{color};margin:0 0 10px;">STEP 1: SELL TARGETS</h3>
  <p style="font-size:13px;color:#666;margin:0 0 10px;">
    Sell into these levels (nearest first). Don't wait for the top — take what the market gives you.
  </p>
  <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
    <tr style="background:{color};color:white;font-size:13px;">
      <th style="padding:10px;text-align:left;">Target</th>
      <th style="padding:10px;text-align:left;">Sell Price</th>
      <th style="padding:10px;text-align:left;">P&L at This</th>
      <th style="padding:10px;text-align:left;">Qty</th>
      <th style="padding:10px;text-align:left;">Reason</th>
    </tr>
    {sell_rows}
  </table>

  <!-- SELL NOW Quick Box -->
  <div style="background:#ffebee;border:2px solid {color};padding:15px;border-radius:6px;margin-bottom:20px;">
    <strong style="color:{color};">IMMEDIATE ACTION:</strong><br>
    SELL <strong>{qty_sell_now:,} shares</strong> @ NPR {pos['price']:,.2f} (current price)<br>
    Proceeds: <strong>NPR {qty_sell_now * pos['price']:,.2f}</strong> | Keep {qty_keep:,} shares remaining<br>
    <small style="color:#888;">Then wait for price to dip → re-enter at BUY-BACK levels below</small>
  </div>

  <!-- BUY-BACK LEVELS (Priority 2) -->
  <h3 style="color:#1565c0;margin:0 0 10px;">STEP 2: BUY-BACK LEVELS (After Selling)</h3>
  <p style="font-size:13px;color:#666;margin:0 0 10px;">
    After you sell, wait for price to pull back to these levels before re-entering.
    <strong>Only buy when a BUY signal is confirmed</strong> — do not catch a falling knife.
  </p>
  <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
    <tr style="background:#1565c0;color:white;font-size:13px;">
      <th style="padding:10px;text-align:left;">Entry Zone</th>
      <th style="padding:10px;text-align:left;">Buy Price</th>
      <th style="padding:10px;text-align:left;">Discount</th>
      <th style="padding:10px;text-align:left;">Qty</th>
      <th style="padding:10px;text-align:left;">Condition</th>
    </tr>
    {buyback_rows}
  </table>

  <!-- Macro Analysis -->
  <h3 style="color:#555;margin:0 0 10px;">MACRO / MARKET CONTEXT</h3>
  <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
    <tr>
      <td style="padding:10px;border-bottom:1px solid #eee;"><strong>Market Breadth</strong></td>
      <td style="padding:10px;border-bottom:1px solid #eee;">{breadth['gainers']} gainers / {breadth['losers']} losers → <strong>{breadth['mood']}</strong></td>
    </tr>
    <tr style="background:#f9f9f9;">
      <td style="padding:10px;border-bottom:1px solid #eee;"><strong>Insurance Sector</strong></td>
      <td style="padding:10px;border-bottom:1px solid #eee;">{insurance['change_pct']:+.2f}% → <strong>{insurance['sentiment']}</strong></td>
    </tr>
    <tr>
      <td style="padding:10px;border-bottom:1px solid #eee;"><strong>Market Regime</strong></td>
      <td style="padding:10px;border-bottom:1px solid #eee;"><strong>{regime}</strong></td>
    </tr>
    <tr style="background:#f9f9f9;">
      <td style="padding:10px;border-bottom:1px solid #eee;"><strong>Volatility</strong></td>
      <td style="padding:10px;border-bottom:1px solid #eee;">{macro['volatility']}</td>
    </tr>
    <tr>
      <td style="padding:10px;"><strong>Macro Stance</strong></td>
      <td style="padding:10px;">{macro['stance']}</td>
    </tr>
  </table>

  <!-- Technical Indicators -->
  <h3 style="color:#555;margin:0 0 10px;">TECHNICAL INDICATORS</h3>
  <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
    <tr style="background:#eee;font-size:12px;">
      <th style="padding:8px;text-align:left;">Indicator</th>
      <th style="padding:8px;text-align:left;">Signal</th>
      <th style="padding:8px;text-align:left;">Detail</th>
    </tr>
    {ta_rows}
  </table>

  <!-- Reporting -->
  <div style="background:#e8f5e9;padding:15px;border-radius:6px;border-left:4px solid #2e7d32;">
    <strong>After you trade, REPORT to system:</strong><br><br>
    <code style="background:#fff;padding:3px 6px;border-radius:3px;">python trade_report.py SELL {qty_sell_now} {int(pos['price'])}</code><br><br>
    <small style="color:#666;">This updates your WACC, P&L, and next email will reflect new position.</small>
  </div>

</div>

<!-- Footer -->
<div style="padding:12px 20px;background:#e0e0e0;border-radius:0 0 10px 10px;font-size:11px;color:#888;text-align:center;">
  ALICL Decision Backbone Signal | Nepal Political Context Active | {date_str}
</div>

</body>
</html>"""

    # ── Plain Text Email ──────────────────────────────────────────────

    def _build_text(self, d: dict) -> str:
        action = d["final_action"]
        confidence = d["confidence"]
        score = d["combined_score"]
        pos = d["position_context"]
        sell_levels = d["sell_levels"]
        buyback_levels = d["buyback_levels"]
        quantities = d["quantities"]
        bias = d["decision_bias"]
        macro = d["macro_detail"]
        breadth = macro["breadth"]
        insurance = macro["insurance_sector"]

        lines = [
            f"ALICL SIGNAL: {action} (Confidence: {confidence}%)",
            "=" * 60,
            "",
            f"Combined Score: {score:+.1f} | Tech: {d['tech_score']:+.1f} | Macro: {d['macro_score']:+.1f}",
            "",
            "POLITICAL CONTEXT:",
            "-" * 40,
            "Nepal major political change active.",
            "Strategy: SELL INTO STRENGTH → WAIT → BUY BACK ON DIP",
            "",
            "WHY THIS SIGNAL:",
            bias["reason"],
            "",
            "YOUR POSITION:",
            f"  Price: NPR {pos['price']:,.2f}",
            f"  WACC:  NPR {pos['wacc']:,.2f}",
            f"  P&L:   {pos['pnl_pct']:+.2f}%",
            f"  Shares: {pos['shares']:,}",
            "",
            "STEP 1 - SELL TARGETS:",
            "-" * 40,
        ]
        for lvl in sell_levels:
            lines.append(f"  [{lvl['label']}]")
            lines.append(f"    Price: NPR {lvl['price']:,.2f} | P&L: {lvl['pnl_at_this_price']:+.1f}% | Qty: {lvl['qty_suggested']:,}")
            lines.append(f"    Reason: {lvl['reason']}")
            lines.append("")

        qty_now = quantities.get("sell_now", 0)
        lines += [
            f"IMMEDIATE: SELL {qty_now:,} shares @ NPR {pos['price']:,.2f}",
            f"Proceeds: NPR {qty_now * pos['price']:,.2f}",
            "",
            "STEP 2 - BUY-BACK LEVELS (After Selling):",
            "-" * 40,
        ]
        for lvl in buyback_levels:
            lines.append(f"  [{lvl['label']}]")
            lines.append(f"    Price: NPR {lvl['price']:,.2f} | Discount: {lvl['discount_from_now']} | Qty: {lvl['qty_suggested']:,}")
            lines.append(f"    Condition: {lvl['condition']}")
            lines.append("")

        lines += [
            "MACRO CONTEXT:",
            f"  Market: {breadth['gainers']} gainers / {breadth['losers']} losers ({breadth['mood']})",
            f"  Insurance sector: {insurance['change_pct']:+.2f}% ({insurance['sentiment']})",
            f"  Regime: {d['macro_regime']}",
            "",
            "AFTER TRADING, REPORT:",
            f"  python trade_report.py SELL {qty_now} {int(pos['price'])}",
        ]

        return "\n".join(lines)

    # ── SMTP Sender ───────────────────────────────────────────────────

    def _send_email(self, subject: str, html: str, text: str) -> bool:
        if not self.sender or not self.password:
            print("[!] Email not configured. Printing to console instead:\n")
            print(text)
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.sender
            msg["To"] = self.recipient

            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(self.sender, self.password)
                server.sendmail(self.sender, self.recipient, msg.as_string())

            print(f"[+] Enhanced signal sent to {self.recipient}: {subject}")
            return True

        except Exception as e:
            print(f"[!] Email error: {e}")
            print("\nPrinting to console instead:\n")
            print(text)
            return False
