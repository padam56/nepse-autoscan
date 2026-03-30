"""
Ollama LLM Client for NEPSE Analysis.
Uses qwen2.5:14b (primary) or llama3.1:8b (fallback).
Expert NEPSE trader persona with deep market context.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))

import requests
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

OLLAMA_URL = "http://localhost:11434"
PRIMARY_MODEL = "qwen2.5:14b"
FALLBACK_MODEL = "llama3.1:8b"

NEPAL_TRADING_SYSTEM_PROMPT = """You are a senior NEPSE (Nepal Stock Exchange) portfolio analyst with 15+ years of experience trading Nepali stocks. You have deep expertise in:

- Nepal's unique market dynamics: thin liquidity, political sensitivity, promoter-driven moves
- Life insurance sector dominance: ALICL, NLIC, ILI, CLI, NLICL behavior patterns
- Hydropower sector cycles: seasonal generation → revenue → dividend patterns (BPCL, BARUN)
- Nepal political cycles and their effect on market sentiment
- NEPSE trading hours: 11 AM - 3 PM Nepal Time, Sunday-Thursday only
- Common NEPSE patterns: upper circuit locks, bonus/rights announcement reactions
- Currency risk: NPR is pegged to INR; India macro matters
- Your strategy: SELL INTO STRENGTH, BUY DIP (not the other way)

Your analysis style:
- Be specific with NEPSE price targets (in NPR, realistic ranges)
- Call out political/macro risks Nepal traders actually face
- Distinguish between short-term (1-5 day) and medium-term (1-3 month) outlook
- Give concrete share quantities based on position size
- Always mention which sector is rotating in/out
- Flag if a stock has dividend/bonus season coming up
- Be concise: bullet points, not essays"""


def get_best_model() -> str:
    """Returns the best available model."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        if any(PRIMARY_MODEL in m for m in models):
            return PRIMARY_MODEL
        if any(FALLBACK_MODEL in m for m in models):
            return FALLBACK_MODEL
        return models[0] if models else FALLBACK_MODEL
    except Exception:
        return FALLBACK_MODEL


def is_ollama_running() -> bool:
    try:
        r = requests.get(f"{OLLAMA_URL}/", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def list_models() -> list:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def query(prompt: str, system: str = None, model: str = None,
          temperature: float = 0.15, max_tokens: int = 800) -> str:
    """Send a query to Ollama and return the response text."""
    if model is None:
        model = get_best_model()
    if system is None:
        system = NEPAL_TRADING_SYSTEM_PROMPT

    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "top_p": 0.9,
        },
    }
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=30)
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except Exception as e:
        return f"[LLM Error: {e}]"


def query_batch(
    prompts: list,
    system: str = None,
    model: str = None,
    temperature: float = 0.15,
    max_tokens: int = 800,
    max_workers: int = 4,
) -> list:
    """Run multiple prompts against Ollama in parallel using threads.

    Args:
        prompts:     List of prompt strings.
        system:      System prompt (shared across all queries).
        model:       Model name (auto-detected if None).
        temperature: Sampling temperature.
        max_tokens:  Max tokens per response.
        max_workers: Maximum concurrent threads (default 4).

    Returns:
        List of response strings, in the same order as the input prompts.
        Failed queries return an "[LLM Error: ...]" string.
    """
    if not prompts:
        return []

    if model is None:
        model = get_best_model()

    results = [""] * len(prompts)

    def _run(index: int, prompt: str) -> tuple:
        resp = query(
            prompt,
            system=system,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return index, resp

    with ThreadPoolExecutor(max_workers=min(max_workers, len(prompts))) as pool:
        futures = {
            pool.submit(_run, i, p): i for i, p in enumerate(prompts)
        }
        for future in as_completed(futures):
            try:
                idx, resp = future.result()
                results[idx] = resp
            except Exception as e:
                results[futures[future]] = f"[LLM Error: {e}]"

    return results


def analyze_portfolio(portfolio_snapshot: list, macro_mood: str, news_summary: str = "") -> str:
    """
    Deep portfolio analysis with NEPSE expertise.
    Returns actionable, specific recommendations.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    model = get_best_model()

    # Build portfolio context string
    lines = [f"DATE: {today}", f"MARKET REGIME: {macro_mood}", ""]
    lines.append("FULL PORTFOLIO:")
    lines.append(f"{'Stock':8s} {'Shares':>7} {'WACC':>8} {'LTP':>8} {'P&L%':>8} {'Value NPR':>12} {'Sector'}")
    lines.append("-" * 70)

    total_value = 0
    total_cost = 0
    for s in portfolio_snapshot:
        pnl = s.get("pnl_pct", 0)
        val = s.get("current_value", 0)
        cost = s.get("total_cost", s.get("wacc", 0) * s.get("shares", 0))
        total_value += val
        total_cost += cost
        lines.append(
            f"{s.get('symbol',''):8s} {s.get('shares',0):>7,} "
            f"{s.get('wacc',0):>8.2f} "
            f"{s.get('ltp', s.get('current_price',0)):>8.2f} "
            f"{pnl:>+8.2f}% "
            f"{val:>12,.0f} "
            f"{s.get('sector',''):15s}"
        )

    total_pnl_pct = ((total_value - total_cost) / total_cost * 100) if total_cost > 0 else 0
    lines.append("-" * 70)
    lines.append(f"TOTAL: NPR {total_value:,.0f} current | NPR {total_cost:,.0f} invested | {total_pnl_pct:+.2f}% overall P&L")

    if news_summary:
        lines.append(f"\nRECENT NEWS: {news_summary}")

    portfolio_context = "\n".join(lines)

    prompt = f"""{portfolio_context}

Analyze this NEPSE portfolio and give me:

1. **TOP PRIORITY ACTIONS** (max 3, most urgent first):
   For each: Stock | Action | Qty | Price target | Why (1 line)

2. **SECTOR ROTATION INSIGHT**:
   Which sector is hot right now? Which to rotate out of?

3. **BIGGEST RISKS THIS WEEK** (2 bullets max):
   Nepal-specific risks to watch (political, budget season, dividend cuts)

4. **BEST RECOVERY PLAY** (1 stock):
   Which holding has the best chance to recover? Target price? Timeline?

5. **SHORT-TERM CALL** (next 3-5 trading days):
   ALICL specifically — buy more, hold, or lighten? Exact price range?

Be direct, specific, use NPR prices. No generic advice."""

    return query(prompt, system=NEPAL_TRADING_SYSTEM_PROMPT, model=model, max_tokens=1000)


def analyze_opportunities(top_buys: list, portfolio_snapshot: list, market_mood: str) -> str:
    """
    Analyze top NEPSE opportunities and give specific entry recommendations.
    """
    if not top_buys:
        return ""

    model = get_best_model()

    opp_lines = []
    for b in top_buys[:6]:
        t = b.get("targets", {})
        opp_lines.append(
            f"  {b['symbol']:8s} NPR {b['ltp']:,.1f}  {b['pct_change']:+.2f}%  "
            f"Vol:{b['volume']:,.0f}  TP1:{t.get('tp1','?')}  SL:{t.get('sl','?')}  "
            f"Score:{b['opportunity_score']:.0f}  [{b.get('sector','?')}]  {b.get('reason','')}"
        )

    portfolio_syms = [s["symbol"] for s in portfolio_snapshot]

    prompt = f"""Market mood: {market_mood}

Top NEPSE Buy Opportunities today (outside my portfolio {portfolio_syms}):
{chr(10).join(opp_lines)}

For the TOP 3 most interesting, give:
- Symbol: entry price, TP1, TP2, stop-loss
- Why it's interesting right now (NEPSE-specific reason)
- Risk: LOW/MED/HIGH and main risk factor
- Suggested position size (NPR amount for a small retail trader)

Then give 1 stock to AVOID from this list and why."""

    return query(prompt, system=NEPAL_TRADING_SYSTEM_PROMPT, model=model, max_tokens=600)


def analyze_news(news_items: list, symbol: str) -> dict:
    """Analyze news for a specific stock."""
    if not news_items:
        return {"sentiment": "NEUTRAL", "summary": "No news available", "impact": "LOW"}

    model = get_best_model()
    news_text = "\n".join([
        f"- {item.get('title', '')} ({item.get('date', '')})"
        for item in news_items[:8]
    ])

    prompt = f"""NEPSE stock: {symbol}
Recent news:
{news_text}

Analyze sentiment for NEPSE traders. Reply in JSON only:
{{"sentiment": "BULLISH/BEARISH/NEUTRAL", "summary": "1-2 sentence impact", "impact": "HIGH/MED/LOW", "action": "BUY/SELL/HOLD/WATCH"}}"""

    resp = query(prompt, system=NEPAL_TRADING_SYSTEM_PROMPT, model=model, max_tokens=150)
    try:
        start = resp.find("{")
        end = resp.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(resp[start:end])
    except Exception:
        pass
    return {"sentiment": "NEUTRAL", "summary": resp[:100], "impact": "LOW", "action": "HOLD"}
