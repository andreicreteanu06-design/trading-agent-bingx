"""
Ce R:R este ARITMETIC posibil, pe date reale, inainte sa scrii o strategie.

    python tools\\feasibility.py
    python tools\\feasibility.py --symbol "SOL/USDT:USDT" --budget 60 180 240
    python tools\\feasibility.py --cost 0.0006

Nu are nevoie de chei API.

DE CE EXISTA:

Ordinea obisnuita e: scrii strategia, o backtestezi, iese prost, ajustezi
parametrii, iese tot prost, te intrebi de ce. Ordinea utila e inversa - intrebi
intai daca obiectivul este posibil, si abia apoi scrii cod pentru el.

Unealta asta raspunde la o singura intrebare, si o pune in cel mai neplacut
mod posibil:

    Ce rata de succes iti trebuie ca sa iesi pe ZERO, dupa costuri?

Aritmetica din spate, care nu se negociaza:

  - Distanta maxima pe care o poate parcurge pretul intr-un buget de timp
    creste cu RADACINA din numarul de lumanari (miscare de tip drum aleator),
    nu liniar:            M = ATR% x sqrt(bare) x toleranta
  - Stopul este un multiplu de ATR:              s = k x ATR%
  - Costurile se platesc in PRET, nu in R:       cost_R = cost% / s
  - Deci R:R brut este:                          gross = M / s

  Un castig aduce (gross - cost_R). O pierdere costa (1 + cost_R), pentru ca
  platesti comisionul si cand gresesti. De aici:

      rata de succes la break-even = (1 + cost_R) / (1 + gross)

Concluzia care iese mereu si pe care merita sa o vezi in cifre proprii:
bugetul de timp este parghia cea mai puternica. Costurile sunt fixe, iar
distanta accesibila creste cu radacina timpului - deci fiecare ora in plus
imbunatateste raportul, si o face repede la inceput.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg
from exchange.bingx_client import BingXClient
from strategy import indicators

C = cfg.CONFIG

_TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}

# Cat din excursia asteptata acceptam sa cerem pietei. Peste 1.5 ceri un
# eveniment rar in mod repetat, adica te bazezi pe noroc ca sistem.
TOLERANCE = 1.5


def breakeven_wr(gross_rr: float, cost_r: float) -> float:
    """Rata de succes care face expectancy zero, dupa costuri."""
    if gross_rr <= 0:
        return 1.0
    return (1.0 + cost_r) / (1.0 + gross_rr)


def analyse(atr_pct: float, bars: int, cost_pct: float, k: float) -> dict:
    """Economia unui trade cu stop la k x ATR si buget de `bars` lumanari."""
    stop = k * atr_pct
    reach = atr_pct * math.sqrt(bars) * TOLERANCE
    gross = reach / stop if stop > 0 else 0.0
    cost_r = cost_pct / stop if stop > 0 else float("inf")
    return {
        "k": k,
        "stop_pct": stop,
        "reach_pct": reach,
        "gross_rr": gross,
        "cost_r": cost_r,
        "net_rr": gross - cost_r,
        "breakeven_wr": breakeven_wr(gross, cost_r),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Fezabilitatea aritmetica a unui R:R")
    p.add_argument("--symbol", action="append")
    p.add_argument("--tf", action="append", help="timeframe de executie")
    p.add_argument("--budget", type=int, nargs="+", default=[60, 120, 240],
                   help="bugete de timp, in MINUTE")
    p.add_argument("--cost", type=float, default=None,
                   help="cost dus-intors ca fractiune (implicit: din config)")
    p.add_argument("--candles", type=int, default=500)
    args = p.parse_args()

    symbols = args.symbol or list(C.market.symbols)
    tfs = args.tf or ["5m", "15m", "1h"]

    if args.cost is not None:
        cost_pct = args.cost
    else:
        # Intrare maker (limit post-only) + iesire taker cu slippage.
        cost_pct = C.maker_fee + C.taker_fee + C.slippage

    client = BingXClient()
    client.load_markets()

    print()
    print("=" * 84)
    print("  FEZABILITATE ARITMETICA - ce R:R este posibil, inainte de orice strategie")
    print(f"  Cost dus-intors modelat: {cost_pct * 100:.3f}%  "
          f"(maker {C.maker_fee*100:.3f} + taker {C.taker_fee*100:.3f} + slip {C.slippage*100:.3f})")
    print(f"  Toleranta: cerem cel mult {TOLERANCE}x excursia asteptata in buget")
    print("=" * 84)

    for symbol in symbols:
        if not client.market_exists(symbol):
            print(f"\n  Simbol inexistent: {symbol}")
            continue

        print(f"\n  {symbol}")
        print("  " + "-" * 80)

        for tf in tfs:
            if tf not in _TF_MIN:
                continue
            try:
                df = client.fetch_ohlcv(symbol, tf, args.candles)
            except Exception as exc:  # noqa: BLE001
                print(f"    {tf}: eroare la date ({exc})")
                continue

            enriched = indicators.enrich(df, C.scalp)
            atr_pct = float((enriched["atr"] / enriched["close"]).dropna().median())

            print(f"    {tf}  (ATR median {atr_pct*100:.3f}% din pret)")
            print(f"      {'buget':>7} {'bare':>5} {'stop':>8} {'cost_R':>7} "
                  f"{'R:R brut':>9} {'R:R net':>8} {'WR break-even':>14}")

            for minutes in args.budget:
                bars = max(1, minutes // _TF_MIN[tf])
                # k = 1.5 ATR: destul cat sa nu te scoata zgomotul normal,
                # destul de stramt cat sa ramana R:R.
                a = analyse(atr_pct, bars, cost_pct, k=1.5)

                verdict = ""
                if a["net_rr"] < 1.0:
                    verdict = "  <- imposibil"
                elif a["breakeven_wr"] > 0.50:
                    verdict = "  <- foarte greu"
                elif a["breakeven_wr"] > 0.40:
                    verdict = "  <- marginal"

                print(f"      {minutes:>5}min {bars:>5} {a['stop_pct']*100:>7.3f}% "
                      f"{a['cost_r']:>7.2f} {a['gross_rr']:>9.2f} {a['net_rr']:>8.2f} "
                      f"{a['breakeven_wr']:>13.1%}{verdict}")

    print()
    print("=" * 84)
    print("  CUM SE CITESTE")
    print("=" * 84)
    print("  'WR break-even' = rata de succes la care nu castigi si nu pierzi nimic.")
    print("  Peste ea incepe profitul. Un setup de tip sweep/reversal are in")
    print("  practica 40-50% rata de succes, deci orice linie care cere peste 50%")
    print("  iti cere sa fii mai bun decat setup-ul respectiv este.")
    print()
    print("  Parghiile, in ordinea puterii:")
    print("   1. BUGETUL DE TIMP. Distanta accesibila creste cu sqrt(timp), iar")
    print("      costurile raman fixe. De la 60 la 240 de minute e cea mai ieftina")
    print("      imbunatatire disponibila - nu cere nicio idee noua de trading.")
    print("   2. VOLATILITATEA SIMBOLULUI. ATR% mai mare inseamna acelasi cost")
    print("      fix impartit la un R mai mare. De asta scalping-ul pe BTC e mai")
    print("      greu decat pe altcoinuri, desi pare invers.")
    print("   3. COSTURILE. Intrare limit post-only in loc de market injumatateste")
    print("      cheltuiala. Ruleaza cu --cost 0.0006 ca sa vezi un nivel VIP.")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    sys.exit(main())
