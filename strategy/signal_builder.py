"""
Generatorul de setup-uri.

Logica este trend-following pe doua timeframe-uri:

  1. HTF (implicit 4h) decide ce directie este PERMISA. Nu tranzactionam
     impotriva trendului mare - acolo se pierd conturile cu leverage.
  2. LTF (implicit 1h) decide DACA si UNDE intram, dupa un pullback catre
     EMA rapida, cu confirmare de momentum, forta de trend si volum.

Stopul nu este un procent rotund ales de om. Este plasat dincolo de structura
(swing low/high) SI dincolo de zgomotul masurat prin ATR - se ia varianta mai
larga dintre cele doua. Tintele sunt exprimate in R, nu in dolari.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal

import pandas as pd

from strategy import indicators

Side = Literal["long", "short"]


@dataclass
class Signal:
    symbol: str
    side: Side
    entry: float
    stop_loss: float
    take_profits: list[float]
    score: float
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def risk_per_unit(self) -> float:
        """Distanta pana la stop, in unitati de pret. Acesta este 1R."""
        return abs(self.entry - self.stop_loss)

    @property
    def stop_distance_pct(self) -> float:
        return self.risk_per_unit / self.entry

    @property
    def risk_reward(self) -> float:
        if self.risk_per_unit == 0 or not self.take_profits:
            return 0.0
        return abs(self.take_profits[0] - self.entry) / self.risk_per_unit

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk_reward"] = round(self.risk_reward, 2)
        data["stop_distance_pct"] = round(self.stop_distance_pct, 5)
        return data


def _htf_bias(htf: pd.DataFrame) -> tuple[Side | None, str]:
    """Directia permisa pe timeframe-ul mare."""
    last = htf.iloc[-1]

    if last["close"] > last["ema_slow"] and last["ema_fast"] > last["ema_slow"]:
        return "long", "HTF bullish: pret peste EMA200, EMA50 peste EMA200"
    if last["close"] < last["ema_slow"] and last["ema_fast"] < last["ema_slow"]:
        return "short", "HTF bearish: pret sub EMA200, EMA50 sub EMA200"
    return None, "HTF neutru: structura EMA amestecata, stam pe margine"


def _score_setup(row: pd.Series, side: Side, cfg) -> tuple[float, list[str], list[str]]:
    """
    Construieste scorul 0-100 din componente independente.

    Fiecare componenta raspunde la o intrebare diferita despre piata. Daca toate
    trag in aceeasi directie, scorul e mare. Daca doar una tipa tare si restul
    tac, scorul ramane mic - exact ce vrem.
    """
    score = 0.0
    reasons: list[str] = []
    warnings: list[str] = []

    # --- 1. Aliniere cu EMA rapida pe LTF (30p)
    if side == "long" and row["close"] > row["ema_fast"]:
        score += 30
        reasons.append("Pret peste EMA50 pe LTF - trendul local sustine long-ul")
    elif side == "short" and row["close"] < row["ema_fast"]:
        score += 30
        reasons.append("Pret sub EMA50 pe LTF - trendul local sustine short-ul")
    else:
        warnings.append("Pretul e de partea gresita a EMA50 pe LTF")

    # --- 2. Momentum in zona utila, nu la extrem (25p)
    rsi_val = float(row["rsi"])
    if side == "long":
        in_zone = cfg.rsi_long_min <= rsi_val <= cfg.rsi_long_max
    else:
        in_zone = cfg.rsi_short_min <= rsi_val <= cfg.rsi_short_max

    if in_zone:
        score += 25
        reasons.append(f"RSI {rsi_val:.1f} in zona de continuare, nu la extrem")
    else:
        warnings.append(f"RSI {rsi_val:.1f} in afara zonei preferate")

    # --- 3. Forta trendului: ADX + directia DI (25p)
    adx_val = float(row["adx"])
    di_ok = (
        row["plus_di"] > row["minus_di"]
        if side == "long"
        else row["minus_di"] > row["plus_di"]
    )
    if adx_val >= 25 and di_ok:
        score += 25
        reasons.append(f"ADX {adx_val:.1f} cu DI aliniat - trend clar")
    elif adx_val >= 20 and di_ok:
        score += 15
        reasons.append(f"ADX {adx_val:.1f} - trend prezent dar moderat")
    else:
        warnings.append(f"ADX {adx_val:.1f} slab sau DI contra directiei - risc de range")

    # --- 4. Participare: volum peste medie (20p)
    vol_ratio = float(row.get("volume_ratio") or 0)
    if vol_ratio >= 1.3:
        score += 20
        reasons.append(f"Volum {vol_ratio:.2f}x fata de medie - miscare sustinuta")
    elif vol_ratio >= cfg.min_volume_ratio:
        score += 10
        reasons.append(f"Volum {vol_ratio:.2f}x fata de medie - acceptabil")
    else:
        warnings.append(f"Volum {vol_ratio:.2f}x sub medie - lipsa de participare")

    return score, reasons, warnings


def build_signal(
    symbol: str,
    htf_raw: pd.DataFrame,
    ltf_raw: pd.DataFrame,
    strategy_cfg,
    risk_cfg,
) -> Signal | None:
    """Returneaza un Signal daca exista un setup valabil, altfel None."""
    htf = indicators.enrich(htf_raw, strategy_cfg)
    ltf = indicators.enrich(ltf_raw, strategy_cfg)

    if len(htf) < strategy_cfg.ema_slow or len(ltf) < strategy_cfg.ema_slow:
        return None

    side, bias_reason = _htf_bias(htf)
    if side is None:
        return None

    last = ltf.iloc[-1]
    atr_val = float(last["atr"])
    atr_pct = float(last["atr_pct"])
    entry = float(last["close"])

    if atr_val <= 0 or entry <= 0:
        return None

    # Filtru de regim de volatilitate - inainte de orice scor.
    if not (risk_cfg.min_atr_pct <= atr_pct <= risk_cfg.max_atr_pct):
        return None
    if float(last["adx"]) < risk_cfg.min_adx:
        return None

    score, reasons, warnings = _score_setup(last, side, strategy_cfg)
    reasons.insert(0, bias_reason)

    if score < strategy_cfg.min_setup_score:
        return None

    # --- plasarea stopului: cea mai larga dintre structura si ATR
    swing_high, swing_low = indicators.swing_levels(ltf, strategy_cfg.swing_lookback)
    atr_buffer = 0.25 * atr_val

    if side == "long":
        stop_structural = swing_low - atr_buffer
        stop_atr = entry - risk_cfg.atr_stop_mult * atr_val
        stop_loss = min(stop_structural, stop_atr)
    else:
        stop_structural = swing_high + atr_buffer
        stop_atr = entry + risk_cfg.atr_stop_mult * atr_val
        stop_loss = max(stop_structural, stop_atr)

    r_value = abs(entry - stop_loss)
    if r_value <= 0:
        return None

    sign = 1 if side == "long" else -1
    take_profits = [
        round(entry + sign * mult * r_value, 8) for mult in risk_cfg.tp_r_multiples
    ]

    return Signal(
        symbol=symbol,
        side=side,
        entry=round(entry, 8),
        stop_loss=round(stop_loss, 8),
        take_profits=take_profits,
        score=round(score, 1),
        reasons=reasons,
        warnings=warnings,
        context={
            "htf_timeframe_close": round(float(htf.iloc[-1]["close"]), 8),
            "rsi": round(float(last["rsi"]), 2),
            "adx": round(float(last["adx"]), 2),
            "atr": round(atr_val, 8),
            "atr_pct": round(atr_pct, 5),
            "volume_ratio": round(float(last.get("volume_ratio") or 0), 2),
            "ema_fast": round(float(last["ema_fast"]), 8),
            "ema_slow": round(float(last["ema_slow"]), 8),
            "swing_high": round(swing_high, 8),
            "swing_low": round(swing_low, 8),
            "candle_time": str(last["datetime"]),
        },
    )
