"""
Care feature-uri prezic randamentul viitor? Masuratoare pe univers larg.

    python tools\\edge_scan.py
    python tools\\edge_scan.py --tf 1h --universe 30 --bars 4000
    python tools\\edge_scan.py --tf 4h --horizons 3 6 12

Nu are nevoie de chei API. Nu construieste nicio strategie.

DE CE EXISTA ACEST FISIER

Proiectul a masurat deja, onest, ca analiza tehnica clasica nu are edge pe 5m:
tools\\score_edge.py a rulat 4111 semnale si a gasit corelatie scor-rezultat de
+0.026, iar validarea walk-forward a dat expectancy -0.380R. Concluzia corecta
nu e "regleaza pragurile", ci "cauta alta sursa de informatie".

Toate testele de pana acum au folosit BTC, ETH si SOL. Trei simboluri care se
misca aproape identic. Asta inseamna ca o singura axa a fost testata: seria de
timp a pretului, pe un esantion care in practica e aproape un singur activ.

Axa netestata este SECTIUNEA TRANSVERSALA. In loc sa intrebi "urca BTC?",
intrebi "care dintre cele 30 de monede o duce mai bine decat celelalte?".
Diferenta nu e cosmetica:

  - E o intrebare relativa, nu directionala. Daca toata piata scade 5%, o
    pozitie long pe cele mai puternice si short pe cele mai slabe poate ramane
    profitabila. Riscul de directie se anuleaza intre picioare.
  - Esantionul creste de zeci de ori. 30 de simboluri x mii de lumanari
    inseamna sute de mii de observatii, nu 116 tranzactii.
  - E singura familie de factori cu dovezi publice serioase in crypto, alaturi
    de carry-ul din funding - care, deloc intamplator, e si singurul lucru din
    proiectul asta cu edge pozitiv masurat (backtest\\basis.py).

CE MASOARA, CONCRET

Pentru fiecare feature si fiecare orizont calculam INFORMATION COEFFICIENT
cross-sectional: la fiecare moment t, corelatia de rang (Spearman) intre
valoarea feature-ului pe toate simbolurile si randamentul lor viitor. Un IC
pozitiv constant inseamna ca ordonarea data de feature prezice ordonarea
randamentelor. Rangurile fac masura automat neutra la piata: daca totul cade,
rangurile nu se schimba.

Apoi testam daca media seriei de IC e semnificativ diferita de zero.

TREI CAPCANE STATISTICE, TRATATE EXPLICIT

1. SUPRAPUNEREA. Randamentul pe 24 de lumanari calculat la fiecare lumanare se
   suprapune cu urmatoarele 23. Observatiile nu sunt independente, iar t-statul
   naiv e umflat cu pana la radical din orizont. Corectam Newey-West. Lectia a
   fost invatata scump in tools\\funding_edge.py, unde t=-3.46 a devenit t=-0.94
   dupa corectie.

2. TESTAREA MULTIPLA. Testand 14 feature-uri la pragul |t|>2, te astepti la
   ~0.7 rezultate "semnificative" pur din noroc. Raportam si un prag Bonferroni,
   mai sever, si numai ce trece de el merita luat in serios.

3. LOOKAHEAD. Fiecare feature foloseste exclusiv date pana la lumanarea t
   inclusiv; randamentul masurat e strict de la t inainte. Universul e insa ales
   dupa volumul de AZI, ceea ce introduce un survivorship bias usor - monedele
   care au murit intre timp lipsesc. Nu e reparabil cu datele disponibile, dar
   trebuie stiut cand citesti rezultatul.

CUM SE CITESTE REZULTATUL

Un IC mediu de 0.02-0.04 care trece de pragul Bonferroni e un rezultat bun si
normal in practica; nimeni nu are 0.3. Ce conteaza e consistenta, nu marimea.
Daca nimic nu trece, acesta e un raspuns valid si util: inseamna ca nu trebuie
scrisa strategia, si tocmai ai economisit banii pe care i-ai fi pierdut
scriind-o.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from exchange.bingx_client import BingXClient
from tools.funding_edge import nw_t_stat

log = logging.getLogger("edge_scan")

# Sub atatea simboluri intr-o lumanare, corelatia de rang e zgomot pur.
MIN_SYMBOLS_PER_BAR = 8

# Active reale tokenizate, excluse explicit din sectiunea transversala.
#
# Nu sunt crypto: XAUT si PAXG sunt aur, iar produsele cu "USD" in baza sunt
# actiuni si marfuri (ASML, petrol). Respecta alt orar, au alti factori de risc,
# si unele nici macar nu tranzactioneaza cand tranzactioneaza restul.
#
# Excluderea nu e cosmetica. Dimensionarea invers proportionala cu volatilitatea
# da cea mai mare pondere celui mai calm activ, iar aurul tokenizat e de departe
# cel mai calm din lista. Lasat inauntru, XAUT primea 11.5% din carte - cea mai
# mare pozitie long - si strategia devenea pe tacute un pariu pe aur, cu numele
# de strategie pe altcoins.
TOKENIZED_RWA = frozenset({"XAUT", "PAXG", "XAU", "TSLA", "NVDA", "AAPL"})

# Pragul clasic de semnificatie, inainte de corectia pentru testare multipla.
T_RAW = 2.0


# --------------------------------------------------------------------- univers
def pick_universe(client: BingXClient, size: int) -> list[str]:
    """
    Cele mai lichide `size` perpetuals USDT-M, dupa volumul pe 24h.

    Lichiditatea nu e un moft aici. Un factor care "merge" pe monede pe care
    nu poti intra fara sa misti pretul nu e un edge, e o iluzie de backtest.
    """
    client.load_markets()
    tickers = client._exchange.fetch_tickers()

    rows = []
    for sym, t in tickers.items():
        if not sym.endswith("/USDT:USDT"):
            continue
        base = sym.split("/")[0]
        # Produsele tokenizate pe actiuni si marfuri au "USD" in baza
        # (NCCOGOLD2USD, NCSKASML2USD); aurul are nume proprii. Ambele afara.
        if "USD" in base or base.upper() in TOKENIZED_RWA:
            continue
        vol = t.get("quoteVolume") or 0.0
        if vol > 0:
            rows.append((sym, float(vol)))

    rows.sort(key=lambda r: r[1], reverse=True)
    return [sym for sym, _ in rows[:size]]


def fetch_panel(
    client: BingXClient,
    symbols: list[str],
    tf: str,
    bars: int,
    min_bars: int = 300,
) -> dict[str, pd.DataFrame]:
    """
    `min_bars`: cate lumanari trebuie sa aiba un simbol ca sa fie pastrat.

    Nu e o constanta, pentru ca cele doua utilizari cer lucruri diferite. La
    cercetare vrem istorie lunga si e corect sa aruncam listarile recente. La
    generarea semnalului curent avem nevoie doar de incalzirea indicatorilor
    (~170 de lumanari), iar un prag de cercetare aplicat acolo goleste universul
    complet - exact ce s-a intamplat: se cereau 300, se primeau 299, pentru ca
    lumanarea curenta incompleta e mereu eliminata, si nu ramanea niciun simbol.
    """
    out: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(symbols, 1):
        try:
            df = client.fetch_ohlcv_history(sym, tf, bars)
        except Exception as exc:  # noqa: BLE001
            log.warning("  %s: fara date (%s)", sym, exc)
            continue
        if len(df) < min_bars:
            log.warning("  %s: doar %d lumanari, sarim", sym, len(df))
            continue
        out[sym] = df.set_index("datetime")
        print(f"  [{i}/{len(symbols)}] {sym:<20} {len(df)} lumanari")
    return out


# -------------------------------------------------------------------- features
def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Feature-uri cauzale, toate folosind date pana la lumanarea t inclusiv.

    Alese sa acopere axe DIFERITE de informatie, nu variatiuni ale aceluiasi
    lucru. A testa RSI(14) langa RSI(21) nu e diversificare, e acelasi test de
    doua ori - si exact genul de lucru care produce fals-pozitive.
    """
    c = df["close"]
    h = df["high"]
    lo = df["low"]
    v = df["volume"]

    ret1 = c.pct_change()
    f = pd.DataFrame(index=df.index)

    # Momentum la trei scari. In actiuni, momentul lung castiga si cel scurt se
    # inverseaza; in crypto nu e deloc stabilit, deci masuram, nu presupunem.
    f["mom_6"] = c / c.shift(6) - 1.0
    f["mom_24"] = c / c.shift(24) - 1.0
    f["mom_72"] = c / c.shift(72) - 1.0
    f["mom_168"] = c / c.shift(168) - 1.0

    # Regim de volatilitate: recenta fata de cea de fond.
    vol_s = ret1.rolling(24).std()
    vol_l = ret1.rolling(168).std()
    f["vol_24"] = vol_s
    f["vol_ratio"] = vol_s / vol_l.replace(0, np.nan)

    # Momentum ajustat la risc. Aceeasi miscare valoreaza mai mult daca a venit
    # linistit decat daca a venit prin haos.
    f["mom_24_sharpe"] = f["mom_24"] / vol_s.replace(0, np.nan)

    # Surpriza de volum fata de propriul normal al monedei.
    f["vol_surprise"] = v / v.rolling(168).median().replace(0, np.nan)

    # Amihud: cat pret misca o unitate de volum. Proxy de iliciditate.
    f["amihud"] = (ret1.abs() / (v * c).replace(0, np.nan)).rolling(24).mean()

    # Unde stam in intervalul recent. 0 = la minim, 1 = la maxim.
    hi72 = h.rolling(72).max()
    lo72 = lo.rolling(72).min()
    f["range_pos"] = (c - lo72) / (hi72 - lo72).replace(0, np.nan)

    # Comprimarea intervalului: interval recent fata de interval lung.
    tr = pd.concat(
        [h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1
    ).max(axis=1)
    atr_s = tr.rolling(14).mean()
    atr_l = tr.rolling(96).mean()
    f["atr_ratio"] = atr_s / atr_l.replace(0, np.nan)

    # Distanta fata de VWAP-ul rulant, in ATR - cat de intinsa e moneda.
    tp = (h + lo + c) / 3.0
    vwap = (tp * v).rolling(24).sum() / v.rolling(24).sum().replace(0, np.nan)
    f["dist_vwap"] = (c - vwap) / atr_s.replace(0, np.nan)

    # Asimetria randamentelor recente.
    f["skew_72"] = ret1.rolling(72).skew()

    # Cererea de "bilet de loterie": cel mai mare castig dintr-o singura
    # lumanare. In actiuni prezice randamente VIITOARE mai mici, pentru ca
    # atrage cumparatori care platesc prea mult. Netestat serios in crypto.
    f["max_ret_24"] = ret1.rolling(24).max()

    # Volatilitatea volatilitatii: cat de instabil e regimul insusi.
    f["vol_of_vol"] = vol_s.rolling(72).std() / vol_s.replace(0, np.nan)

    return f


def relative_to_btc(feats: dict[str, pd.DataFrame], btc: str) -> None:
    """
    Adauga puterea relativa fata de BTC, in loc.

    Aproape toata miscarea unei monede e miscarea pietei. Ce ramane dupa ce
    scazi BTC-ul e partea proprie a monedei - si daca exista informatie
    intr-un feature directional, acolo e cel mai probabil sa fie.
    """
    if btc not in feats:
        return
    btc_mom = feats[btc]["mom_24"]
    for sym, f in feats.items():
        f["rs_btc_24"] = f["mom_24"] - btc_mom.reindex(f.index)


# -------------------------------------------------------------------------- IC
def cross_sectional_ic(
    feature_wide: pd.DataFrame, fwd_wide: pd.DataFrame
) -> pd.Series:
    """
    Corelatia Spearman, la fiecare moment, intre feature si randamentul viitor,
    calculata PESTE SIMBOLURI.

    Rangul face masura neutra la piata fara niciun efort suplimentar: daca toate
    monedele cad cu 5%, ordinea lor nu se schimba, deci IC-ul nu se schimba.
    Exact ce vrem, pentru ca o strategie long-short traieste din ordine, nu din
    directie.
    """
    common = feature_wide.index.intersection(fwd_wide.index)
    fw = feature_wide.loc[common]
    rw = fwd_wide.loc[common]

    valid = fw.notna() & rw.notna()
    counts = valid.sum(axis=1)
    keep = counts >= MIN_SYMBOLS_PER_BAR
    if not keep.any():
        return pd.Series(dtype=float)

    fw = fw[keep].where(valid[keep])
    rw = rw[keep].where(valid[keep])

    fr = fw.rank(axis=1)
    rr = rw.rank(axis=1)

    fr = fr.sub(fr.mean(axis=1), axis=0)
    rr = rr.sub(rr.mean(axis=1), axis=0)

    num = (fr * rr).sum(axis=1)
    den = np.sqrt((fr**2).sum(axis=1) * (rr**2).sum(axis=1))
    ic = num / den.replace(0, np.nan)
    return ic.dropna()


def analyse(
    feats: dict[str, pd.DataFrame],
    closes: pd.DataFrame,
    horizons: list[int],
    feature_names: list[str],
) -> pd.DataFrame:
    rows = []
    for h in horizons:
        fwd = closes.shift(-h) / closes - 1.0

        for name in feature_names:
            wide = pd.DataFrame(
                {sym: f[name] for sym, f in feats.items() if name in f}
            )
            ic = cross_sectional_ic(wide, fwd)
            if len(ic) < 100:
                continue

            # Suprapunerea randamentelor pe h lumanari face IC-urile
            # consecutive dependente; lag-ul Newey-West trebuie sa acopere
            # exact acea fereastra.
            t = nw_t_stat(ic, lag=h)

            rows.append(
                {
                    "feature": name,
                    "horizon": h,
                    "ic_mean": ic.mean(),
                    "ic_std": ic.std(),
                    "t_nw": t,
                    "hit": (np.sign(ic) > 0).mean(),
                    "n_bars": len(ic),
                }
            )

    return pd.DataFrame(rows)


# ----------------------------------------------------------------------- print
def report(res: pd.DataFrame, tf: str, n_symbols: int, n_tests: int) -> None:
    # Bonferroni: pentru n teste simultane, pragul per test se inaspreste ca sa
    # tina rata totala de fals-pozitiv sub 5%.
    from scipy.stats import norm

    t_bonf = float(norm.ppf(1.0 - 0.025 / max(n_tests, 1)))

    print()
    print("=" * 78)
    print(f"  PUTERE PREDICTIVA CROSS-SECTIONALA   {tf}   {n_symbols} simboluri")
    print("=" * 78)
    print()
    print(f"  Teste rulate      : {n_tests}")
    print(f"  Prag simplu       : |t| > {T_RAW:.2f}")
    print(f"  Prag Bonferroni   : |t| > {t_bonf:.2f}   <- singurul care conteaza")
    print()

    res = res.reindex(res["t_nw"].abs().sort_values(ascending=False).index)

    print(f"  {'feature':<16} {'oriz':>5} {'IC mediu':>10} {'t (NW)':>9} "
          f"{'hit':>7} {'bare':>7}  verdict")
    print("  " + "-" * 74)

    survivors = []
    for _, r in res.iterrows():
        t = r["t_nw"]
        if abs(t) > t_bonf:
            verdict = "SEMNIFICATIV"
            survivors.append(r)
        elif abs(t) > T_RAW:
            verdict = "slab (cade la Bonferroni)"
        else:
            verdict = "zgomot"

        print(f"  {r['feature']:<16} {int(r['horizon']):>5} {r['ic_mean']:>10.4f} "
              f"{t:>9.2f} {r['hit']:>7.1%} {int(r['n_bars']):>7}  {verdict}")

    print()
    print("=" * 78)
    if survivors:
        print(f"  {len(survivors)} rezultate trec pragul sever.")
        print()
        for r in survivors:
            direction = "MARE prezice CRESTERE" if r["ic_mean"] > 0 else "MARE prezice SCADERE"
            print(f"    {r['feature']} @ {int(r['horizon'])} lumanari: {direction}")
        print()
        print("  Urmatorul pas NU e sa tranzactionezi asta. E sa construiesti un")
        print("  portofoliu long-short pe factorul castigator si sa il treci prin")
        print("  backtest cu costuri reale. Un IC pozitiv spune ca ordonarea are")
        print("  informatie; nu spune ca ramane ceva dupa taxe si slippage.")
    else:
        print("  Niciun feature nu trece pragul sever.")
        print()
        print("  Acesta este un rezultat, nu un esec. Inseamna ca pe acest univers")
        print("  si acest timeframe nu exista semnal cross-sectional destul de")
        print("  puternic incat sa merite o strategie. Incearca alt timeframe")
        print("  (--tf 4h), un univers mai larg (--universe 50), sau accepta ca")
        print("  edge-ul din acest proiect ramane carry-ul din funding.")
    print("=" * 78)
    print()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tf", default="1h", help="timeframe (implicit 1h)")
    p.add_argument("--universe", type=int, default=30, help="cate simboluri")
    p.add_argument("--bars", type=int, default=3000, help="lumanari per simbol")
    p.add_argument("--horizons", type=int, nargs="+", default=[6, 24, 72])
    p.add_argument("--csv", default="", help="salveaza rezultatele aici")
    args = p.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    client = BingXClient()

    print()
    print(f"Aleg cele mai lichide {args.universe} perpetuals...")
    symbols = pick_universe(client, args.universe)
    if len(symbols) < MIN_SYMBOLS_PER_BAR:
        print(f"Prea putine simboluri ({len(symbols)}). Nu are sens.")
        return 1

    print(f"Aduc {args.bars} lumanari {args.tf} pentru {len(symbols)} simboluri...")
    panel = fetch_panel(client, symbols, args.tf, args.bars)
    if len(panel) < MIN_SYMBOLS_PER_BAR:
        print(f"Doar {len(panel)} simboluri cu date suficiente. Nu are sens.")
        return 1

    print()
    print("Calculez feature-urile...")
    feats = {sym: compute_features(df) for sym, df in panel.items()}

    btc = "BTC/USDT:USDT"
    relative_to_btc(feats, btc)

    closes = pd.DataFrame({sym: df["close"] for sym, df in panel.items()})

    feature_names = sorted(next(iter(feats.values())).columns)
    n_tests = len(feature_names) * len(args.horizons)

    print(f"Masor {len(feature_names)} feature-uri x {len(args.horizons)} orizonturi...")
    res = analyse(feats, closes, args.horizons, feature_names)

    if res.empty:
        print("Nu s-a putut calcula niciun IC. Prea putine date suprapuse.")
        return 1

    report(res, args.tf, len(panel), n_tests)

    if args.csv:
        res.to_csv(args.csv, index=False)
        print(f"Salvat in {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
