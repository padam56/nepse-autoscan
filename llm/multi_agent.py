"""
Multi-Agent LLM Pipeline for NEPSE Analysis.
Chain of specialized NEPSE analyst agents, each building on previous output.
Uses qwen2.5:14b with chain-of-thought prompting.

Agent Chain:
  1. SECTOR ANALYST    → Rank sectors, identify rotation trades
  2. STOCK SCREENER    → Validate pre-breakout picks per sector thesis
  3. RISK ASSESSOR     → Flag what to avoid, why
  4. PORTFOLIO ADVISOR → Personalized advice for your specific positions
  5. FINAL SYNTHESIZER → One-page action plan (the email content)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))

import requests, json
from datetime import datetime

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen2.5:14b"
FALLBACK_MODEL = "llama3.1:8b"

NEPSE_EXPERT_BASE = """You are a senior NEPSE (Nepal Stock Exchange) quantitative analyst and portfolio manager with 20 years of experience. You have deep domain expertise:

NEPSE MECHANICS:
- Market hours: 11:00 AM – 3:00 PM NPT, Sunday–Thursday only
- Price limits: ±10% daily circuit breakers (upper/lower circuit)
- Settlement: T+3 (3 days after trade)
- Script: SEBON-regulated, CDSC for dematerialization
- Thin liquidity: Most stocks have 10-50 trades/day; institutions can move prices 3-5% on single orders
- Political sensitivity: Nepal's coalition politics directly moves insurance and banking sectors
- Remittance economy: USD inflows → NPR liquidity → market bullish cycles
- Promoter lock-in: Major promoters can't sell easily; retail + institutions drive free float

SECTOR EXPERTISE:
- LIFE INSURANCE: Most volatile sector. Regulatory-driven. Beema Samiti interventions cause ±20% swings. Bonus share/dividend season March-May.
- HYDROPOWER: Seasonal play. Oct-Mar = generation peak = revenue peak = stock peak. May-Sep = dry season = sell. NEA rate agreements matter.
- BANKING: NRB policy-sensitive. Credit growth + NPL ratio determines sector momentum. Merger news creates arbitrage.
- MICROFINANCE: Rural lending. High growth but regulatory risk. NRB microfinance caps create compression.

TRADING PATTERNS (NEPAL-SPECIFIC):
- "Upper circuit hunting": Retail traders pile into stocks approaching 10% limit hoping for lock-up arbitrage
- "Dividend play": Stocks run up 2-3 weeks before record date, crash after
- "Political pop": Any government stability news = insurance + banking +3-5% in one day
- "Promoter selling": When promoter lock-in expires, watch for distribution patterns (high volume + price near high but failing to break)
- "Rights issue dilution": Stocks often fall 15-30% after rights announcement

AVOID THESE TRAPS:
1. Stocks already up 5%+ today — too late, next day reversal likely
2. Low volume gaps up — no conviction, reversal imminent
3. After-dividend stocks — typically fall 5-10% post-record date
4. Stocks being recommended on social media/forums — retail FOMO, institution exit

YOUR EDGE: Finding stocks 3-5 TRADING DAYS before the move, not after.
"""


def _get_model() -> str:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        for m in [MODEL, FALLBACK_MODEL]:
            if any(m in x for x in models):
                return m
        return models[0] if models else FALLBACK_MODEL
    except:
        return FALLBACK_MODEL


def _query(prompt: str, system: str = None, temperature: float = 0.1, max_tokens: int = 800) -> str:
    model = _get_model()
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system or NEPSE_EXPERT_BASE,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens, "top_p": 0.85},
    }
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=180)
        return r.json().get("response", "").strip()
    except Exception as e:
        return f"[Agent Error: {e}]"


# ─────────────────────────────────────────────────────────────────────────────
# Agent 1: Sector Analyst
# ─────────────────────────────────────────────────────────────────────────────
def agent_sector_analyst(sector_data: dict, today: str) -> str:
    sectors = sector_data.get("sectors", [])
    sector_lines = []
    for s in sectors:
        sector_lines.append(
            f"  {s['heat']:15s} {s['sector']:22s} avg:{s['avg_change_pct']:+.2f}%  "
            f"breadth:{s['gainers']}/{s['stocks_count']}  score:{s['momentum_score']:+.0f}  "
            f"turnover:NPR{s['total_turnover']/1e6:.0f}M"
        )

    prompt = f"""DATE: {today}
TODAY'S SECTOR MOMENTUM (ranked best→worst):
{chr(10).join(sector_lines)}

TASK: As a NEPSE sector rotation expert, analyze this data and give me:

**SECTOR ROTATION CALL** (be specific):
1. Which 2-3 sectors should I be BUYING INTO right now? Why? (Include seasonal/catalyst reasoning)
2. Which 2-3 sectors should I be ROTATING OUT OF? Why?
3. Which sector has the BEST risk/reward for next 3-5 trading days?

**MARKET NARRATIVE** (what story does today's data tell?):
- Are institutions accumulating or distributing overall?
- Is this a sector rotation day or broad-based move?
- What's the political/macro context driving this?

Be specific. Use NPR figures. Max 250 words."""

    return _query(prompt, temperature=0.1, max_tokens=500)


# ─────────────────────────────────────────────────────────────────────────────
# Agent 2: Pre-Breakout Stock Validator
# ─────────────────────────────────────────────────────────────────────────────
def agent_stock_validator(top_picks: list, sector_call: str, history_days: int) -> str:
    if not top_picks:
        return "No pre-breakout candidates found today."

    pick_lines = []
    for b in top_picks[:8]:
        t = b.get("targets", {})
        hist = f"{b.get('multi_day_return',0):+.1f}% {b.get('history_days','')} day trend" if b.get("multi_day_return") else ""
        vol_info = f"vol {b.get('volume_label','?')} ({b.get('volume_ratio',1):.1f}x avg)" if b.get("volume_ratio") else ""
        pick_lines.append(
            f"  {b['symbol']:8s} {b['sector']:18s} ltp:NPR{b['ltp']:,.1f}  {b['pc']:+.2f}%  "
            f"pos:{b.get('price_position',0.5):.0%}  score:{b['composite_score']:.0f}  "
            f"TP1:{t.get('tp1','?')}  SL:{t.get('sl','?')}  {vol_info}  {hist}  | {b.get('reason','')[:50]}"
        )

    prompt = f"""SECTOR CONTEXT (from Agent 1):
{sector_call[:300]}

SCREENER PICKS (algorithm found these pre-breakout candidates, {history_days} days of history):
{chr(10).join(pick_lines)}

TASK: Validate these picks. For each, determine:
- Is this a REAL pre-breakout setup OR a false signal?
- What NEPSE-specific catalyst could trigger the move in next 3-5 days?
- What's the RISK FACTOR specific to Nepal market conditions?

Then give me:
**TOP 3 VALIDATED PICKS** (only picks you'd actually trade):
For each: Symbol | Entry zone (NPR range) | TP1 | TP2 | SL | Catalyst | Risk | Conviction (HIGH/MED/LOW)

**1 PICK TO AVOID from the list** and exactly why (NEPSE-specific reason).

Be ruthless. Only recommend if you'd put real money in. Max 300 words."""

    return _query(prompt, temperature=0.1, max_tokens=600)


# ─────────────────────────────────────────────────────────────────────────────
# Agent 3: Risk Assessor
# ─────────────────────────────────────────────────────────────────────────────
def agent_risk_assessor(avoid_stocks: list, sector_call: str, macro_regime: str) -> str:
    avoid_lines = [
        f"  {a['symbol']:8s} {a['pc']:+.2f}%  pos:{a.get('price_position',0.5):.0%}  "
        f"vol:{a.get('volume_ratio',1):.1f}x | {a.get('avoid_reason','')}"
        for a in avoid_stocks[:5]
    ]

    prompt = f"""MARKET REGIME: {macro_regime}

ALGORITHMIC AVOID FLAGS (stocks showing distribution/peak patterns):
{chr(10).join(avoid_lines) if avoid_lines else "  None flagged today"}

TASK: As NEPSE risk officer, give:

**THIS WEEK'S RISK FACTORS** (Nepal-specific, 2-3 bullets):
- Political: Any government events, budget, NRB meetings?
- Sector: Any sector-specific risks (rights issues, dividend exdate, regulatory)?
- Market: Liquidity traps, circuit-breaker risks?

**SHORT-SELL / AVOID LIST** (stocks likely to fall next 3-5 days):
For each: Symbol | Current level | Target drop | Why (NEPSE pattern)

**DEFENSIVE MOVES** for existing positions in difficult markets.

Max 200 words. Be specific to NEPSE, not generic finance advice."""

    return _query(prompt, temperature=0.1, max_tokens=400)


# ─────────────────────────────────────────────────────────────────────────────
# Agent 4: Portfolio Advisor (personalized)
# ─────────────────────────────────────────────────────────────────────────────
def agent_portfolio_advisor(portfolio: list, macro_regime: str,
                             sector_call: str, stock_picks: str, news: str = "") -> str:
    port_lines = []
    total_val = 0
    total_cost = 0
    for s in portfolio:
        ltp  = s.get("ltp", s.get("current_price", 0))
        wacc = s.get("wacc", 0)
        pnl  = s.get("pnl_pct", 0)
        val  = s.get("current_value", 0)
        cost = s.get("total_cost", wacc * s.get("shares", 0))
        total_val  += val
        total_cost += cost
        port_lines.append(
            f"  {s.get('symbol',''):8s} {s.get('sector',''):18s} "
            f"shares:{s.get('shares',0):,}  wacc:NPR{wacc:,.1f}  ltp:NPR{ltp:,.1f}  "
            f"pnl:{pnl:+.2f}%  val:NPR{val:,.0f}"
        )
    total_pnl_pct = ((total_val - total_cost) / total_cost * 100) if total_cost > 0 else 0

    prompt = f"""MARKET: {macro_regime}
PORTFOLIO TOTAL: NPR {total_val:,.0f} current / NPR {total_cost:,.0f} invested / {total_pnl_pct:+.2f}% P&L

YOUR POSITIONS:
{chr(10).join(port_lines)}

SECTOR CONTEXT:
{sector_call[:250]}

VALIDATED STOCK PICKS TODAY:
{stock_picks[:300]}

{"NEWS: " + news[:200] if news else ""}

TASK: Give personalized, specific advice:

**TOP 3 ACTIONS (most urgent first):**
For each: Stock | SELL/BUY/HOLD | Exact quantity | Price range | Why NOW (not generic)

**ALICL SPECIFIC CALL** (your biggest holding, NPR 3.9M, -11.25%):
- Short-term (next 5 days): Price range? Action?
- Recovery target: When realistically does it hit NPR 549? What catalyst?
- Should you average down? At what price?

**OPPORTUNITY FROM YOUR PORTFOLIO** (which holding to add more on dip):
- Stock | Entry price | Why this one | Max qty to add

**BIGGEST RISK** to your portfolio this week (1 specific Nepal risk).

Max 350 words. Be direct, use exact NPR prices, specific share quantities."""

    return _query(prompt, temperature=0.12, max_tokens=700)


# ─────────────────────────────────────────────────────────────────────────────
# Master: Run Full Multi-Agent Pipeline
# ─────────────────────────────────────────────────────────────────────────────
def run_multi_agent_analysis(
    portfolio: list,
    sector_data: dict,
    top_picks: list,
    avoid_stocks: list,
    macro_regime: str,
    history_days: int = 0,
    news: str = "",
) -> dict:
    """Run the full 4-agent pipeline. Returns structured dict with all outputs."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    print("    [Agent 1/4] Sector analyst...")
    sector_call   = agent_sector_analyst(sector_data, today)
    print(f"              → {len(sector_call)} chars")

    print("    [Agent 2/4] Stock validator...")
    stock_picks   = agent_stock_validator(top_picks, sector_call, history_days)
    print(f"              → {len(stock_picks)} chars")

    print("    [Agent 3/4] Risk assessor...")
    risk_report   = agent_risk_assessor(avoid_stocks, sector_call, macro_regime)
    print(f"              → {len(risk_report)} chars")

    print("    [Agent 4/4] Portfolio advisor...")
    port_advice   = agent_portfolio_advisor(portfolio, macro_regime, sector_call, stock_picks, news)
    print(f"              → {len(port_advice)} chars")

    return {
        "sector_analysis": sector_call,
        "validated_picks": stock_picks,
        "risk_report":     risk_report,
        "portfolio_advice": port_advice,
        "model_used": _get_model(),
        "date": today,
    }
