"""
Generator de setup-uri pentru pozitii intraday (buget implicit: 3 ore).

Trei setup-uri, alese pentru ca acopera regimuri DIFERITE de piata. Un agent cu
un singur tipar tace saptamani intregi si apoi forteaza intrari cand tiparul lui
nu se potriveste cu ce face piata.

  1. SWEEP + RECLAIM  (reversie)
     Maturare de lichiditate urmata de recucerirea nivelului. Linda Raschke il
     numeste "Turtle Soup" in Street Smarts, Al Brooks "failed breakout", Bob
     Volman "false break". Functioneaza in range si la extremele unui trend.

  2. SQUEEZE BREAKOUT  (continuare)
     Bollinger intra in Keltner (compresie), apoi rupe cu volum. Tiparul lui
     John Bollinger, popularizat ca "TTM squeeze" de John Carter. Functioneaza
     la iesirea din consolidare.

  3. REVERSIE LA VWAP  (reversie)
     Pretul intins peste 2.2 deviatii fata de pretul mediu ponderat cu volum al
     sesiunii, cu oscilatori epuizati. Reperul standard al deskurilor de
     executie. Functioneaza in zilele fara directie.

DOUA REGULI care traverseaza tot fisierul:

  Oscilatorii nu declanseaza niciodata singuri. Confirma sau anuleaza. Un sweep
  fara divergenta si fara volum e doar o scadere, iar scaderile continua.

  Confluenta se citeste DIFERIT dupa tipul setup-ului. La o reversie vrei
  momentum care franeaza si divergente; la o continuare vrei exact opusul -
  momentum care accelereaza. Aplicarea acelorasi filtre pe ambele este greseala
  care face ca sistemele cu multi indicatori sa nu functioneze nicaieri.

Aritmetica de care depinde totul (vezi tools\\feasibility.py):
costurile se platesc in PRET, iar R-ul unui scalp e mic in pret, deci
`cost_R = cost_dus_intors / distanta_stop` decide daca setup-ul are voie sa
existe. Singurul R:R care conteaza este cel de dupa costuri.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from strategy import indicators, oscillators
from strategy.oscillators import Divergence
from strategy.signal_builder import Signal, Side

Mode = Literal["reversal", "continuation"]


@dataclass
class SetupCandidate:
    """
    Un tipar detectat, inainte de scorul de confluenta si de portile de cost.

    Fiecare detector produce asa ceva; restul pipeline-ului e comun. Asta tine
    logica specifica fiecarui setup separata de aritmetica de risc, care nu are
    voie sa difere intre setup-uri.
    """

    name: str
    mode: Mode
    side: Side
    stop_ref: float  # extremul dincolo de care se aseaza stopul
    limit_ref: float  # nivelul la care asteptam cu ordinul limit
    base_score: float  # calitatea tiparului in sine, 0-40
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    tp1_cap: float | None = None  # tinta naturala, daca setup-ul are una


# ============================================================== setup 1: sweep
@dataclass(frozen=True)
class Sweep:
    """Un nivel de lichiditate maturat si recucerit."""

    side: Side
    level: float
    extreme: float
    sweep_pos: int
    penetration_atr: float
    wick_ratio: float
    volume_ratio: float
    bars_since: int


def _find_sweep(df: pd.DataFrame, cfg, side: Side) -> Sweep | None:
    """
    Pentru long: un pivot LOW a fost strapuns in jos si pretul a inchis inapoi
    deasupra lui. Pentru short: simetric pe maxime.
    """
    last = df.iloc[-1]
    atr_val = float(last["atr"])
    close = float(last["close"])
    if atr_val <= 0:
        return None

    window = cfg.reclaim_within + 1
    if len(df) < cfg.liquidity_lookback + window:
        return None

    pivot_kind = "low" if side == "long" else "high"
    price_col = "low" if side == "long" else "high"

    pivots = oscillators.recent_pivots(
        df, price_col=price_col, osc_col="rsi",
        left=cfg.pivot_left, right=cfg.pivot_right, kind=pivot_kind,
        lookback=cfg.liquidity_lookback, max_count=3,
    )
    if not pivots:
        return None

    start = len(df) - window
    recent = df.iloc[start:]

    for pivot in pivots:
        # Pivotul trebuie sa PRECEADA sweep-ul, altfel comparam ceva cu el insusi.
        if pivot.pos >= start:
            continue

        level = pivot.price
        if side == "long":
            extreme = float(recent["low"].min())
            sweep_offset = int(recent["low"].values.argmin())
            penetrated = level - extreme
            reclaimed = close > level
        else:
            extreme = float(recent["high"].max())
            sweep_offset = int(recent["high"].values.argmax())
            penetrated = extreme - level
            reclaimed = close < level

        if penetrated <= 0 or not reclaimed:
            continue
        penetration_atr = penetrated / atr_val
        if penetration_atr < cfg.min_sweep_atr:
            continue  # atingere, nu maturare

        sweep_pos = start + sweep_offset
        bar = df.iloc[sweep_pos]
        bar_range = float(bar["high"]) - float(bar["low"])
        if bar_range <= 0:
            continue

        if side == "long":
            wick = min(float(bar["open"]), float(bar["close"])) - float(bar["low"])
        else:
            wick = float(bar["high"]) - max(float(bar["open"]), float(bar["close"]))

        return Sweep(
            side=side, level=level, extreme=extreme, sweep_pos=sweep_pos,
            penetration_atr=penetration_atr,
            wick_ratio=max(wick, 0.0) / bar_range,
            volume_ratio=float(bar.get("volume_ratio") or 0.0),
            bars_since=(len(df) - 1) - sweep_pos,
        )
    return None


def _setup_sweep_reclaim(df: pd.DataFrame, ctx, cfg, sides: list[Side]) -> SetupCandidate | None:
    for side in sides:
        sweep = _find_sweep(df, cfg, side)
        if sweep is None:
            continue

        score = 0.0
        reasons = [
            f"Sweep al nivelului {sweep.level:.6g} cu {sweep.penetration_atr:.2f} ATR, "
            f"reclaim in {sweep.bars_since + 1} lumanari"
        ]
        warnings: list[str] = []

        # Volum pe lumanarea de sweep: dovada ca s-au executat stopuri (16p)
        if sweep.volume_ratio >= cfg.min_sweep_volume:
            score += 16
            reasons.append(
                f"Volum {sweep.volume_ratio:.2f}x pe sweep - stopuri executate, nu drift"
            )
        else:
            warnings.append(
                f"Volum doar {sweep.volume_ratio:.2f}x pe sweep - poate nu a fost maturare reala"
            )

        # Wick de respingere (12p)
        if sweep.wick_ratio >= 0.5:
            score += 12
            reasons.append(f"Wick de respingere {sweep.wick_ratio:.0%} din lumanare")
        elif sweep.wick_ratio >= 0.3:
            score += 6

        # Adancime: destul cat sa doara, nu atat cat sa fie miscare reala (12p)
        if 0.3 <= sweep.penetration_atr <= 1.5:
            score += 12
            reasons.append("Adancime de sweep in zona tipica pentru capcana")
        elif sweep.penetration_atr > 2.5:
            warnings.append(
                f"Strapungere de {sweep.penetration_atr:.1f} ATR - prea adanc, "
                "poate fi miscare reala, nu capcana"
            )

        offset = cfg.limit_offset_atr * float(df.iloc[-1]["atr"])
        return SetupCandidate(
            name="sweep_reclaim", mode="reversal", side=side,
            stop_ref=sweep.extreme,
            limit_ref=sweep.level + offset if side == "long" else sweep.level - offset,
            base_score=score, reasons=reasons, warnings=warnings,
            context={
                "swept_level": round(sweep.level, 8),
                "sweep_extreme": round(sweep.extreme, 8),
                "penetration_atr": round(sweep.penetration_atr, 3),
                "sweep_wick_ratio": round(sweep.wick_ratio, 3),
                "sweep_volume_ratio": round(sweep.volume_ratio, 2),
                "bars_since_sweep": sweep.bars_since,
            },
        )
    return None


# =========================================================== setup 2: squeeze
def _setup_squeeze_breakout(df: pd.DataFrame, ctx, cfg, sides: list[Side]) -> SetupCandidate | None:
    """
    Compresie Bollinger-in-Keltner urmata de o lumanare care rupe cu volum.

    Directia NU vine din squeeze - squeeze-ul nu stie incotro. Vine din
    lumanarea care il rupe, si tocmai de aceea cerem volum: fara el, ruptura e
    doar prima incercare din trei.
    """
    if not cfg.squeeze_enabled or len(df) < cfg.min_squeeze_bars + 3:
        return None

    last = df.iloc[-1]
    atr_val = float(last["atr"])
    close = float(last["close"])
    if atr_val <= 0:
        return None

    # Trebuie sa fi fost comprimat pana recent si sa fi iesit acum.
    recent_squeeze = df["squeeze"].iloc[-(cfg.min_squeeze_bars + 2):-1]
    if not bool(recent_squeeze.all()):
        return None
    if bool(last["squeeze"]):
        return None  # inca in compresie, nu s-a eliberat

    upper = float(last["bb_upper"])
    lower = float(last["bb_lower"])
    vol_ratio = float(last.get("volume_ratio") or 0.0)

    if close > upper + cfg.min_breakout_atr * atr_val:
        side: Side = "long"
        breakout_atr = (close - upper) / atr_val
        band = upper
    elif close < lower - cfg.min_breakout_atr * atr_val:
        side = "short"
        breakout_atr = (lower - close) / atr_val
        band = lower
    else:
        return None

    if side not in sides:
        return None
    if vol_ratio < cfg.min_breakout_volume:
        return None

    # Cat timp a stat comprimat - cu cat mai mult, cu atat mai multa energie.
    squeeze_len = 0
    for val in reversed(df["squeeze"].iloc[:-1].tolist()):
        if val:
            squeeze_len += 1
        else:
            break

    score = 0.0
    reasons = [
        f"Squeeze de {squeeze_len} lumanari eliberat in {side} "
        f"({breakout_atr:.2f} ATR peste banda)"
    ]
    warnings: list[str] = []

    if squeeze_len >= cfg.min_squeeze_bars * 2:
        score += 14
        reasons.append("Compresie lunga - energie acumulata semnificativa")
    elif squeeze_len >= cfg.min_squeeze_bars:
        score += 8

    if vol_ratio >= cfg.min_breakout_volume * 1.5:
        score += 14
        reasons.append(f"Volum {vol_ratio:.2f}x pe ruptura - participare reala")
    else:
        score += 7

    if breakout_atr >= 0.5:
        score += 12
        reasons.append("Ruptura decisiva, nu atingere de banda")
    elif breakout_atr >= cfg.min_breakout_atr:
        score += 6
        warnings.append("Ruptura modesta - risc de intoarcere in banda")

    # Stopul: dincolo de mijlocul benzii, adica inapoi in consolidare. Daca
    # pretul revine acolo, teza de expansiune a fost gresita.
    return SetupCandidate(
        name="squeeze_breakout", mode="continuation", side=side,
        stop_ref=float(last["bb_mid"]),
        limit_ref=band,  # asteptam retestul benzii rupte
        base_score=score, reasons=reasons, warnings=warnings,
        context={
            "squeeze_bars": squeeze_len,
            "breakout_atr": round(breakout_atr, 3),
            "breakout_volume_ratio": round(vol_ratio, 2),
            "band_level": round(band, 8),
            "bb_bandwidth": round(float(last["bb_bandwidth"]), 5)
            if pd.notna(last["bb_bandwidth"]) else None,
        },
    )


# ============================================================== setup 3: VWAP
def _setup_vwap_reversion(df: pd.DataFrame, ctx, cfg, sides: list[Side]) -> SetupCandidate | None:
    """
    Fade al unei intinderi extreme fata de VWAP-ul sesiunii.

    Conditia de context este cea mai importanta si e usor de uitat: NU fadeuim
    intr-un trend puternic. Acolo "intins" ramane intins ore intregi, iar
    reversia la medie devine cel mai scump mod de a avea dreptate prea devreme.
    """
    if not cfg.vwap_reversion_enabled or "vwap_z" not in df.columns:
        return None

    last = df.iloc[-1]
    if pd.isna(last["vwap_z"]) or pd.isna(last.get("vwap")):
        return None

    atr_val = float(last["atr"])
    if atr_val <= 0:
        return None

    ctx_adx = float(ctx.iloc[-1]["adx"]) if len(ctx) else 0.0
    if ctx_adx > cfg.vwap_max_context_adx:
        return None

    z = float(last["vwap_z"])
    vwap_val = float(last["vwap"])

    if z <= -cfg.vwap_z_entry:
        side: Side = "long"
    elif z >= cfg.vwap_z_entry:
        side = "short"
    else:
        return None

    if side not in sides:
        return None

    # Epuizarea trebuie confirmata de oscilatori, altfel fadeuim un trend.
    mfi_val = float(last["mfi"]) if pd.notna(last["mfi"]) else 50.0
    wr_val = float(last["williams_r"]) if pd.notna(last["williams_r"]) else -50.0
    if side == "long":
        exhausted = mfi_val <= cfg.mfi_oversold or wr_val <= cfg.williams_oversold
    else:
        exhausted = mfi_val >= cfg.mfi_overbought or wr_val >= cfg.williams_overbought
    if not exhausted:
        return None

    score = 0.0
    reasons = [
        f"Pret intins {abs(z):.2f}sd fata de VWAP ({vwap_val:.6g}) "
        f"cu context calm (ADX {ctx_adx:.1f})"
    ]
    warnings: list[str] = []

    if abs(z) >= cfg.vwap_z_entry + 0.8:
        score += 18
        reasons.append("Intindere extrema - reversia la medie e statistic favorizata")
    else:
        score += 10

    if ctx_adx < 20:
        score += 12
        reasons.append("Context fara trend - VWAP-ul chiar functioneaza ca magnet")
    else:
        score += 6
        warnings.append(f"ADX de context {ctx_adx:.1f} - trend prezent, fade mai riscant")

    score += 10  # epuizarea a fost deja verificata ca preconditie

    # Extremul recent devine reperul stopului: daca il depaseste, intinderea
    # nu era epuizare, era inceput de miscare.
    lookback = df.iloc[-min(len(df), 12):]
    stop_ref = float(lookback["low"].min()) if side == "long" else float(lookback["high"].max())

    return SetupCandidate(
        name="vwap_reversion", mode="reversal", side=side,
        stop_ref=stop_ref,
        limit_ref=float(last["close"]),
        base_score=score, reasons=reasons, warnings=warnings,
        # Tinta naturala este VWAP-ul, nu un multiplu inventat de R.
        tp1_cap=vwap_val,
        context={
            "vwap_z_at_entry": round(z, 2),
            "context_adx": round(ctx_adx, 1),
            "vwap_target": round(vwap_val, 8),
        },
    )


# ======================================================== scor de confluenta
def _confluence_reversal(df: pd.DataFrame, side: Side, cfg) -> tuple[float, list[str], list[str], list[Divergence]]:
    """La o reversie cautam epuizare: momentum care franeaza si divergente."""
    last = df.iloc[-1]
    prev = df.iloc[-2]
    is_long = side == "long"

    score = 0.0
    reasons: list[str] = []
    warnings: list[str] = []
    divs: list[Divergence] = []

    # --- divergente pret/oscilator (20p) - confirmarea cea mai grea de obtinut
    kind: Literal["bullish", "bearish"] = "bullish" if is_long else "bearish"
    for col in ("rsi", "mfi"):
        d = oscillators.find_divergence(
            df, osc_col=col, kind=kind,
            left=cfg.pivot_left, right=cfg.pivot_right,
            lookback=cfg.div_lookback, min_bars_apart=cfg.div_min_bars_apart,
            max_bars_since=cfg.div_max_bars_since, min_osc_gap=cfg.div_min_osc_gap,
        )
        if d is not None:
            divs.append(d)
            reasons.append(d.describe())

    if len(divs) >= 2:
        score += 20
        reasons.append("Divergenta pe DOI oscilatori independenti")
    elif len(divs) == 1:
        score += 12
    else:
        warnings.append("Fara divergenta - miscarea poate fi reala, nu epuizare")

    # --- Stoch RSI se intoarce din extrem (12p)
    k = float(last["stoch_k"]) if pd.notna(last["stoch_k"]) else 50.0
    k_prev = float(prev["stoch_k"]) if pd.notna(prev["stoch_k"]) else 50.0
    d_val = float(last["stoch_d"]) if pd.notna(last["stoch_d"]) else 50.0

    turning = (k > k_prev and k > d_val) if is_long else (k < k_prev and k < d_val)
    from_extreme = k_prev <= cfg.stoch_oversold if is_long else k_prev >= cfg.stoch_overbought

    if turning and from_extreme:
        score += 12
        reasons.append(f"Stoch RSI se intoarce din extrem (K {k_prev:.0f} -> {k:.0f})")
    elif turning:
        score += 6
    else:
        warnings.append(f"Stoch RSI nu confirma intoarcerea (K {k:.0f}, D {d_val:.0f})")

    # --- epuizare pe MFI / Williams %R (8p)
    mfi_val = float(last["mfi"]) if pd.notna(last["mfi"]) else 50.0
    wr_val = float(last["williams_r"]) if pd.notna(last["williams_r"]) else -50.0
    if is_long:
        done = mfi_val <= cfg.mfi_oversold or wr_val <= cfg.williams_oversold
    else:
        done = mfi_val >= cfg.mfi_overbought or wr_val >= cfg.williams_overbought
    if done:
        score += 8
        reasons.append(f"Epuizare confirmata (MFI {mfi_val:.0f}, %R {wr_val:.0f})")

    # --- exces fata de VWAP (10p)
    z = float(last["vwap_z"]) if "vwap_z" in last and pd.notna(last["vwap_z"]) else 0.0
    if (is_long and z <= -cfg.vwap_z_stretch) or (not is_long and z >= cfg.vwap_z_stretch):
        score += 10
        reasons.append(f"Pret intins {abs(z):.1f}sd fata de VWAP - magnet in directia trade-ului")
    elif abs(z) < 0.5:
        warnings.append(f"Pret lipit de VWAP (z={z:.2f}) - fara magnet, tinta e mai grea")

    # --- MACD franeaza (10p)
    hist = float(last["macd_hist"]) if pd.notna(last["macd_hist"]) else 0.0
    hist_prev = float(prev["macd_hist"]) if pd.notna(prev["macd_hist"]) else 0.0
    if (is_long and hist > hist_prev) or (not is_long and hist < hist_prev):
        score += 10 if (hist_prev < 0 if is_long else hist_prev > 0) else 5
        reasons.append("Histograma MACD se restrange - presiunea contrara scade")
    else:
        warnings.append("MACD inca accelereaza impotriva setup-ului")

    return score, reasons, warnings, divs


def _confluence_continuation(df: pd.DataFrame, side: Side, cfg) -> tuple[float, list[str], list[str], list[Divergence]]:
    """
    La o continuare cautam exact opusul: momentum care ACCELEREAZA.

    Aici o divergenta este un semnal de ALARMA, nu de confirmare - inseamna ca
    ruptura se face cu mai putina forta decat miscarea anterioara.
    """
    last = df.iloc[-1]
    prev = df.iloc[-2]
    is_long = side == "long"

    score = 0.0
    reasons: list[str] = []
    warnings: list[str] = []
    divs: list[Divergence] = []

    # --- MACD accelereaza in directia rupturii (18p)
    hist = float(last["macd_hist"]) if pd.notna(last["macd_hist"]) else 0.0
    hist_prev = float(prev["macd_hist"]) if pd.notna(prev["macd_hist"]) else 0.0
    if is_long and hist > hist_prev and hist > 0:
        score += 18
        reasons.append("Histograma MACD se extinde in sus - momentum in expansiune")
    elif not is_long and hist < hist_prev and hist < 0:
        score += 18
        reasons.append("Histograma MACD se extinde in jos - momentum in expansiune")
    else:
        warnings.append("MACD nu confirma expansiunea - ruptura fara momentum")

    # --- Stoch in directie, dar nu deja epuizat (12p)
    k = float(last["stoch_k"]) if pd.notna(last["stoch_k"]) else 50.0
    k_prev = float(prev["stoch_k"]) if pd.notna(prev["stoch_k"]) else 50.0
    if is_long and k > k_prev and k < 92:
        score += 12
        reasons.append(f"Stoch RSI urca ({k_prev:.0f} -> {k:.0f}) fara sa fie deja saturat")
    elif not is_long and k < k_prev and k > 8:
        score += 12
        reasons.append(f"Stoch RSI coboara ({k_prev:.0f} -> {k:.0f}) fara sa fie deja saturat")
    else:
        warnings.append(f"Stoch RSI la {k:.0f} - saturat sau contra directiei")

    # --- MFI confirma cu volum (10p)
    mfi_val = float(last["mfi"]) if pd.notna(last["mfi"]) else 50.0
    if (is_long and mfi_val > 55) or (not is_long and mfi_val < 45):
        score += 10
        reasons.append(f"MFI {mfi_val:.0f} confirma fluxul in directia rupturii")
    else:
        warnings.append(f"MFI {mfi_val:.0f} nu sustine directia - miscare fara bani in spate")

    # --- CCI depaseste pragul clasic de trend (10p)
    cci_val = float(last["cci"]) if pd.notna(last["cci"]) else 0.0
    if (is_long and cci_val > 100) or (not is_long and cci_val < -100):
        score += 10
        reasons.append(f"CCI {cci_val:+.0f} - iesire din intervalul de consolidare")

    # --- divergenta = avertisment, exact invers fata de reversie (10p)
    opposite: Literal["bullish", "bearish"] = "bearish" if is_long else "bullish"
    d = oscillators.find_divergence(
        df, osc_col="rsi", kind=opposite,
        left=cfg.pivot_left, right=cfg.pivot_right,
        lookback=cfg.div_lookback, min_bars_apart=cfg.div_min_bars_apart,
        max_bars_since=cfg.div_max_bars_since, min_osc_gap=cfg.div_min_osc_gap,
    )
    if d is None:
        score += 10
    else:
        divs.append(d)
        warnings.append(
            "Divergenta CONTRA rupturii - se rupe cu mai putina forta decat miscarea anterioara"
        )

    return score, reasons, warnings, divs


# ------------------------------------------------------------------ economie
def roundtrip_cost_pct(app_cfg, scfg) -> float:
    """
    Costul dus-intors, ca fractiune din pret.

    Intrare limit post-only: platim maker si NU platim slippage - noi suntem
    lichiditatea. Iesire pe stop sau TP: in cel mai rau caz taker plus slippage,
    si asa o modelam. Optimismul aici se plateste mai tarziu, in bani reali.
    """
    if getattr(scfg, "entry_order_type", "market") == "limit_post_only":
        entry_cost = app_cfg.maker_fee
    else:
        entry_cost = app_cfg.taker_fee + app_cfg.slippage
    return entry_cost + app_cfg.taker_fee + app_cfg.slippage


def _time_feasibility(r_distance: float, atr_val: float, bars: int) -> float:
    """
    Cat de realista e tinta in bugetul de timp.

    Excursia asteptata creste cu RADACINA numarului de lumanari (drum aleator),
    nu liniar: ~ATR x sqrt(bare). Sub 1.0 = confortabil. Peste 1.5 = ceri
    pietei un eveniment rar in mod repetat, adica te bazezi pe noroc ca sistem.
    """
    if atr_val <= 0 or bars <= 0:
        return float("inf")
    return float(r_distance / (atr_val * np.sqrt(bars)))


def _context_bias(ctx: pd.DataFrame) -> tuple[Side | None, str]:
    """
    Ce directii sunt permise, dupa timeframe-ul mare.

    Nuanta care lipseste din majoritatea implementarilor: intr-un trend
    crescator NU vinzi sweep-ul unui maxim. Sweep-ul care se plateste e cel al
    minimelor - flush-ul care scoate long-urile slabe inainte de continuare.
    Deci bias-ul nu filtreaza directia sweep-ului, ci directia TRADE-ului.
    """
    last = ctx.iloc[-1]
    close = float(last["close"])
    ema_fast = float(last["ema_fast"])
    ema_slow = float(last["ema_slow"])
    adx_val = float(last["adx"])

    if adx_val < 20:
        return None, f"Context in range (ADX {adx_val:.1f}) - ambele directii permise"
    if close > ema_slow and ema_fast > ema_slow:
        return "long", f"Context bullish (ADX {adx_val:.1f}) - doar long"
    if close < ema_slow and ema_fast < ema_slow:
        return "short", f"Context bearish (ADX {adx_val:.1f}) - doar short"
    return None, "Context neutru - structura EMA amestecata"


# --------------------------------------------------------------------- public
def warmup_bars(cfg) -> int:
    """Cate lumanari de executie sunt necesare inainte de prima evaluare."""
    return max(
        cfg.ema_slow,
        cfg.liquidity_lookback + cfg.reclaim_within,
        cfg.div_lookback,
    ) + cfg.pivot_right + 5


def build_scalp_signal(symbol, ctx_raw, exec_raw, cfg, risk_per_trade=0.01, app_cfg=None):
    """Calea LIVE: primeste OHLCV brut, calculeaza indicatorii, cauta setup-ul."""
    return build_from_enriched(
        symbol,
        indicators.enrich(ctx_raw, cfg),
        oscillators.enrich(exec_raw, cfg),
        cfg, risk_per_trade, app_cfg,
    )


def make_backtest_fn(cfg, ctx_enriched, exec_enriched, risk_per_trade=0.01, app_cfg=None):
    """
    Adaptor pentru Backtester, construit pentru viteza si pentru onestitate.

    Motorul apeleaza functia de semnal la FIECARE lumanare. Daca la fiecare apel
    am recalcula indicatorii pe tot istoricul, costul ar fi O(n^2) - ore de
    rulare pentru un rezultat identic.

    Indicatorii se calculeaza O SINGURA DATA pe seria completa, iar aici doar se
    citeste fereastra care se termina la bara curenta. Este corect pentru ca toti
    indicatorii sunt cauzali: a calcula EMA pe toata seria si a citi pozitia i da
    exact acelasi numar ca a calcula EMA pe primele i lumanari. Pentru pivoti,
    decalajul cu `right` din pivot_flags pastreaza proprietatea - de asta e
    implementat acolo si nu aici.
    """
    window = warmup_bars(cfg) + cfg.liquidity_lookback

    def signal_fn(symbol, htf_slice, ltf_slice):
        idx = len(ltf_slice) - 1
        if idx < warmup_bars(cfg):
            return None
        df = exec_enriched.iloc[max(0, idx - window): idx + 1]
        ts = int(ltf_slice.iloc[-1]["timestamp"])
        ctx = ctx_enriched[ctx_enriched["timestamp"] <= ts]
        if len(ctx) < cfg.ema_slow:
            return None
        return build_from_enriched(symbol, ctx, df, cfg, risk_per_trade, app_cfg)

    return signal_fn


def build_from_enriched(symbol, ctx, df, cfg, risk_per_trade=0.01, app_cfg=None) -> Signal | None:
    """
    Nucleul. Presupune indicatorii deja calculati pe `ctx` (indicators.enrich) si
    pe `df` (oscillators.enrich), ambele terminate la bara curenta.
    """
    if app_cfg is None:
        import config as _config
        app_cfg = _config.CONFIG

    if len(df) < cfg.liquidity_lookback + cfg.reclaim_within + 2:
        return None
    if len(ctx) == 0:
        return None

    last = df.iloc[-1]
    atr_val = float(last["atr"])
    close_px = float(last["close"])
    if atr_val <= 0 or close_px <= 0:
        return None

    bias, bias_reason = _context_bias(ctx)
    sides: list[Side] = [bias] if bias is not None else ["long", "short"]

    # Ordinea conteaza: primul care se potriveste castiga. Sweep-ul e primul
    # pentru ca are stopul cel mai bine definit, deci R:R-ul cel mai onest.
    detectors = (_setup_sweep_reclaim, _setup_squeeze_breakout, _setup_vwap_reversion)

    candidate: SetupCandidate | None = None
    for detector in detectors:
        candidate = detector(df, ctx, cfg, sides)
        if candidate is not None:
            break
    if candidate is None:
        return None

    # --- confluenta, citita dupa tipul setup-ului
    if candidate.mode == "reversal":
        conf_score, conf_reasons, conf_warnings, divs = _confluence_reversal(df, candidate.side, cfg)
    else:
        conf_score, conf_reasons, conf_warnings, divs = _confluence_continuation(df, candidate.side, cfg)

    score = candidate.base_score + conf_score
    if score < cfg.min_setup_score:
        return None

    reasons = [bias_reason, *candidate.reasons, *conf_reasons]
    warnings = [*candidate.warnings, *conf_warnings]

    # --- intrarea: limit la retest, nu la piata.
    # Post-only inseamna comision de maker si zero slippage la intrare; pentru
    # un scalp cu R mic in pret, asta e diferenta dintre viabil si hrana pentru
    # exchange. Setup-urile cer retestul oricum.
    if candidate.side == "long":
        limit_entry = min(candidate.limit_ref, close_px)
        entry_is_taker = limit_entry >= close_px
    else:
        limit_entry = max(candidate.limit_ref, close_px)
        entry_is_taker = limit_entry <= close_px

    buffer = cfg.stop_buffer_atr * atr_val
    stop_loss = candidate.stop_ref - buffer if candidate.side == "long" else candidate.stop_ref + buffer

    r_value = abs(limit_entry - stop_loss)
    if r_value <= 0:
        return None

    stop_pct = r_value / limit_entry
    if not (cfg.min_stop_distance_pct <= stop_pct <= cfg.max_stop_distance_pct):
        return None

    # --- poarta de cost
    cost_pct = roundtrip_cost_pct(app_cfg, cfg)
    cost_r = cost_pct / stop_pct
    if cost_r > cfg.max_cost_r:
        return None

    sign = 1 if candidate.side == "long" else -1
    take_profits = [
        round(limit_entry + sign * mult * r_value, 8) for mult in cfg.tp_r_multiples
    ]

    # Unele setup-uri au o tinta naturala (VWAP). Nu cerem pietei mai mult decat
    # promite teza: daca teza e "revine la medie", tinta este media.
    if candidate.tp1_cap is not None:
        capped = candidate.tp1_cap
        if candidate.side == "long":
            take_profits[0] = round(min(take_profits[0], capped), 8)
        else:
            take_profits[0] = round(max(take_profits[0], capped), 8)

    rr = abs(take_profits[0] - limit_entry) / r_value
    rr_net = rr - cost_r  # singurul R:R care conteaza
    if rr_net < cfg.min_risk_reward:
        return None

    feasibility = _time_feasibility(
        abs(take_profits[0] - limit_entry), atr_val, cfg.max_bars_in_trade
    )
    if feasibility > 1.5:
        warnings.append(
            f"TP1 cere {feasibility:.2f}x excursia asteptata in "
            f"{cfg.max_bars_in_trade} lumanari - putin probabil in buget"
        )
    else:
        reasons.append(
            f"TP1 la {feasibility:.2f}x excursia asteptata in "
            f"{cfg.max_bars_in_trade} lumanari - incape in buget"
        )

    if entry_is_taker:
        warnings.append(
            "Pretul a plecat deja de la nivel - limita cade pe pretul curent, "
            "deci intrarea va fi taker si costul real e mai mare decat cel calculat"
        )

    # Leverage-ul NU se alege; rezulta din cat de stramt e stopul.
    implied_leverage = risk_per_trade / stop_pct

    ctx_out = {
        "setup": candidate.name,
        "mode": candidate.mode,
        "exec_timeframe": cfg.exec_tf,
        "context_timeframe": cfg.context_tf,
        "max_bars_in_trade": cfg.max_bars_in_trade,
        "entry_order_type": cfg.entry_order_type,
        "limit_valid_bars": cfg.limit_valid_bars,
        "last_close": round(close_px, 8),
        "base_score": round(candidate.base_score, 1),
        "confluence_score": round(conf_score, 1),
        "divergences": [d.osc_name for d in divs],
        "stop_distance_pct": round(stop_pct, 5),
        # Economia trade-ului, dupa costuri.
        "roundtrip_cost_pct": round(cost_pct, 5),
        "cost_r": round(cost_r, 3),
        "risk_reward_gross": round(rr, 2),
        "risk_reward_net": round(rr_net, 2),
        "implied_leverage": round(implied_leverage, 2),
        "time_feasibility": round(feasibility, 3),
        "atr": round(atr_val, 8),
        "atr_pct": round(atr_val / close_px, 5),
        "rsi": round(float(last["rsi"]), 2) if pd.notna(last["rsi"]) else None,
        "stoch_k": round(float(last["stoch_k"]), 2) if pd.notna(last["stoch_k"]) else None,
        "mfi": round(float(last["mfi"]), 2) if pd.notna(last["mfi"]) else None,
        "williams_r": round(float(last["williams_r"]), 2) if pd.notna(last["williams_r"]) else None,
        "cci": round(float(last["cci"]), 2) if pd.notna(last["cci"]) else None,
        "macd_hist": round(float(last["macd_hist"]), 8) if pd.notna(last["macd_hist"]) else None,
        "bb_pct_b": round(float(last["bb_pct_b"]), 3) if pd.notna(last["bb_pct_b"]) else None,
        "vwap": round(float(last["vwap"]), 8) if "vwap" in last and pd.notna(last["vwap"]) else None,
        "vwap_z": round(float(last["vwap_z"]), 2) if "vwap_z" in last and pd.notna(last["vwap_z"]) else None,
        "candle_time": str(last["datetime"]),
    }
    ctx_out.update(candidate.context)

    return Signal(
        symbol=symbol,
        side=candidate.side,
        entry=round(limit_entry, 8),
        stop_loss=round(stop_loss, 8),
        take_profits=take_profits,
        score=round(score, 1),
        reasons=reasons,
        warnings=warnings,
        context=ctx_out,
    )
