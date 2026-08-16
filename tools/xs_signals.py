"""
Cartea de pozitii cross-sectionala pe altcoins, acum. Semnale, nu executie.

    python tools\\xs_signals.py
    python tools\\xs_signals.py --capital 500
    python tools\\xs_signals.py --capital 500 --factor composite

Nu are nevoie de chei API si nu plaseaza niciun ordin. Afiseaza ce ar trebui sa
detii; tranzactionarea ramane manuala si a ta.

CUM SE FOLOSESTE, PRACTIC

Strategia nu da "semnale" in sensul clasic - nu exista un moment in care suna un
clopotel si intri intr-un trade. Detii permanent o carte de pozitii long si
short, si o rebalansezi o data la cateva zile. Randamentul vine din diferenta
dintre cele doua picioare, nu din vreo tranzactie individuala.

De aceea unealta arata doua lucruri, iar al doilea conteaza mai mult:

  - CARTEA TINTA: ce ar trebui sa detii acum.
  - MODIFICARILE: ce trebuie sa faci ca sa ajungi de la ce ai la ce ar trebui.
    Asta e ce tranzactionezi efectiv. Restul pozitiilor ramane pe loc.

Cartea anterioara se tine in logs/xs_book.json, ca sa se poata calcula diferenta
intre rulari. Prima rulare nu are cu ce compara, deci arata totul ca fiind nou.

DE CE POZITIILE NU SUNT EGALE IN DOLARI

Sunt dimensionate invers proportional cu volatilitatea fiecarei monede. Un
memecoin care se misca 15% pe zi primeste o pozitie mult mai mica decat un
altcoin mare care se misca 3%, astfel incat sa contribuie cu risc comparabil.
Cu marimi egale in dolari, cartea ar parea diversificata dar ar fi in fapt un
pariu pe cateva monede - masurat, exact acesta a fost motivul pentru care
prima varianta de portofoliu pierdea bani chiar si inainte de costuri.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from backtest.validate_xs import GRID, build_weights, factor_signal
from exchange.bingx_client import BingXClient
from strategy import xs_gate
from tools.edge_scan import compute_features, fetch_panel, pick_universe

log = logging.getLogger("xs_signals")

BOOK_PATH = "logs/xs_book.json"


def load_book() -> dict:
    if not os.path.exists(BOOK_PATH):
        return {}
    try:
        with open(BOOK_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def save_book(weights: pd.Series) -> None:
    os.makedirs(os.path.dirname(BOOK_PATH) or ".", exist_ok=True)
    with open(BOOK_PATH, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "weights": {k: float(v) for k, v in weights.items()},
            },
            fh,
            indent=2,
        )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tf", default="4h")
    p.add_argument("--universe", type=int, default=50)
    p.add_argument("--factor", default="range_pos")
    p.add_argument("--capital", type=float, default=0.0,
                   help="capital total in USDT, pentru marimi concrete")
    p.add_argument("--top", type=int, default=8,
                   help="cate pozitii pe fiecare parte sa afiseze")
    p.add_argument("--commit", action="store_true",
                   help="salveaza cartea ca fiind cea curenta")
    args = p.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    gate = xs_gate.check(
        args.factor, args.tf, args.universe, GRID,
        path=xs_gate.path_for(args.factor),
    )

    client = BingXClient()
    symbols = pick_universe(client, args.universe)
    # 400 aduse, 200 cerute: cel mai lung indicator din compute_features are o
    # fereastra de 168 de lumanari, deci 200 e incalzire suficienta si nu
    # elimina inutil monedele listate recent - care sunt adesea exact cele cu
    # cea mai mare dispersie, adica exact ce ordoneaza strategia.
    panel = fetch_panel(client, symbols, args.tf, 400, min_bars=200)
    if len(panel) < 8:
        print(f"Doar {len(panel)} simboluri cu date. Prea putine.")
        return 1

    feats = {s: compute_features(d) for s, d in panel.items()}
    sig = factor_signal(feats, args.factor)
    vols = pd.DataFrame({s: f["vol_24"] for s, f in feats.items()})

    w = build_weights(sig.iloc[-1], vols.iloc[-1], vol_scale=True)
    if w.empty:
        print("Nu s-a putut construi cartea (prea putine date valide).")
        return 1

    asof = sig.index[-1]

    print()
    print("=" * 78)
    print(f"  CARTE CROSS-SECTIONALA   {args.factor}   {args.tf}   "
          f"{len(panel)} simboluri")
    print(f"  ultima lumanare inchisa: {asof:%Y-%m-%d %H:%M} UTC")
    print("=" * 78)

    if gate.tradeable:
        print(f"  Poarta: TRANZACTIONABIL")
        print(f"    {gate.reason}")
    else:
        print(f"  Poarta: NETRANZACTIONABIL")
        print(f"    {gate.reason}")
        print("    Cartea de mai jos e informativa. Nu o tranzactiona.")
    print("=" * 78)
    print()

    longs = w[w > 0].sort_values(ascending=False).head(args.top)
    shorts = w[w < 0].sort_values().head(args.top)

    def show(part: pd.Series, label: str) -> None:
        print(f"  {label}")
        head = f"    {'simbol':<22}{'pondere':>9}"
        if args.capital:
            head += f"{'USDT':>11}"
        print(head)
        print("    " + "-" * (len(head) - 4))
        for sym, weight in part.items():
            line = f"    {sym:<22}{weight:>8.2%}"
            if args.capital:
                line += f"{abs(weight) * args.capital:>11.2f}"
            print(line)
        print()

    show(longs, f"LONG  (cele mai bine clasate {len(longs)} din {(w > 0).sum()})")
    show(shorts, f"SHORT (cele mai slab clasate {len(shorts)} din {(w < 0).sum()})")

    # --- scurgerea din funding ---
    #
    # Nu e o formalitate pe aceasta strategie. range_pos cumpara monedele
    # aproape de maximele lor - exact acelea unde long-urile sunt aglomerate si
    # funding-ul e cel mai mare - si shorteaza monedele de la minime, unde
    # funding-ul e mic sau negativ. Pe un cos long-short obisnuit funding-ul se
    # anuleaza intre picioare; aici factorul se aliniaza cu el, deci se aduna.
    # Se plateste la fiecare 8 ore, iar pozitiile se tin zile intregi.
    try:
        rates = client.fetch_funding_rates(list(w.index))
        fr = pd.Series(rates, dtype=float).reindex(w.index).dropna()
        if not fr.empty:
            ww = w.reindex(fr.index)
            # sum(w * fr) este PLATA: un long cu funding pozitiv plateste, un
            # short cu funding pozitiv incaseaza. Impactul pe randament e opusul
            # ei, si asta se afiseaza - ca sa nu existe niciun dubiu de semn.
            paid_8h = float((ww * fr).sum())
            impact_year = -paid_8h * 3 * 365
            verb = "adauga" if impact_year >= 0 else "scade"
            print(f"  funding: {verb} {abs(impact_year):.1%} pe an "
                  f"({-paid_8h:+.4%} pe 8h)")
            if impact_year < -0.05:
                print("    Atentie: peste 5% pe an pierdut din funding. NU e")
                print("    modelat in validare, deci randamentul real e cu atat")
                print("    mai mic decat cel din certificat.")
            print()
    except Exception as exc:  # noqa: BLE001
        log.warning("Nu am putut citi funding: %s", exc)

    gross = w.abs().sum()
    net = w.sum()
    print(f"  expunere bruta {gross:.2f}x, neta {net:+.4f}x "
          f"(neta ~0 = neutru la directia pietei)")
    if args.capital:
        print(f"  capital {args.capital:.0f} USDT -> "
              f"{gross * args.capital:.0f} USDT expunere bruta totala")
    print()

    # --- ce s-a schimbat fata de ultima carte salvata ---
    prev = load_book()
    prev_w = pd.Series(prev.get("weights", {}), dtype=float)

    if prev_w.empty:
        print("  Nu exista carte anterioara salvata - totul e nou.")
        print(f"  Ruleaza cu --commit dupa ce ai executat, ca sa o poti compara")
        print(f"  data viitoare si sa tranzactionezi doar diferenta.")
    else:
        idx = w.index.union(prev_w.index)
        delta = w.reindex(idx).fillna(0.0) - prev_w.reindex(idx).fillna(0.0)
        delta = delta[delta.abs() > 0.005].sort_values(key=abs, ascending=False)

        print(f"  MODIFICARI fata de {prev.get('updated_at', '?')[:16]}")
        print(f"    (doar astea se tranzactioneaza; restul ramane pe loc)")
        if delta.empty:
            print("    Nimic de schimbat peste pragul de 0.5%.")
        else:
            for sym, d in delta.head(20).items():
                verb = "cumpara" if d > 0 else "vinde  "
                line = f"    {verb} {sym:<22}{d:>+8.2%}"
                if args.capital:
                    line += f"{abs(d) * args.capital:>11.2f} USDT"
                print(line)
            print(f"    turnover total: {delta.abs().sum():.1%} din carte")
    print()

    if args.commit:
        save_book(w)
        print(f"  Carte salvata in {BOOK_PATH}.")
        print()

    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
