"""Portfolio Email — NEPSE Portfolio Report with sector heat map + multi-agent outputs."""
import os, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

def _pnl_c(pnl):
    if pnl>=10: return "#1b5e20","#e8f5e9"
    if pnl>=3:  return "#2e7d32","#f1f8e9"
    if pnl>=0:  return "#388e3c","#f9fbe7"
    if pnl>=-5: return "#e65100","#fff3e0"
    if pnl>=-15:return "#c62828","#ffebee"
    return "#880e4f","#fce4ec"

def _badge(action):
    s={"SELL":("white","#c62828","SELL"),"STRONG_SELL":("white","#880e4f","STRONG SELL"),
       "SELL_PARTIAL":("white","#e65100","SELL PARTIAL"),"CONSIDER_EXIT":("white","#bf360c","EXIT"),
       "BUY":("white","#1b5e20","BUY"),"HOLD":("#37474f","#eceff1","HOLD"),
       "HOLD_RECOVERY":("#4e342e","#efebe9","RECOVER")}.get(action,("#555","#eee",action))
    return f'<span style="background:{s[1]};color:{s[0]};padding:2px 8px;border-radius:3px;font-weight:bold;font-size:11px;">{s[2]}</span>'

def _sector_heatmap(sector_analysis):
    sectors = sector_analysis.get("sectors",[])
    if not sectors: return ""
    heat_colors = {
        "HOT":     ("#1b5e20","#e8f5e9"),
        "WARMING": ("#2e7d32","#f1f8e9"),
        "NEUTRAL": ("#546e7a","#eceff1"),
        "COOLING": ("#e65100","#fff3e0"),
        "COLD":    ("#c62828","#ffebee"),
    }
    rows = ""
    for i,s in enumerate(sectors):
        tc,bg = heat_colors.get(s["heat"],("#555","#fff"))
        row_bg = "#ffffff" if i%2==0 else "#fafafa"
        rows += f"""<tr style="background:{row_bg};">
            <td style="padding:6px 8px;font-weight:bold;color:{tc};background:{bg};border-radius:3px;font-size:12px;">{s['heat']}</td>
            <td style="padding:6px 8px;font-weight:bold;font-size:12px;">{s['sector']}</td>
            <td style="padding:6px 8px;text-align:center;color:{'#2e7d32' if s['avg_change_pct']>=0 else '#c62828'};font-weight:bold;font-size:12px;">{s['avg_change_pct']:+.2f}%</td>
            <td style="padding:6px 8px;font-size:11px;color:#555;">{s['action']}</td>
        </tr>"""
    return f"""
    <div style="margin:16px 0;">
        <h2 style="color:#1565c0;margin-bottom:5px;">Sector Heat Map</h2>
        <p style="font-size:11px;color:#888;margin:0 0 8px;">Rotate INTO hot sectors, OUT OF cold</p>
        <table style="width:100%;border-collapse:collapse;">
            <thead><tr style="background:#283593;color:white;">
                <th style="padding:6px 8px;text-align:left;font-size:11px;">Heat</th>
                <th style="padding:6px 8px;text-align:left;font-size:11px;">Sector</th>
                <th style="padding:6px 8px;text-align:center;font-size:11px;">Chg%</th>
                <th style="padding:6px 8px;text-align:left;font-size:11px;">Call</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>
    </div>"""

def _portfolio_table(snapshot):
    cards=""
    for i,s in enumerate(snapshot):
        pnl=s.get("pnl_pct",0); ltp=s.get("ltp",s.get("current_price",0))
        wacc=s.get("wacc",0); pnl_abs=s.get("pnl_abs",(ltp-wacc)*s.get("shares",0))
        tc,bg=_pnl_c(pnl)
        chg=s.get("change_today",s.get("pct_change",0))
        chg_c="#2e7d32" if chg>=0 else "#c62828"
        ltp_str=f"NPR {ltp:,.2f}" if ltp>0 else "N/A"
        cards+=f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;margin-bottom:8px;">
            <tr>
                <td style="padding:10px 12px;">
                    <table width="100%" cellpadding="0" cellspacing="0">
                        <tr>
                            <td style="font-weight:bold;font-size:15px;color:#1565c0;">{s.get("symbol","")}</td>
                            <td align="right">{_badge(s.get("action","HOLD"))}</td>
                        </tr>
                        <tr>
                            <td style="font-size:11px;color:#888;padding-top:2px;">{s.get("sector","")[:20]} &middot; {s.get("shares",0):,} shares @ {wacc:,.0f}</td>
                            <td></td>
                        </tr>
                    </table>
                    <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px;">
                        <tr>
                            <td width="33%" style="font-size:10px;color:#888;">LTP</td>
                            <td width="33%" style="font-size:10px;color:#888;">TODAY</td>
                            <td width="34%" style="font-size:10px;color:#888;">P&amp;L</td>
                        </tr>
                        <tr>
                            <td style="font-size:14px;font-weight:bold;">{ltp_str}</td>
                            <td style="font-size:14px;font-weight:bold;color:{chg_c};">{chg:+.2f}%</td>
                            <td style="font-size:14px;font-weight:bold;color:{tc};">{pnl:+.2f}% (NPR {pnl_abs:+,.0f})</td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>"""
    return f"""<div style="margin:16px 0;">
        <h2 style="color:#1565c0;margin-bottom:8px;">Portfolio Breakdown</h2>
        {cards}
    </div>"""

def _action_cards(signals):
    urgent=[s for s in signals if s.get("action") in ("SELL","STRONG_SELL","SELL_PARTIAL","CONSIDER_EXIT")]
    if not urgent:
        return '<div style="background:#e8f5e9;border-left:4px solid #2e7d32;padding:14px;border-radius:4px;margin:15px 0;"><strong>[OK] No urgent sells today.</strong> Market regime says HOLD. Monitor sector heat for entry signals.</div>'
    cards=""
    for s in urgent:
        pnl=s.get("pnl_pct",0); tc,bg=_pnl_c(pnl); ltp=s.get("current_price",0)
        cards+=f"""<div style="background:{bg};border-left:5px solid {tc};border-radius:5px;padding:10px 12px;margin:8px 0;">
            <div><span style="font-size:18px;font-weight:bold;color:{tc};">{s.get("symbol","")}</span> {_badge(s.get("action",""))}</div>
            <div style="font-size:12px;color:#555;margin-top:4px;">NPR {ltp:,.2f} &rarr; target NPR {s.get("sell_target",ltp):,.2f} | {s.get("qty_suggested",0):,} shares</div>
            <div style="margin-top:4px;font-size:12px;color:#444;">P&amp;L: <strong style="color:{tc};">{pnl:+.2f}%</strong> &middot; {s.get("reason","")}</div>
        </div>"""
    return f'<div style="margin:20px 0;"><h2 style="color:#c62828;margin-bottom:5px;">Priority Actions</h2>{cards}</div>'

def _opportunities_table(top_picks, avoid_stocks):
    if not top_picks: return ""
    cards=""
    for i,b in enumerate(top_picks[:8]):
        t=b.get("targets",{}); pct=b.get("pct_change",b.get("pc",0))
        ltp=b.get("ltp",b.get("price",0)); score=b.get("composite_score",b.get("opportunity_score",0))
        pct_c="#2e7d32" if pct>=0 else "#c62828"
        rr=t.get("risk_reward",0)
        cards+=f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;margin-bottom:8px;">
            <tr><td style="padding:10px 12px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                        <td style="font-weight:bold;font-size:15px;color:#1a237e;">{b.get("symbol","")}</td>
                        <td align="right"><span style="background:#e3f2fd;color:#0d47a1;padding:2px 8px;border-radius:8px;font-weight:bold;font-size:12px;">{score:.0f}</span></td>
                    </tr>
                    <tr><td style="font-size:11px;color:#888;padding-top:2px;">{b.get("sector","")} &middot; NPR {ltp:,.2f} (<span style="color:{pct_c};font-weight:bold;">{pct:+.2f}%</span>) &middot; R:R {rr:.1f}x</td><td></td></tr>
                </table>
                <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:6px;">
                    <tr>
                        <td width="33%" style="font-size:10px;color:#888;">TARGET 1</td>
                        <td width="33%" style="font-size:10px;color:#888;">TARGET 2</td>
                        <td width="34%" style="font-size:10px;color:#888;">STOP LOSS</td>
                    </tr>
                    <tr>
                        <td style="font-size:13px;font-weight:bold;color:#2e7d32;">NPR {t.get("tp1",0):,.0f}</td>
                        <td style="font-size:13px;font-weight:bold;color:#2e7d32;">NPR {t.get("tp2",0):,.0f}</td>
                        <td style="font-size:13px;font-weight:bold;color:#c62828;">NPR {t.get("sl",0):,.0f}</td>
                    </tr>
                </table>
                {"<div style='font-size:11px;color:#555;margin-top:4px;'>" + b.get("reason","")[:80] + "</div>" if b.get("reason") else ""}
            </td></tr>
        </table>"""
    avoid_html=""
    if avoid_stocks:
        avoid_rows = "".join(f'<div style="background:#ffebee;border-left:3px solid #c62828;padding:8px 12px;margin:4px 0;border-radius:3px;font-size:13px;"><strong style="color:#c62828;">AVOID {a["symbol"]}</strong> {a.get("pc",0):+.2f}% — {a.get("avoid_reason","")}</div>' for a in avoid_stocks[:4])
        avoid_html = f'<div style="margin-top:8px;">{avoid_rows}</div>'
    return f"""<div style="margin:16px 0;">
        <h2 style="color:#1565c0;margin-bottom:3px;">Pre-Breakout Picks</h2>
        <p style="font-size:12px;color:#888;margin:0 0 8px;">Algorithm-screened stocks in accumulation phase</p>
        {cards}
        {avoid_html}
    </div>"""

def _agent_section(agent_outputs):
    if not agent_outputs: return ""
    model = agent_outputs.get("model_used","AI")
    sections = []
    for key, title, color in [
        ("sector_analysis", "Agent 1: Sector Rotation Analysis", "#1a237e"),
        ("validated_picks",  "Agent 2: Validated Stock Picks",   "#1b5e20"),
        ("risk_report",      "Agent 3: Risk Assessment",          "#b71c1c"),
        ("portfolio_advice", "Agent 4: Your Portfolio Strategy",  "#e65100"),
    ]:
        content = agent_outputs.get(key,"")
        if content and not content.startswith("[Agent Error"):
            sections.append(f"""<div style="border-left:4px solid {color};padding:12px 16px;margin:12px 0;background:#fafafa;border-radius:0 5px 5px 0;">
                <h3 style="color:{color};margin:0 0 8px;font-size:14px;">{title}</h3>
                <div style="font-size:13px;color:#333;line-height:1.7;white-space:pre-wrap;">{content}</div>
            </div>""")
    if not sections: return ""
    return f"""<div style="margin:20px 0;">
        <h2 style="color:#e65100;margin-bottom:5px;">Multi-Agent AI Analysis <span style="font-size:13px;color:#888;font-weight:normal;">({model})</span></h2>
        <p style="font-size:12px;color:#888;margin:0 0 10px;">4 specialized agents: Sector Analyst → Stock Validator → Risk Officer → Portfolio Advisor</p>
        {"".join(sections)}
    </div>"""

def build_portfolio_email(portfolio_snapshot, per_stock_signals, macro_data,
                          top_opportunities=None, avoid_stocks=None, sector_analysis=None,
                          agent_outputs=None, total_invested=0, total_current_value=0):
    today=datetime.utcnow().strftime("%Y-%m-%d")
    pnl=total_current_value-total_invested
    pnl_pct=(pnl/total_invested*100) if total_invested>0 else 0
    sign="+" if pnl_pct>=0 else ""; trend_label="UP" if pnl_pct>=0 else "DOWN"
    urgent=sum(1 for s in per_stock_signals if s.get("action") in ("SELL","STRONG_SELL","SELL_PARTIAL","CONSIDER_EXIT"))
    call=f"{urgent} SELL signal{'s' if urgent!=1 else ''}" if urgent else "HOLD — monitor"
    pnl_tc,_=_pnl_c(pnl_pct)
    pnl_bar_c="#a5d6a7" if pnl_pct>=0 else "#ef9a9a"

    # Macro summary
    breadth=macro_data.get("breadth",{}); regime=macro_data.get("regime","UNCERTAIN")
    rc={"RISK_ON":"#1b5e20","RISK_OFF":"#c62828","UNCERTAIN":"#e65100"}.get(regime,"#555")
    movers=macro_data.get("top_movers",{}); ins=macro_data.get("insurance_sector",{})
    top_g=" · ".join(f"{g['symbol']} {float(g.get('change',0)):+.1f}%" for g in movers.get("top_gainers",[])[:4])
    top_l=" · ".join(f"{l['symbol']} {float(l.get('change',0)):+.1f}%" for l in movers.get("top_losers",[])[:4])

    subject=f"NEPSE {today} | {trend_label} {sign}{pnl_pct:.1f}% | NPR {total_current_value:,.0f} | {call.upper()}"

    html=f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:Arial,sans-serif;margin:0;padding:0;background:#f0f2f5;">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;">
<tr><td>
<div style="background:linear-gradient(135deg,#0d1b6e,#1a237e,#1565c0);color:white;padding:20px 16px;border-radius:10px 10px 0 0;">
    <h1 style="margin:0;font-size:18px;">NEPSE Portfolio Report</h1>
    <p style="margin:4px 0 0;font-size:11px;opacity:0.8;">{today} | AI + ML Screener</p>
</div>
<table width="100%" cellpadding="0" cellspacing="0" style="background:#1a237e;color:white;border-collapse:collapse;">
<tr>
    <td width="50%" style="padding:10px 12px;border-right:1px solid rgba(255,255,255,0.15);border-bottom:1px solid rgba(255,255,255,0.15);">
        <div style="font-size:10px;opacity:0.7;text-transform:uppercase;">Invested</div>
        <div style="font-size:16px;font-weight:bold;">NPR {total_invested:,.0f}</div>
    </td>
    <td width="50%" style="padding:10px 12px;border-bottom:1px solid rgba(255,255,255,0.15);">
        <div style="font-size:10px;opacity:0.7;text-transform:uppercase;">Current Value</div>
        <div style="font-size:16px;font-weight:bold;">NPR {total_current_value:,.0f}</div>
    </td>
</tr>
<tr>
    <td style="padding:10px 12px;border-right:1px solid rgba(255,255,255,0.15);">
        <div style="font-size:10px;opacity:0.7;text-transform:uppercase;">Total P&amp;L</div>
        <div style="font-size:16px;font-weight:bold;color:{pnl_bar_c};">{sign}NPR {pnl:,.0f} ({sign}{pnl_pct:.2f}%)</div>
    </td>
    <td style="padding:10px 12px;">
        <div style="font-size:10px;opacity:0.7;text-transform:uppercase;">Regime &middot; Call</div>
        <div style="font-size:14px;font-weight:bold;color:{'#a5d6a7' if regime=='RISK_ON' else '#ef9a9a' if regime=='RISK_OFF' else '#fff176'};">{regime} &middot; {call.upper()}</div>
    </td>
</tr></table>

<div style="padding:12px;background:#fafafa;">
<!-- Macro -->
<div style="background:#f5f5f5;padding:12px;border-radius:8px;margin:0 0 12px;border:1px solid #e0e0e0;">
    <h3 style="color:#1565c0;margin:0 0 8px;font-size:14px;">Market Macro</h3>
    <div style="font-size:12px;line-height:1.6;">
        <strong>Regime:</strong> <span style="color:{rc};font-weight:bold;">{regime} ({macro_data.get("score",0):+.1f})</span><br>
        <strong>Breadth:</strong> {breadth.get("gainers",0)} up / {breadth.get("losers",0)} down ({breadth.get("mood","")})<br>
        <strong>Insurance:</strong> {ins.get("change_pct",0):+.2f}% ({ins.get("sentiment","")})
    </div>
    {"<div style='font-size:11px;margin-top:6px;color:#2e7d32;'>Top: " + top_g + "</div>" if top_g else ""}
    {"<div style='font-size:11px;margin-top:2px;color:#c62828;'>Bottom: " + top_l + "</div>" if top_l else ""}
    <p style="margin:6px 0 0;font-size:12px;color:#666;font-style:italic;">{macro_data.get("stance","")}</p>
</div>

{_sector_heatmap(sector_analysis or {})}
{_action_cards(per_stock_signals)}
{_portfolio_table(portfolio_snapshot)}
{_opportunities_table(top_opportunities or [], avoid_stocks or [])}
{_agent_section(agent_outputs or {})}

<div style="background:#e8eaf6;padding:12px;border-radius:5px;font-size:11px;color:#666;margin-top:12px;">
    Automated signal — not financial advice. Verify on TMS before trading.<br>
    Market 11 AM-3 PM NPT, Sun-Thu | TITAN RTX 24GB
</div></div>
</td></tr></table>
</body></html>"""

    text_lines=[f"NEPSE PORTFOLIO REPORT — {today}","="*55,
                f"Value: NPR {total_current_value:,.0f} | P&L: {sign}{pnl_pct:.2f}%","","PORTFOLIO:"]
    for s in portfolio_snapshot:
        ltp=s.get("ltp",s.get("current_price",0))
        text_lines.append(f"  {s.get('symbol',''):8s} NPR{ltp:,.2f}  {s.get('pnl_pct',0):+.2f}%  → {s.get('action','HOLD')}")
    ao=agent_outputs or {}
    if ao.get("portfolio_advice"):
        text_lines+=["","AI ADVICE:",ao["portfolio_advice"]]

    return subject, html, "\n".join(text_lines)


def send_portfolio_email(portfolio_snapshot, per_stock_signals, macro_data,
                         top_opportunities=None, avoid_stocks=None, sector_analysis=None,
                         agent_outputs=None, total_invested=0, total_current_value=0):
    to=os.getenv("ALERT_EMAIL","tpadamjung@gmail.com")
    pw=os.getenv("ALERT_PASSWORD","")
    if not pw: print("[!] ALERT_PASSWORD not set"); return False
    subject,html,text=build_portfolio_email(
        portfolio_snapshot,per_stock_signals,macro_data,
        top_opportunities,avoid_stocks,sector_analysis,
        agent_outputs,total_invested,total_current_value)
    msg=MIMEMultipart("alternative")
    msg["Subject"]=subject; msg["From"]=to; msg["To"]=to
    msg.attach(MIMEText(text,"plain")); msg.attach(MIMEText(html,"html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com",465) as s:
            s.login(to,pw); s.sendmail(to,to,msg.as_string())
        print(f"[OK] Email sent to {to}"); return True
    except Exception as e:
        print(f"[!] Email error: {e}"); return False
