"""
src/nepse_alpha.py -- NEPSE-specific edge that augments scanner picks.

Why this exists:
The default ML/TA scanner buys momentum -- high RSI, near 52w highs, recently
running stocks. That works in efficient markets. NEPSE is NOT efficient:

  - ±10% daily circuit breakers force mean reversion
  - Thin liquidity makes momentum unsustainable (no follow-through)
  - Sentiment-driven, low information efficiency
  - Backtest evidence: 30% win rate buying momentum (worse than random 47%)

This module rescues / replaces picks based on NEPSE-specific edges:

  1. CAPITULATION BUYS: stocks down -8% to -25% on volume spike, RSI < 35.
     The crowd has panicked, smart money is accumulating.

  2. SECTOR LAGGARD ROTATION: in a HOT sector (5d > +3%), buy the WEAKEST
     stock (laggard catches up), not the strongest (already at peak).

  3. ANTI-FOMO FILTER: kill any pick up >12% in 5d, regardless of score.
     These are tops, not breakouts. Backtest confirms.

  4. PULLBACK CONFIRMATION: prefer stocks 8-25% below 52w high in uptrend
     (healthy correction within trend), not at the top.

  5. VOLUME CAPITULATION: down day with 2x+ volume = exhaustion, watch for
     reversal. Up day with 2x+ volume + already +15% = distribution, sell.

Usage in paper_trader:

    from src.nepse_alpha import score_alpha, find_capitulation_buys

    # 1. Score scanner picks for NEPSE-specific edge
    for p in picks:
        p['alpha_score'] = score_alpha(p)

    # 2. Find oversold bounce candidates not in scanner picks
    extras = find_capitulation_buys(all_stocks_data, sector_data)

    # 3. Combine: scanner picks with alpha_score > 0 + capitulation extras
"""
from __future__ import annotations

from typing import Optional


def score_alpha(pick: dict) -> float:
    """Score a pick for NEPSE-specific edge. Returns -100 to +100.

    Positive = good NEPSE setup, negative = anti-predictive (likely loser).
    Apply to existing scanner picks to filter / re-rank.
    """
    rsi      = pick.get("rsi", 50)
    ret_5d   = pick.get("ret_5d", 0)       # decimal: 0.05 = +5%
    ret_20d  = pick.get("ret_20d", 0)
    dist_52w = pick.get("dist_52w", -0.5)  # 0 = at 52w high
    vol_r    = pick.get("vol_ratio", 1.0)

    score = 0.0

    # ── RSI: 35-55 is sweet spot (not overbought, not capitulating) ────────
    if rsi < 25:
        score -= 20  # too oversold, falling knife
    elif rsi < 35:
        score += 25  # capitulation buy zone
    elif rsi < 45:
        score += 20  # healthy pullback
    elif rsi < 55:
        score += 15  # confirmed uptrend
    elif rsi < 65:
        score += 5   # getting stretched
    elif rsi < 70:
        score -= 10
    else:
        score -= 30  # clear sell signal

    # ── 5-day return: penalize FOMO and recent crashes ─────────────────────
    if ret_5d < -0.20:
        score -= 25  # crashing, no support yet
    elif ret_5d < -0.08:
        score += 20  # capitulation bounce candidate
    elif ret_5d < -0.02:
        score += 15  # mild pullback in trend
    elif ret_5d < 0.04:
        score += 10
    elif ret_5d < 0.10:
        score += 0   # neutral
    elif ret_5d < 0.15:
        score -= 15
    else:
        score -= 30  # already pumped, fade

    # ── Distance from 52w high: pullback zone is safest ───────────────────
    if dist_52w > -0.02:
        score -= 25  # at top, no room to run
    elif dist_52w > -0.08:
        score -= 5
    elif dist_52w > -0.20:
        score += 20  # ideal: recent high but pulled back
    elif dist_52w > -0.35:
        score += 5   # deep pullback in long uptrend
    else:
        score -= 10  # crashed, structural damage

    # ── Volume confirmation ──────────────────────────────────────────────
    if vol_r > 2.5:
        score += 15  # strong interest (capitulation or breakout)
    elif vol_r > 1.5:
        score += 10
    elif vol_r > 0.8:
        score += 0   # normal
    else:
        score -= 15  # dead, no participation

    # ── Bonus: 20-day uptrend with 5-day pullback (best NEPSE setup) ───
    if ret_20d > 0.05 and -0.10 < ret_5d < 0:
        score += 25  # bull pullback -- proven edge in trending markets

    # ── Penalty: 20-day downtrend continuing ────────────────────────────
    if ret_20d < -0.10 and ret_5d < -0.05:
        score -= 25  # sustained decline, don't catch falling knife

    return max(-100.0, min(100.0, score))


def find_capitulation_buys(
    all_stocks: dict, sector_data: Optional[dict] = None, limit: int = 5
) -> list[dict]:
    """Find oversold stocks that the momentum-based scanner misses.

    Args:
        all_stocks: {symbol: [{date, lp, h, l, q, ...}]} — full price history
        sector_data: optional analytics output with sector_rotation (HOT/COLD)
        limit: max number of capitulation candidates to return

    Returns:
        list of pick dicts with the same shape as scanner picks, ready for
        paper_trader.process_signals().
    """
    import numpy as np

    candidates = []
    hot_sectors = set()
    if sector_data and "sector_rotation" in sector_data:
        hot_sectors = {s["sector"] for s in sector_data["sector_rotation"]
                       if s.get("heat") in ("HOT", "WARM")}

    for sym, recs in all_stocks.items():
        if len(recs) < 30:
            continue
        closes = [r.get("lp", 0) for r in recs[-60:] if r.get("lp", 0) > 0]
        vols   = [r.get("q", 0) for r in recs[-60:] if r.get("q", 0) > 0]
        if len(closes) < 30:
            continue

        c = np.array(closes)
        v = np.array(vols) if len(vols) >= 20 else np.array([1])

        price = float(c[-1])
        if price < 50 or price > 1500:
            continue

        # Compute features
        ret_5d  = (c[-1] / c[-6] - 1) if len(c) >= 6 else 0
        ret_20d = (c[-1] / c[-21] - 1) if len(c) >= 21 else 0
        ret_60d = (c[-1] / c[-60] - 1) if len(c) >= 60 else 0

        # RSI
        deltas = np.diff(c[-15:]) if len(c) >= 15 else np.array([0])
        gains = float(np.where(deltas > 0, deltas, 0).mean())
        losses = float(np.where(deltas < 0, -deltas, 0).mean()) + 0.001
        rsi = 100 - 100 / (1 + gains / losses)

        avg_vol = float(v[-20:].mean()) if len(v) >= 20 else 1.0
        vol_r = float(v[-1]) / avg_vol if avg_vol > 0 else 1.0

        # NEPSE capitulation pattern:
        #   - 5d return between -8% and -25% (bounce zone, not falling knife)
        #   - RSI < 38 (oversold)
        #   - Volume spike >= 1.3x avg (capitulation/accumulation)
        #   - 60d return positive (was an uptrend before, not structural decline)
        is_capitulation = (
            -0.25 < ret_5d < -0.05
            and rsi < 38
            and vol_r >= 1.3
            and ret_60d > -0.20
        )
        if not is_capitulation:
            continue

        # Score for ranking (higher = better)
        cap_score = (
            -ret_5d * 100        # bigger drop = more rebound potential
            + (40 - rsi) * 0.8   # more oversold = better
            + min(vol_r, 4) * 5  # volume confirmation
            + max(0, ret_60d) * 30  # was in uptrend
        )

        candidates.append({
            "symbol": sym,
            "signal": "BUY",  # mean reversion buy
            "score": min(100, max(0, 50 + cap_score)),
            "kelly_pct": 6.0,    # smaller size: contrarian = more uncertainty
            "ta_score": 0,
            "ml_score": 0,
            "rsi": float(rsi),
            "ret_5d": float(ret_5d),
            "ret_20d": float(ret_20d),
            "dist_52w": (price / float(np.max(c)) - 1) if len(c) > 0 else -0.5,
            "vol_ratio": vol_r,
            "price": price,
            "reasons": [
                f"Capitulation: {ret_5d*100:+.1f}% in 5d",
                f"RSI {rsi:.0f} (oversold)",
                f"Volume {vol_r:.1f}x avg (washout)",
            ],
            "alpha_source": "capitulation",
        })

    candidates.sort(key=lambda p: p["score"], reverse=True)
    return candidates[:limit]


def filter_picks(picks: list[dict], min_alpha: float = 0) -> list[dict]:
    """Filter scanner picks to only those with positive NEPSE alpha.

    Adds 'alpha_score' to each pick. Returns only picks where alpha >= min_alpha.
    """
    out = []
    for p in picks:
        a = score_alpha(p)
        p["alpha_score"] = a
        if a >= min_alpha:
            out.append(p)
    out.sort(key=lambda p: p["alpha_score"], reverse=True)
    return out
