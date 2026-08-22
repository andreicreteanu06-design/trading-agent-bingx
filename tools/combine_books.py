"""
Cat castiga portofoliul daca rulezi doua carti in loc de una?

    python tools\\combine_books.py
    python tools\\combine_books.py --freq 1D --step 0.1

Nu are nevoie de chei API. Citeste serii deja produse de:
    backtest\\validate_xs.py   ->  logs/xs_returns_*.csv
    backtest\\basis.py         ->  logs/basis_returns_*.csv

DE CE

Doua strategii cu edge, ale caror randamente nu se misca impreuna, dau un Sharpe
combinat mai mare decat oricare dintre ele. Nu putin mai mare: pentru doua carti
NECORELATE cu risc egal, Sharpe-ul combinat e sqrt(S1^2 + S2^2). E singurul
castig din finante care nu cere nicio predictie in plus - vine din geometrie,
nu din informatie.

Conditia e insa reala, si se verifica, nu se presupune: daca cele doua carti sunt
corelate, castigul dispare. De aceea aceasta unealta MASOARA corelatia inainte
sa propuna ceva.

DE CE NU MAXIMIZEAZA SHARPE SINGURA

Captura de funding are un profil de risc care insala orice optimizator de
medie-varianta: incaseaza sume mici si regulate, deci volatilitate mica si
Sharpe aparent enorm, dar riscul ei real nu e volatilitatea - e coada. Piciorul
short de pe perpetual se poate lichida daca pretul urca brusc, iar castigul de
pe spot NU alimenteaza automat marja de pe perpetual (vezi backtest/basis.py).
La asta se adauga riscul de exchange, care nu apare in nicio serie de randamente
fiindca nu s-a intamplat inca in esantion.

Un optimizator care vede doar medie si varianta ar aloca aproape tot acolo. Ar
fi exact eroarea clasica: aduni monede in fata unui compactor. De aceea aici se
afiseaza toata grila de alocari cu masuratorile ei, si decizia ramane la om.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from tools.funding_edge import nw_t_stat

PERIODS = {"1D": 365.0, "1W": 52.0, "8h": 1095.0, "4h": 2190.0}


def load_series(path: str) -> pd.Series:
    df = pd.read_csv(path)
    col = "datetime" if "datetime" in df.columns else df.columns[0]
    s = pd.Series(df["ret"].values, index=pd.to_datetime(df[col], utc=True, format="mixed"))
    return s.sort_index().dropna()


def to_freq(s: pd.Series, freq: str) -> pd.Series:
    """
    Compune randamentele pe intervalul cerut.

    Compunere, nu suma: doua carti esantionate diferit (4h vs decontari de 8h)
    nu se pot compara altfel fara sa introduci o eroare care creste cu marimea
    randamentelor.
    """
    return (1.0 + s).resample(freq).prod() - 1.0


def stats(s: pd.Series, ppy: float) -> dict:
    if s.empty or s.std() == 0:
        return {"ann": 0.0, "sharpe": 0.0, "t": 0.0, "maxdd": 0.0, "vol": 0.0}
    equity = (1.0 + s).cumprod()
    dd = equity / equity.cummax() - 1.0
    return {
        "ann": float(s.mean() * ppy),
        "vol": float(s.std() * np.sqrt(ppy)),
        "sharpe": float(s.mean() / s.std() * np.sqrt(ppy)),
        "t": float(nw_t_stat(s, lag=10)),
        "maxdd": float(dd.min()),
    }


def pick(pattern: str, label: str) -> str | None:
    hits = sorted(glob.glob(pattern))
    if not hits:
        print(f"  Lipseste seria pentru {label} ({pattern}).")
        return None
    if len(hits) > 1:
        print(f"  {label}: {len(hits)} serii gasite, o folosesc pe {os.path.basename(hits[0])}")
    return hits[0]


def main() -> int:
    p = argparse.ArgumentParser(description="Combina doua carti si masoara castigul")
    p.add_argument("--xs", help="calea catre seria cross-sectionala")
    p.add_argument("--basis", help="calea catre seria de captura de funding")
    p.add_argument("--freq", default="1D", choices=sorted(PERIODS))
    p.add_argument("--step", type=float, default=0.1, help="pasul grilei de alocare")
    args = p.parse_args()

    xs_path = args.xs or pick("logs/xs_returns_*.csv", "cartea cross-sectionala")
    basis_path = args.basis or pick("logs/basis_returns_*.csv", "captura de funding")
    if not xs_path or not basis_path:
        print("\n  Ruleaza intai backtest\\validate_xs.py si backtest\\basis.py.\n")
        return 1

    ppy = PERIODS[args.freq]
    xs = to_freq(load_series(xs_path), args.freq)
    bs = to_freq(load_series(basis_path), args.freq)

    both = pd.concat({"xs": xs, "basis": bs}, axis=1).dropna()
    if len(both) < 30:
        print(f"\n  Doar {len(both)} observatii comune. Prea putin pentru o corelatie.\n")
        return 1

    xs, bs = both["xs"], both["basis"]
    corr = float(xs.corr(bs))

    print()
    print("=" * 78)
    print("  DOUA CARTI IMPREUNA")
    print("=" * 78)
    print(f"  cross-sectional : {os.path.basename(xs_path)}")
    print(f"  captura funding : {os.path.basename(basis_path)}")
    print(f"  perioada comuna : {both.index[0]:%Y-%m-%d} .. {both.index[-1]:%Y-%m-%d} "
          f"({len(both)} observatii la {args.freq})")
    print()
    print(f"  CORELATIA INTRE ELE: {corr:+.3f}")
    if abs(corr) < 0.2:
        print("  Sub 0.2 in valoare absoluta - practic independente. Aici e castigul.")
    elif abs(corr) < 0.5:
        print("  Corelatie moderata. Castigul din combinare exista, dar e redus.")
    else:
        print("  Corelatie mare. Cele doua carti pariaza in mare parte pe acelasi")
        print("  lucru, iar combinarea nu adauga aproape nimic.")

    print()
    print(f"  {'alocare xs':>11}{'alocare fund':>14}{'anualizat':>12}{'vol':>9}"
          f"{'Sharpe':>9}{'t(NW)':>8}{'maxDD':>9}")
    print("  " + "-" * 74)

    best_w, best_sh = None, -1e9
    steps = int(round(1.0 / args.step))
    for i in range(steps + 1):
        w = i * args.step
        combo = w * xs + (1.0 - w) * bs
        st = stats(combo, ppy)
        if st["sharpe"] > best_sh:
            best_w, best_sh = w, st["sharpe"]
        mark = ""
        if abs(w - 1.0) < 1e-9:
            mark = "  <- doar cross-sectional"
        elif w < 1e-9:
            mark = "  <- doar funding"
        print(f"  {w:>10.0%}{1 - w:>14.0%}{st['ann']:>11.1%}{st['vol']:>9.1%}"
              f"{st['sharpe']:>9.2f}{st['t']:>8.2f}{st['maxdd']:>9.1%}{mark}")

    s_xs = stats(xs, ppy)
    s_bs = stats(bs, ppy)
    theoretical = float(np.hypot(s_xs["sharpe"], s_bs["sharpe"]))

    print()
    print("=" * 78)
    print("  CITIRE")
    print("=" * 78)
    print(f"  Sharpe separat      : cross-sectional {s_xs['sharpe']:.2f}, "
          f"funding {s_bs['sharpe']:.2f}")
    print(f"  Cel mai bun amestec : {best_w:.0%} cross-sectional, Sharpe {best_sh:.2f}")
    print(f"  Limita teoretica    : {theoretical:.2f} "
          f"(sqrt(S1^2+S2^2), atinsa doar la corelatie zero)")
    print()
    print("  ATENTIE la alocarea pe captura de funding. Sharpe-ul ei e umflat de")
    print("  un profil de risc pe care varianta nu il vede: incasari mici si")
    print("  regulate, dar cu o coada stanga groasa (lichidarea piciorului short,")
    print("  riscul de platforma). Nu aloca dupa Sharpe acolo - aloca dupa cat")
    print("  esti dispus sa pierzi daca acea coada se materializeaza.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
