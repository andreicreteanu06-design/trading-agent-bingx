"""
Diagnostic: arata DE CE nu exista semnal pe fiecare simbol.

Fara asta, "niciun setup valabil" e o cutie neagra - nu stii daca piata chiar e
plictisitoare sau daca ti s-a stricat un indicator si returneaza NaN.

    python tools/diagnose.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg
from exchange.bingx_client import BingXClient
from strategy import indicators, signal_builder

C = cfg.CONFIG


def main() -> None:
    client = BingXClient(cfg.BINGX_API_KEY, cfg.BINGX_SECRET_KEY)
    client.load_markets()

    for symbol in C.market.symbols:
        print("\n" + "=" * 70)
        print(f"  {symbol}")
        print("=" * 70)

        htf_raw = client.fetch_ohlcv(symbol, C.market.htf, C.market.candles)
        ltf_raw = client.fetch_ohlcv(symbol, C.market.ltf, C.market.candles)

        htf = indicators.enrich(htf_raw, C.strategy)
        ltf = indicators.enrich(ltf_raw, C.strategy)
        h, l = htf.iloc[-1], ltf.iloc[-1]

        print(f"  Lumanari: HTF {len(htf)} | LTF {len(ltf)}")
        print(f"  Ultima lumanare LTF: {l['datetime']}")

        print(f"\n  --- HTF ({C.market.htf}) ---")
        print(f"  close     {h['close']:.4f}")
        print(f"  EMA50     {h['ema_fast']:.4f}")
        print(f"  EMA200    {h['ema_slow']:.4f}")

        bias, reason = signal_builder._htf_bias(htf)
        print(f"  Bias      {bias or 'NEUTRU'}  ->  {reason}")

        print(f"\n  --- LTF ({C.market.ltf}) ---")
        print(f"  close     {l['close']:.4f}")
        print(f"  EMA50     {l['ema_fast']:.4f}")
        print(f"  RSI       {l['rsi']:.2f}")
        print(f"  ATR       {l['atr']:.4f}   ({l['atr_pct']:.3%} din pret)")
        print(f"  ADX       {l['adx']:.2f}   (+DI {l['plus_di']:.1f} / -DI {l['minus_di']:.1f})")
        print(f"  Volum     {l['volume_ratio']:.2f}x fata de medie")

        print("\n  --- Filtre ---")
        atr_ok = C.risk.min_atr_pct <= l["atr_pct"] <= C.risk.max_atr_pct
        adx_ok = l["adx"] >= C.risk.min_adx
        print(f"  {'PASS' if bias else 'FAIL'}  bias HTF definit")
        print(
            f"  {'PASS' if atr_ok else 'FAIL'}  ATR% in interval "
            f"[{C.risk.min_atr_pct:.2%}, {C.risk.max_atr_pct:.2%}]"
        )
        print(f"  {'PASS' if adx_ok else 'FAIL'}  ADX >= {C.risk.min_adx}")

        if bias and atr_ok and adx_ok:
            score, reasons, warns = signal_builder._score_setup(l, bias, C.strategy)
            verdict = "PASS" if score >= C.strategy.min_setup_score else "FAIL"
            print(f"  {verdict}  scor {score:.0f} >= {C.strategy.min_setup_score:.0f}")
            for r in reasons:
                print(f"        + {r}")
            for w in warns:
                print(f"        ! {w}")

        sig = signal_builder.build_signal(symbol, htf_raw, ltf_raw, C.strategy, C.risk)
        print(f"\n  REZULTAT: {'SEMNAL ' + sig.side.upper() if sig else 'fara semnal'}")


if __name__ == "__main__":
    main()
