"""
Indicatori tehnici, implementati in pandas curat (fara ta-lib, care are nevoie
de compilare C pe Windows).

Toate mediile folosesc netezirea Wilder (alpha = 1/period) acolo unde definitia
originala o cere - RSI, ATR, ADX. Diferenta fata de o EMA clasica e mica pe
grafic, dar suficient de mare cat sa mute un stop.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = _wilder(gain, period)
    avg_loss = _wilder(loss, period)

    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))

    # Cazuri-limita: fara pierderi in fereastra -> 100; fara castiguri -> 0.
    out = out.mask(avg_loss == 0.0, 100.0)
    out = out.mask((avg_gain == 0.0) & (avg_loss > 0.0), 0.0)
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _wilder(true_range(df), period)


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Returneaza un DataFrame cu coloanele adx, plus_di, minus_di."""
    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index
    )

    atr_val = _wilder(true_range(df), period).replace(0.0, np.nan)
    plus_di = 100.0 * _wilder(plus_dm, period) / atr_val
    minus_di = 100.0 * _wilder(minus_dm, period) / atr_val

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum

    return pd.DataFrame(
        {
            "adx": _wilder(dx.fillna(0.0), period),
            "plus_di": plus_di.fillna(0.0),
            "minus_di": minus_di.fillna(0.0),
        }
    )


def swing_levels(df: pd.DataFrame, lookback: int) -> tuple[float, float]:
    """Cel mai recent swing high si swing low din ultimele `lookback` lumanari."""
    window = df.iloc[-lookback:]
    return float(window["high"].max()), float(window["low"].min())


def enrich(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Adauga toate coloanele de indicatori pe un DataFrame OHLCV."""
    out = df.copy()

    out["ema_fast"] = ema(out["close"], cfg.ema_fast)
    out["ema_slow"] = ema(out["close"], cfg.ema_slow)
    out["rsi"] = rsi(out["close"], cfg.rsi_period)
    out["atr"] = atr(out, cfg.atr_period)
    out["atr_pct"] = out["atr"] / out["close"]

    adx_df = adx(out, cfg.adx_period)
    out["adx"] = adx_df["adx"]
    out["plus_di"] = adx_df["plus_di"]
    out["minus_di"] = adx_df["minus_di"]

    out["volume_ma"] = out["volume"].rolling(cfg.volume_ma_period).mean()
    out["volume_ratio"] = out["volume"] / out["volume_ma"]

    return out
