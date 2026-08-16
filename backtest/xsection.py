"""
Supravietuieste reversal-ul cross-sectional costurilor? Backtest long-short.

    python backtest\\xsection.py
    python backtest\\xsection.py --k 5 --hold 6 --buffer 0.25
    python backtest\\xsection.py --sweep

Nu are nevoie de chei API.

CE TESTEAZA

tools\\edge_scan.py a gasit un singur factor care trece pragul sever de
semnificatie: mom_6, cu IC cross-sectional -0.0255 si t Newey-West -3.60 pe
2987 de lumanari orare. Semnul negativ inseamna reversal - monedele care au
urcat cel mai mult in ultimele 6 ore raman in urma in urmatoarele 6.

Un IC pozitiv nu e profit. Spune doar ca ordonarea contine informatie. Intre
informatie si bani stau taxele, slippage-ul si turnover-ul, iar la o rebalansare
la fiecare 6 ore turnover-ul este exact locul unde mor strategiile de genul
asta. Un IC de 0.0255 e mic; costul unui round-trip taker e 0.2%. Intrebarea
nu e daca factorul exista, ci daca mai ramane ceva din el dupa ce platesti.

CUM E CONSTRUIT PORTOFOLIUL

La fiecare rebalansare ordonam universul dupa mom_6 si construim o carte
dollar-neutral: long pe cele mai slabe k monede, short pe cele mai puternice k,
in greutati egale. Neutralitatea nu e un detaliu - factorul prezice ordonarea,
nu directia. Daca ai fi doar long, ai tranzactiona in principal riscul de piata
si ai afla, scump, ca BTC-ul decide totul.

TREI MECANISME CARE DECID DACA TRAIESTE SAU NU

  - ZONA TAMPON (--buffer). Fara ea, o moneda care aluneca de pe locul k pe
    locul k+1 e vanduta si recumparata la urmatoarea oscilatie, platind de doua
    ori pentru nimic. Cu tampon, o pozitie deja deschisa e pastrata cat timp
    ramane in primele k*(1+buffer) locuri. Reduce turnover-ul substantial fara
    sa schimbe practic ce detii.

  - K (cate pozitii pe fiecare parte). Mic inseamna semnal mai concentrat dar
    mai zgomotos; mare inseamna diversificare dar diluezi factorul cu monede
    din mijlocul clasamentului, unde nu e informatie.

  - HOLD (la cate lumanari rebalansezi). Mai rar inseamna costuri mai mici, dar
    semnalul mom_6 se invecheste - orizontul lui masurat e tot de 6 lumanari.

MODELUL DE COST, DELIBERAT PESIMIST

Se plateste taker plus slippage pe fiecare schimbare de pozitie, pe ambele
capete. Cu ordine limita s-ar putea plati maker, dar o strategie care depinde
de umplerea la limita pe 10 simboluri simultan, la fiecare 6 ore, e o strategie
care presupune noroc. Daca merge pe taker, merge sigur.

Costul de finantare (funding) NU e inclus. Fiind dollar-neutral, platile de
funding se anuleaza partial intre picioare - dar nu perfect, si un studiu serios
ar trebui sa il masoare separat.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from config import CONFIG
from exchange.bingx_client import BingXClient
from tools.edge_scan import MIN_SYMBOLS_PER_BAR, fetch_panel, pick_universe
from tools.funding_edge import nw_t_stat

log = logging.getLogger("xsection")


def build_signal(
    panel: dict[str, pd.DataFrame], factor: str, reverse: bool
) -> pd.DataFrame:
    """
    Construieste matricea de semnal pentru factorul cerut.

    Conventia: se merge LONG pe capatul de jos al clasamentului. Pentru factorii
    cu IC negativ (amihud, vol_24, max_ret_24) asta e directia corecta din prima
    - valoare mica inseamna randament viitor mai mare. Pentru cei cu IC pozitiv
    (range_pos) trebuie inversat semnul, altfel tranzactionezi exact pe dos.
    """
    from tools.edge_scan import compute_features

    cols = {}
    for sym, df in panel.items():
        f = compute_features(df)
        if factor not in f:
            raise SystemExit(f"Factor necunoscut: {factor}. Disponibile: "
                             f"{', '.join(sorted(f.columns))}")
        cols[sym] = f[factor]

    sig = pd.DataFrame(cols)
    return -sig if reverse else sig


def run(
    closes: pd.DataFrame,
    signal: pd.DataFrame,
    k: int,
    hold: int,
    buffer: float,
    cost_per_side: float,
) -> dict:
    """
    Simuleaza cartea long-short, lumanare cu lumanare.

    Pozitiile intra la inchiderea lumanarii la care se ia decizia, deci semnalul
    foloseste exclusiv informatie disponibila atunci. Randamentul se acumuleaza
    din lumanarea urmatoare.
    """
    rets = closes.pct_change()

    longs: set[str] = set()
    shorts: set[str] = set()

    pnl_gross: list[float] = []
    pnl_net: list[float] = []
    stamps: list[pd.Timestamp] = []
    turnover_log: list[float] = []

    keep_rank = int(k * (1.0 + buffer))

    for i in range(len(closes)):
        ts = closes.index[i]

        # --- randamentul cartii curente, castigat pe ACEASTA lumanare ---
        if longs or shorts:
            r = rets.iloc[i]
            long_r = r[list(longs)].mean() if longs else 0.0
            short_r = r[list(shorts)].mean() if shorts else 0.0
            gross = 0.5 * (np.nan_to_num(long_r) - np.nan_to_num(short_r))
        else:
            gross = 0.0

        cost = 0.0

        # --- rebalansare ---
        if i % hold == 0:
            s = signal.iloc[i].dropna()
            # Nu rebalansa pe o sectiune prea subtire: cu putine simboluri,
            # clasamentul e zgomot si ai plati costuri pentru nimic.
            if len(s) >= MIN_SYMBOLS_PER_BAR:
                ranked = s.sort_values()
                n = len(ranked)

                want_long = list(ranked.index[:k])
                want_short = list(ranked.index[-k:])

                # Zona tampon: pastreaza ce ai deja, daca inca e rezonabil.
                keep_long = [x for x in longs if x in ranked.index[:keep_rank]]
                keep_short = [x for x in shorts if x in ranked.index[n - keep_rank:]]

                new_long = list(dict.fromkeys(keep_long + want_long))[:k]
                new_short = list(dict.fromkeys(keep_short + want_short))[:k]

                # O moneda nu poate fi si long si short.
                overlap = set(new_long) & set(new_short)
                new_short = [x for x in new_short if x not in overlap]

                changed = len(set(new_long) ^ longs) + len(set(new_short) ^ shorts)
                # Fiecare schimbare e o intrare sau o iesire, si fiecare pozitie
                # cantareste 1/(2k) din carte.
                turnover = changed / (2.0 * k) if k else 0.0
                cost = turnover * cost_per_side
                turnover_log.append(turnover)

                longs = set(new_long)
                shorts = set(new_short)

        pnl_gross.append(gross)
        pnl_net.append(gross - cost)
        stamps.append(ts)

    g = pd.Series(pnl_gross, index=stamps)
    n_ = pd.Series(pnl_net, index=stamps)

    return {
        "gross": g,
        "net": n_,
        "turnover": float(np.mean(turnover_log)) if turnover_log else 0.0,
        "rebalances": len(turnover_log),
    }


def stats(series: pd.Series, bars_per_year: float) -> dict:
    if series.std() == 0 or series.empty:
        return {"total": 0.0, "ann": 0.0, "sharpe": 0.0, "maxdd": 0.0, "t": 0.0}

    equity = (1.0 + series).cumprod()
    total = equity.iloc[-1] - 1.0
    years = len(series) / bars_per_year
    ann = (equity.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else 0.0
    sharpe = series.mean() / series.std() * np.sqrt(bars_per_year)
    dd = (equity / equity.cummax() - 1.0).min()

    return {
        "total": float(total),
        "ann": float(ann),
        "sharpe": float(sharpe),
        "maxdd": float(dd),
        "t": float(nw_t_stat(series, lag=6)),
    }


def bars_per_year_for(tf: str) -> float:
    per_hour = {"5m": 12, "15m": 4, "30m": 2, "1h": 1, "2h": 0.5, "4h": 0.25}
    return 365.0 * 24.0 * per_hour.get(tf, 1)


def report(res: dict, tf: str, k: int, hold: int, buffer: float, cost: float) -> None:
    bpy = bars_per_year_for(tf)
    sg = stats(res["gross"], bpy)
    sn = stats(res["net"], bpy)

    print()
    print("=" * 78)
    print(f"  PORTOFOLIU LONG-SHORT CROSS-SECTIONAL   {tf}")
    print("=" * 78)
    print(f"  k={k} pe fiecare parte | rebalansare la {hold} lumanari | "
          f"tampon {buffer:.0%}")
    print(f"  cost per rebalansare completa: {cost:.2%}")
    print(f"  turnover mediu: {res['turnover']:.1%} din carte, "
          f"{res['rebalances']} rebalansari")
    print()
    print(f"  {'':<18}{'BRUT':>14}{'NET':>14}")
    print("  " + "-" * 46)
    print(f"  {'randament total':<18}{sg['total']:>13.2%}{sn['total']:>14.2%}")
    print(f"  {'anualizat':<18}{sg['ann']:>13.2%}{sn['ann']:>14.2%}")
    print(f"  {'Sharpe':<18}{sg['sharpe']:>13.2f}{sn['sharpe']:>14.2f}")
    print(f"  {'drawdown maxim':<18}{sg['maxdd']:>13.2%}{sn['maxdd']:>14.2%}")
    print(f"  {'t-stat (NW)':<18}{sg['t']:>13.2f}{sn['t']:>14.2f}")
    print()

    drag = sg["ann"] - sn["ann"]
    print(f"  Costurile mananca {drag:.2%} pe an.")
    print()

    # --- stabilitate: acelasi lucru, pe treimi de esantion ---
    net = res["net"]
    third = len(net) // 3
    print("  Stabilitate pe sub-perioade (net):")
    labels = ["prima treime", "a doua treime", "a treia treime"]
    signs = []
    for j, label in enumerate(labels):
        chunk = net.iloc[j * third:(j + 1) * third]
        st = stats(chunk, bpy)
        signs.append(st["ann"] > 0)
        print(f"    {label:<16} {st['ann']:>9.2%} anualizat, "
              f"Sharpe {st['sharpe']:>6.2f}")
    print()

    print("=" * 78)
    if sn["ann"] > 0 and sn["sharpe"] > 1.0 and all(signs):
        print("  TRECE: pozitiv net, Sharpe peste 1, si pozitiv in toate treimile.")
        print("  Merita dus mai departe intr-o validare walk-forward formala.")
    elif sn["ann"] > 0:
        print("  PARTIAL: pozitiv net, dar fara consistenta ceruta.")
        print("  Un rezultat pozitiv care nu e stabil pe sub-perioade e de obicei")
        print("  noroc pe o bucata de piata, nu edge. Nu tranzactiona asta.")
    else:
        print("  CADE: negativ dupa costuri.")
        print("  Factorul exista statistic, dar nu supravietuieste taxelor la acest")
        print("  turnover. Asta nu il face inutil - il face imposibil de exploatat")
        print("  ASA. Incearca --sweep pentru alte combinatii de k, hold si tampon.")
    print("=" * 78)
    print()


def sweep(closes: pd.DataFrame, signal: pd.DataFrame, tf: str, cost: float) -> None:
    """
    Cauta combinatia care supravietuieste. Rezultatele de aici sunt IN-SAMPLE:
    cea mai buna celula dintr-o grila e aproape sigur si cea mai norocoasa.
    Foloseste-le ca sa afli daca EXISTA vreo zona viabila, nu ca sa alegi
    parametrii finali.
    """
    bpy = bars_per_year_for(tf)
    rows = []

    for k in (3, 5, 8):
        for hold in (6, 12, 24):
            for buf in (0.0, 0.5, 1.0):
                res = run(closes, signal, k, hold, buf, cost)
                sn = stats(res["net"], bpy)
                rows.append({
                    "k": k, "hold": hold, "buffer": buf,
                    "ann": sn["ann"], "sharpe": sn["sharpe"],
                    "maxdd": sn["maxdd"], "turnover": res["turnover"],
                })

    df = pd.DataFrame(rows).sort_values("sharpe", ascending=False)

    print()
    print("=" * 78)
    print(f"  GRILA DE PARAMETRI   {tf}   (net, dupa costuri)")
    print("=" * 78)
    print(f"  {'k':>3} {'hold':>5} {'tampon':>7} {'anualizat':>11} "
          f"{'Sharpe':>8} {'maxDD':>9} {'turnover':>9}")
    print("  " + "-" * 60)
    for _, r in df.iterrows():
        print(f"  {int(r['k']):>3} {int(r['hold']):>5} {r['buffer']:>7.0%} "
              f"{r['ann']:>11.2%} {r['sharpe']:>8.2f} {r['maxdd']:>9.2%} "
              f"{r['turnover']:>9.1%}")

    pos = (df["ann"] > 0).sum()
    print()
    print(f"  {pos} din {len(df)} combinatii sunt pozitive net.")
    if pos == 0:
        print("  Niciuna. Factorul nu e exploatabil la aceste costuri, indiferent")
        print("  cum il impachetezi. Acesta e un rezultat curat - opreste-te aici.")
    elif pos < len(df) * 0.3:
        print("  Putine, si imprastiate: semnul clasic de suprapotrivire pe grila.")
        print("  Daca ar fi edge real, ar fi pozitiv pe o REGIUNE, nu pe o celula.")
    else:
        print("  O regiune larga e pozitiva - asta e semnul bun. Alege din mijlocul")
        print("  ei, nu de la varf, si valideaza walk-forward inainte de bani reali.")
    print("=" * 78)
    print()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tf", default="1h")
    p.add_argument("--universe", type=int, default=30)
    p.add_argument("--bars", type=int, default=3000)
    p.add_argument("--factor", default="amihud", help="ce factor tranzactionam")
    p.add_argument("--reverse", action="store_true",
                   help="inverseaza semnalul (pentru factori cu IC pozitiv)")
    p.add_argument("--k", type=int, default=5, help="pozitii pe fiecare parte")
    p.add_argument("--hold", type=int, default=30, help="rebalansare la N lumanari")
    p.add_argument("--buffer", type=float, default=0.5, help="zona tampon")
    p.add_argument("--sweep", action="store_true", help="grila de parametri")
    args = p.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    cost_per_side = CONFIG.taker_fee + CONFIG.slippage

    client = BingXClient()
    print()
    print(f"Aleg cele mai lichide {args.universe} perpetuals...")
    symbols = pick_universe(client, args.universe)

    print(f"Aduc {args.bars} lumanari {args.tf}...")
    panel = fetch_panel(client, symbols, args.tf, args.bars)
    if len(panel) < MIN_SYMBOLS_PER_BAR:
        print(f"Doar {len(panel)} simboluri. Prea putine.")
        return 1

    closes = pd.DataFrame({s: df["close"] for s, df in panel.items()})
    signal = build_signal(panel, args.factor, args.reverse)

    print(f"Factor: {args.factor}{' (inversat)' if args.reverse else ''}")

    if args.sweep:
        sweep(closes, signal, args.tf, cost_per_side)
        return 0

    res = run(closes, signal, args.k, args.hold, args.buffer, cost_per_side)
    report(res, args.tf, args.k, args.hold, args.buffer, cost_per_side)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
