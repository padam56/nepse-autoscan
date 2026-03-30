"""
llm/news_intelligence.py -- Claude-powered news analysis for NEPSE.

Scrapes financial news from Sharesansar + MeroLagani, sends to Claude
for market impact analysis, and generates actionable alerts.

Handles:
  - NRB monetary policy announcements
  - Government/political changes affecting market
  - Sector-specific regulations (banking CAR, hydro tariffs, etc.)
  - Corporate actions (mergers, rights, dividends, fraud)
  - Budget announcements and fiscal policy
  - Political instability signals

Cost: ~$0.02-0.05 per analysis (one Claude Sonnet call with all headlines).
"""
import os
import sys
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent

NPT = timezone(timedelta(hours=5, minutes=45))
CACHE_FILE = ROOT / "data" / "news_intelligence.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _scrape_sharesansar() -> List[dict]:
    """Scrape latest headlines from Sharesansar."""
    articles = []
    try:
        session = requests.Session()
        session.headers.update(HEADERS)

        for category in ["latest", "dividend", "right-share", "bonus-share", "ipo"]:
            url = f"https://www.sharesansar.com/category/{category}"
            r = session.get(url, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")

            for h4 in soup.find_all("h4"):
                link = h4.find("a")
                if not link:
                    continue
                title = link.get_text(strip=True)
                href = link.get("href", "")
                if title and len(title) > 10:
                    articles.append({
                        "title": title,
                        "url": href if href.startswith("http") else f"https://www.sharesansar.com{href}",
                        "source": "sharesansar",
                        "category": category,
                    })
            time.sleep(0.3)
    except Exception as e:
        logger.warning("[NEWS] Sharesansar scrape failed: %s" % e)
    return articles


def _scrape_merolagani() -> List[dict]:
    """Scrape latest headlines from MeroLagani."""
    articles = []
    try:
        r = requests.get("https://merolagani.com/NewsList.aspx",
                         headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        for item in soup.find_all("h4"):
            text = item.get_text(strip=True)
            link = item.find("a")
            if text and len(text) > 10:
                href = link.get("href", "") if link else ""
                articles.append({
                    "title": text,
                    "url": f"https://merolagani.com/{href}" if href else "",
                    "source": "merolagani",
                    "category": "news",
                })
    except Exception as e:
        logger.warning("[NEWS] MeroLagani scrape failed: %s" % e)
    return articles


def _scrape_nepse_news() -> List[dict]:
    """Scrape from NEPSE's own website and NRB."""
    articles = []
    try:
        session = requests.Session()
        session.headers.update(HEADERS)

        # Bizmandu (English business news)
        try:
            r = session.get("https://bizmandu.com/category/stock-market", timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            for h in soup.find_all(["h2", "h3", "h4"]):
                link = h.find("a")
                if link:
                    title = link.get_text(strip=True)
                    if title and len(title) > 15:
                        articles.append({
                            "title": title,
                            "url": link.get("href", ""),
                            "source": "bizmandu",
                            "category": "market",
                        })
        except Exception:
            pass
        time.sleep(0.3)

        # Khabarhub business (English)
        try:
            r = session.get("https://english.khabarhub.com/category/business/", timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            for h in soup.find_all(["h2", "h3"]):
                link = h.find("a")
                if link:
                    title = link.get_text(strip=True)
                    if title and len(title) > 15:
                        articles.append({
                            "title": title,
                            "url": link.get("href", ""),
                            "source": "khabarhub",
                            "category": "business",
                        })
        except Exception:
            pass
        time.sleep(0.3)

        # NRB (Nepal Rastra Bank) notices — monetary policy moves markets
        try:
            r = session.get("https://www.nrb.org.np/category/notices/", timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            for h in soup.find_all(["h2", "h3", "h4"]):
                link = h.find("a")
                if link:
                    title = link.get_text(strip=True)
                    if title and len(title) > 10:
                        articles.append({
                            "title": title,
                            "url": link.get("href", ""),
                            "source": "nrb",
                            "category": "policy",
                        })
        except Exception:
            pass

    except Exception as e:
        logger.warning("[NEWS] Additional sources scrape failed: %s" % e)
    return articles


def scrape_all_news() -> List[dict]:
    """Scrape news from all sources and deduplicate."""
    all_news = _scrape_sharesansar() + _scrape_merolagani() + _scrape_nepse_news()

    # Deduplicate by normalized title
    seen = set()
    unique = []
    for a in all_news:
        key = a["title"].lower().strip()[:50]
        if key not in seen:
            seen.add(key)
            unique.append(a)

    return unique


def analyze_with_claude(headlines: List[dict], regime: str = "UNKNOWN") -> Optional[dict]:
    """Send headlines to Claude for market impact analysis.

    Returns structured analysis with:
      - market_outlook: BULLISH / BEARISH / NEUTRAL
      - impact_score: -10 to +10
      - key_events: list of significant events with affected sectors/stocks
      - action_items: specific things to do based on news
      - risks: potential negative catalysts
    """
    try:
        import anthropic
    except ImportError:
        logger.warning("[NEWS] anthropic package not installed")
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("[NEWS] ANTHROPIC_API_KEY not set")
        return None

    client = anthropic.Anthropic(api_key=api_key)

    # Build headlines text
    headlines_text = ""
    for i, h in enumerate(headlines[:30], 1):
        headlines_text += f"{i}. [{h['source']}] {h['title']}\n"

    if not headlines_text.strip():
        return None

    system = (
        "You are a senior Nepal financial market analyst. Analyze these NEPSE-related "
        "news headlines and determine their market impact. You understand:\n"
        "- NRB (Nepal Rastra Bank) monetary policy affects banking stocks (40% of NEPSE)\n"
        "- Political instability = bearish, stable government = bullish\n"
        "- Budget season (May-Jun) creates sector-specific volatility\n"
        "- Monsoon season boosts hydropower stocks\n"
        "- Rights/bonus share announcements move individual stocks 5-15%\n"
        "- SEBON regulations can halt sectors overnight\n"
        "- Nepal-India relations affect trade/manufacturing stocks\n"
        "- Headlines may be in Nepali — translate and analyze regardless\n\n"
        "Respond in this exact JSON format (no markdown, just raw JSON):\n"
        '{\n'
        '  "market_outlook": "BULLISH" or "BEARISH" or "NEUTRAL",\n'
        '  "impact_score": -10 to +10 (0 = no impact),\n'
        '  "summary": "2-3 sentence market impact summary",\n'
        '  "key_events": [\n'
        '    {"headline": "...", "impact": "POSITIVE/NEGATIVE/NEUTRAL", '
        '"affected_sectors": ["Banking", "Hydro"], '
        '"affected_stocks": ["NABIL", "HBL"], '
        '"explanation": "1 sentence"}\n'
        '  ],\n'
        '  "action_items": ["specific action 1", "specific action 2"],\n'
        '  "stocks_to_watch": ["SYM1", "SYM2"],\n'
        '  "stocks_to_avoid": ["SYM3"],\n'
        '  "risks": ["risk 1", "risk 2"]\n'
        '}'
    )

    prompt = (
        f"Current market regime: {regime}\n"
        f"Today's date: {datetime.now(NPT).strftime('%Y-%m-%d')}\n\n"
        f"HEADLINES:\n{headlines_text}\n\n"
        f"Analyze the market impact of these headlines."
    )

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()

        # Parse JSON — strip markdown code blocks, then extract JSON object
        import re
        text = re.sub(r'```json\s*|\s*```', '', text).strip()
        # Find the first valid JSON object by trying progressively from each '{'
        start = text.find('{')
        analysis = None
        if start >= 0:
            for end in range(len(text) - 1, start, -1):
                if text[end] == '}':
                    try:
                        analysis = json.loads(text[start:end + 1])
                        break
                    except json.JSONDecodeError:
                        continue
        if analysis is None:
            analysis = json.loads(text)
        analysis["analyzed_at"] = datetime.now(NPT).isoformat()
        analysis["headline_count"] = len(headlines)

        # Cache result
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(analysis, indent=2, ensure_ascii=False))

        return analysis

    except json.JSONDecodeError as e:
        logger.warning("[NEWS] Failed to parse Claude response as JSON: %s" % e)
        return None
    except Exception as e:
        logger.warning("[NEWS] Claude analysis failed: %s" % e)
        return None


def format_telegram_alert(analysis: dict) -> str:
    """Format analysis as Telegram message."""
    outlook = analysis.get("market_outlook", "UNKNOWN")
    score = analysis.get("impact_score", 0)
    summary = analysis.get("summary", "")

    emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(outlook, "⚪")
    score_bar = "+" * max(score, 0) + "-" * max(-score, 0) if score != 0 else "~"

    msg = f"<b>{emoji} News Intelligence — {outlook}</b>\n"
    msg += f"Impact: {score_bar} ({score:+d}/10)\n\n"
    msg += f"<i>{summary}</i>\n\n"

    # Key events
    events = analysis.get("key_events", [])
    if events:
        msg += "<b>Key Events:</b>\n"
        for e in events[:5]:
            impact_icon = {"POSITIVE": "🟢", "NEGATIVE": "🔴"}.get(e.get("impact"), "🟡")
            msg += f"{impact_icon} {e.get('explanation', '')}\n"
            stocks = e.get("affected_stocks", [])
            if stocks:
                msg += f"   Stocks: {', '.join(stocks)}\n"
        msg += "\n"

    # Action items
    actions = analysis.get("action_items", [])
    if actions:
        msg += "<b>Actions:</b>\n"
        for a in actions[:3]:
            msg += f"→ {a}\n"
        msg += "\n"

    # Stocks to watch / avoid
    watch = analysis.get("stocks_to_watch", [])
    avoid = analysis.get("stocks_to_avoid", [])
    if watch:
        msg += f"👀 Watch: {', '.join(watch)}\n"
    if avoid:
        msg += f"⚠️ Avoid: {', '.join(avoid)}\n"

    return msg


def format_email_html(analysis: dict) -> str:
    """Format analysis as HTML for email newsletter."""
    outlook = analysis.get("market_outlook", "UNKNOWN")
    score = analysis.get("impact_score", 0)
    summary = analysis.get("summary", "")
    color = {"BULLISH": "#00e475", "BEARISH": "#ff5252", "NEUTRAL": "#ffd740"}.get(outlook, "#888")

    html = f'''
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#12151f;border-radius:8px;border:1px solid #1e2233;margin-bottom:12px">
      <tr><td style="padding:12px 16px;border-bottom:1px solid #1e2233">
        <table width="100%"><tr>
          <td style="font-size:14px;font-weight:bold;color:{color}">News Intelligence — {outlook}</td>
          <td align="right" style="font-size:12px;color:#8c909e">Impact: {score:+d}/10</td>
        </tr></table>
      </td></tr>
      <tr><td style="padding:12px 16px;font-size:13px;color:#c2c6d5">{summary}</td></tr>'''

    events = analysis.get("key_events", [])
    for e in events[:3]:
        impact_color = {"POSITIVE": "#00e475", "NEGATIVE": "#ff5252"}.get(e.get("impact"), "#ffd740")
        stocks = ", ".join(e.get("affected_stocks", []))
        html += f'''
      <tr><td style="padding:6px 16px;font-size:12px;border-top:1px solid #1e2233">
        <span style="color:{impact_color};font-weight:bold">{e.get("impact", "")}</span>
        <span style="color:#a0a4b8"> — {e.get("explanation", "")}</span>
        {f'<br><span style="color:#4f8ff7;font-size:11px">Stocks: {stocks}</span>' if stocks else ''}
      </td></tr>'''

    actions = analysis.get("action_items", [])
    if actions:
        html += '<tr><td style="padding:10px 16px;border-top:1px solid #1e2233;font-size:12px;color:#00e475">'
        html += "<br>".join(f"→ {a}" for a in actions[:3])
        html += "</td></tr>"

    html += "</table>"
    return html


def run_news_analysis(regime: str = "UNKNOWN", send_telegram: bool = True) -> Optional[dict]:
    """Full pipeline: scrape -> analyze -> alert."""
    print("[NEWS] Scraping headlines...")
    headlines = scrape_all_news()
    print(f"[NEWS] Found {len(headlines)} headlines")

    if not headlines:
        print("[NEWS] No headlines found")
        return None

    print("[NEWS] Sending to Claude for analysis...")
    analysis = analyze_with_claude(headlines, regime)

    if not analysis:
        print("[NEWS] Analysis failed")
        return None

    outlook = analysis.get("market_outlook", "?")
    score = analysis.get("impact_score", 0)
    print(f"[NEWS] Outlook: {outlook} | Impact: {score:+d}/10")
    print(f"[NEWS] {analysis.get('summary', '')}")

    if send_telegram:
        try:
            token = os.getenv("TELEGRAM_BOT_TOKEN", "")
            chat = os.getenv("TELEGRAM_CHAT_ID", "")
            if token and chat:
                msg = format_telegram_alert(analysis)
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat, "text": msg, "parse_mode": "HTML"},
                    timeout=10,
                )
                print("[NEWS] Telegram alert sent")
        except Exception as e:
            print(f"[NEWS] Telegram failed: {e}")

    return analysis


if __name__ == "__main__":
    run_news_analysis(regime="BULL")
