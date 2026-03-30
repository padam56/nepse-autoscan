"""
Advanced Pre-Breakout Stock Screener.
Uses full OHLCV data from MeroLagani turnover detail.
Finds stocks in ACCUMULATION (before breakout), NOT stocks at peak.

Key insight: We want stocks that are:
  - Gaining quietly (0.5–3%), not explosively (already peaked)
  - Closing near day HIGH (buyers in control at end of session)
  - High volume/turnover relative to price (institutional interest)
  - NOT near upper circuit (avoid pump-and-dump targets)
  - In a HOT or WARMING sector (macro tailwind)

Screens out:
  - Stocks already up 5%+ today (too late to enter)
  - Stocks near upper circuit (10% limit in NEPSE)
  - Stocks closing near day LOW (sellers in control)
  - Low liquidity stocks (turnover < NPR 5L)
"""
import sys, os, json
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import requests
from typing import List, Dict, Optional
from portfolio.config import PORTFOLIO
from scrapers.sector_analyzer import SECTOR_MAP, analyze_sectors

MEROLAGANI_URL = "https://merolagani.com/handlers/webrequesthandler.ashx?type=market_summary"
HEADERS = {"User-Agent": "Mozilla/5.0"}
PRICE_HISTORY_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'price_history')


def fetch_market_ohlcv() -> tuple:
    """Fetch full market data including OHLCV from turnover detail."""
    try:
        r = requests.get(MEROLAGANI_URL, headers=HEADERS, timeout=15)
        data = r.json()
        return data, data.get("turnover", {}).get("detail", [])
    except Exception as e:
        print(f"[!] OHLCV fetch error: {e}")
        return {}, []


def save_daily_snapshot(stocks: list, date_str: str = None):
    """Save daily OHLCV snapshot for future ML training."""
    if date_str is None:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
    os.makedirs(PRICE_HISTORY_DIR, exist_ok=True)
    path = os.path.join(PRICE_HISTORY_DIR, f"{date_str}.json")
    snapshot = {
        "date": date_str,
        "stocks": {
            str(s.get("s", "")).upper(): {
                "lp": s.get("lp", 0), "pc": s.get("pc", 0),
                "h": s.get("h", 0), "l": s.get("l", 0),
                "op": s.get("op", 0), "t": s.get("t", 0), "q": s.get("q", 0),
            }
            for s in stocks if s.get("s")
        }
    }
    with open(path, "w") as f:
        json.dump(snapshot, f)
    return path


def load_historical_snapshots(days: int = 10) -> Dict[str, list]:
    """Load past N days of price snapshots for momentum calculation."""
    os.makedirs(PRICE_HISTORY_DIR, exist_ok=True)
    history = {}
    files = sorted([f for f in os.listdir(PRICE_HISTORY_DIR) if f.endswith(".json")], reverse=True)
    for fname in files[:days]:
        date = fname.replace(".json", "")
        try:
            with open(os.path.join(PRICE_HISTORY_DIR, fname)) as f:
                data = json.load(f)
                for sym, vals in data.get("stocks", {}).items():
                    if sym not in history:
                        history[sym] = []
                    history[sym].append({"date": date, **vals})
        except Exception:
            pass
    return history


def compute_momentum_score(stock: dict, history: Dict[str, list] = None) -> dict:
    """
    Compute comprehensive momentum score for a single stock.
    Uses OHLCV for today + historical data if available.
    """
    sym  = str(stock.get("s", "")).upper()
    lp   = float(stock.get("lp", 0) or 0)
    pc   = float(stock.get("pc", 0) or 0)
    h    = float(stock.get("h", lp) or lp)
    l    = float(stock.get("l", lp) or lp)
    op   = float(stock.get("op", lp) or lp)
    t    = float(stock.get("t", 0) or 0)
    q    = float(stock.get("q", 0) or 0)

    # ── Today's signals ───────────────────────────────────────────────
    range_size = h - l
    price_pos  = (lp - l) / range_size if range_size > 0 else 0.5  # 0=low, 1=high
    intraday   = (lp - op) / op * 100 if op > 0 else 0             # vs open
    spread_pct = range_size / l * 100 if l > 0 else 0

    # ── Historical momentum (multi-day trend) ─────────────────────────
    hist_data  = (history or {}).get(sym, [])
    multi_day_return = 0.0
    avg_turnover     = t  # fallback to today
    price_trend      = "UNKNOWN"

    if len(hist_data) >= 3:
        # 5-day return
        oldest = hist_data[-1].get("lp", lp)
        multi_day_return = (lp - oldest) / oldest * 100 if oldest > 0 else 0
        # Average turnover
        avg_turnover = sum(d.get("t", 0) for d in hist_data) / len(hist_data)
        # Price trend direction
        closes = [d.get("lp", 0) for d in hist_data[-5:] if d.get("lp", 0) > 0]
        if len(closes) >= 2:
            up_days = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
            price_trend = "UPTREND" if up_days >= 3 else ("DOWNTREND" if up_days <= 1 else "SIDEWAYS")
    elif len(hist_data) >= 1:
        prev = hist_data[0].get("lp", lp)
        multi_day_return = (lp - prev) / prev * 100 if prev > 0 else 0
        price_trend = "SIDEWAYS"

    # ── Volume relative to history ─────────────────────────────────────
    volume_ratio = min(t / avg_turnover if avg_turnover > 0 else 1.0, 100.0)
    volume_label = "SURGE" if volume_ratio > 2 else ("HIGH" if volume_ratio > 1.3 else ("NORMAL" if volume_ratio > 0.7 else "LOW"))

    # ── Screening filters ─────────────────────────────────────────────
    # Hard disqualifiers:
    if lp < 50:              return None  # penny stock
    if t < 200_000:          return None  # too illiquid (< NPR 2L)
    if pc >= 9.5:            return None  # upper circuit — already pumped
    if pc <= -8:             return None  # lower circuit — avoid freefall
    if price_pos < 0.2 and pc < 0:  return None  # closing near low + falling

    # ── Composite score ───────────────────────────────────────────────
    score = 0.0
    reasons = []

    # Momentum quality (sweet spot: 0.5–3.5%)
    if 0.5 <= pc <= 3.5:
        pts = 35 - max(0, (pc - 2) * 8)  # peaks at 2%, tapers off
        score += pts
        reasons.append(f"healthy +{pc:.2f}% momentum")
    elif 3.5 < pc < 9:
        score += 12
        reasons.append(f"strong {pc:.2f}% (watch reversal risk)")
    elif -0.5 <= pc < 0.5:
        score += 5
        reasons.append(f"flat {pc:+.2f}% (consolidating)")
    elif pc < -0.5:
        score -= 20
        reasons.append(f"falling {pc:.2f}%")

    # Price position (closing near high = buyers in control)
    if price_pos >= 0.75:
        score += 25
        reasons.append(f"closing near day high ({price_pos:.0%} range)")
    elif price_pos >= 0.5:
        score += 10
        reasons.append(f"above midrange ({price_pos:.0%})")
    else:
        score -= 10

    # Intraday strength (vs open)
    if intraday > 1:
        score += 15
        reasons.append(f"+{intraday:.2f}% vs open (intraday strength)")
    elif intraday > 0:
        score += 5
    elif intraday < -1:
        score -= 15

    # Volume/turnover
    if t > 5_000_000:  # 50L+
        score += 25
        reasons.append(f"NPR {t/1e6:.1f}M volume (institutional interest)")
    elif t > 1_000_000:  # 10L+
        score += 15
        reasons.append(f"NPR {t/1e6:.1f}M turnover (good liquidity)")
    elif t > 300_000:
        score += 5
    else:
        score -= 15

    # Volume surge vs history
    if volume_ratio > 2 and pc > 0:
        score += 20
        reasons.append(f"volume SURGE {volume_ratio:.1f}x normal (breakout signal!)")
    elif volume_ratio > 1.3 and pc > 0:
        score += 10
        reasons.append(f"volume {volume_ratio:.1f}x above average")

    # Historical trend
    if price_trend == "UPTREND" and multi_day_return > 0:
        score += 15
        reasons.append(f"{multi_day_return:+.1f}% multi-day trend (uptrend)")
    elif price_trend == "SIDEWAYS" and pc > 0:
        score += 8
        reasons.append("breaking out of sideways consolidation")
    elif price_trend == "DOWNTREND":
        score -= 10

    # Portfolio overlap (don't recommend what user already owns)
    in_portfolio = sym in PORTFOLIO
    if in_portfolio:
        score -= 30

    sector = SECTOR_MAP.get(sym, "Other")

    return {
        "symbol": sym,
        "sector": sector,
        "ltp": lp, "price": lp,
        "pc": pc, "pct_change": pc, "change_pct": pc,
        "high": h, "low": l, "open": op,
        "price_position": round(price_pos, 3),
        "intraday_strength": round(intraday, 3),
        "turnover": t,
        "volume": q,
        "volume_ratio": round(volume_ratio, 2),
        "volume_label": volume_label,
        "multi_day_return": round(multi_day_return, 2),
        "price_trend": price_trend,
        "composite_score": round(score, 1),
        "opportunity_score": round(score, 1),
        "reasons": reasons,
        "reason": " | ".join(reasons[:3]),
        "in_portfolio": in_portfolio,
        "targets": _compute_targets(lp, h, l, pc, price_pos),
    }


def _compute_targets(lp, h, l, pc, price_pos) -> dict:
    """Compute realistic TP1, TP2, SL based on day's range and momentum."""
    range_pct = ((h - l) / l * 100) if l > 0 else 2.0
    volatility = max(1.5, min(5, range_pct))

    # TP1: 1 range above current (realistic next session target)
    tp1 = round(lp * (1 + volatility / 100), 2)
    # TP2: 2 ranges above (2-3 day target)
    tp2 = round(lp * (1 + volatility * 2 / 100), 2)
    # SL: 0.75 range below (tight stop)
    sl  = round(lp * (1 - volatility * 0.75 / 100), 2)
    rr  = round((tp1 - lp) / (lp - sl), 2) if lp > sl else 0

    return {"entry": lp, "tp1": tp1, "tp2": tp2, "sl": sl, "risk_reward": rr}


def find_avoid_stocks(all_scores: list) -> list:
    """Find stocks that are likely at peak / being distributed."""
    avoid = []
    for s in all_scores:
        if s is None:
            continue
        pc = s.get("pc", 0)
        pos = s.get("price_position", 0.5)
        t = s.get("turnover", 0)
        trend = s.get("price_trend", "UNKNOWN")

        is_avoid = False
        reason = ""
        if pc >= 7 and pos < 0.4:
            is_avoid = True; reason = f"Surged {pc:.1f}% but closing near LOW — distribution (sell the news)"
        elif pc >= 5 and trend == "DOWNTREND":
            is_avoid = True; reason = f"+{pc:.1f}% spike in existing downtrend — likely dead cat bounce"
        elif s.get("volume_ratio", 1) > 3 and pc <= 0:
            is_avoid = True; reason = f"High volume {s['volume_ratio']:.1f}x but falling — institutional exit"
        elif s.get("in_portfolio") and pc >= 5:
            is_avoid = False  # User owns it, handled elsewhere

        if is_avoid:
            avoid.append({**s, "avoid_reason": reason})

    return sorted(avoid, key=lambda x: x["pc"], reverse=True)[:5]


def run_advanced_screen(market_data: dict = None) -> dict:
    """Main entry point for the advanced screener."""
    if market_data is None:
        market_data, turnover_stocks = fetch_market_ohlcv()
    else:
        turnover_stocks = market_data.get("turnover", {}).get("detail", [])
        if isinstance(market_data.get("turnover"), dict):
            turnover_stocks = market_data["turnover"].get("detail", [])

    if not turnover_stocks:
        return {"top_buys": [], "avoid": [], "sector_analysis": {}, "total": 0}

    # Save daily snapshot for future ML
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        save_daily_snapshot(turnover_stocks, today)
    except Exception as e:
        pass

    # Load history for multi-day momentum
    history = load_historical_snapshots(days=10)

    # Sector analysis
    sector_analysis = analyze_sectors(market_data)

    # Build sector momentum map for bonus scoring
    hot_sectors = set(sector_analysis.get("hottest", []))

    # Score all stocks
    all_scores = []
    for s in turnover_stocks:
        result = compute_momentum_score(s, history)
        if result is not None:
            # Bonus for being in a hot sector
            if result["sector"] in hot_sectors:
                result["composite_score"] += 10
                result["opportunity_score"] += 10
                result["reasons"].append(f"in HOT sector: {result['sector']}")
                result["reason"] = " | ".join(result["reasons"][:3])
            all_scores.append(result)

    # Top buys: not in portfolio, score >= 40
    top_buys = [s for s in all_scores if not s["in_portfolio"] and s["composite_score"] >= 40]
    top_buys.sort(key=lambda x: x["composite_score"], reverse=True)

    # Avoid list
    avoid_stocks = find_avoid_stocks(all_scores)

    # Market breadth
    gainers = sum(1 for s in all_scores if s["pc"] > 0.5)
    losers  = sum(1 for s in all_scores if s["pc"] < -0.5)
    total   = len(all_scores)
    ratio   = gainers / total if total > 0 else 0.5

    return {
        "top_buys": top_buys[:12],
        "avoid": avoid_stocks,
        "sector_analysis": sector_analysis,
        "all_scores": all_scores,
        "total": total,
        "gainers": gainers,
        "losers": losers,
        "market_breadth": round(ratio, 3),
        "history_days": len(set(d for vals in history.values() for d in [v["date"] for v in vals])),
    }


if __name__ == "__main__":
    print("Running advanced screen...")
    result = run_advanced_screen()
    print(f"\n[INFO] Market: {result['gainers']} gainers / {result['losers']} losers ({result['total']} total)")
    print(f"[INFO] Historical data: {result['history_days']} days stored")
    print(f"\n[INFO] Sector Heat Map:")
    for sec in result["sector_analysis"].get("sectors", [])[:6]:
        print(f"  {sec['heat']:15s} {sec['sector']:20s} {sec['avg_change_pct']:+.2f}%  -> {sec['action']}")
    print(f"\n[INFO] Top Pre-Breakout Picks ({len(result['top_buys'])} found):")
    for b in result["top_buys"][:5]:
        t = b["targets"]
        print(f"  {b['symbol']:8s} NPR {b['ltp']:,.1f}  {b['pc']:+.2f}%  "
              f"Pos:{b['price_position']:.0%}  Score:{b['composite_score']:.0f}  "
              f"TP1:{t['tp1']}  SL:{t['sl']}  | {b['reason'][:55]}")
    if result["avoid"]:
        print(f"\n[WARN] AVOID (at peak/distributing):")
        for a in result["avoid"]:
            print(f"  {a['symbol']:8s} {a['pc']:+.2f}%  | {a['avoid_reason'][:60]}")
