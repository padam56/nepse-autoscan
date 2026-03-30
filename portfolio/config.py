"""
Portfolio Configuration - All your NEPSE holdings with WACC.
Update this whenever you buy/sell (or use trade_report.py which auto-updates).
"""

# Your complete portfolio: symbol -> {shares, wacc, sector}
PORTFOLIO = {
    "ALICL": {
        "shares": 8046,
        "wacc": 549.87,
        "total_cost": 4_424_248.16,
        "sector": "Life Insurance",
        "company": "Asian Life Insurance Company Ltd.",
    },
    "BARUN": {
        "shares": 400,
        "wacc": 391.408,
        "total_cost": 156_563.20,
        "sector": "Hydropower",
        "company": "Barun Hydro Power Company Ltd.",
    },
    "BPCL": {
        "shares": 200,
        "wacc": 535.1771,
        "total_cost": 107_035.42,
        "sector": "Hydropower",
        "company": "Butwal Power Company Ltd.",
    },
    "CLI": {
        "shares": 13,
        "wacc": 210.7692,
        "total_cost": 2_740.00,
        "sector": "Life Insurance",
        "company": "Chhimek Life Insurance Co. Ltd.",
    },
    "BL": {
        "shares": 6,
        "wacc": 133.3333,
        "total_cost": 799.9998,
        "sector": "Finance",
        "company": "Bagbazar Laghubitta Bittiya Sanstha Ltd.",
    },
    "ILI": {
        "shares": 12,
        "wacc": 214.0917,
        "total_cost": 2_569.10,
        "sector": "Life Insurance",
        "company": "IME Life Insurance Company Ltd.",
    },
    "NLIC": {
        "shares": 273,
        "wacc": 746.842,
        "total_cost": 203_887.87,
        "sector": "Life Insurance",
        "company": "Nepal Life Insurance Company Ltd.",
    },
    "NLICL": {
        "shares": 2,
        "wacc": 100.00,
        "total_cost": 200.00,
        "sector": "Life Insurance",
        "company": "National Life Insurance Company Ltd.",
    },
    "RNLI": {
        "shares": 12,
        "wacc": 230.8333,
        "total_cost": 2_770.00,
        "sector": "Life Insurance",
        "company": "Reliable Nepal Life Insurance Ltd.",
    },
    "SALICO": {
        "shares": 7,
        "wacc": 1261.7414,
        "total_cost": 8_832.19,
        "sector": "Non-Life Insurance",
        "company": "Sagarmatha Lumbini Insurance Co. Ltd.",
    },
    "TTL": {
        "shares": 368,
        "wacc": 922.9215,
        "total_cost": 339_635.12,
        "sector": "Telecom",
        "company": "Times Cable Network Ltd.",
    },
}

# Sector weights for portfolio analysis
SECTOR_MAP = {
    "Life Insurance": ["ALICL", "CLI", "ILI", "NLIC", "NLICL", "RNLI"],
    "Non-Life Insurance": ["SALICO"],
    "Hydropower": ["BARUN", "BPCL"],
    "Finance": ["BL"],
    "Telecom": ["TTL"],
}

# Total portfolio cost
TOTAL_INVESTED = sum(s["total_cost"] for s in PORTFOLIO.values())

# Alert email
ALERT_EMAIL = "tpadamjung@gmail.com"
