"""
Oscilatori, benzi de volatilitate si detectie de divergente.

De ce un fisier separat de indicators.py: acolo stau indicatorii de TREND si de
STRUCTURA (EMA, ATR, ADX, swing-uri) - cei care raspund la "incotro merge
piata". Aici stau cei care raspund la "cat de intinsa e miscarea curenta si
cine oboseste" - momentum normalizat, exces fata de valoarea medie, comprimarea
volatilitatii.

Distinctia conteaza pentru un agent care tine pozitii sub o ora. Pe orizontul
asta directia mare e aproape zgomot; ce plateste este epuizarea, capcana si
revenirea la valoare.

TOATE functiile sunt CAUZALE - valoarea de la indexul i foloseste exclusiv date
de la indecsii <= i. Singura exceptie este detectorul de pivoti, care prin
definitie are nevoie de lumanari in viitor ca sa confirme un varf. De aceea nu
returneaza niciodata pivotul la bara lui, ci decalat cu `right` lumanari: un
pivot format la bara i devine CUNOSCUT abia la i + right. Fara decalajul asta
backtestul ar arata superb si contul ar arata invers - este cea mai frecventa
forma de lookahead din strategiile bazate pe divergente.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from strategy import indicators

Kind = Literal["high", "low"]


# --------------------------------------------------------------- momentum pur
def stoch_rsi(
    close: pd.Series,
    rsi_period: int = 14,
    stoch_period: int = 14,
    k_smooth: int = 3,
    d_smooth: int = 3,
) -> pd.DataFrame:
    """
    Stochastic RSI - unde se afla RSI-ul in propriul lui interval recent.

    RSI simplu e prea lent ca sa cronometreze o intrare pe 5m: sta in zona
    45-60 ore intregi. Aplicand stochastic peste el obtinem un oscilator care
    atinge extremele des si se intoarce repede - potrivit pentru declansator,
    nepotrivit pentru filtru de directie.
    """
    r = indicators.rsi(close, rsi_period)

    lo = r.rolling(stoch_period).min()
    hi = r.rolling(stoch_period).max()
    span = (hi - lo).replace(0.0, np.nan)

    raw = 100.0 * (r - lo) / span
    # RSI perfect plat pe toata fereastra: nu e nici extrem, nici tendinta.
    raw = raw.mask(span.isna() & hi.notna(), 50.0)

    k = raw.rolling(k_smooth).mean()
    d = k.rolling(d_smooth).mean()
    return pd.DataFrame({"stoch_k": k, "stoch_d": d})


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """
    MACD clasic. Ne intereseaza aproape exclusiv histograma.

    Linia MACD spune unde am fost; histograma spune daca acceleram sau franam.
    Pentru o pozitie de sub o ora, doar a doua informatie are valoare.
    """
    line = indicators.ema(close, fast) - indicators.ema(close, slow)
    sig = indicators.ema(line, signal)
    return pd.DataFrame(
        {"macd": line, "macd_signal": sig, "macd_hist": line - sig}
    )


def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Commodity Channel Index (Lambert).

    Atentie la definitie: numitorul este deviatia medie ABSOLUTA, nu deviatia
    standard. Multe implementari gresesc aici si obtin valori cu ~20% mai mici,
    ceea ce muta pragurile clasice de +/-100.
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(
        lambda x: np.abs(x - x.mean()).mean(), raw=True
    )
    return (tp - sma) / (0.015 * mad.replace(0.0, np.nan))


def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Williams %R: 0 = inchidere la maximul perioadei, -100 = la minim."""
    hh = df["high"].rolling(period).max()
    ll = df["low"].rolling(period).min()
    span = (hh - ll).replace(0.0, np.nan)
    return -100.0 * (hh - df["close"]) / span


def mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Money Flow Index - RSI ponderat cu volum.

    Valoros exact acolo unde RSI minte: intr-un squeeze de lichiditate pretul
    se misca mult cu volum mic. RSI vede momentum, MFI vede ca nu participa
    nimeni. Divergenta dintre ele este semnalul, nu fiecare in parte.
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    flow = tp * df["volume"]
    delta = tp.diff()

    pos = flow.where(delta > 0, 0.0).rolling(period).sum()
    neg = flow.where(delta < 0, 0.0).rolling(period).sum()

    ratio = pos / neg.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + ratio))

    out = out.mask(neg == 0.0, 100.0)
    out = out.mask((pos == 0.0) & (neg > 0.0), 0.0)
    return out


# ------------------------------------------------------- benzi de volatilitate
def bollinger(
    close: pd.Series, period: int = 20, mult: float = 2.0
) -> pd.DataFrame:
    """Benzi Bollinger + %B (pozitia in banda) si bandwidth (latimea relativa)."""
    mid = close.rolling(period).mean()
    # ddof=0: deviatia populatiei, cum o defineste Bollinger.
    sd = close.rolling(period).std(ddof=0)

    upper = mid + mult * sd
    lower = mid - mult * sd
    span = (upper - lower).replace(0.0, np.nan)

    return pd.DataFrame(
        {
            "bb_mid": mid,
            "bb_upper": upper,
            "bb_lower": lower,
            "bb_pct_b": (close - lower) / span,
            "bb_bandwidth": span / mid.replace(0.0, np.nan),
        }
    )


def keltner(
    df: pd.DataFrame, period: int = 20, atr_period: int = 10, mult: float = 1.5
) -> pd.DataFrame:
    """Canale Keltner - aceeasi idee ca Bollinger, dar latimea vine din ATR."""
    mid = indicators.ema(df["close"], period)
    rng = indicators.atr(df, atr_period)
    return pd.DataFrame(
        {"kc_mid": mid, "kc_upper": mid + mult * rng, "kc_lower": mid - mult * rng}
    )


def squeeze_on(bb: pd.DataFrame, kc: pd.DataFrame) -> pd.Series:
    """
    Squeeze in sensul lui Carter: Bollinger INTRA complet in Keltner.

    Bollinger reactioneaza la deviatia standard, Keltner la ATR. Cand cea dintai
    se strange sub cea din urma, piata a incetat sa se mai deplaseze fata de cat
    de mult se agita - energie acumulata. Nu spune directia, doar ca urmeaza o
    expansiune. Directia o dau alte componente.
    """
    return (bb["bb_lower"] > kc["kc_lower"]) & (bb["bb_upper"] < kc["kc_upper"])


# ------------------------------------------------------------------ valoare
def session_vwap(df: pd.DataFrame, reset: str = "D") -> pd.DataFrame:
    """
    VWAP ancorat pe sesiune, cu benzi de deviatie standard ponderata cu volum.

    VWAP este pretul mediu la care s-a schimbat efectiv marfa in sesiune. Deskurile
    care trebuie sa execute volum mare sunt evaluate fata de el, deci se comporta
    ca un magnet real, nu ca o linie desenata. Pentru scalping e cel mai util
    reper unic: `vwap_z` spune de cate deviatii standard e intins pretul fata de
    valoarea consensuala a zilei.

    `reset` este o eticheta de frecventa pandas ("D" = zi UTC, "W" = saptamana).
    """
    out = pd.DataFrame(index=df.index)

    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"]
    bucket = pd.to_datetime(df["datetime"]).dt.floor(reset)

    cum_v = vol.groupby(bucket).cumsum().replace(0.0, np.nan)
    cum_pv = (tp * vol).groupby(bucket).cumsum()
    cum_pv2 = (tp * tp * vol).groupby(bucket).cumsum()

    vwap = cum_pv / cum_v
    # Varianta ponderata: E[x^2] - E[x]^2. Clip pentru eroare de rotunjire.
    var = (cum_pv2 / cum_v) - vwap * vwap
    sd = np.sqrt(var.clip(lower=0.0))

    out["vwap"] = vwap
    out["vwap_sd"] = sd
    out["vwap_z"] = (df["close"] - vwap) / sd.replace(0.0, np.nan)
    return out


# -------------------------------------------------------------------- pivoti
def pivot_flags(series: pd.Series, left: int, right: int, kind: Kind) -> pd.Series:
    """
    Marcheaza pivotii, DECALAT cu `right` bare ca sa ramana cauzal.

    Rezultatul de la bara j este True daca bara (j - right) a fost un pivot.
    Adica: exact momentul in care un trader real ar fi putut sti asta.
    """
    cond = pd.Series(True, index=series.index)

    if kind == "high":
        for i in range(1, left + 1):
            cond &= series > series.shift(i)
        for i in range(1, right + 1):
            cond &= series > series.shift(-i)
    else:
        for i in range(1, left + 1):
            cond &= series < series.shift(i)
        for i in range(1, right + 1):
            cond &= series < series.shift(-i)

    return cond.shift(right).fillna(False).astype(bool)


@dataclass(frozen=True)
class Pivot:
    """Un pivot confirmat: unde era, cat valora pretul si oscilatorul acolo."""

    pos: int  # pozitia intreaga in DataFrame (iloc)
    price: float
    osc: float


def recent_pivots(
    df: pd.DataFrame,
    price_col: str,
    osc_col: str,
    left: int,
    right: int,
    kind: Kind,
    lookback: int,
    max_count: int = 3,
) -> list[Pivot]:
    """
    Ultimii pivoti CONFIRMATI pana la ultima bara, cei mai recenti primii.

    `lookback` limiteaza cautarea la ultimele N bare - un pivot de acum 400 de
    lumanari nu mai spune nimic despre momentul curent.
    """
    if len(df) == 0:
        return []

    window = df.iloc[-lookback:] if lookback < len(df) else df
    flags = pivot_flags(window[price_col], left, right, kind)

    base = len(df) - len(window)
    found: list[Pivot] = []

    # Mergem inapoi: cele mai recente confirmari conteaza cel mai mult.
    for offset in range(len(window) - 1, -1, -1):
        if not bool(flags.iloc[offset]):
            continue

        pivot_offset = offset - right  # bara reala a pivotului
        if pivot_offset < 0:
            continue

        row = window.iloc[pivot_offset]
        osc_val = row.get(osc_col)
        if osc_val is None or pd.isna(osc_val):
            continue

        found.append(
            Pivot(
                pos=base + pivot_offset,
                price=float(row[price_col]),
                osc=float(osc_val),
            )
        )
        if len(found) >= max_count:
            break

    return found


@dataclass(frozen=True)
class Divergence:
    """O divergenta gasita intre pret si un oscilator."""

    kind: Literal["bullish", "bearish"]
    osc_name: str
    price_prev: float
    price_last: float
    osc_prev: float
    osc_last: float
    bars_apart: int
    bars_since: int  # de cate bare s-a confirmat al doilea pivot

    def describe(self) -> str:
        directie = "minim" if self.kind == "bullish" else "maxim"
        sens = "mai jos" if self.kind == "bullish" else "mai sus"
        contra = "mai sus" if self.kind == "bullish" else "mai jos"
        return (
            f"Divergenta {self.kind} pe {self.osc_name}: pretul face un {directie} "
            f"{sens} ({self.price_prev:.6g} -> {self.price_last:.6g}), "
            f"oscilatorul face un {directie} {contra} "
            f"({self.osc_prev:.2f} -> {self.osc_last:.2f})"
        )


def find_divergence(
    df: pd.DataFrame,
    osc_col: str,
    kind: Literal["bullish", "bearish"],
    left: int = 3,
    right: int = 3,
    lookback: int = 80,
    min_bars_apart: int = 4,
    max_bars_since: int = 6,
    min_osc_gap: float = 2.0,
) -> Divergence | None:
    """
    Cauta o divergenta regulata intre ultimii doi pivoti de acelasi tip.

    Bullish: pretul face un minim MAI JOS, oscilatorul un minim MAI SUS.
    Vanzarea a impins pretul mai adanc, dar cu mai putina forta - epuizare.

    Bearish: simetric, pe maxime.

    Filtrele nu sunt cosmetice, fiecare taie o clasa de fals pozitiv:
      - `min_bars_apart`: doi pivoti lipiti sunt aceeasi miscare vazuta de doua
        ori, nu doua incercari.
      - `max_bars_since`: o divergenta confirmata acum 30 de bare a fost deja
        tranzactionata de altii. Pentru orizont sub o ora ne trebuie proaspata.
      - `min_osc_gap`: fara un prag, orice diferenta de 0.1 puncte trece drept
        divergenta si semnalul devine zgomot.
    """
    price_col = "low" if kind == "bullish" else "high"
    pivot_kind: Kind = "low" if kind == "bullish" else "high"

    pivots = recent_pivots(
        df,
        price_col=price_col,
        osc_col=osc_col,
        left=left,
        right=right,
        kind=pivot_kind,
        lookback=lookback,
        max_count=2,
    )
    if len(pivots) < 2:
        return None

    last, prev = pivots[0], pivots[1]

    bars_apart = last.pos - prev.pos
    if bars_apart < min_bars_apart:
        return None

    # Cat de veche e confirmarea celui de-al doilea pivot.
    bars_since = (len(df) - 1) - (last.pos + right)
    if bars_since < 0 or bars_since > max_bars_since:
        return None

    if abs(last.osc - prev.osc) < min_osc_gap:
        return None

    if kind == "bullish":
        ok = last.price < prev.price and last.osc > prev.osc
    else:
        ok = last.price > prev.price and last.osc < prev.osc

    if not ok:
        return None

    return Divergence(
        kind=kind,
        osc_name=osc_col,
        price_prev=prev.price,
        price_last=last.price,
        osc_prev=prev.osc,
        osc_last=last.osc,
        bars_apart=bars_apart,
        bars_since=bars_since,
    )


# ------------------------------------------------------------------- pipeline
def enrich(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """
    Adauga toate coloanele de oscilatori pe un DataFrame OHLCV.

    Se aplica PESTE indicators.enrich(), nu in locul lui - are nevoie de `atr`
    pentru canalele Keltner.
    """
    out = df.copy()

    if "atr" not in out.columns:
        out = indicators.enrich(out, cfg)

    st = stoch_rsi(
        out["close"],
        cfg.rsi_period,
        cfg.stoch_period,
        cfg.stoch_k,
        cfg.stoch_d,
    )
    out["stoch_k"] = st["stoch_k"]
    out["stoch_d"] = st["stoch_d"]

    mc = macd(out["close"], cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    out["macd"] = mc["macd"]
    out["macd_signal"] = mc["macd_signal"]
    out["macd_hist"] = mc["macd_hist"]

    out["cci"] = cci(out, cfg.cci_period)
    out["williams_r"] = williams_r(out, cfg.williams_period)
    out["mfi"] = mfi(out, cfg.mfi_period)

    bb = bollinger(out["close"], cfg.bb_period, cfg.bb_mult)
    for col in bb.columns:
        out[col] = bb[col]

    kc = keltner(out, cfg.bb_period, cfg.atr_period, cfg.kc_mult)
    for col in kc.columns:
        out[col] = kc[col]

    out["squeeze"] = squeeze_on(bb, kc)
    # Squeeze care tocmai s-a ELIBERAT: era strans pe bara trecuta, nu mai e.
    out["squeeze_fired"] = (~out["squeeze"]) & out["squeeze"].shift(1).fillna(False)

    if "datetime" in out.columns:
        vw = session_vwap(out, cfg.vwap_reset)
        for col in vw.columns:
            out[col] = vw[col]

    return out
