"""Simplified crypto market regime score (0-100, 100 = risk-on).

Adapted from claude-trading-skills' crypto-regime-analyzer. The original
combines 6 components (BTC trend, alt breadth, dominance, funding,
drawdown/vol, momentum thrust) pulled from CoinGecko. This version keeps
the two components that are cheapest to source live from an exchange API
(BTC trend structure, perp funding rate) and re-weights them to 100%.
Add the other components later if you wire up a data source for them.

Component 1 - BTC Trend Structure (originally 25% of 100%, now ~62.5%):
  price vs 50DMA/200DMA stack, plus 200DMA slope. See btc_trend scoring
  below for exact thresholds (ported from the source skill).

Component 2 - Perpetual Funding Regime (originally 15% of 100%, now ~37.5%):
  average 8h funding rate across tracked perps; contrarian at the extremes.
"""

from __future__ import annotations

import math

# Re-weighted to sum to 1.0 over the two components we actually compute.
WEIGHT_BTC_TREND = 0.25 / (0.25 + 0.15)
WEIGHT_FUNDING = 0.15 / (0.25 + 0.15)

SLOPE_LOOKBACK = 20
MIN_FULL_HISTORY = 200 + SLOPE_LOOKBACK


def _sma(values: list[float], window: int) -> float:
    return sum(values[-window:]) / window


def score_btc_trend(closes: list[float]) -> dict:
    """closes: BTC daily closes, oldest first. Needs >= 220 observations."""
    if not closes or len(closes) < MIN_FULL_HISTORY:
        return {
            "score": 50,
            "signal": f"NO DATA: need >= {MIN_FULL_HISTORY} daily closes",
            "data_available": False,
        }

    price = closes[-1]
    ma50 = _sma(closes, 50)
    ma200 = _sma(closes, 200)
    ma200_prev = _sma(closes[: -SLOPE_LOOKBACK], 200)

    if math.isclose(ma200, ma200_prev, rel_tol=1e-12):
        direction = "flat"
    elif ma200 > ma200_prev:
        direction = "rising"
    else:
        direction = "falling"

    if price > ma50 > ma200:
        base, structure = 90, "BULL STACK"
    elif price > ma200 and price <= ma50 and ma50 > ma200:
        base, structure = 65, "BULL PULLBACK"
    elif price <= ma200 and price <= ma50 and ma50 > ma200:
        base, structure = 55, "STACK INTACT, PRICE BELOW"
    elif price > ma50 and ma50 <= ma200:
        base, structure = 45, "RECOVERY ATTEMPT"
    else:
        base, structure = 15, "BEAR STACK"

    slope_modifier = {"rising": 10, "falling": -10, "flat": 0}[direction]
    score = max(0, min(100, base + slope_modifier))

    return {
        "score": score,
        "signal": f"{structure}; 200DMA {direction}",
        "data_available": True,
        "price": round(price, 2),
        "ma50": round(ma50, 2),
        "ma200": round(ma200, 2),
    }


def score_funding_regime(funding_rates: dict[str, float]) -> dict:
    """funding_rates: {symbol: latest 8h funding rate as decimal}, e.g. 0.0001 = 0.01%."""
    rates = [r for r in funding_rates.values() if r is not None]
    invalid = [r for r in rates if not isinstance(r, (int, float)) or not math.isfinite(r) or abs(r) > 1]
    if invalid or len(rates) < 2:
        return {
            "score": 50,
            "signal": "NO DATA: need funding for >= 2 symbols",
            "data_available": False,
        }

    avg = sum(rates) / len(rates)
    if avg <= -0.00010:
        score, label = 80, "WASHED OUT (negative funding)"
    elif avg < 0.0:
        score, label = 65, "SKEPTICAL"
    elif avg <= 0.00010:
        score, label = 75, "NEUTRAL"
    elif avg <= 0.00030:
        score, label = 55, "WARMING (leverage building)"
    elif avg <= 0.00060:
        score, label = 30, "CROWDED (hot funding)"
    else:
        score, label = 10, "EUPHORIC (extreme funding, cascade risk)"

    return {
        "score": score,
        "signal": f"{label}; avg {avg * 100:.4f}%/8h across {len(rates)} perps",
        "data_available": True,
        "avg_funding_8h": avg,
    }


def calculate_regime(closes: list[float], funding_rates: dict[str, float]) -> dict:
    trend = score_btc_trend(closes)
    funding = score_funding_regime(funding_rates)

    available = [
        (c, w) for c, w in ((trend, WEIGHT_BTC_TREND), (funding, WEIGHT_FUNDING))
        if c["data_available"]
    ]
    if not available:
        return {
            "score": None,
            "zone": "UNKNOWN",
            "components": {"btc_trend": trend, "funding": funding},
        }

    total_weight = sum(w for _, w in available)
    score = round(sum(c["score"] * (w / total_weight) for c, w in available), 1)

    if score >= 80:
        zone = "RISK_ON"
    elif score >= 40:
        zone = "NEUTRAL"
    else:
        zone = "RISK_OFF"

    return {
        "score": score,
        "zone": zone,
        "components": {"btc_trend": trend, "funding": funding},
        "components_available": len(available),
        "components_total": 2,
    }


if __name__ == "__main__":
    import json

    # Replace with real BTC daily closes (>= 220) and live funding rates
    # pulled from your exchange's public API before using in the bot.
    fake_closes = [60000.0 + i * 10 for i in range(230)]
    fake_funding = {"BTCUSDT": 0.00008, "ETHUSDT": 0.00012}
    print(json.dumps(calculate_regime(fake_closes, fake_funding), indent=2))
