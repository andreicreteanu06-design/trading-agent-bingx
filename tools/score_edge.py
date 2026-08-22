"""
Are scorul putere predictiva? Singura intrebare care conteaza inainte de reglaje.

    python tools\\score_edge.py
    python tools\\score_edge.py --candles 20000 --by setup

Nu are nevoie de chei API.

DE CE EXISTA, si de ce ar trebui rulata INAINTEA oricarui baleiaj de parametri:

Un sistem de scor presupune ceva ce aproape nimeni nu verifica: ca un setup de
85 de puncte se comporta mai bine decat unul de 65. Daca presupunerea e falsa,
scorul e un numar decorativ, iar ridicarea pragului nu face decat sa reduca
numarul de tranzactii pastrand aceeasi asteptare - adica sa iti ia mai mult timp
ca sa pierzi aceiasi bani.

Verificarea e simpla: grupam semnalele pe intervale de scor si ne uitam daca
expectancy creste cu scorul. Daca da, avem de unde extrage - un prag mai sus
chiar da o rata de succes mai buna. Daca nu, problema e in DETECTIE, si niciun
reglaj de tinte sau de buget nu o repara.

DIFERENTA FATA DE BACKTESTUL NORMAL, si de ce e importanta aici:

`backtest/engine.py` simuleaza un portofoliu - o singura pozitie la un moment
dat. Perfect pentru "cati bani face", gresit pentru intrebarea de fata: un
semnal slab care apare primul blocheaza un semnal bun care apare la doua
lumanari distanta, iar rezultatul ar descrie ordinea sosirii, nu calitatea.

Aici fiecare semnal se evalueaza IZOLAT, ca si cum ar fi singurul din lume.
Nu e o simulare de cont; e o masuratoare de putere predictiva.
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
from exchange.bingx_client import BingXClient
from strategy import indicators, oscillators, scalp_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("score")

C = cfg.CONFIG


def simulate_one(df: pd.DataFrame, start: int, sig, scfg, cost_r: float) -> dict | None:
    """
    Rezultatul unui singur semnal, izolat de restul.

    Reguli pesimiste, pentru ca datele OHLCV nu spun ordinea ticksurilor dintr-o
    lumanare. Cand si stopul si tinta cad in aceeasi lumanare, presupunem ca
    stopul a venit primul. Presupunerea inversa ar inventa castiguri exact in
    lumanarile volatile, adica acolo unde se decid rezultatele.
    """
    entry = float(sig.entry)
    stop = float(sig.stop_loss)
    tp = float(sig.take_profits[0])
    is_long = sig.side == "long"
    r_unit = abs(entry - stop)
    if r_unit <= 0:
        return None

    # --- 1. ordinul limit se umple?
    fill_idx = None
    for j in range(start + 1, min(start + 1 + scfg.limit_valid_bars, len(df))):
        bar = df.iloc[j]
        touched = float(bar["low"]) <= entry if is_long else float(bar["high"]) >= entry
        if touched:
            fill_idx = j
            break
    if fill_idx is None:
        return {"filled": False}

    # --- 2. ce se intampla in bugetul de timp
    end = min(fill_idx + scfg.max_bars_in_trade, len(df) - 1)
    for j in range(fill_idx + 1, end + 1):
        bar = df.iloc[j]
        hi, lo = float(bar["high"]), float(bar["low"])

        hit_stop = lo <= stop if is_long else hi >= stop
        hit_tp = hi >= tp if is_long else lo <= tp

        if hit_stop:  # verificat primul, intentionat
            return {"filled": True, "r": -1.0 - cost_r, "outcome": "stop",
                    "bars": j - fill_idx}
        if hit_tp:
            gross = abs(tp - entry) / r_unit
            return {"filled": True, "r": gross - cost_r, "outcome": "tp",
                    "bars": j - fill_idx}

    # --- 3. expirare pe timp: iesim la inchidere
    close = float(df.iloc[end]["close"])
    gross = (close - entry) / r_unit if is_long else (entry - close) / r_unit
    return {"filled": True, "r": gross - cost_r, "outcome": "timp",
            "bars": end - fill_idx}


def main() -> int:
    p = argparse.ArgumentParser(description="Puterea predictiva a scorului")
    p.add_argument("--symbol", action="append")
    p.add_argument("--candles", type=int, default=12000)
    p.add_argument("--by", choices=["score", "setup", "both"], default="both")
    args = p.parse_args()

    symbols = args.symbol or list(C.market.symbols)
    base = C.scalp
    # Prag zero la generare: vrem TOATE setup-urile, ca sa vedem daca cele slabe
    # chiar sunt mai slabe. Filtrand dinainte, am masura doar ce am ales deja.
    permissive = dataclasses.replace(base, min_setup_score=0.0, min_risk_reward=0.0)

    client = BingXClient()
    client.load_markets()

    rows = []
    warmup = scalp_signal.warmup_bars(base)
    window = warmup + base.liquidity_lookback

    for symbol in symbols:
        if not client.market_exists(symbol):
            continue
        log.info("Aduc date pentru %s...", symbol)
        ctx_raw = client.fetch_ohlcv_history(symbol, base.context_tf,
                                             int(args.candles / 12) + 500)
        exec_raw = client.fetch_ohlcv_history(symbol, base.exec_tf, args.candles)
        ctx_e = indicators.enrich(ctx_raw.sort_values("timestamp").reset_index(drop=True), base)
        exec_e = oscillators.enrich(exec_raw.sort_values("timestamp").reset_index(drop=True), base)

        log.info("  evaluez %d lumanari...", len(exec_e) - warmup)
        for idx in range(warmup, len(exec_e) - base.max_bars_in_trade - 2):
            df = exec_e.iloc[max(0, idx - window): idx + 1]
            ts = int(exec_e.iloc[idx]["timestamp"])
            ctx = ctx_e[ctx_e["timestamp"] <= ts]
            if len(ctx) < base.ema_slow:
                continue

            sig = scalp_signal.build_from_enriched(
                symbol, ctx, df, permissive, C.risk.risk_per_trade, C
            )
            if sig is None:
                continue

            res = simulate_one(exec_e, idx, sig, base, sig.context.get("cost_r", 0.0))
            if res is None or not res.get("filled"):
                continue

            rows.append({
                "symbol": symbol,
                "setup": sig.context["setup"],
                "score": sig.score,
                "r": res["r"],
                "outcome": res["outcome"],
                "bars": res["bars"],
            })

    if not rows:
        print("Niciun semnal. Verifica datele.")
        return 1

    df = pd.DataFrame(rows)
    print()
    print("=" * 84)
    print(f"  PUTEREA PREDICTIVA A SCORULUI   |   {len(df)} semnale evaluate izolat")
    print("=" * 84)

    if args.by in ("score", "both"):
        bins = [0, 40, 50, 60, 70, 80, 101]
        labels = ["<40", "40-50", "50-60", "60-70", "70-80", "80+"]
        df["bucket"] = pd.cut(df["score"], bins=bins, labels=labels, right=False)

        print(f"\n  {'scor':>8} {'n':>6} {'WR':>8} {'expect':>10} {'R total':>10}")
        print("  " + "-" * 46)
        for label in labels:
            sub = df[df["bucket"] == label]
            if len(sub) == 0:
                continue
            wr = (sub["r"] > 0).mean()
            print(f"  {label:>8} {len(sub):>6} {wr:>7.1%} {sub['r'].mean():>+9.3f}R "
                  f"{sub['r'].sum():>+9.1f}R")

        # Corelatia e testul cel mai direct: pozitiva = scorul spune ceva.
        corr = df["score"].corr(df["r"])
        print(f"\n  Corelatie scor <-> rezultat: {corr:+.3f}")
        if corr > 0.10:
            print("  => Scorul ARE putere predictiva. Un prag mai sus chiar ajuta.")
        elif corr < -0.10:
            print("  => Scorul e INVERS corelat. Componentele lui trag in directia gresita.")
        else:
            print("  => Scorul NU discrimineaza. Problema e in DETECTIE, nu in praguri.")
            print("     Ridicarea pragului va reduce numarul de trade-uri fara sa")
            print("     imbunatateasca asteptarea - vei pierde aceiasi bani mai lent.")

    if args.by in ("setup", "both"):
        print(f"\n  {'setup':>18} {'n':>6} {'WR':>8} {'expect':>10} {'R total':>10}")
        print("  " + "-" * 56)
        for name, sub in df.groupby("setup"):
            wr = (sub["r"] > 0).mean()
            print(f"  {name:>18} {len(sub):>6} {wr:>7.1%} {sub['r'].mean():>+9.3f}R "
                  f"{sub['r'].sum():>+9.1f}R")

        print(f"\n  {'iesire':>18} {'n':>6} {'expect':>10}")
        print("  " + "-" * 36)
        for name, sub in df.groupby("outcome"):
            print(f"  {name:>18} {len(sub):>6} {sub['r'].mean():>+9.3f}R")

    print()
    print("=" * 84)
    return 0


if __name__ == "__main__":
    sys.exit(main())
