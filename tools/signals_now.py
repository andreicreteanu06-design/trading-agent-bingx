"""
Ce semnale exista ACUM, cu strategia intraday. Citire, nu executie.

    python tools\\signals_now.py
    python tools\\signals_now.py --symbol "SOL/USDT:USDT" --all
    python tools\\signals_now.py --watch          # reevalueaza la fiecare 5 minute

Nu are nevoie de chei API - foloseste date publice.

DE CE EXISTA SEPARAT DE DASHBOARD

Dashboard-ul (app/server.py -> core/scanner.py) ruleaza strategia VECHE de trend
pe 1h/4h din strategy/signal_builder.py - cea cu edge negativ documentat in
README. Strategia intraday construita ulterior (strategy/scalp_signal.py) nu e
conectata acolo, si intentionat nu am conectat-o: nu a trecut validarea, iar a
o pune in fluxul principal ar face-o sa arate oficiala.

Fisierul asta o arata pentru ce este: o ipoteza care se inspecteaza, nu un
semnal care se tranzactioneaza. De aceea afiseaza intotdeauna, langa fiecare
setup, verdictul portii de validare si aritmetica de cost - numerele care spun
daca merita sau nu.

CUM SE CITESTE UN SEMNAL

  R:R net    - singurul R:R real. R:R brut minus costurile, exprimate in R.
  cost_R     - cat din risc se duce pe comisioane. Peste ~0.7 e ingrijorator.
  fezabil    - de cate ori excursia asteptata in bugetul de timp cere tinta.
               Sub 1.0 confortabil, peste 1.5 ceri pietei un eveniment rar.
  leverage   - NU se alege. Rezulta din cat de stramt e stopul si din riscul
               pe tranzactie. Daca iese 8x, asta cere setup-ul, nu tu.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg
from exchange.bingx_client import BingXClient
from news import sentiment
from strategy import scalp_signal, validation_gate

logging.basicConfig(level=logging.WARNING, format="%(message)s")
log = logging.getLogger("signals")

C = cfg.CONFIG


def fmt_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}"
    return f"{value:.6f}"


def show_signal(sig, allowed: bool, block_reason: str) -> None:
    ctx = sig.context
    side = sig.side.upper()
    arrow = "^" if sig.side == "long" else "v"

    print()
    print("  " + "=" * 74)
    print(f"  {arrow} {side}  {sig.symbol}   setup: {ctx['setup']}   scor: {sig.score:.0f}/100")
    print("  " + "=" * 74)

    r = abs(sig.entry - sig.stop_loss)
    print(f"    Intrare (limit)  {fmt_price(sig.entry):>14}   "
          f"[pret acum {fmt_price(ctx['last_close'])}]")
    print(f"    Stop             {fmt_price(sig.stop_loss):>14}   "
          f"({ctx['stop_distance_pct']*100:.3f}% = 1R)")
    for i, tp in enumerate(sig.take_profits, 1):
        mult = abs(tp - sig.entry) / r if r else 0
        print(f"    TP{i}              {fmt_price(tp):>14}   ({mult:.1f}R brut)")

    print()
    print(f"    R:R net {ctx['risk_reward_net']:.2f}  |  cost_R {ctx['cost_r']:.2f}  |  "
          f"fezabil {ctx['time_feasibility']:.2f}  |  leverage implicit {ctx['implied_leverage']:.1f}x")
    print(f"    Buget de timp: {ctx['max_bars_in_trade']} lumanari de "
          f"{ctx['exec_timeframe']}  |  limita valabila {ctx['limit_valid_bars']} lumanari")

    if ctx.get("divergences"):
        print(f"    Divergente: {', '.join(ctx['divergences'])}")

    print()
    print("    De ce:")
    for reason in sig.reasons:
        print(f"      + {reason}")

    if sig.warnings:
        print()
        print("    Avertismente:")
        for w in sig.warnings:
            print(f"      ! {w}")

    print()
    if not allowed:
        print(f"    >> BLOCAT DE SENTIMENT: {block_reason}")
    print("    >> NU tranzactiona pe baza acestui semnal cat timp poarta de")
    print("       validare spune NEtranzactionabil (vezi antetul).")


def scan_once(client: BingXClient, symbols: list[str], scfg, show_all: bool) -> int:
    gate = validation_gate.check(scfg)

    try:
        funding = client.fetch_funding_rates(list(C.regime.funding_symbols))
    except Exception:  # noqa: BLE001
        funding = {}
    mood = sentiment.evaluate(sentiment.SentimentConfig(), funding_rates=funding)

    now = datetime.now(timezone.utc)
    print()
    print("=" * 78)
    print(f"  SEMNALE INTRADAY   {now:%Y-%m-%d %H:%M} UTC")
    print(f"  Executie {scfg.exec_tf} | context {scfg.context_tf} | "
          f"buget {scfg.max_bars_in_trade} lumanari")
    print("=" * 78)

    status = "TRANZACTIONABIL" if gate.tradeable else "NETRANZACTIONABIL"
    print(f"  Poarta de validare: {status}")
    for line in gate.reason.split(". "):
        if line.strip():
            print(f"    {line.strip()}")

    if mood.available:
        blocked = ", ".join(sorted(mood.blocked_sides)) or "niciuna"
        print(f"  Sentiment: directii blocate = {blocked}")
        for reason in mood.reasons:
            print(f"    {reason}")

    print("=" * 78)

    found = 0
    for symbol in symbols:
        if not client.market_exists(symbol):
            print(f"  {symbol:<20} simbol inexistent")
            continue
        try:
            ctx_raw = client.fetch_ohlcv(symbol, scfg.context_tf, 400)
            exec_raw = client.fetch_ohlcv(symbol, scfg.exec_tf, scfg.exec_candles)
        except Exception as exc:  # noqa: BLE001
            print(f"  {symbol:<20} eroare de date: {str(exc)[:60]}")
            continue

        sig = scalp_signal.build_scalp_signal(
            symbol, ctx_raw, exec_raw, scfg, C.risk.risk_per_trade, C
        )

        if sig is None:
            if show_all:
                print(f"  {symbol:<20} niciun setup")
            continue

        found += 1
        allowed = mood.allows(sig.side)
        show_signal(sig, allowed, "; ".join(mood.reasons))

    if found == 0:
        print()
        print("  Niciun setup in acest moment.")
        print("  Este normal si intentionat: pragurile resping majoritatea")
        print("  lumanarilor. Ruleaza cu --watch ca sa reevaluezi periodic.")

    print()
    return found


def replay(client: BingXClient, symbols: list[str], scfg, count: int) -> int:
    """
    Ultimele semnale care AU APARUT, cu ce s-a intamplat dupa fiecare.

    Exista pentru ca "niciun setup acum" nu spune daca sistemul functioneaza
    sau e stricat. Aici vezi semnale reale, generate exact cu aceleasi reguli,
    plus rezultatul lor - inclusiv cele care au pierdut, pentru ca acelea sunt
    majoritatea si ascunderea lor ar fi o minciuna prin selectie.
    """
    from strategy import indicators, oscillators
    from tools.score_edge import simulate_one

    print()
    print("=" * 78)
    print(f"  ULTIMELE {count} SEMNALE PER SIMBOL, CU REZULTAT")
    print("=" * 78)

    warmup = scalp_signal.warmup_bars(scfg)
    window = warmup + scfg.liquidity_lookback
    total = 0

    for symbol in symbols:
        if not client.market_exists(symbol):
            continue
        try:
            ctx_raw = client.fetch_ohlcv_history(symbol, scfg.context_tf, 400)
            exec_raw = client.fetch_ohlcv_history(symbol, scfg.exec_tf, 1500)
        except Exception as exc:  # noqa: BLE001
            print(f"  {symbol}: eroare de date ({str(exc)[:50]})")
            continue

        ctx_e = indicators.enrich(ctx_raw.sort_values("timestamp").reset_index(drop=True), scfg)
        exec_e = oscillators.enrich(exec_raw.sort_values("timestamp").reset_index(drop=True), scfg)

        hits = []
        # Mergem inapoi ca sa gasim intai cele mai recente.
        last_eval = len(exec_e) - scfg.max_bars_in_trade - 2
        for idx in range(last_eval, warmup, -1):
            df = exec_e.iloc[max(0, idx - window): idx + 1]
            ts = int(exec_e.iloc[idx]["timestamp"])
            ctx = ctx_e[ctx_e["timestamp"] <= ts]
            if len(ctx) < scfg.ema_slow:
                continue
            sig = scalp_signal.build_from_enriched(
                symbol, ctx, df, scfg, C.risk.risk_per_trade, C
            )
            if sig is None:
                continue
            res = simulate_one(exec_e, idx, sig, scfg, sig.context.get("cost_r", 0.0))
            hits.append((idx, sig, res))
            if len(hits) >= count:
                break

        if not hits:
            print(f"\n  {symbol}: niciun semnal in ultimele {len(exec_e)} lumanari")
            continue

        print(f"\n  {symbol}")
        print("  " + "-" * 74)
        print(f"    {'cand':<17} {'dir':<6} {'setup':<18} {'scor':>5} {'R:R net':>8} {'rezultat':>10} {'R real':>8}")
        for idx, sig, res in hits:
            when = str(exec_e.iloc[idx]["datetime"])[:16]
            if not res or not res.get("filled"):
                outcome, r_val = "neumplut", "-"
            else:
                outcome = res["outcome"]
                r_val = f"{res['r']:+.2f}"
            print(f"    {when:<17} {sig.side:<6} {sig.context['setup']:<18} "
                  f"{sig.score:>5.0f} {sig.context['risk_reward_net']:>8.2f} "
                  f"{outcome:>10} {r_val:>8}")
            total += 1

    print()
    print("  'neumplut' = ordinul limit nu a fost atins, deci n-a existat trade.")
    print("  'timp' = a expirat bugetul fara stop si fara tinta.")
    print("  R real include costurile. Un stop costa ~1.7R, nu 1R.")
    print()
    return total


def main() -> int:
    p = argparse.ArgumentParser(description="Semnale intraday, acum")
    p.add_argument("--symbol", action="append")
    p.add_argument("--all", action="store_true", help="arata si simbolurile fara setup")
    p.add_argument("--watch", action="store_true", help="reevalueaza periodic")
    p.add_argument("--interval", type=int, default=300, help="secunde intre reevaluari")
    p.add_argument("--replay", type=int, metavar="N",
                   help="arata ultimele N semnale per simbol, cu rezultatul lor")
    args = p.parse_args()

    symbols = args.symbol or list(C.market.symbols)
    scfg = C.scalp

    client = BingXClient()
    client.load_markets()

    if args.replay:
        replay(client, symbols, scfg, args.replay)
        return 0

    if not args.watch:
        scan_once(client, symbols, scfg, args.all)
        return 0

    print(f"Reevaluez la fiecare {args.interval} secunde. Ctrl+C pentru oprire.")
    try:
        while True:
            scan_once(client, symbols, scfg, args.all)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nOprit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
