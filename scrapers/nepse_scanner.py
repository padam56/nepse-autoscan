"""
NEPSE Full Market Scanner.
Scans all 342 stocks, scores opportunities with technical reasoning,
generates specific TP/SL targets for each buy candidate.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import requests
from typing import List, Dict
from portfolio.config import PORTFOLIO

MEROLAGANI_URL = "https://merolagani.com/handlers/webrequesthandler.ashx?type=market_summary"
HEADERS = {"User-Agent": "Mozilla/5.0"}

MIN_PRICE = 50
MIN_VOLUME = 1000  # shares


def fetch_all_stocks() -> List[dict]:
    """Fetch all 342 NEPSE stocks with live data."""
    try:
        r = requests.get(MEROLAGANI_URL, headers=HEADERS, timeout=15)
        data = r.json()

        if isinstance(data, list):
            raw = data
        elif isinstance(data.get("stock"), dict):
            raw = data["stock"].get("detail", [])
        elif "d" in data:
            raw = data["d"]
        else:
            raw = []

        stocks = []
        for s in raw:
            try:
                sym = str(s.get("s", "")).upper()
                ltp = float(s.get("lp", 0) or 0)
                change = float(s.get("c", 0) or 0)
                prev = ltp - change
                pct = (change / prev * 100) if prev != 0 else 0
                vol = float(s.get("q", 0) or 0)
                # Estimate turnover if not available
                turnover = float(s.get("t", ltp * vol) or ltp * vol)
                high = float(s.get("h", ltp) or ltp)
                low = float(s.get("l", ltp) or ltp)

                if sym and ltp >= MIN_PRICE and vol >= MIN_VOLUME:
                    stocks.append({
                        "symbol": sym,
                        "ltp": ltp,
                        "price": ltp,  # alias for email compatibility
                        "pct_change": round(pct, 3),
                        "change_pct": round(pct, 3),  # alias
                        "change": change,
                        "volume": vol,
                        "turnover": turnover,
                        "high": high,
                        "low": low,
                        "in_portfolio": sym in PORTFOLIO,
                    })
            except (ValueError, TypeError):
                pass
        return stocks
    except Exception as e:
        print(f"[!] Market scan error: {e}")
        return []


def score_opportunity(stock: dict) -> tuple:
    """
    Score a stock's opportunity level and generate a reason.
    Returns (score, reason_string).
    """
    score = 0.0
    reasons = []

    pct = stock["pct_change"]
    vol = stock["volume"]
    turnover = stock["turnover"]
    ltp = stock["ltp"]
    high = stock["high"]
    low = stock["low"]

    # ── Momentum scoring ──────────────────────────────────────────────
    if 0.5 < pct < 3:
        score += 30
        reasons.append(f"gaining {pct:+.2f}% (healthy momentum)")
    elif 3 <= pct < 5:
        score += 18
        reasons.append(f"strong rally {pct:+.2f}% (watch for reversal)")
    elif pct >= 5:
        score += 8
        reasons.append(f"surging {pct:+.2f}% (overbought risk)")
    elif -1 < pct <= -0.5:
        score += 5
        reasons.append(f"minor dip {pct:+.2f}% (potential entry)")
    elif pct <= -1:
        score -= 15
        reasons.append(f"falling {pct:+.2f}% (wait for floor)")

    # ── Volume confirmation ───────────────────────────────────────────
    if turnover > 10_000_000:  # 1Cr+
        score += 25
        reasons.append(f"high liquidity NPR {turnover/1e6:.1f}M")
    elif turnover > 2_000_000:  # 20L+
        score += 15
        reasons.append(f"good volume NPR {turnover/1e6:.1f}M")
    elif turnover > 500_000:
        score += 8
    else:
        score -= 10
        reasons.append("low liquidity")

    # ── Price range (intraday volatility) ────────────────────────────
    spread = ((high - low) / low * 100) if low > 0 else 0
    if 1 < spread < 4:
        score += 10
        reasons.append(f"healthy {spread:.1f}% range")
    elif spread >= 4:
        score += 5  # volatile but could be opportunity

    # ── Price zone (avoid penny stocks and ultra-premium) ────────────
    if 100 <= ltp <= 800:
        score += 5
        reasons.append("accessible price zone")
    elif 800 < ltp <= 2000:
        score += 2

    # ── Portfolio overlap penalty ─────────────────────────────────────
    if stock["in_portfolio"]:
        score -= 20  # Already own it, don't double-recommend

    reason_str = " | ".join(reasons) if reasons else "Momentum + liquidity signal"
    return round(score, 1), reason_str


def generate_targets(stock: dict) -> dict:
    """Generate TP1, TP2, and SL targets based on current price and volatility."""
    ltp = stock["ltp"]
    pct = stock["pct_change"]
    spread = ((stock["high"] - stock["low"]) / stock["low"] * 100) if stock["low"] > 0 else 2

    # Dynamic target calculation based on volatility
    volatility_factor = max(1.5, min(4, spread))

    tp1 = round(ltp * (1 + volatility_factor / 100), 2)
    tp2 = round(ltp * (1 + volatility_factor * 2 / 100), 2)
    sl = round(ltp * (1 - volatility_factor * 0.75 / 100), 2)

    return {
        "entry": ltp,
        "tp1": tp1,
        "tp2": tp2,
        "sl": sl,
        "risk_reward": round((tp1 - ltp) / (ltp - sl), 2) if ltp > sl else 0,
    }


def get_sector_for_symbol(symbol: str) -> str:
    """Guess sector from symbol patterns (crude but useful)."""
    hydro = ["AHPC", "AKPL", "API", "BARUN", "BPCL", "GHL", "HDHPC", "NHPC", "NWCFL",
             "RHPL", "RRHP", "SHL", "SSHL", "TPC", "UMHL", "UPPER", "USHEC"]
    banking = ["ADBL", "CBL", "EBL", "GBIME", "HBL", "KBL", "MBL", "NABIL", "NBL",
               "NCCB", "NIB", "NMB", "PCBL", "PRVU", "NICA", "SANIMA", "SCB", "SBI", "SBL"]
    insurance = ["ALICL", "CLI", "ILI", "JLIC", "LICN", "MLICL", "NLIC", "NLICL",
                 "PLICL", "RNLI", "SALICO", "SLICL"]
    finance = ["BFC", "BL", "CFCL", "GMFIL", "GUFL", "ICFC", "JFL", "MFIL", "MPFL",
               "NHDL", "NIDC", "PROFL", "RLFL", "SFL", "SFMF"]

    if symbol in hydro: return "Hydropower"
    if symbol in banking: return "Banking"
    if symbol in insurance: return "Insurance"
    if symbol in finance: return "Finance"
    return "Other"


def scan_market() -> dict:
    """
    Full NEPSE market scan.
    Returns top buy opportunities with scores, reasons, and price targets.
    """
    all_stocks = fetch_all_stocks()
    if not all_stocks:
        return {"top_buys": [], "top_sells": [], "market_mood": "UNKNOWN", "total_scanned": 0}

    portfolio_symbols = set(PORTFOLIO.keys())

    buy_candidates = []
    sell_pressure = []

    for stock in all_stocks:
        sym = stock["symbol"]
        pct = stock["pct_change"]

        score, reason = score_opportunity(stock)
        targets = generate_targets(stock)
        sector = get_sector_for_symbol(sym)

        stock_data = {
            **stock,
            "sector": sector,
            "opportunity_score": score,
            "reason": reason,
            "targets": targets,
        }

        if not stock["in_portfolio"] and score >= 40:
            buy_candidates.append(stock_data)
        elif stock["in_portfolio"] and pct < -2:
            sell_pressure.append(stock_data)

    # Sort and take top results
    buy_candidates.sort(key=lambda x: x["opportunity_score"], reverse=True)
    sell_pressure.sort(key=lambda x: x["pct_change"])

    # Determine market mood from breadth
    gainers = sum(1 for s in all_stocks if s["pct_change"] > 0.5)
    losers = sum(1 for s in all_stocks if s["pct_change"] < -0.5)
    total = len(all_stocks)
    ratio = gainers / total if total > 0 else 0.5

    if ratio >= 0.65:
        mood = "STRONGLY BULLISH"
    elif ratio >= 0.55:
        mood = "BULLISH"
    elif ratio >= 0.45:
        mood = "MIXED"
    elif ratio >= 0.35:
        mood = "BEARISH"
    else:
        mood = "STRONGLY BEARISH"

    return {
        "top_buys": buy_candidates[:10],
        "top_sells": sell_pressure[:5],
        "market_mood": mood,
        "total_scanned": total,
        "gainers": gainers,
        "losers": losers,
        "ratio": round(ratio, 3),
    }


if __name__ == "__main__":
    print("Scanning NEPSE market...")
    result = scan_market()
    print(f"Mood: {result['market_mood']} | {result['gainers']} gainers / {result['losers']} losers")
    print(f"\nTop Buy Opportunities:")
    for b in result["top_buys"][:5]:
        t = b["targets"]
        print(f"  {b['symbol']:8s} NPR {b['ltp']:,.1f}  {b['pct_change']:+.2f}%  "
              f"Score:{b['opportunity_score']:.0f}  TP1:{t['tp1']}  SL:{t['sl']}  | {b['reason'][:60]}")
