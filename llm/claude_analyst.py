"""
llm/claude_analyst.py -- Claude-powered stock analysis for NEPSE AutoScan.

Replaces/supplements Qwen 2.5 14B with Claude Sonnet 4.6 for:
  1. Per-pick rationale (2-3 sentences, actionable)
  2. Portfolio-level advice (per-stock action + weekly suggestion)
  3. AI top-3 screening thesis

Uses ~$0.03 per daily scan (~$0.66/month for 22 trading days).
"""
import os
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 400


def _get_client():
    try:
        import anthropic
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            return None
        return anthropic.Anthropic(api_key=key)
    except ImportError:
        logger.warning("[CLAUDE] anthropic package not installed")
        return None


def _call(client, system: str, prompt: str, max_tokens: int = MAX_TOKENS) -> str:
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.warning("[CLAUDE] API error: %s" % e)
        return ""


def generate_rationales(picks: List[dict], regime: str) -> Dict[str, str]:
    """Generate 2-3 sentence rationale for each top pick using Claude.

    6 calls fan out in parallel via ThreadPoolExecutor (~2s vs ~12s serial).

    Args:
        picks: list of dicts with symbol, signal, score, ta_score, ml_score, reasons
        regime: market regime string (BULL/RANGE/BEAR)

    Returns:
        {symbol: rationale_text}
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    client = _get_client()
    if not client:
        return {}

    system = (
        "You are a senior NEPSE stock analyst. Write concise, actionable rationales "
        "for stock picks. Each rationale should be 2-3 sentences covering: "
        "why the setup is attractive, what confirms the signal, and the key risk. "
        "Be specific about numbers. No generic filler."
    )

    def _rationale_for(p: dict) -> tuple[str, str]:
        sym = p.get("symbol", "")
        reasons = p.get("reasons", [])
        reasons_str = ", ".join(reasons) if reasons else "N/A"
        prompt = (
            f"Stock: {sym}\n"
            f"Signal: {p.get('signal', 'N/A')} (Score: {p.get('score', 0):.0f}/100)\n"
            f"TA Score: {p.get('ta_score', 0):.0f}, ML Score: {p.get('ml_score', 0):.0f}\n"
            f"Key Indicators: {reasons_str}\n"
            f"Market Regime: {regime}\n\n"
            f"Write a 2-3 sentence rationale for this pick."
        )
        return sym, _call(client, system, prompt, max_tokens=200)

    rationales = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_rationale_for, p) for p in picks[:6]]
        for fut in as_completed(futures):
            try:
                sym, text = fut.result(timeout=30)
                if text and sym:
                    rationales[sym] = text
            except Exception as e:
                logger.warning("[CLAUDE] rationale failed: %s" % e)

    return rationales


def analyze_portfolio(holdings: List[dict], regime: str) -> str:
    """Generate portfolio-level advice using Claude.

    Args:
        holdings: list of dicts with symbol, shares, wacc, current_price, pnl_pct, rsi, ret_5d
        regime: market regime string

    Returns:
        Formatted analysis text (for email/console)
    """
    client = _get_client()
    if not client:
        return ""

    system = (
        "You are a senior NEPSE portfolio advisor. Analyze the portfolio and give "
        "specific, actionable advice. For each stock, recommend: HOLD, ADD, TRIM, or EXIT "
        "with a target price and one-sentence reason. End with one specific action "
        "to take this week. Be direct, no disclaimers."
    )

    holdings_text = ""
    for h in holdings:
        holdings_text += (
            f"- {h['symbol']}: {h.get('shares', 0):,} shares @ WACC {h.get('wacc', 0):.0f}, "
            f"Current: {h.get('current_price', 0):.0f}, P&L: {h.get('pnl_pct', 0):+.1f}%, "
            f"RSI: {h.get('rsi', 50):.0f}, 5d: {h.get('ret_5d', 0):+.1f}%\n"
        )

    prompt = (
        f"PORTFOLIO ({len(holdings)} holdings):\n{holdings_text}\n"
        f"Market Regime: {regime}\n\n"
        f"For each stock, give: ACTION | Target | Reason\n"
        f"Then: WEEKLY ACTION: [one specific thing to do]"
    )

    return _call(client, system, prompt, max_tokens=500)


def screen_ai_picks(candidates: List[dict], regime: str) -> str:
    """Generate AI thesis for top 3 screened picks.

    Args:
        candidates: list of dicts with symbol, price, rsi, ret_5d, vol_ratio
        regime: market regime

    Returns:
        Formatted analysis for the top 3
    """
    client = _get_client()
    if not client:
        return ""

    system = (
        "You are a NEPSE quant analyst. For each stock, write a one-sentence thesis "
        "explaining why this is a good entry point right now. Focus on the confluence "
        "of RSI position, momentum, and volume. Be specific."
    )

    stocks_text = ""
    for c in candidates[:3]:
        stocks_text += (
            f"- {c['symbol']}: Price={c.get('price', 0):.0f}, RSI={c.get('rsi', 50):.0f}, "
            f"5d={c.get('ret_5d', 0):+.1f}%, Vol={c.get('vol_ratio', 1):.1f}x avg\n"
        )

    prompt = (
        f"Market: {regime}\n"
        f"These 3 stocks passed screening (RSI 45-65, EMA aligned, moderate momentum):\n\n"
        f"{stocks_text}\n"
        f"For each, write: SYMBOL: [one-sentence thesis]"
    )

    return _call(client, system, prompt, max_tokens=300)
