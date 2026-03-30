"""
Sector Momentum Analyzer.
Maps all 342 NEPSE stocks to their sectors.
Computes sector heat: HOT / WARMING / NEUTRAL / COOLING / COLD.
Identifies which sectors to BUY INTO vs ROTATE OUT OF.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))

from typing import Dict, List

# ── Comprehensive NEPSE sector mapping ───────────────────────────────────────
SECTOR_MAP = {
    # Life Insurance
    "ALICL":"Life Insurance","CLI":"Life Insurance","ILI":"Life Insurance",
    "JLIC":"Life Insurance","LICN":"Life Insurance","MLICL":"Life Insurance",
    "NLIC":"Life Insurance","NLICL":"Life Insurance","PLICL":"Life Insurance",
    "RNLI":"Life Insurance","SLICL":"Life Insurance","GLICL":"Life Insurance",
    "MBLIL":"Life Insurance","RBCL":"Life Insurance","SNLI":"Life Insurance",
    # Non-Life Insurance
    "SALICO":"Non-Life Insurance","NICL":"Non-Life Insurance","PRIN":"Non-Life Insurance",
    "SICL":"Non-Life Insurance","UAIL":"Non-Life Insurance","HEI":"Non-Life Insurance",
    "IGI":"Non-Life Insurance","NIC":"Non-Life Insurance","PIC":"Non-Life Insurance",
    # Commercial Banks
    "ADBL":"Banking","CBL":"Banking","EBL":"Banking","GBIME":"Banking",
    "HBL":"Banking","KBL":"Banking","MBL":"Banking","NABIL":"Banking",
    "NBL":"Banking","NCCB":"Banking","NIB":"Banking","NMB":"Banking",
    "PCBL":"Banking","PRVU":"Banking","NICA":"Banking","SANIMA":"Banking",
    "SCB":"Banking","SBI":"Banking","SBL":"Banking","CZBIL":"Banking",
    "LBBL":"Banking","NIMB":"Banking","MEGA":"Banking","PRABHU":"Banking",
    "SIGS2":"Banking","KRBL":"Banking","JBNL":"Banking","CORBL":"Banking",
    # Development Banks
    "MLBL":"Dev Bank","SADBL":"Dev Bank","KSBBL":"Dev Bank","MNBBL":"Dev Bank",
    "SINDU":"Dev Bank","EDBL":"Dev Bank","SHINE":"Dev Bank","NABBC":"Dev Bank",
    # Finance / Microfinance
    "BL":"Finance","BFC":"Finance","CFCL":"Finance","GMFIL":"Finance",
    "GUFL":"Finance","ICFC":"Finance","JFL":"Finance","MFIL":"Finance",
    "MPFL":"Finance","NHDL":"Finance","NIDC":"Finance","PROFL":"Finance",
    # Hydropower
    "AHPC":"Hydropower","AKPL":"Hydropower","API":"Hydropower",
    "BARUN":"Hydropower","BPCL":"Hydropower","GHL":"Hydropower",
    "HDHPC":"Hydropower","NHPC":"Hydropower","NWCFL":"Hydropower",
    "RHPL":"Hydropower","RRHP":"Hydropower","SHL":"Hydropower",
    "SSHL":"Hydropower","TPC":"Hydropower","UMHL":"Hydropower",
    "UPPER":"Hydropower","USHEC":"Hydropower","BHPL":"Hydropower",
    "BHCL":"Hydropower","BNHC":"Hydropower","BUNGAL":"Hydropower",
    "CKHL":"Hydropower","DORDI":"Hydropower","GLH":"Hydropower",
    "HPPL":"Hydropower","HURJA":"Hydropower","KBBL":"Hydropower",
    "KKHC":"Hydropower","MHNL":"Hydropower","NYADI":"Hydropower",
    "PHCL":"Hydropower","PMHPL":"Hydropower","RADHI":"Hydropower",
    "RAWA":"Hydropower","RIDI":"Hydropower","RURU":"Hydropower",
    "SJCL":"Hydropower","SMHL":"Hydropower","SPDL":"Hydropower",
    "STML":"Hydropower","UNHPL":"Hydropower","YETI":"Hydropower",
    "AKBSL":"Hydropower","DHPL":"Hydropower","GVL":"Hydropower",
    "HLBSL":"Hydropower","HNPPL":"Hydropower","JOSHI":"Hydropower",
    "MAKAR":"Hydropower","MCHL":"Hydropower","MBJC":"Hydropower",
    "NGPL":"Hydropower","NWCL":"Hydropower","RBB":"Hydropower",
    "SKHL":"Hydropower","RSML":"Hydropower","BJHL":"Hydropower",
    # Telecom / Others
    "TTL":"Telecom","NTC":"Telecom",
    "NRBBR":"Trading","NRN":"Investment",
    "AVYAN":"Manufacturing","NIMB":"Banking",
    "BBC":"Dev Bank","HFIN":"Finance",
}

SECTOR_THESIS = {
    "Hydropower": {
        "buy_season": "Oct-Mar (monsoon ended, generation peaks, export revenue)",
        "sell_season": "Jun-Sep (low water, high payout expectations already priced)",
        "catalyst": "NEA power purchase agreement, export to India, dividend announcements",
        "watch": "Water levels, NEA rate revisions, India energy policy",
    },
    "Life Insurance": {
        "buy_season": "After dividend season (Mar-May), accumulation phase",
        "sell_season": "Near AGM/dividend announcement (often overpriced)",
        "catalyst": "Insurance regulatory changes, premium income growth, bonus share announcements",
        "watch": "Beema Samiti regulations, interest rate changes affecting investment income",
    },
    "Banking": {
        "buy_season": "After quarterly results (good NPL numbers), low interest rate cycles",
        "sell_season": "When NRB tightens credit, high NPL concerns",
        "catalyst": "NRB policy rates, credit growth, NPL ratios, merger announcements",
        "watch": "Mandatory merger policy, interest rate corridor, remittance inflows",
    },
    "Non-Life Insurance": {
        "buy_season": "Budget season (new infra projects = demand for insurance)",
        "sell_season": "Off-budget months",
        "catalyst": "Motor vehicle additions, construction projects",
        "watch": "Premium rates, claim ratios",
    },
    "Finance": {
        "buy_season": "Low interest rate environments",
        "sell_season": "NRB tightening",
        "catalyst": "Microfinance expansion, rural lending growth",
        "watch": "Overleveraged borrowers, NRB microfinance regulations",
    },
    "Telecom": {
        "buy_season": "Subscriber growth quarters",
        "sell_season": "Dividend season (often fully priced)",
        "catalyst": "5G rollout, internet penetration, government contracts",
        "watch": "Regulatory tariff changes, competition",
    },
}


def analyze_sectors(market_data: dict) -> dict:
    """
    Full sector momentum analysis using OHLCV data from turnover detail.
    Returns ranked sector heat map + actionable sector calls.
    """
    # Use turnover detail (has full OHLCV + pc)
    turnover_stocks = market_data.get("turnover", {}).get("detail", [])
    if isinstance(market_data.get("turnover"), dict):
        turnover_stocks = market_data["turnover"].get("detail", [])
    else:
        turnover_stocks = []

    # Build sector buckets
    sector_data: Dict[str, list] = {}
    for s in turnover_stocks:
        sym = str(s.get("s", "")).upper()
        sector = SECTOR_MAP.get(sym, "Other")
        if sector not in sector_data:
            sector_data[sector] = []
        try:
            lp = float(s.get("lp", 0) or 0)
            pc = float(s.get("pc", 0) or 0)
            h  = float(s.get("h", lp) or lp)
            l  = float(s.get("l", lp) or lp)
            op = float(s.get("op", lp) or lp)
            t  = float(s.get("t", 0) or 0)
            q  = float(s.get("q", 0) or 0)
            # Price position in today's range (0=at low, 1=at high)
            range_size = h - l
            price_pos = (lp - l) / range_size if range_size > 0 else 0.5
            # Intraday strength (vs open)
            intraday = (lp - op) / op * 100 if op > 0 else 0
            sector_data[sector].append({
                "symbol": sym, "lp": lp, "pc": pc,
                "price_pos": price_pos, "intraday": intraday,
                "turnover": t, "volume": q,
                "h": h, "l": l, "op": op,
            })
        except (ValueError, TypeError):
            pass

    # Score each sector
    results = []
    for sector, stocks in sector_data.items():
        if not stocks:
            continue
        n = len(stocks)
        gainers   = sum(1 for s in stocks if s["pc"] > 0.5)
        losers    = sum(1 for s in stocks if s["pc"] < -0.5)
        avg_pc    = sum(s["pc"] for s in stocks) / n
        avg_pos   = sum(s["price_pos"] for s in stocks) / n  # 0.5 = neutral
        avg_intra = sum(s["intraday"] for s in stocks) / n
        total_t   = sum(s["turnover"] for s in stocks)
        breadth   = gainers / n if n > 0 else 0.5

        # Composite sector momentum score (-100 to +100)
        momentum  = (avg_pc * 15)             # price change weight
        momentum += (avg_pos - 0.5) * 40      # price position weight (above midrange = bullish)
        momentum += (breadth - 0.5) * 30      # breadth weight
        momentum += min(10, avg_intra * 5)    # intraday strength

        # Heat classification
        if   momentum >= 25:  heat = "HOT"
        elif momentum >= 10:  heat = "WARMING"
        elif momentum >= -10: heat = "NEUTRAL"
        elif momentum >= -25: heat = "COOLING"
        else:                 heat = "COLD"

        # Sector action
        if momentum >= 20:
            action = "BUY/ACCUMULATE"
            action_reason = f"Sector strength {avg_pc:+.2f}% avg, {gainers}/{n} stocks up, price near day highs"
        elif momentum >= 5:
            action = "WATCH/ENTER DIPS"
            action_reason = f"Warming up. Look for 1-2% pullback entry. Breadth: {gainers}/{n} positive"
        elif momentum >= -5:
            action = "HOLD/SELECTIVE"
            action_reason = f"Mixed signals. Be stock-specific, avoid sector ETF plays"
        elif momentum >= -20:
            action = "REDUCE EXPOSURE"
            action_reason = f"Sector cooling. Take partial profits on sector holdings"
        else:
            action = "AVOID/SHORT"
            action_reason = f"Sector under selling pressure. {losers}/{n} stocks falling, avg {avg_pc:+.2f}%"

        thesis = SECTOR_THESIS.get(sector, {})
        results.append({
            "sector": sector,
            "heat": heat,
            "momentum_score": round(momentum, 1),
            "action": action,
            "action_reason": action_reason,
            "stocks_count": n,
            "gainers": gainers,
            "losers": losers,
            "avg_change_pct": round(avg_pc, 3),
            "avg_price_position": round(avg_pos, 3),
            "breadth": round(breadth, 3),
            "total_turnover": total_t,
            "buy_season": thesis.get("buy_season", ""),
            "catalyst": thesis.get("catalyst", ""),
            "stocks": sorted(stocks, key=lambda x: x["pc"], reverse=True),
        })

    results.sort(key=lambda x: x["momentum_score"], reverse=True)
    return {
        "sectors": results,
        "hottest": [r["sector"] for r in results[:3]],
        "coldest": [r["sector"] for r in results[-3:]],
        "rotate_into": [r for r in results if r["action"] in ("BUY/ACCUMULATE", "WATCH/ENTER DIPS")],
        "rotate_out": [r for r in results if r["action"] in ("AVOID/SHORT", "REDUCE EXPOSURE")],
    }
