"""
Baleiaj empiric: ce rata de succes si ce expectancy dau diferite tinte.

    python tools\\tp_sweep.py
    python tools\\tp_sweep.py --candles 15000 --tp 1.5 2.0 2.5 3.0 4.0
    python tools\\tp_sweep.py --bars 24 36 48

Nu are nevoie de chei API.

DE CE EXISTA:

Rata de succes si R:R nu se pot alege independent. Sunt doua fete ale aceleiasi
decizii - cat de departe pui tinta. Tinta aproape = castigi des, putin. Tinta
departe = castigi rar, mult. Ce le leaga:

    rata de break-even = (1 + cost_R) / (1 + R:R brut)

Ce NU se poate obtine este rata mare de succes SI R:R mare in acelasi timp. Daca
ar exista, ar fi bani gratis, si ar fi fost deja luati.

Ce se poate obtine este punctul optim de pe curba pentru un setup anume, iar
acela nu se ghiceste - se masoara. Unealta asta ruleaza acelasi backtest pe o
grila de tinte si bugete de timp si arata unde e maximul de expectancy si unde
e maximul de rata de succes. De obicei nu sunt in acelasi loc, si atunci alegi
in cunostinta de cauza, nu din preferinta.

ATENTIE la felul in care se citeste rezultatul: o rata de succes de 75% cu
expectancy negativa este mai proasta decat 35% cu expectancy pozitiva. Contul
creste din expectancy, nu din numarul de trade-uri castigate. Coloana care
decide este 'expect', nu 'WR'.

Si un avertisment onest despre ce este acest baleiaj: alegand cea mai buna
combinatie din grila pe ACELEASI date, o supraestimezi. De aceea rezultatul de
aici este o IPOTEZA, nu o concluzie. Confirmarea se face ruland
`python -m backtest.validate` cu valorile alese, pe o perioada diferita.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import config as cfg
from backtest.engine import Backtester
from exchange.bingx_client import BingXClient
from strategy import indicators, oscillators, scalp_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("sweep")

C = cfg.CONFIG


def main() -> int:
    p = argparse.ArgumentParser(description="Baleiaj tinte x buget de timp")
    p.add_argument("--symbol", action="append")
    p.add_argument("--candles", type=int, default=12000)
    p.add_argument("--tp", type=float, nargs="+",
                   default=[1.5, 2.0, 2.5, 3.0, 4.0])
    p.add_argument("--bars", type=int, nargs="+", default=[24, 36, 48])
    p.add_argument("--equity", type=float, default=1000.0)
    args = p.parse_args()

    symbols = args.symbol or list(C.market.symbols)
    base = C.scalp

    client = BingXClient()
    client.load_markets()

    # Datele si indicatorii se calculeaza O SINGURA DATA pentru toata grila.
    # Indicatorii nu depind de tinta sau de buget, deci ar fi risipa sa ii
    # recalculam pentru fiecare combinatie.
    data: dict[str, tuple] = {}
    for symbol in symbols:
        if not client.market_exists(symbol):
            log.error("Simbol inexistent: %s", symbol)
            continue
        log.info("Aduc date pentru %s...", symbol)
        ctx_raw = client.fetch_ohlcv_history(symbol, base.context_tf,
                                             int(args.candles / 12) + 500)
        exec_raw = client.fetch_ohlcv_history(symbol, base.exec_tf, args.candles)
        ctx_raw = ctx_raw.sort_values("timestamp").reset_index(drop=True)
        exec_raw = exec_raw.sort_values("timestamp").reset_index(drop=True)
        data[symbol] = (
            ctx_raw, exec_raw,
            indicators.enrich(ctx_raw, base),
            oscillators.enrich(exec_raw, base),
        )

    if not data:
        print("Nicio serie de date. Verifica reteaua.")
        return 1

    period = str(next(iter(data.values()))[1].iloc[0]["datetime"])[:10]
    period_end = str(next(iter(data.values()))[1].iloc[-1]["datetime"])[:10]

    print()
    print("=" * 92)
    print(f"  BALEIAJ TINTA x BUGET   |   {period} -> {period_end}   |   "
          f"{len(data)} simboluri, {args.candles} lumanari {base.exec_tf}")
    print("=" * 92)
    print(f"  {'TP1':>5} {'buget':>7} {'trades':>7} {'WR':>7} {'expect':>9} "
          f"{'PF':>6} {'cost_R':>7} {'WR break-even':>14} {'verdict':>10}")
    print("  " + "-" * 88)

    rows = []
    warmup = scalp_signal.warmup_bars(base)

    # --- pre-generarea semnalelor, o singura data per simbol.
    #
    # Optimizarea care face unealta utilizabila: detectarea setup-urilor si
    # scorul de confluenta NU depind de tinta sau de bugetul de timp. Doar
    # IESIRILE depind. Fara pasul asta, o grila de 6 tinte x 3 bugete x 3
    # simboluri inseamna 54 de rulari complete - peste o ora. Cu el, calculul
    # scump se face de 3 ori, iar restul e doar simulare de iesiri.
    #
    # `min_risk_reward=0` la generare: lasam sa treaca tot, iar filtrul pe R:R
    # net se aplica mai jos, per combinatie, unde chiar difera.
    log.info("Generez semnalele o singura data (partea scumpa)...")
    permissive = dataclasses.replace(base, min_risk_reward=0.0)
    cached: dict[str, dict[int, object]] = {}

    gen_window = warmup + base.liquidity_lookback
    for symbol, (ctx_raw, exec_raw, ctx_e, exec_e) in data.items():
        found: dict[int, object] = {}
        for idx in range(warmup, len(exec_e)):
            # Fereastra marginita, nu tot istoricul - altfel copiem O(n^2) randuri.
            df = exec_e.iloc[max(0, idx - gen_window): idx + 1]
            ts = int(exec_e.iloc[idx]["timestamp"])
            ctx = ctx_e[ctx_e["timestamp"] <= ts]
            if len(ctx) < base.ema_slow:
                continue
            sig = scalp_signal.build_from_enriched(
                symbol, ctx, df, permissive, C.risk.risk_per_trade, C
            )
            if sig is not None:
                found[idx] = sig
        cached[symbol] = found
        log.info("  %s: %d semnale brute", symbol, len(found))

    def make_cached_fn(symbol: str, tp1: float, min_rr: float):
        """Reasaza tintele pe semnalele deja gasite si aplica filtrul de R:R net."""
        store = cached[symbol]

        def fn(_sym, _htf, ltf_slice):
            sig = store.get(len(ltf_slice) - 1)
            if sig is None:
                return None

            r = abs(sig.entry - sig.stop_loss)
            if r <= 0:
                return None
            cost_r = sig.context.get("cost_r", 0.0)
            if tp1 - cost_r < min_rr:
                return None

            sign = 1 if sig.side == "long" else -1
            clone = dataclasses.replace(
                sig,
                take_profits=[
                    round(sig.entry + sign * tp1 * r, 8),
                    round(sig.entry + sign * tp1 * 1.75 * r, 8),
                ],
            )
            return clone

        return fn

    for bars in args.bars:
        for tp1 in args.tp:
            # TP2 proportional, ca sa nu introducem inca o variabila in grila.
            scfg = dataclasses.replace(
                base,
                tp_r_multiples=(tp1, tp1 * 1.75),
                max_bars_in_trade=bars,
                # Filtrul pe R:R net trebuie sa lase tinta sa fie testata; altfel
                # tintele mici sunt respinse inainte sa aiba ocazia sa arate ceva.
                min_risk_reward=0.5,
            )

            all_trades = []
            for symbol, (ctx_raw, exec_raw, ctx_e, exec_e) in data.items():
                fn = make_cached_fn(symbol, tp1, min_rr=0.5)
                bt = Backtester(
                    C, signal_fn=fn, strategy_cfg=scfg, min_bars=warmup,
                    max_bars_in_trade=bars,
                    limit_valid_bars=(
                        scfg.limit_valid_bars
                        if scfg.entry_order_type == "limit_post_only" else None
                    ),
                )
                rep = bt.run(symbol, ctx_raw, exec_raw, starting_equity=args.equity)
                all_trades.extend(rep.trades)

            n = len(all_trades)
            if n == 0:
                print(f"  {tp1:>5.1f} {bars:>7} {0:>7}   - fara trades")
                continue

            r_vals = [t.r_multiple for t in all_trades]
            wins = [r for r in r_vals if r > 0]
            gains = sum(wins)
            losses = -sum(r for r in r_vals if r < 0)

            wr = len(wins) / n
            expect = sum(r_vals) / n
            pf = gains / losses if losses > 0 else float("inf")

            cost_pct = scalp_signal.roundtrip_cost_pct(C, scfg)
            # cost_R mediu observat, din distantele reale de stop.
            stops = [abs(t.entry - t.stop_loss) / t.entry for t in all_trades]
            avg_stop = sum(stops) / len(stops)
            cost_r = cost_pct / avg_stop if avg_stop > 0 else 0.0
            be_wr = (1 + cost_r) / (1 + tp1)

            verdict = "POZITIV" if expect > 0.15 else ("marginal" if expect > 0 else "")

            rows.append({
                "tp1": tp1, "bars": bars, "n": n, "wr": wr,
                "expect": expect, "pf": pf, "be_wr": be_wr,
            })
            print(f"  {tp1:>5.1f} {bars:>7} {n:>7} {wr:>6.1%} {expect:>+8.3f}R "
                  f"{pf:>6.2f} {cost_r:>7.2f} {be_wr:>13.1%} {verdict:>10}")

    if not rows:
        return 1

    df = pd.DataFrame(rows)
    best_exp = df.loc[df["expect"].idxmax()]
    best_wr = df.loc[df["wr"].idxmax()]

    print()
    print("=" * 92)
    print("  CONCLUZII")
    print("=" * 92)
    print(f"  Expectancy maxima : TP1={best_exp.tp1:.1f}R  buget={int(best_exp.bars)} "
          f"lumanari  ->  {best_exp.expect:+.3f}R  la {best_exp.wr:.1%} rata de succes")
    print(f"  Rata max de succes: TP1={best_wr.tp1:.1f}R  buget={int(best_wr.bars)} "
          f"lumanari  ->  {best_wr.wr:.1%}  cu expectancy {best_wr.expect:+.3f}R")
    print()
    if best_wr.expect <= 0:
        print("  Atentie: configuratia cu cea mai buna rata de succes are expectancy")
        print("  NEGATIVA. Ai castiga mai des si ai pierde bani mai sigur. Contul")
        print("  creste din expectancy, nu din numarul de trade-uri castigate.")
    print()
    print("  Aceste cifre sunt o IPOTEZA, nu o concluzie: alegand cea mai buna")
    print("  combinatie din grila pe aceleasi date, o supraestimezi. Confirma cu:")
    print("      python -m backtest.validate")
    print("  dupa ce pui valorile alese in ScalpConfig, pe o perioada diferita.")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    sys.exit(main())
