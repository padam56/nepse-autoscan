"""
Risk controls for the NEPSE scanner pipeline.

#3  Sector diversification cap  -- apply_sector_cap()
#6  Drawdown circuit breaker    -- apply_drawdown_brake()
"""

from __future__ import annotations


def apply_sector_cap(
    picks: list[dict],
    sectors: dict,
    max_per_sector: int = 3,
) -> list[dict]:
    """Filter picks so no more than max_per_sector stocks from the same sector.

    Args:
        picks: ranked list of picks (already sorted by score, best first)
        sectors: dict mapping symbol -> sector name
                 (from data/sectors.json 'symbol_to_sector')
        max_per_sector: max picks from one sector (default 3)

    Returns:
        Filtered list maintaining original ranking order.
        If a pick would exceed the sector cap, skip it and take the next
        best pick.
    """
    sector_counts: dict[str, int] = {}
    filtered: list[dict] = []

    for pick in picks:
        sym = pick.get("symbol", "")
        sector = sectors.get(sym, "UNKNOWN")
        count = sector_counts.get(sector, 0)

        if count < max_per_sector:
            filtered.append(pick)
            sector_counts[sector] = count + 1

    return filtered


def apply_drawdown_brake(
    kelly_pct: float,
    signal_tracker_stats: dict,
) -> float:
    """Scale down Kelly sizing when recent performance is poor.

    Logic:
    - If hit_rate >= 50%: no adjustment (full Kelly)
    - If hit_rate 40-50%: reduce Kelly by 30%
    - If hit_rate 30-40%: reduce Kelly by 50%
    - If hit_rate < 30%: reduce Kelly by 75%
    - If not enough data (< 10 evaluated signals): no adjustment

    Also check avg_return_pct:
    - If avg 5d return < -2%: additional 25% reduction

    Returns adjusted kelly_pct (never below 1%).
    """
    evaluated = signal_tracker_stats.get("evaluated", 0)
    if evaluated < 10:
        return kelly_pct

    hit_rate = signal_tracker_stats.get("hit_rate", 50.0)
    avg_return_pct = signal_tracker_stats.get("avg_return_pct", 0.0)

    # Hit-rate based scaling
    if hit_rate >= 50.0:
        scale = 1.0
    elif hit_rate >= 40.0:
        scale = 0.70
    elif hit_rate >= 30.0:
        scale = 0.50
    else:
        scale = 0.25

    adjusted = kelly_pct * scale

    # Additional reduction for negative average returns
    if avg_return_pct < -2.0:
        adjusted *= 0.75

    return max(adjusted, 1.0)


def check_sector_exposure(
    picks: list,
    portfolio: list,
    sectors: dict,
    max_sector_pct: float = 0.35,
) -> list:
    """Check if picks + existing portfolio would create excessive sector concentration.

    Args:
        picks: new buy recommendations, each a dict with at least
               {symbol, kelly_pct} (kelly_pct used to estimate allocation)
        portfolio: current holdings [{symbol, shares, wacc}]
        sectors: {symbol: sector_name} mapping
        max_sector_pct: max fraction of total portfolio in one sector (default 35%)

    Returns:
        list of warning strings for sectors that would exceed the cap.
    """
    # Calculate current portfolio value by sector
    sector_value: dict[str, float] = {}
    total_value = 0.0

    for pos in portfolio:
        sym   = pos.get("symbol", "")
        value = pos.get("shares", 0) * pos.get("wacc", 0)
        if value <= 0:
            continue
        sector = sectors.get(sym, "UNKNOWN")
        sector_value[sector] = sector_value.get(sector, 0) + value
        total_value += value

    # Estimate value of proposed picks using kelly_pct as fraction of total
    # If portfolio is empty, use a notional base so percentages still work
    base_value = total_value if total_value > 0 else 1_000_000
    pick_values: dict[str, float] = {}

    for pick in picks:
        sym = pick.get("symbol", "")
        kelly = pick.get("kelly_pct", 5.0)
        est_value = base_value * (kelly / 100.0)
        sector = sectors.get(sym, "UNKNOWN")
        pick_values[sym] = est_value
        sector_value[sector] = sector_value.get(sector, 0) + est_value
        total_value += est_value

    if total_value <= 0:
        return []

    # Check each sector against the cap
    warnings = []
    for sector, value in sorted(sector_value.items()):
        pct = value / total_value
        if pct > max_sector_pct:
            # Figure out which picks are contributing to the breach
            contributing = [
                p.get("symbol", "") for p in picks
                if sectors.get(p.get("symbol", ""), "UNKNOWN") == sector
            ]
            sector_label = sector.replace("_", " ").title()
            if contributing:
                syms_str = ", ".join(contributing)
                warnings.append(
                    "Adding %s would put %.0f%% of portfolio in %s (max %.0f%%)"
                    % (syms_str, pct * 100, sector_label, max_sector_pct * 100)
                )
            else:
                # Existing portfolio already over-concentrated
                warnings.append(
                    "Portfolio already has %.0f%% in %s (max %.0f%%)"
                    % (pct * 100, sector_label, max_sector_pct * 100)
                )

    return warnings
