"""
src/analytics.py -- Market analytics engine for NEPSE.

Computes sector rotation, breadth, momentum, volatility, and correlation
metrics from daily price history. Results are saved as JSON and consumed
by the dashboard generator.
"""
import json
import math
from pathlib import Path
from collections import defaultdict

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = ROOT / "data" / "price_history"
SECTORS_FILE = ROOT / "data" / "sectors.json"
OUTPUT_FILE = ROOT / "data" / "analytics.json"


def _load_sectors():
    """Load symbol-to-sector mapping."""
    try:
        data = json.loads(SECTORS_FILE.read_text())
        return data.get("symbol_to_sector", {})
    except Exception:
        return {}


def _load_history(days=60):
    """Load last N days of price history."""
    files = sorted(HISTORY_DIR.glob("*.json"))[-days:]
    snapshots = []
    for f in files:
        try:
            d = json.loads(f.read_text())
            snapshots.append({"date": f.stem, "stocks": d.get("stocks", {})})
        except Exception:
            continue
    return snapshots


def _get_closes(snapshots, symbol):
    """Extract close prices for a symbol across snapshots."""
    closes = []
    dates = []
    for snap in snapshots:
        s = snap["stocks"].get(symbol, {})
        lp = s.get("lp", 0)
        if lp > 0:
            closes.append(lp)
            dates.append(snap["date"])
    return np.array(closes), dates


def compute_sector_rotation(snapshots, s2s):
    """Compute sector performance over multiple timeframes."""
    sector_labels = {
        "COMMERCIAL_BANK": "Banking", "DEVELOPMENT_BANK": "Dev Banks",
        "FINANCE": "Finance", "LIFE_INSURANCE": "Life Ins",
        "NONLIFE_INSURANCE": "Non-Life Ins", "HYDROPOWER": "Hydro",
        "MANUFACTURING": "Manu", "HOTEL_TOURISM": "Hotel",
        "MICROFINANCE": "Micro", "MUTUAL_FUND": "Mutual Fund",
    }
    sectors = defaultdict(lambda: {"1d": [], "5d": [], "20d": []})

    for sym, sector_key in s2s.items():
        closes, _ = _get_closes(snapshots, sym)
        if len(closes) < 2:
            continue
        label = sector_labels.get(sector_key, sector_key)
        # 1-day return
        sectors[label]["1d"].append((closes[-1] / closes[-2] - 1) * 100)
        # 5-day return
        if len(closes) >= 6:
            sectors[label]["5d"].append((closes[-1] / closes[-6] - 1) * 100)
        # 20-day return
        if len(closes) >= 21:
            sectors[label]["20d"].append((closes[-1] / closes[-21] - 1) * 100)

    result = []
    for label in sorted(sectors.keys()):
        d = sectors[label]
        avg_1d = np.mean(d["1d"]) if d["1d"] else 0
        avg_5d = np.mean(d["5d"]) if d["5d"] else 0
        avg_20d = np.mean(d["20d"]) if d["20d"] else 0
        n_stocks = len(d["1d"])
        n_positive = sum(1 for x in d["1d"] if x > 0)

        # Heat classification
        if avg_5d > 3:
            heat = "HOT"
        elif avg_5d > 1:
            heat = "WARM"
        elif avg_5d > -1:
            heat = "NEUTRAL"
        elif avg_5d > -3:
            heat = "COOL"
        else:
            heat = "COLD"

        result.append({
            "sector": label,
            "avg_1d": round(avg_1d, 2),
            "avg_5d": round(avg_5d, 2),
            "avg_20d": round(avg_20d, 2),
            "n_stocks": n_stocks,
            "n_positive": n_positive,
            "heat": heat,
        })

    result.sort(key=lambda x: x["avg_5d"], reverse=True)
    return result


def compute_breadth(snapshots):
    """Compute market breadth metrics over time."""
    if len(snapshots) < 2:
        return {}

    latest = snapshots[-1]["stocks"]
    all_rsi = []
    above_sma20 = 0
    new_highs = 0
    new_lows = 0
    total = 0

    for sym in latest:
        closes, _ = _get_closes(snapshots, sym)
        if len(closes) < 20:
            continue
        total += 1

        # SMA20
        sma20 = np.mean(closes[-20:])
        if closes[-1] > sma20:
            above_sma20 += 1

        # RSI
        if len(closes) >= 15:
            deltas = np.diff(closes[-15:])
            gains = np.where(deltas > 0, deltas, 0).mean()
            losses = np.where(deltas < 0, -deltas, 0).mean() + 0.001
            rsi = 100 - (100 / (1 + gains / losses))
            all_rsi.append(rsi)

        # 20-day high/low
        high_20 = np.max(closes[-20:])
        low_20 = np.min(closes[-20:])
        if closes[-1] >= high_20 * 0.99:
            new_highs += 1
        if closes[-1] <= low_20 * 1.01:
            new_lows += 1

    rsi_arr = np.array(all_rsi) if all_rsi else np.array([50])
    pct_above_sma20 = round(above_sma20 / total * 100, 1) if total > 0 else 0

    # RSI distribution buckets
    rsi_dist = {
        "oversold_0_30": int(np.sum(rsi_arr < 30)),
        "neutral_30_50": int(np.sum((rsi_arr >= 30) & (rsi_arr < 50))),
        "neutral_50_70": int(np.sum((rsi_arr >= 50) & (rsi_arr < 70))),
        "overbought_70_100": int(np.sum(rsi_arr >= 70)),
    }

    # Breadth over time (last 30 days)
    breadth_history = []
    for i in range(max(1, len(snapshots) - 30), len(snapshots)):
        prev = snapshots[i - 1]["stocks"]
        curr = snapshots[i]["stocks"]
        g = l = u = 0
        for sym in curr:
            pc = curr[sym].get("pc", 0)
            if pc > 0:
                g += 1
            elif pc < 0:
                l += 1
            else:
                u += 1
        breadth_history.append({
            "date": snapshots[i]["date"],
            "gainers": g, "losers": l, "unchanged": u,
            "ad_ratio": round(g / max(l, 1), 2),
        })

    return {
        "total_stocks": total,
        "pct_above_sma20": pct_above_sma20,
        "new_20d_highs": new_highs,
        "new_20d_lows": new_lows,
        "rsi_mean": round(float(rsi_arr.mean()), 1),
        "rsi_median": round(float(np.median(rsi_arr)), 1),
        "rsi_distribution": rsi_dist,
        "breadth_history": breadth_history,
    }


def compute_momentum_rankings(snapshots, s2s, top_n=20):
    """Rank stocks by risk-adjusted momentum."""
    sector_labels = {
        "COMMERCIAL_BANK": "Banking", "DEVELOPMENT_BANK": "Dev Banks",
        "FINANCE": "Finance", "LIFE_INSURANCE": "Life Ins",
        "NONLIFE_INSURANCE": "Non-Life Ins", "HYDROPOWER": "Hydro",
        "MANUFACTURING": "Manu", "HOTEL_TOURISM": "Hotel",
        "MICROFINANCE": "Micro", "MUTUAL_FUND": "Mutual Fund",
    }
    rankings = []

    for sym in snapshots[-1]["stocks"]:
        closes, _ = _get_closes(snapshots, sym)
        if len(closes) < 20:
            continue
        ltp = closes[-1]
        if ltp < 50 or ltp > 1500:
            continue

        ret_5d = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
        ret_20d = (closes[-1] / closes[-21] - 1) * 100 if len(closes) >= 21 else 0

        # Volatility (20-day std of daily returns)
        if len(closes) >= 21:
            daily_rets = np.diff(closes[-21:]) / closes[-21:-1]
            vol = np.std(daily_rets) * 100
        else:
            vol = 5.0

        # Risk-adjusted momentum = return / volatility
        sharpe_5d = ret_5d / max(vol, 0.5)
        sharpe_20d = ret_20d / max(vol, 0.5)

        # RSI
        rsi = 50
        if len(closes) >= 15:
            deltas = np.diff(closes[-15:])
            gains = np.where(deltas > 0, deltas, 0).mean()
            losses = np.where(deltas < 0, -deltas, 0).mean() + 0.001
            rsi = 100 - (100 / (1 + gains / losses))

        # Volume trend
        vols = []
        for snap in snapshots[-20:]:
            s = snap["stocks"].get(sym, {})
            q = s.get("q", 0)
            if q > 0:
                vols.append(q)
        vol_ratio = vols[-1] / np.mean(vols) if len(vols) >= 2 and np.mean(vols) > 0 else 1

        sector = sector_labels.get(s2s.get(sym, ""), "Other")

        rankings.append({
            "symbol": sym,
            "sector": sector,
            "price": round(float(ltp), 1),
            "ret_5d": round(ret_5d, 2),
            "ret_20d": round(ret_20d, 2),
            "volatility": round(vol, 2),
            "sharpe_5d": round(sharpe_5d, 2),
            "sharpe_20d": round(sharpe_20d, 2),
            "rsi": round(rsi, 1),
            "vol_ratio": round(vol_ratio, 1),
        })

    # Sort by risk-adjusted 5-day momentum
    rankings.sort(key=lambda x: x["sharpe_5d"], reverse=True)
    return {
        "top_momentum": rankings[:top_n],
        "worst_momentum": rankings[-top_n:][::-1],
        "oversold_bounces": sorted(
            [r for r in rankings if r["rsi"] < 35 and r["ret_5d"] > 0],
            key=lambda x: x["ret_5d"], reverse=True
        )[:10],
        "volume_breakouts": sorted(
            [r for r in rankings if r["vol_ratio"] > 2.0 and r["ret_5d"] > 0],
            key=lambda x: x["vol_ratio"], reverse=True
        )[:10],
    }


def compute_volatility_regime(snapshots):
    """Compute market-wide volatility metrics."""
    if len(snapshots) < 10:
        return {}

    # Compute daily market returns (avg across all stocks)
    market_rets = []
    for i in range(1, len(snapshots)):
        rets = []
        curr = snapshots[i]["stocks"]
        prev = snapshots[i - 1]["stocks"]
        for sym in curr:
            if sym in prev:
                c = curr[sym].get("lp", 0)
                p = prev[sym].get("lp", 0)
                if c > 0 and p > 0:
                    rets.append((c / p - 1) * 100)
        if rets:
            market_rets.append({
                "date": snapshots[i]["date"],
                "mean_ret": round(np.mean(rets), 3),
                "median_ret": round(float(np.median(rets)), 3),
                "std_ret": round(np.std(rets), 3),
                "pct_positive": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
            })

    if not market_rets:
        return {}

    recent_vol = np.std([m["mean_ret"] for m in market_rets[-10:]]) if len(market_rets) >= 10 else 0
    historical_vol = np.std([m["mean_ret"] for m in market_rets]) if market_rets else 0

    if recent_vol > historical_vol * 1.5:
        regime = "HIGH_VOL"
    elif recent_vol < historical_vol * 0.5:
        regime = "LOW_VOL"
    else:
        regime = "NORMAL"

    return {
        "regime": regime,
        "recent_vol": round(float(recent_vol), 3),
        "historical_vol": round(float(historical_vol), 3),
        "vol_ratio": round(float(recent_vol / max(historical_vol, 0.001)), 2),
        "daily_stats": market_rets[-30:],
    }


def compute_sector_correlation(snapshots, s2s, period=20):
    """Compute inter-sector correlation matrix."""
    sector_labels = {
        "COMMERCIAL_BANK": "Banking", "DEVELOPMENT_BANK": "Dev Banks",
        "FINANCE": "Finance", "LIFE_INSURANCE": "Life Ins",
        "NONLIFE_INSURANCE": "Non-Life Ins", "HYDROPOWER": "Hydro",
        "MANUFACTURING": "Manu", "HOTEL_TOURISM": "Hotel",
        "MICROFINANCE": "Micro",
    }

    # Compute daily sector returns
    sector_rets = defaultdict(list)
    for i in range(max(1, len(snapshots) - period), len(snapshots)):
        day_sector = defaultdict(list)
        curr = snapshots[i]["stocks"]
        prev = snapshots[i - 1]["stocks"]
        for sym in curr:
            if sym in prev:
                c = curr[sym].get("lp", 0)
                p = prev[sym].get("lp", 0)
                if c > 0 and p > 0:
                    sec = sector_labels.get(s2s.get(sym, ""), "")
                    if sec:
                        day_sector[sec].append((c / p - 1) * 100)
        for sec, rets in day_sector.items():
            sector_rets[sec].append(np.mean(rets))

    # Build correlation matrix
    sectors = sorted(sector_rets.keys())
    n = len(sectors)
    corr = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            a = np.array(sector_rets[sectors[i]])
            b = np.array(sector_rets[sectors[j]])
            min_len = min(len(a), len(b))
            if min_len >= 5:
                c = np.corrcoef(a[:min_len], b[:min_len])[0, 1]
                corr[i][j] = round(float(c), 2) if not math.isnan(c) else 0
            elif i == j:
                corr[i][j] = 1.0

    return {
        "sectors": sectors,
        "matrix": corr,
    }


def run_analytics(days=60):
    """Run all analytics and save results."""
    print("[ANALYTICS] Loading data...")
    snapshots = _load_history(days)
    s2s = _load_sectors()

    if len(snapshots) < 5:
        print("[ANALYTICS] Not enough data")
        return {}

    print(f"[ANALYTICS] {len(snapshots)} days, {len(snapshots[-1]['stocks'])} stocks")

    results = {
        "date": snapshots[-1]["date"],
        "sector_rotation": compute_sector_rotation(snapshots, s2s),
        "breadth": compute_breadth(snapshots),
        "momentum": compute_momentum_rankings(snapshots, s2s),
        "volatility": compute_volatility_regime(snapshots),
        "correlation": compute_sector_correlation(snapshots, s2s),
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[ANALYTICS] Saved to {OUTPUT_FILE}")
    return results


if __name__ == "__main__":
    run_analytics()
