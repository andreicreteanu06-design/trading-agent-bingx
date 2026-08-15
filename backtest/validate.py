"""
Validare walk-forward pentru strategia intraday (sweep + reclaim).

    python -m backtest.validate
    python -m backtest.validate --candles 20000 --folds 5
    python -m backtest.validate --symbol "BTC/USDT:USDT" --trades

Nu are nevoie de chei API - foloseste date publice.

DE CE EXISTA acest fisier separat de backtest/run.py:

run.py raspunde la "cat a facut strategia pe perioada X". Este intrebarea
gresita, si este exact intrebarea care a produs strategia care pierde bani din
README. Orice set de reguli poate fi ajustat pana arata bine pe o perioada -
asta nu se numeste edge, se numeste memorare.

validate.py raspunde la "s-a comportat la fel pe perioade pe care nu le-am
privit cand am ales parametrii". Taie istoricul in ferestre consecutive si cere
consistenta intre ele. O strategie care merge pe 3 din 4 ferestre si pe 3 din 3
simboluri are sanse sa fie reala. Una care face tot profitul intr-o singura
fereastra a prins un regim, nu un tipar.

Pragul implementat este cel pe care il cere README-ul proiectului:
expectancy_r > +0.15 pe cel putin doua perioade diferite si pe mai multe
simboluri. Plus un test de semnificatie, pentru ca 8 tranzactii norocoase
produc statistici superbe si conturi goale.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import config as cfg
from backtest.engine import Backtester, BacktestReport
from exchange.bingx_client import BingXClient
from strategy import indicators, oscillators, scalp_signal

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S"
)
log = logging.getLogger("validate")

C = cfg.CONFIG

_TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "12h": 720, "1d": 1440,
}


def _tf_minutes(tf: str) -> int:
    if tf not in _TF_MINUTES:
        raise ValueError(f"Timeframe nesuportat: {tf}")
    return _TF_MINUTES[tf]


def _t_stat(r_multiples: list[float]) -> float:
    """
    Statistica t pentru ipoteza "expectancy este zero".

    Nu e rafinament academic: fara ea, 12 tranzactii cu +0.4R mediu arata la fel
    ca 300 de tranzactii cu +0.4R mediu, desi prima e zgomot si a doua e afacere.
    Regula practica pe care o folosesc deskurile: sub 2.0 nu discuti rezultatul.
    """
    n = len(r_multiples)
    if n < 2:
        return 0.0
    mean = sum(r_multiples) / n
    var = sum((r - mean) ** 2 for r in r_multiples) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0
    return mean / (sd / math.sqrt(n))


def _fold_label(df: pd.DataFrame, start: int, end: int) -> str:
    a = str(df.iloc[start]["datetime"])[:10]
    b = str(df.iloc[min(end, len(df) - 1)]["datetime"])[:10]
    return f"{a} -> {b}"


def run_symbol(
    client: BingXClient,
    symbol: str,
    scfg,
    exec_candles: int,
    folds: int,
    equity: float,
) -> list[tuple[str, BacktestReport]]:
    """Ruleaza toate ferestrele walk-forward pentru un simbol."""
    exec_min = _tf_minutes(scfg.exec_tf)
    ctx_min = _tf_minutes(scfg.context_tf)

    # Contextul trebuie sa acopere aceeasi perioada + rezerva pentru warmup.
    ctx_candles = int(exec_candles * exec_min / ctx_min) + 500

    log.info("%s - aduc %d x %s si %d x %s...",
             symbol, exec_candles, scfg.exec_tf, ctx_candles, scfg.context_tf)

    ctx_raw = client.fetch_ohlcv_history(symbol, scfg.context_tf, ctx_candles)
    exec_raw = client.fetch_ohlcv_history(symbol, scfg.exec_tf, exec_candles)

    ctx_raw = ctx_raw.sort_values("timestamp").reset_index(drop=True)
    exec_raw = exec_raw.sort_values("timestamp").reset_index(drop=True)

    log.info("  %d lumanari executie | %s -> %s",
             len(exec_raw), str(exec_raw.iloc[0]["datetime"])[:16],
             str(exec_raw.iloc[-1]["datetime"])[:16])

    # Indicatorii se calculeaza O SINGURA DATA, pe seria completa. Vezi
    # scalp_signal.make_backtest_fn pentru de ce ramane cauzal.
    ctx_enriched = indicators.enrich(ctx_raw, scfg)
    exec_enriched = oscillators.enrich(exec_raw, scfg)

    warmup = scalp_signal.warmup_bars(scfg)
    tradeable = len(exec_raw) - warmup
    if tradeable < folds * 200:
        log.warning("  Prea putine lumanari pentru %d ferestre. Reduc la 2.", folds)
        folds = 2
    if tradeable < 400:
        log.error("  %s - istoric insuficient, sar peste.", symbol)
        return []

    size = tradeable // folds
    results: list[tuple[str, BacktestReport]] = []

    for k in range(folds):
        start = warmup + k * size
        end = warmup + (k + 1) * size if k < folds - 1 else len(exec_raw)

        # Feliem CU prefix de warmup, ca indicatorii sa fie calzi la prima bara
        # tranzactionabila a ferestrei. Fara prefix, fiecare fereastra ar pierde
        # primele ~300 de lumanari.
        lo = start - warmup
        fold_raw = exec_raw.iloc[lo:end].reset_index(drop=True)
        fold_enriched = exec_enriched.iloc[lo:end].reset_index(drop=True)

        signal_fn = scalp_signal.make_backtest_fn(
            scfg, ctx_enriched, fold_enriched, C.risk.risk_per_trade
        )

        bt = Backtester(
            C,
            signal_fn=signal_fn,
            strategy_cfg=scfg,
            min_bars=warmup,
            max_bars_in_trade=scfg.max_bars_in_trade,
            limit_valid_bars=(
                scfg.limit_valid_bars
                if scfg.entry_order_type == "limit_post_only"
                else None
            ),
        )

        label = _fold_label(exec_raw, start, end - 1)
        report = bt.run(symbol, ctx_raw, fold_raw, starting_equity=equity)
        results.append((label, report))

        log.info(
            "  Fereastra %d/%d  %s  |  %3d trades  expectancy %+.3fR",
            k + 1, folds, label, report.total_trades, report.expectancy_r,
        )

    return results


def main() -> int:
    p = argparse.ArgumentParser(
        description="Validare walk-forward a strategiei intraday"
    )
    p.add_argument("--symbol", action="append", help="implicit: toate din config")
    p.add_argument("--candles", type=int, default=15000,
                   help="lumanari de executie (15000 x 5m = ~52 zile)")
    p.add_argument("--folds", type=int, default=4, help="cate ferestre consecutive")
    p.add_argument("--equity", type=float, default=1000.0)
    p.add_argument("--threshold", type=float, default=0.15,
                   help="expectancy minima per fereastra")
    p.add_argument("--min-trades", type=int, default=20,
                   help="sub atatea trades, fereastra nu se numara")
    p.add_argument("--trades", action="store_true", help="listeaza fiecare trade")
    args = p.parse_args()

    symbols = args.symbol or list(C.market.symbols)
    scfg = C.scalp

    client = BingXClient()
    client.load_markets()

    print()
    print("=" * 78)
    print("  VALIDARE WALK-FORWARD - strategie sweep + reclaim")
    print(f"  Executie {scfg.exec_tf} | context {scfg.context_tf} | "
          f"buget {scfg.max_bars_in_trade} lumanari "
          f"({scfg.max_bars_in_trade * _tf_minutes(scfg.exec_tf)} min)")
    print(f"  Prag: expectancy > {args.threshold:+.2f}R pe fereastra, "
          f"minim {args.min_trades} trades")
    print("=" * 78)

    all_rows: list[tuple[str, str, BacktestReport]] = []

    for symbol in symbols:
        if not client.market_exists(symbol):
            log.error("Simbol inexistent: %s", symbol)
            continue
        try:
            for label, report in run_symbol(
                client, symbol, scfg, args.candles, args.folds, args.equity
            ):
                all_rows.append((symbol, label, report))
        except Exception as exc:  # noqa: BLE001
            log.error("%s - eroare: %s", symbol, exc)
            continue

    if not all_rows:
        print("\n  Nicio fereastra rulata. Verifica reteaua sau simbolurile.")
        return 1

    # ------------------------------------------------------------- tabel
    print()
    print("=" * 78)
    print("  REZULTATE PE FERESTRE")
    print("=" * 78)
    print(f"  {'Simbol':<18} {'Perioada':<24} {'Trades':>7} {'WR':>7} "
          f"{'Expect':>9} {'PF':>6}")
    print("  " + "-" * 74)

    for symbol, label, r in all_rows:
        print(f"  {symbol:<18} {label:<24} {r.total_trades:>7} "
              f"{r.win_rate:>6.1%} {r.expectancy_r:>+8.3f}R {r.profit_factor:>6.2f}")

    if args.trades:
        for symbol, label, r in all_rows:
            if not r.trades:
                continue
            print(f"\n  --- {symbol} {label}")
            for t in r.trades:
                print(f"    {t.exit_time[:16]}  {t.side:<5} {t.exit_reason:<28} "
                      f"{t.r_multiple:>+6.2f}R")

    # ------------------------------------------------------------- verdict
    counted = [(s, l, r) for s, l, r in all_rows if r.total_trades >= args.min_trades]
    passing = [(s, l, r) for s, l, r in counted if r.expectancy_r > args.threshold]

    symbols_ok = {s for s, _, r in counted if r.expectancy_r > args.threshold}
    all_r = [t.r_multiple for _, _, r in all_rows for t in r.trades]
    total_trades = len(all_r)
    overall = sum(all_r) / total_trades if total_trades else 0.0
    t_stat = _t_stat(all_r)

    print()
    print("=" * 78)
    print("  VERDICT")
    print("=" * 78)
    print(f"  Ferestre rulate           : {len(all_rows)}")
    print(f"  Ferestre cu >= {args.min_trades} trades  : {len(counted)}")
    print(f"  Ferestre peste prag       : {len(passing)}")
    print(f"  Simboluri peste prag      : {len(symbols_ok)} / {len(symbols)}")
    print(f"  Trades totale             : {total_trades}")
    print(f"  Expectancy agregata       : {overall:+.3f}R")
    print(f"  Statistica t              : {t_stat:.2f}"
          f"   ({'semnificativ' if t_stat > 2.0 else 'NEsemnificativ'})")
    print("  " + "-" * 74)

    ok_periods = len(passing) >= 2
    ok_symbols = len(symbols_ok) >= 2
    ok_sample = total_trades >= 100
    ok_signif = t_stat > 2.0
    ok_positive = overall > args.threshold

    for label, ok in [
        (f"expectancy > {args.threshold:+.2f}R pe >= 2 perioade", ok_periods),
        ("consistent pe >= 2 simboluri", ok_symbols),
        ("esantion >= 100 trades", ok_sample),
        ("t > 2.0 (nu e noroc)", ok_signif),
        (f"expectancy agregata > {args.threshold:+.2f}R", ok_positive),
    ]:
        print(f"  [{'x' if ok else ' '}] {label}")

    print("  " + "-" * 74)

    if all([ok_periods, ok_symbols, ok_sample, ok_signif, ok_positive]):
        print("  TRECUT. Strategia are edge consistent pe date nevazute la calibrare.")
        print("  Pasul urmator este paper trading, nu bani reali. Backtestul nu")
        print("  modeleaza cozile de ordine, opririle de exchange si nici mana ta.")
        code = 0
    else:
        print("  RESPINS. Nu trece pe bani reali.")
        print("  Un risk engine bun nu salveaza un edge negativ - doar intarzie")
        print("  pierderea. Schimba ipoteza strategiei, nu pragurile de mai sus.")
        code = 2

    print("=" * 78)
    return code


if __name__ == "__main__":
    sys.exit(main())
