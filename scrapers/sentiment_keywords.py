"""
Sentiment keyword dictionaries and company alias mappings for NEPSE news analysis.
"""

BULLISH_KEYWORDS = [
    'profit', 'net profit', 'dividend', 'bonus share', 'rights share', 'IPO',
    'growth', 'surge', 'rally', 'breakout', 'record high', 'highest',
    'bullish', 'positive', 'approved', 'increased', 'strong', 'upgrade',
    'outperform', 'buy', 'accumulate', 'NRB rate cut', 'expansion', 'merger',
    'earning', 'revenue growth', 'all-time high', 'new high', 'oversubscribed',
    'rights issue', 'book closure', 'AGM approved', 'target achieved',
    'recovery', 'rebound', 'upturn', 'momentum', 'inflow', 'green',
    'upper circuit', 'price appreciation', 'strong fundamentals',
    'quarterly profit', 'annual profit', 'eps increase', 'npat increase',
    'capital gain', 'proposed dividend', 'cash dividend', 'stock dividend',
]

BEARISH_KEYWORDS = [
    'loss', 'net loss', 'decline', 'penalty', 'fraud', 'scandal', 'fine',
    'crash', 'bearish', 'negative', 'suspend', 'suspended', 'default',
    'NPA', 'downgrade', 'sell', 'avoid', 'NRB rate hike', 'restriction',
    'ban', 'warning', 'probe', 'investigation', 'delisted', 'frozen',
    'lower circuit', 'circuit break', 'correction', 'outflow', 'red',
    'decrease', 'reduced', 'slump', 'plunge', 'drop', 'fall',
    'quarterly loss', 'annual loss', 'eps decrease', 'npat decrease',
    'impairment', 'write-off', 'writeoff', 'regulatory action',
    'SEBON action', 'auction', 'liquidation', 'insolvency',
]

# Weighted keywords: (keyword, weight) for stronger signals
BULLISH_WEIGHTED = [
    ('record profit', 1.0),
    ('dividend approved', 0.9),
    ('bonus share approved', 0.9),
    ('rights share', 0.7),
    ('IPO oversubscribed', 0.8),
    ('merger approved', 0.7),
    ('NRB rate cut', 0.6),
    ('strong quarterly result', 0.8),
    ('all-time high', 0.7),
    ('upper circuit', 0.5),
]

BEARISH_WEIGHTED = [
    ('fraud case', -1.0),
    ('suspended trading', -0.9),
    ('penalty imposed', -0.8),
    ('net loss', -0.7),
    ('NPA increased', -0.7),
    ('NRB rate hike', -0.6),
    ('delisted', -1.0),
    ('lower circuit', -0.5),
    ('SEBON warning', -0.7),
    ('loan default', -0.8),
]

# Company name -> symbol mapping for headline symbol extraction
COMPANY_ALIASES = {
    # Commercial Banks
    'nabil bank': 'NABIL',
    'nabil': 'NABIL',
    'himalayan bank': 'HBL',
    'nepal investment bank': 'NIBL',
    'nepal investment mega bank': 'NIMB',
    'standard chartered': 'SCB',
    'standard chartered nepal': 'SCB',
    'everest bank': 'EBL',
    'nepal bank': 'NBL',
    'nic asia': 'NICA',
    'nic asia bank': 'NICA',
    'nmb bank': 'NMB',
    'kumari bank': 'KBL',
    'sanima bank': 'SANIMA',
    'sunrise bank': 'SRBL',
    'global ime bank': 'GBIME',
    'prime commercial bank': 'PRIME',
    'citizens bank': 'CZBIL',
    'prabhu bank': 'PRVU',
    'sbi bank': 'SBI',
    'nepal sbi bank': 'SBI',
    'nepal credit': 'NCC',
    'agriculture development bank': 'ADBL',
    'laxmi bank': 'LBL',
    'laxmi sunrise': 'LBL',
    'machhapuchchhre bank': 'MBL',
    'bank of kathmandu': 'BOKL',
    'century bank': 'CCBL',
    'civil bank': 'CBL',

    # Life Insurance
    'nepal life insurance': 'NLIC',
    'nepal life': 'NLIC',
    'asian life': 'ALICL',
    'asian life insurance': 'ALICL',
    'surya life': 'SLICL',
    'surya life insurance': 'SLICL',
    'life insurance corporation': 'LICN',
    'prime life': 'PMLI',
    'rastriya beema': 'RNLI',
    'sun nepal life': 'SNLI',

    # Non-Life Insurance
    'nepal insurance': 'NIL',
    'himalayan general': 'HGI',
    'sagarmatha insurance': 'SGI',
    'shikhar insurance': 'SICL',
    'siddhartha insurance': 'SIL',
    'premier insurance': 'PICL',

    # Hydropower
    'chilime': 'CHL',
    'chilime hydropower': 'CHL',
    'upper tamakoshi': 'UPPER',
    'butwal power': 'BPCL',
    'nepal hydro': 'NHPC',
    'ruru hydropower': 'RURU',
    'barun hydropower': 'BARUN',
    'api power': 'API',
    'sanjen hydropower': 'SANJEN',

    # Manufacturing / Others
    'nepal telecom': 'NTC',
    'himalayan distillery': 'HDL',
    'bottlers nepal': 'BNL',
    'shivam cement': 'SHIVM',
    'unilever nepal': 'UML',

    # Hotels
    'soaltee hotel': 'SHL',
    'taragaon regency': 'TRH',
    'oriental hotel': 'OHL',

    # Microfinance
    'chhimek': 'CBBL',
    'nirdhan': 'NRDHN',
    'forward community': 'FOWAD',
}

# Sector-level keywords that affect all stocks in a sector
SECTOR_KEYWORDS = {
    'COMMERCIAL_BANK': ['banking sector', 'commercial bank', 'base rate', 'spread rate', 'CCD ratio'],
    'LIFE_INSURANCE': ['insurance sector', 'life insurance', 'beema samiti', 'insurance board'],
    'HYDROPOWER': ['hydropower sector', 'NEA', 'PPA', 'electricity', 'load shedding', 'power purchase'],
    'MICROFINANCE': ['microfinance', 'microcredit', 'deprived sector'],
    'DEVELOPMENT_BANK': ['development bank', 'dev bank'],
    'MUTUAL_FUND': ['mutual fund', 'NAV', 'unit price'],
}
