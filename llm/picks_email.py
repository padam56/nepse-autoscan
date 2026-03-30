"""
llm/picks_email.py -- Build mobile-friendly HTML email for Claude's stock picks.

Converts structured pick data into responsive email HTML that works
on Gmail, Outlook, and mobile clients (no CSS grid, no flexbox,
tables-only layout for maximum compatibility).
"""


def build_picks_email(picks_analysis: str, scan_date: str = "",
                      regime: str = "BULL") -> str:
    """Convert Claude's analysis text into mobile-friendly HTML email.

    Args:
        picks_analysis: raw text from Claude with stock recommendations
        scan_date: date string for header
        regime: market regime

    Returns:
        Complete HTML email string
    """
    regime_color = {"BULL": "#00e475", "RANGE": "#ffd740", "BEAR": "#ff5252"}.get(regime, "#888")

    # Parse the markdown table if present
    picks_rows = ""
    portfolio_section = ""
    risks_section = ""
    special_notes = ""

    lines = picks_analysis.split("\n")
    in_table = False
    in_portfolio = False
    in_risks = False
    table_data = []

    for line in lines:
        stripped = line.strip()

        # Detect table rows (| data |)
        if stripped.startswith("|") and "SYMBOL" not in stripped.upper() and "---" not in stripped:
            parts = [p.strip() for p in stripped.split("|")[1:-1]]
            if len(parts) >= 7 and parts[0].strip().isdigit():
                table_data.append(parts)
            continue

        # Portfolio summary section
        if "TOTAL CAPITAL" in stripped or "ALLOCATION BREAKDOWN" in stripped:
            in_portfolio = True
            in_risks = False
        elif "KEY RISKS" in stripped:
            in_risks = True
            in_portfolio = False
        elif stripped.startswith(">"):
            note_text = stripped.lstrip("> ").replace("**", "").replace("*", "")
            special_notes += '<tr><td style="padding:12px;background:#1a1200;border-left:3px solid #ff9800;font-size:13px;color:#ffd740">%s</td></tr>' % note_text

        if in_portfolio and stripped and not stripped.startswith("#") and not stripped.startswith("```") and not stripped.startswith("="):
            stripped_clean = stripped.replace("├──", "").replace("└──", "").replace("│", "").strip()
            if stripped_clean and "Rs" in stripped_clean:
                portfolio_section += '<tr><td style="padding:4px 12px;font-size:12px;color:#c2c6d5;font-family:monospace">%s</td></tr>' % stripped_clean

        if in_risks and stripped and stripped[0].isdigit():
            risk_text = stripped.lstrip("0123456789. ").replace("**", "")
            risks_section += '<tr><td style="padding:6px 12px;font-size:12px;color:#c2c6d5">%s</td></tr>' % risk_text

    # Handle empty state -- no table data parsed from analysis
    if not table_data:
        picks_rows = '''
        <tr><td style="padding:20px;text-align:center">
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#12151f;border-radius:8px;border:1px solid #1e2233">
            <tr><td style="padding:24px 16px;text-align:center;font-size:14px;color:#8c909e">
              No stock picks could be parsed from the analysis.<br>
              Check the raw scanner output for details.
            </td></tr>
          </table>
        </td></tr>'''

    # Build pick cards (mobile-friendly, one per row)
    for parts in table_data:
        try:
            rank = parts[0].strip()
            symbol = parts[1].strip().replace("**", "").replace("*", "")
            buy_range = parts[2].strip()
            t1 = parts[3].strip()
            t2 = parts[4].strip()
            sl = parts[5].strip()
            alloc = parts[6].strip()
            why = parts[7].strip() if len(parts) > 7 else ""

            # Color targets
            t1_color = "#00e475"
            t2_color = "#00e475"
            sl_color = "#ff5252"

            picks_rows += f'''
            <tr><td style="padding:0 0 12px 0">
              <table width="100%" cellpadding="0" cellspacing="0" style="background:#12151f;border-radius:8px;border:1px solid #1e2233">
                <tr>
                  <td style="padding:12px 16px;border-bottom:1px solid #1e2233">
                    <table width="100%" cellpadding="0" cellspacing="0">
                      <tr>
                        <td style="font-size:20px;font-weight:bold;color:#e2e2eb">{symbol}</td>
                        <td align="right">
                          <span style="background:{regime_color};color:#000;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:bold">{alloc}</span>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding:10px 16px">
                    <table width="100%" cellpadding="0" cellspacing="0">
                      <tr>
                        <td width="33%" style="font-size:11px;color:#8c909e">BUY RANGE</td>
                        <td width="33%" style="font-size:11px;color:#8c909e">TARGET 1</td>
                        <td width="34%" style="font-size:11px;color:#8c909e">STOP LOSS</td>
                      </tr>
                      <tr>
                        <td style="font-size:14px;font-weight:bold;color:#4f8ff7;padding-top:2px">{buy_range}</td>
                        <td style="font-size:14px;font-weight:bold;color:{t1_color};padding-top:2px">{t1}</td>
                        <td style="font-size:14px;font-weight:bold;color:{sl_color};padding-top:2px">{sl}</td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding:4px 16px 8px">
                    <table width="100%" cellpadding="0" cellspacing="0">
                      <tr>
                        <td style="font-size:11px;color:#8c909e">TARGET 2</td>
                      </tr>
                      <tr>
                        <td style="font-size:13px;font-weight:bold;color:{t2_color};padding-top:2px">{t2}</td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding:8px 16px 12px;font-size:12px;color:#a0a4b8;border-top:1px solid #1e2233">{why}</td>
                </tr>
              </table>
            </td></tr>'''
        except (IndexError, ValueError):
            continue

    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{ margin:0; padding:0; background:#0b0d13; font-family:'Segoe UI',Arial,sans-serif; }}
  table {{ border-collapse:collapse; }}
</style>
</head><body style="background:#0b0d13">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#0b0d13">
  <!-- Header -->
  <tr><td style="padding:24px 16px;background:linear-gradient(135deg,#0d1b3e,#1a237e);border-radius:12px 12px 0 0">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td style="font-size:22px;font-weight:bold;color:#fff">NEPSE AutoScan</td>
        <td align="right"><span style="background:{regime_color};color:#000;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:bold">{regime}</span></td>
      </tr>
      <tr><td colspan="2" style="font-size:12px;color:#8c9eff;padding-top:4px">{scan_date} &middot; Top 10 Buy Picks with Exact Targets</td></tr>
    </table>
  </td></tr>

  <!-- Special Notes -->
  {special_notes}

  <!-- Pick Cards -->
  <tr><td style="padding:16px">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr><td style="font-size:14px;font-weight:bold;color:#4f8ff7;padding-bottom:12px">TOP PICKS</td></tr>
      {picks_rows}
    </table>
  </td></tr>

  <!-- Portfolio Summary -->
  <tr><td style="padding:0 16px 16px">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#12151f;border-radius:8px;border:1px solid #1e2233">
      <tr><td style="padding:12px 16px;font-size:13px;font-weight:bold;color:#00e475;border-bottom:1px solid #1e2233">PORTFOLIO ALLOCATION</td></tr>
      {portfolio_section}
    </table>
  </td></tr>

  <!-- Risks -->
  <tr><td style="padding:0 16px 16px">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#12151f;border-radius:8px;border:1px solid #1e2233">
      <tr><td style="padding:12px 16px;font-size:13px;font-weight:bold;color:#ff5252;border-bottom:1px solid #1e2233">KEY RISKS</td></tr>
      {risks_section}
    </table>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px;text-align:center;font-size:10px;color:#555">
    Generated by NEPSE AutoScan + Claude Sonnet 4.6<br>
    Not financial advice. Paper trading only. DYOR.
  </td></tr>
</table>
</body></html>'''

    return html
