"""
Validare walk-forward pentru strategia cross-sectionala pe altcoins.

    python backtest\\validate_xs.py
    python backtest\\validate_xs.py --factor range_pos --folds 5
    python backtest\\validate_xs.py --hedge-regime

Nu are nevoie de chei API.

DE CE UN VALIDATOR SEPARAT

`backtest/validate.py` valideaza strategii bazate pe TRANZACTII: intra, iese,
rezulta un R. Strategia cross-sectionala nu are tranzactii individuale - are o
carte de pozitii rebalansata periodic, iar performanta e o serie de randamente
de portofoliu. Aceleasi principii, alta unitate de masura.

CE E DIFERIT FATA DE `backtest/xsection.py`

Acela a fost explorare: ruleaza un factor pe tot esantionul si uita-te la
rezultat. Problema e ca alegerea parametrilor s-a facut privind acelasi esantion
pe care s-a masurat rezultatul. Asta nu e validare, e memorare.

Aici parametrii se aleg pe date DINAINTE si se masoara pe date DE DUPA, fold cu
fold. Randamentele raportate sunt exclusiv out-of-sample, cusute cap la cap.
Fereastra de antrenament creste (ancorata), pentru ca in tranzactionare nu uiti
istoria - o acumulezi.

TREI REPARATII FATA DE VERSIUNEA DE EXPLORARE

Niciuna nu e reglaj de parametri. Fiecare raspunde unei probleme MASURATE.

  1. PONDERI PE RANG, nu doar extremele. Diagnosticul a aratat ca informatia
     din IC traieste in toata distributia, iar extremele - singurul lucru pe
     care il tranzactiona versiunea veche - au cozi care inverseaza semnul
     mediei. O carte cu ponderi proportionale cu rangul demediat foloseste
     intreaga sectiune si diluteaza exact acele cozi.

  2. SIZING INVERS VOLATILITATII. Cu ponderi egale in dolari, un memecoin cu
     volatilitate de 15% pe zi domina complet un altcoin mare cu 3%. Cartea
     "diversificata" era de fapt un pariu pe cateva monede. Impartind la
     volatilitate, fiecare pozitie contribuie cu risc comparabil.

  3. HEDGE PE REGIM (optional, --hedge-regime). S-a masurat ca familia low-vol
     avea corelatie +0.556 cu (altcoins - BTC): jumatate din "alfa" era un pariu
     pe altseason. Hedge-ul scade beta estimata pe fereastra de antrenament,
     nu pe cea de test - altfel ar fi tot privire in viitor.

PRAGUL DE TRECERE

Aceleasi cerinte ca restul proiectului, traduse in limbajul portofoliului:
randament out-of-sample pozitiv, t Newey-West peste 2, si pozitiv in majoritatea
foldurilor. Ultima conditie e cea mai importanta: un rezultat agregat pozitiv
care vine dintr-un singur fold e noroc, nu edge - exact ce a descalificat
familia low-vol, unde alfa lipsea in prima treime si exploda in ultima.
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
from strategy import xs_gate
from tools.edge_scan import MIN_SYMBOLS_PER_BAR, compute_features, fetch_panel, pick_universe
from tools.funding_edge import nw_t_stat

log = logging.getLogger("validate_xs")

# Cerintele de trecere.
MIN_T = 2.0
MIN_FOLD_WIN_RATE = 0.6

# Praguri de esantion, fara de care restul verificarilor nu inseamna nimic.
#
# O rulare de test pe 12 simboluri si 0.27 ani a raportat +210% pe an, Sharpe
# 6.58 si t 2.58 - a trecut toate cele patru conditii statistice si a scris un
# certificat valid. Amprenta a impiedicat folosirea lui pe universul real, dar
# asta a fost noroc de proiectare, nu intentie. Un t calculat pe trei luni si o
# mana de monede masoara zgomot cu multe zecimale.
MIN_OOS_YEARS = 0.75
MIN_SYMBOLS = 20

# Grila de configuratii incercate la fiecare fold, si parte din amprenta
# certificatului. Deliberat mica: fiecare parametru in plus e o sansa in plus
# de a gasi noroc pe fereastra de antrenament si a-l confunda cu edge.
#
# Traieste aici, la nivel de modul, ca sa o importe si tools/xs_signals.py.
# A fost copiata manual acolo la inceput, iar doua copii ale aceleiasi liste in
# fisiere diferite inseamna ca prima modificare a uneia inchide poarta fara ca
# nimeni sa inteleaga de ce - amprenta nu se mai potriveste, si mesajul spune
# doar "configuratia s-a schimbat".
GRID = tuple((h, vs) for h in (24, 30, 42) for vs in (True, False))


# Semnul IC-ului masurat de tools/edge_scan.py, per factor. Un factor cu IC
# negativ (valoare mare -> randament viitor mic) trebuie intors inainte de a fi
# folosit ca semnal, ca sa ramana valabila conventia "long valorile mari".
# Tinut aici, intr-un singur tabel, tocmai ca sa nu se mai imprastie decizii de
# semn prin cod.
IC_SIGN = {
    "range_pos": +1,
    "mom_24_sharpe": +1,
    "atr_ratio": +1,
    "dist_vwap": +1,
    "amihud": -1,
    "vol_24": -1,
    "max_ret_24": -1,
    "skew_72": -1,
    "vol_surprise": -1,
    "vol_of_vol": -1,
    "mom_6": -1,
}


# Factorii care intra in semnalul compozit. range_pos e singurul cu IC pozitiv,
# restul sunt familia low-vol / iliciditate - care, masurata separat, e UN
# efect vazut din patru unghiuri, nu patru efecte. Sunt inclusi toti tocmai
# pentru ca mediarea a patru masuratori zgomotoase ale aceluiasi lucru da o
# estimare mai stabila decat oricare dintre ele.
COMPOSITE_FACTORS = ("range_pos", "amihud", "vol_24", "max_ret_24")


def _rank_norm(df: pd.DataFrame) -> pd.DataFrame:
    """Rang procentual pe fiecare rand, centrat in zero."""
    return df.rank(axis=1, pct=True) - 0.5


def factor_signal(feats: dict[str, pd.DataFrame], factor: str) -> pd.DataFrame:
    """
    Semnalul gata orientat: valoare mare inseamna intotdeauna "candidat de long".
    """
    if factor == "composite":
        # Se mediaza RANGURILE, nu valorile. amihud si vol_24 traiesc pe scari
        # complet diferite; o medie a valorilor brute ar fi dominata de factorul
        # cu cele mai mari numere, iar ponderea reala ar fi un accident de unitati.
        parts = [
            _rank_norm(
                pd.DataFrame({s: f[name] for s, f in feats.items()}) * IC_SIGN[name]
            )
            for name in COMPOSITE_FACTORS
        ]
        return sum(parts) / len(parts)

    if factor not in IC_SIGN:
        raise SystemExit(
            f"Nu stiu semnul IC pentru '{factor}'. Ruleaza intai "
            f"tools/edge_scan.py si adauga-l in IC_SIGN. Cunoscuti: "
            f"{', '.join(sorted(IC_SIGN))}"
        )
    sig = pd.DataFrame({s: f[factor] for s, f in feats.items()})
    return sig * IC_SIGN[factor]


def build_weights(
    sig_row: pd.Series, vol_row: pd.Series, vol_scale: bool
) -> pd.Series:
    """
    Transforma un rand de semnal intr-o carte dollar-neutral cu expunere bruta 1.

    Rangul, nu valoarea bruta: un factor ca amihud are cozi de ordine de marime
    intregi, iar o pondere proportionala cu valoarea ar pune tot capitalul intr-o
    singura moneda. Rangul e imun la asta prin constructie.

    CONVENTIE: pondere POZITIVA (long) pentru valorile MARI ale semnalului.

    Atentie, e invers fata de `backtest/xsection.py`, care merge long pe capatul
    de jos al clasamentului. Diferenta a produs deja o validare intoarsa pe dos:
    factorul corect, directia gresita, si un rezultat de -36% care parea o
    respingere curata. De aceea semnul se stabileste intr-un singur loc, in
    `factor_signal()`, si nu se mai atinge nicaieri altundeva.
    """
    s = sig_row.dropna()
    if len(s) < MIN_SYMBOLS_PER_BAR:
        return pd.Series(dtype=float)

    w = s.rank()
    w = w - w.mean()

    if vol_scale:
        v = vol_row.reindex(w.index)
        # Fara volatilitate cunoscuta nu putem dimensiona; mediana e o
        # presupunere neutra, preferabila eliminarii simbolului.
        v = v.fillna(v.median())
        v = v.where(v > 0, v.median())
        if v.notna().all() and v.median() > 0:
            w = w / v
            w = w - w.mean()

    total = w.abs().sum()
    if total <= 0:
        return pd.Series(dtype=float)
    return w / total


def simulate(
    closes: pd.DataFrame,
    signal: pd.DataFrame,
    vols: pd.DataFrame,
    hold: int,
    vol_scale: bool,
    cost_per_side: float,
    start: int,
    end: int,
) -> pd.Series:
    """
    Randamentele nete ale cartii intre barele [start, end).

    Semnalul la bara i foloseste doar informatie pana la i inclusiv; cartea
    construita atunci castiga randamentul barei i+1 incolo.
    """
    rets = closes.pct_change()
    w = pd.Series(dtype=float)
    out: list[float] = []
    stamps: list[pd.Timestamp] = []

    for i in range(start, min(end, len(closes))):
        gross = 0.0
        if not w.empty:
            r = rets.iloc[i].reindex(w.index).fillna(0.0)
            gross = float((w * r).sum())

        cost = 0.0
        # Rebalansarea se numara de la INCEPUTUL ferestrei, nu de la indexul
        # absolut. Cu `i % hold` fiecare fereastra pornea cu cartea goala si
        # ramanea fara expunere pana cand indexul absolut se nimerea divizibil -
        # masurat, 4.1% din barele OOS, si crescand monoton de la 0.9% in primul
        # fold la 7.2% in al optulea, pentru ca 333 % 42 muta startul cu -3 la
        # fiecare fold. Foldurile tarzii erau sistematic handicapate, iar
        # rezultatul depindea de o coincidenta intre marimea chunk-ului si
        # perioada de detinere.
        if (i - start) % hold == 0:
            new_w = build_weights(signal.iloc[i], vols.iloc[i], vol_scale)
            if not new_w.empty:
                idx = w.index.union(new_w.index)
                turnover = float(
                    (new_w.reindex(idx).fillna(0.0) - w.reindex(idx).fillna(0.0))
                    .abs().sum()
                )
                cost = turnover * cost_per_side
                w = new_w

        out.append(gross - cost)
        stamps.append(closes.index[i])

    return pd.Series(out, index=stamps)


def hedge(series: pd.Series, regime: pd.Series, beta: float) -> pd.Series:
    """Scade `beta` unitati de regim din randamente."""
    g = regime.reindex(series.index).fillna(0.0)
    return series - beta * g


def sharpe(s: pd.Series, bpy: float) -> float:
    if s.empty or s.std() == 0:
        return 0.0
    return float(s.mean() / s.std() * np.sqrt(bpy))


def annualized(s: pd.Series, bpy: float) -> float:
    if s.empty:
        return 0.0
    return float(s.mean() * bpy)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tf", default="4h")
    p.add_argument("--universe", type=int, default=50)
    p.add_argument("--bars", type=int, default=3000)
    p.add_argument("--factor", default="range_pos")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--hedge-regime", action="store_true",
                   help="neutralizeaza beta pe (altcoins - BTC)")
    args = p.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    bpy = 365.0 * 24.0 / {"1h": 1, "2h": 2, "4h": 4, "1d": 24}.get(args.tf, 4)
    cost = CONFIG.taker_fee + CONFIG.slippage

    client = BingXClient()
    print()
    print(f"Aleg cele mai lichide {args.universe} perpetuals...")
    symbols = pick_universe(client, args.universe)
    print(f"Aduc {args.bars} lumanari {args.tf}...")
    panel = fetch_panel(client, symbols, args.tf, args.bars)
    if len(panel) < MIN_SYMBOLS_PER_BAR:
        print(f"Doar {len(panel)} simboluri. Prea putine.")
        return 1

    closes = pd.DataFrame({s: d["close"] for s, d in panel.items()})

    feats = {s: compute_features(d) for s, d in panel.items()}
    sig = factor_signal(feats, args.factor)
    vols = pd.DataFrame({s: f["vol_24"] for s, f in feats.items()})

    rets = closes.pct_change()
    btc = "BTC/USDT:USDT"
    regime = (
        rets.drop(columns=[btc]).mean(axis=1) - rets[btc]
        if btc in rets else pd.Series(0.0, index=rets.index)
    )

    grid = GRID

    n = len(closes)
    chunk = n // (args.folds + 1)

    print()
    print("=" * 78)
    print(f"  VALIDARE WALK-FORWARD   {args.factor}   {args.tf}   "
          f"{len(panel)} simboluri")
    print("=" * 78)
    print(f"  {args.folds} folduri out-of-sample, fereastra de antrenament ancorata")
    print(f"  grila: {len(grid)} configuratii "
          f"(hold x sizing invers volatilitatii)")
    if args.hedge_regime:
        print("  hedge pe regim: DA (beta estimata doar pe antrenament)")
    print()
    print(f"  {'fold':<6}{'perioada':<26}{'config ales':<16}"
          f"{'OOS anual':>11}{'Sharpe':>8}")
    print("  " + "-" * 74)

    oos_all: list[pd.Series] = []
    fold_rows = []

    for k in range(args.folds):
        is_end = (k + 1) * chunk
        oos_end = (k + 2) * chunk

        # --- alegerea configuratiei, exclusiv pe date de antrenament ---
        best, best_sh = None, -1e9
        for hold, vs in grid:
            r = simulate(closes, sig, vols, hold, vs, cost, 200, is_end)
            if args.hedge_regime:
                g = regime.reindex(r.index).fillna(0.0)
                if g.std() > 0:
                    b = float(np.polyfit(g, r, 1)[0])
                    r = hedge(r, regime, b)
            sh = sharpe(r, bpy)
            if sh > best_sh:
                best, best_sh = (hold, vs), sh

        hold, vs = best

        # Beta pentru hedge se estimeaza tot pe antrenament, apoi se APLICA pe
        # test. Reestimarea pe test ar fi privire in viitor.
        beta_is = 0.0
        if args.hedge_regime:
            r_is = simulate(closes, sig, vols, hold, vs, cost, 200, is_end)
            g_is = regime.reindex(r_is.index).fillna(0.0)
            if g_is.std() > 0:
                beta_is = float(np.polyfit(g_is, r_is, 1)[0])

        # --- masurarea, exclusiv pe date nevazute ---
        r_oos = simulate(closes, sig, vols, hold, vs, cost, is_end, oos_end)
        if args.hedge_regime:
            r_oos = hedge(r_oos, regime, beta_is)

        if r_oos.empty:
            continue

        oos_all.append(r_oos)
        ann = annualized(r_oos, bpy)
        sh = sharpe(r_oos, bpy)
        fold_rows.append(ann)

        period = (f"{r_oos.index[0]:%Y-%m-%d} .. {r_oos.index[-1]:%Y-%m-%d}")
        cfg = f"h{hold} {'ivol' if vs else 'egal'}"
        print(f"  {k + 1:<6}{period:<26}{cfg:<16}{ann:>10.1%}{sh:>8.2f}")

    if not oos_all:
        print("\n  Niciun fold nu a produs randamente. Prea putine date.")
        return 1

    stitched = pd.concat(oos_all)
    ann = annualized(stitched, bpy)
    sh = sharpe(stitched, bpy)
    t = float(nw_t_stat(stitched, lag=42))
    equity = (1.0 + stitched).cumprod()
    maxdd = float((equity / equity.cummax() - 1.0).min())
    wins = sum(1 for a in fold_rows if a > 0)
    win_rate = wins / len(fold_rows)

    print()
    print("  " + "-" * 74)
    print(f"  AGREGAT OUT-OF-SAMPLE ({len(stitched)} bare, "
          f"{len(stitched) / bpy:.2f} ani)")
    print(f"    randament anualizat : {ann:>8.1%}")
    print(f"    Sharpe              : {sh:>8.2f}")
    print(f"    drawdown maxim      : {maxdd:>8.1%}")
    print(f"    t-stat (Newey-West) : {t:>8.2f}")
    print(f"    folduri pozitive    : {wins}/{len(fold_rows)}")

    beta_oos = 0.0
    g = regime.reindex(stitched.index).fillna(0.0)
    if g.std() > 0:
        beta_oos = float(np.polyfit(g, stitched, 1)[0])
    print(f"    beta pe (alt - BTC) : {beta_oos:>8.2f}")

    years = len(stitched) / bpy
    checks = [
        (f"esantion OOS >= {MIN_OOS_YEARS} ani (are {years:.2f})",
         years >= MIN_OOS_YEARS),
        (f"univers >= {MIN_SYMBOLS} simboluri (are {len(panel)})",
         len(panel) >= MIN_SYMBOLS),
        ("randament OOS pozitiv", ann > 0),
        (f"t Newey-West > {MIN_T}", t > MIN_T),
        (f"folduri pozitive >= {MIN_FOLD_WIN_RATE:.0%}", win_rate >= MIN_FOLD_WIN_RATE),
        ("beta pe regim sub 0.30", abs(beta_oos) < 0.30),
    ]

    print()
    for label, ok in checks:
        print(f"    [{'OK ' if ok else 'NU '}] {label}")

    passed = all(ok for _, ok in checks)

    # Certificatul se scrie SI cand validarea pica. O respingere consemnata e la
    # fel de valoroasa ca o trecere: e dovada ca intrebarea a fost pusa, si
    # opreste poarta sa lase semnale sa treaca doar pentru ca nu stie nimic.
    xs_gate.save_certificate(
        xs_gate.XSCertificate(
            passed=passed,
            fingerprint=xs_gate.fingerprint(
                args.factor, args.tf, args.universe, tuple(grid)
            ),
            created_at=pd.Timestamp.now("UTC").isoformat(),
            factor=args.factor,
            tf=args.tf,
            universe=args.universe,
            ann_return=ann,
            sharpe=sh,
            t_stat=t,
            max_dd=maxdd,
            regime_beta=beta_oos,
            folds_total=len(fold_rows),
            folds_positive=wins,
            period_start=str(stitched.index[0]),
            period_end=str(stitched.index[-1]),
            notes=[lbl for lbl, ok in checks if not ok],
        ),
        path=xs_gate.path_for(args.factor),
    )

    print()
    print("=" * 78)
    if passed:
        print("  TRECE VALIDAREA.")
        print("  Randamentele de mai sus sunt exclusiv out-of-sample: parametrii au")
        print("  fost alesi de fiecare data pe date anterioare. Asta e cel mai bun")
        print("  test disponibil fara bani reali - dar ramane un test pe trecut.")
        print("  Urmatorul pas e hartie, cu marimi mici, nu capital deplin.")
    else:
        failed = [lbl for lbl, ok in checks if not ok]
        print("  NU TRECE. Conditii nesatisfacute:")
        for lbl in failed:
            print(f"    - {lbl}")
        print()
        print("  Poarta de validare ramane inchisa. Nu regla parametrii ca sa treaca")
        print("  - fiecare incercare consuma din credibilitatea testului insusi.")
    print("=" * 78)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
