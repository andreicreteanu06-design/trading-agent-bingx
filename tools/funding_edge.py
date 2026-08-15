"""
Au funding rate si open interest putere predictiva? Studiu, nu strategie.

    python tools\\funding_edge.py
    python tools\\funding_edge.py --days 30 --horizons 4 12 24

Nu are nevoie de chei API.

IPOTEZA TESTATA

Graficul de pret spune ce s-a intamplat. Funding-ul si open interest-ul spun
CINE e pozitionat si cat de scump il costa sa ramana acolo - informatie pe care
nicio combinatie de oscilatori nu o contine, pentru ca nu e in pret.

  - FUNDING RATE: la fiecare 8 ore, o parte plateste celeilalte. Funding pozitiv
    mare inseamna ca long-urile platesc ca sa isi tina pozitia. Cu cat platesc
    mai mult, cu atat pozitionarea e mai aglomerata si mai fragila: o miscare
    mica in jos declanseaza lichidari, care impinge pretul mai jos, care
    declanseaza alte lichidari. De aici vin cascadele.

  - OPEN INTEREST: cate contracte sunt deschise. Combinat cu directia pretului,
    da patru regimuri cu semnificatii complet diferite:
       pret sus + OI sus  = bani noi intra pe long (acumulare cu levier)
       pret sus + OI jos  = short-uri care se inchid (short squeeze, mai fragil)
       pret jos + OI sus  = short-uri noi care intra
       pret jos + OI jos  = long-uri lichidate (capitulare)

METODA, si de ce nu e un backtest

Nu construim nicio strategie aici. Masuram randamentul VIITOR conditionat de
starea curenta si ne uitam daca difera semnificativ intre stari. Daca nu difera,
nu are rost sa scriem o strategie - am invatat deja lectia asta in aceeasi
sesiune cu tools\\score_edge.py, unde scorul de setup s-a dovedit zgomot pur.

Ordinea corecta este: masoara puterea predictiva, apoi construieste. Invers se
numeste "am o idee" si costa bani.

O NOTA ONESTA DESPRE DATE

Istoricul de funding vine de la BingX (locul unde chiar tranzactionezi).
Istoricul de open interest NU e oferit de BingX prin ccxt, deci vine de la
Binance ca proxy. Este o aproximare rezonabila - pozitionarea agregata e o
marime de piata, iar Binance e locul dominant - dar ramane o aproximare, si
trebuie tinuta minte cand citesti rezultatul.

Fereastra Binance pentru OI e limitata la ~30 de zile. Esantionul e deci mic;
tratati orice rezultat de aici ca ipoteza, nu ca dovada.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccxt
import numpy as np
import pandas as pd

import config as cfg
from exchange.bingx_client import BingXClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("funding")

C = cfg.CONFIG


def t_stat(values: pd.Series) -> float:
    """Statistica t naiva. Presupune observatii independente - vezi nw_t_stat."""
    v = values.dropna()
    n = len(v)
    if n < 3:
        return 0.0
    sd = v.std(ddof=1)
    if sd == 0 or math.isnan(sd):
        return 0.0
    return float(v.mean() / (sd / math.sqrt(n)))


def nw_t_stat(values: pd.Series, lag: int) -> float:
    """
    Statistica t corectata Newey-West, singura onesta pentru randamente suprapuse.

    Problema pe care o rezolva, si care invalideaza majoritatea studiilor de
    genul asta: daca masori randamentul pe 24 de ore din ora in ora, doua
    observatii consecutive impart 23 din cele 24 de ore. Nu sunt doua dovezi,
    sunt aproape aceeasi dovada numarata de doua ori. Formula naiva imparte la
    radacina din numarul de randuri si produce un t umflat de pana la sqrt(24)
    ori - adica transforma zgomotul in "descoperire".

    Newey-West estimeaza varianta tinand cont de autocorelatia introdusa de
    suprapunere: la numitor intra nu doar dispersia, ci si cat de mult seamana
    fiecare observatie cu vecinele ei.
    """
    v = values.dropna().to_numpy()
    n = len(v)
    if n < lag + 3:
        return 0.0

    mean = v.mean()
    dev = v - mean
    gamma0 = float((dev * dev).sum() / n)
    if gamma0 <= 0:
        return 0.0

    total = gamma0
    for k in range(1, min(lag, n - 1) + 1):
        weight = 1.0 - k / (lag + 1.0)  # kernel Bartlett
        gamma_k = float((dev[k:] * dev[:-k]).sum() / n)
        total += 2.0 * weight * gamma_k

    if total <= 0:
        return 0.0
    return float(mean / math.sqrt(total / n))


def fetch_funding_history(symbol: str, days: int, venue: str = "binance") -> pd.DataFrame:
    """
    Istoricul ratelor de funding (interval de 8h).

    Implicit de la Binance, si nu din comoditate: BingX intoarce maximum 200 de
    inregistrari (~67 de zile), insuficient pentru orice concluzie statistica.
    Binance da 2.5 ani.

    Corelatia dintre cele doua funding-uri pe perioada comuna este doar ~0.45,
    deci NU sunt interschimbabile ca numar. Sunt insa interschimbabile ca SEMNAL:
    efectul studiat - pozitionare aglomerata urmata de reversie - este un
    fenomen de piata, iar pretul BTC e acelasi peste tot. Concluzia practica:
    citesti semnalul de la Binance (locul dominant, deci cel mai informativ) si
    executi pe BingX. Nu invers.
    """
    ex = (
        ccxt.binance({"options": {"defaultType": "future"}})
        if venue == "binance"
        else ccxt.bingx({"options": {"defaultType": "swap"}})
    )
    ex.load_markets()

    limit = 1000 if venue == "binance" else 200
    since = ex.milliseconds() - days * 24 * 3600 * 1000
    rows: list[dict] = []
    cursor = since

    for _ in range(80):
        batch = ex.fetch_funding_rate_history(symbol, since=cursor, limit=limit)
        if not batch:
            break
        rows.extend(batch)
        last = batch[-1]["timestamp"]
        if last <= cursor or len(batch) < 2:
            break
        cursor = last + 1
        if last > ex.milliseconds() - 8 * 3600 * 1000:
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        [{"timestamp": r["timestamp"], "funding": r["fundingRate"]} for r in rows]
    ).drop_duplicates("timestamp").sort_values("timestamp")
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.reset_index(drop=True)


def fetch_oi_history(symbol: str, days: int, timeframe: str = "1h") -> pd.DataFrame:
    """
    Istoricul de open interest de la Binance (proxy - BingX nu il ofera).

    Binance limiteaza la ~30 de zile in urma si 500 de inregistrari per apel,
    deci paginam inainte de la cel mai vechi punct disponibil.
    """
    ex = ccxt.binance({"options": {"defaultType": "future"}})
    ex.load_markets()

    since = ex.milliseconds() - days * 24 * 3600 * 1000
    rows: list[dict] = []
    cursor = since

    while True:
        try:
            batch = ex.fetch_open_interest_history(symbol, timeframe, since=cursor, limit=500)
        except Exception as exc:  # noqa: BLE001
            log.warning("  OI: %s", str(exc)[:100])
            break
        if not batch:
            break
        rows.extend(batch)
        last = batch[-1]["timestamp"]
        if last <= cursor:
            break
        cursor = last + 1
        if len(batch) < 500:
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        [
            {
                "timestamp": r["timestamp"],
                "oi": r.get("openInterestAmount") or r.get("openInterestValue"),
            }
            for r in rows
        ]
    ).drop_duplicates("timestamp").sort_values("timestamp")
    df = df[df["oi"].notna()]
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.reset_index(drop=True)


def fetch_price_binance(symbol: str, days: int) -> pd.DataFrame:
    """Pret orar de la Binance, paginat. Pentru istoric lung e singura optiune."""
    ex = ccxt.binance({"options": {"defaultType": "future"}})
    ex.load_markets()

    cursor = ex.milliseconds() - days * 24 * 3600 * 1000
    rows: list[list] = []
    for _ in range(60):
        batch = ex.fetch_ohlcv(symbol, "1h", since=cursor, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        last = batch[-1][0]
        if last <= cursor:
            break
        cursor = last + 1
        if last > ex.milliseconds() - 3600 * 1000:
            break

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def build_panel(symbol: str, days: int, venue: str = "binance") -> pd.DataFrame:
    """Aliniaza pret, funding si OI pe o grila orara."""
    if venue == "binance" or days > 60:
        # Peste 60 de zile, BingX ar cere sute de pagini. Pretul e practic
        # identic intre cele doua (arbitrajul il tine asa), spre deosebire de
        # funding, care chiar difera.
        price = fetch_price_binance(symbol, days)
    else:
        client = BingXClient()
        client.load_markets()
        price = client.fetch_ohlcv_history(symbol, "1h", days * 24 + 50)
        price = price.sort_values("timestamp").reset_index(drop=True)
        price["datetime"] = pd.to_datetime(price["datetime"], utc=True)

    panel = price[["timestamp", "datetime", "close"]].copy()

    # --- funding: la 8h, il propagam inainte. Rata stampilata la T e decontata
    # la T, deci a o considera cunoscuta de la T incolo este cauzal corect.
    fund = fetch_funding_history(symbol, days, venue)
    if fund.empty:
        log.warning("  fara istoric de funding pentru %s", symbol)
        panel["funding"] = np.nan
    else:
        panel = pd.merge_asof(
            panel.sort_values("datetime"),
            fund[["datetime", "funding"]].sort_values("datetime"),
            on="datetime", direction="backward",
        )

    # --- open interest de la Binance (proxy)
    oi = fetch_oi_history(symbol, days)
    if oi.empty:
        log.warning("  fara istoric de OI pentru %s", symbol)
        panel["oi"] = np.nan
    else:
        panel = pd.merge_asof(
            panel.sort_values("datetime"),
            oi[["datetime", "oi"]].sort_values("datetime"),
            on="datetime", direction="backward",
        )

    return panel


def main() -> int:
    p = argparse.ArgumentParser(description="Putere predictiva funding + OI")
    p.add_argument("--symbol", action="append")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--horizons", type=int, nargs="+", default=[4, 12, 24],
                   help="orizonturi de masurare, in ore")
    p.add_argument("--venue", choices=["binance", "bingx"], default="binance",
                   help="de unde luam funding-ul (binance = istoric 2.5 ani)")
    p.add_argument("--lookback", type=int, default=72,
                   help="fereastra pentru z-score si variatii, in ore")
    args = p.parse_args()

    symbols = args.symbol or list(C.market.symbols)

    frames = []
    for symbol in symbols:
        log.info("Construiesc panelul pentru %s...", symbol)
        try:
            panel = build_panel(symbol, args.days, args.venue)
        except Exception as exc:  # noqa: BLE001
            log.error("  %s esuat: %s", symbol, str(exc)[:120])
            continue
        panel["symbol"] = symbol
        frames.append(panel)

    if not frames:
        print("Niciun panel construit.")
        return 1

    out = []
    for panel in frames:
        d = panel.copy()
        lb = args.lookback

        # --- semnale de stare, toate cauzale (doar date pana la t)
        d["funding_z"] = (
            (d["funding"] - d["funding"].rolling(lb).mean())
            / d["funding"].rolling(lb).std(ddof=0)
        )
        d["oi_chg"] = d["oi"].pct_change(8)       # variatie OI pe 8 ore
        d["px_chg"] = d["close"].pct_change(8)    # variatie pret pe 8 ore

        # --- randamente VIITOARE (singurul loc unde privim inainte, intentionat)
        for h in args.horizons:
            d[f"fwd_{h}"] = d["close"].shift(-h) / d["close"] - 1.0

        out.append(d)

    df = pd.concat(out, ignore_index=True).dropna(subset=["funding"])

    print()
    print("=" * 86)
    print(f"  PUTERE PREDICTIVA: FUNDING + OPEN INTEREST")
    print(f"  {len(df)} ore, {len(frames)} simboluri, ultimele {args.days} zile")
    print(f"  Funding: BingX (real).  Open interest: Binance (proxy).")
    print("=" * 86)

    # ---------------------------------------------------------------- funding
    print("\n  A. RANDAMENT VIITOR dupa NIVELUL FUNDING (z-score)")
    print("     Ipoteza contrarian: z mare (long-uri aglomerate) -> randament negativ")
    print()
    bins = [-99, -1.5, -0.5, 0.5, 1.5, 99]
    labels = ["z<-1.5", "-1.5..-0.5", "-0.5..0.5", "0.5..1.5", "z>1.5"]
    df["fz_bucket"] = pd.cut(df["funding_z"], bins=bins, labels=labels)

    header = f"     {'bucket':>12} {'n':>6}"
    for h in args.horizons:
        header += f" {'fwd'+str(h)+'h':>10} {'tNW':>6}"
    print(header)
    print("     " + "-" * (len(header) - 5))

    for label in labels:
        sub = df[df["fz_bucket"] == label]
        if len(sub) < 10:
            continue
        line = f"     {label:>12} {len(sub):>6}"
        for h in args.horizons:
            col = sub[f"fwd_{h}"]
            line += f" {col.mean()*100:>+9.3f}% {nw_t_stat(col, h):>6.2f}"
        print(line)

    # ------------------------------------------------------------ OI quadrants
    if df["oi"].notna().any():
        print("\n  B. RANDAMENT VIITOR dupa REGIMUL PRET x OPEN INTEREST (8h)")
        print("     Cele patru combinatii au semnificatii complet diferite")
        print()

        def regime(row):
            if pd.isna(row["oi_chg"]) or pd.isna(row["px_chg"]):
                return None
            if row["px_chg"] > 0 and row["oi_chg"] > 0:
                return "pret+ OI+ (long nou)"
            if row["px_chg"] > 0 and row["oi_chg"] <= 0:
                return "pret+ OI- (short squeeze)"
            if row["px_chg"] <= 0 and row["oi_chg"] > 0:
                return "pret- OI+ (short nou)"
            return "pret- OI- (lichidari)"

        df["regime"] = df.apply(regime, axis=1)

        header = f"     {'regim':>26} {'n':>6}"
        for h in args.horizons:
            header += f" {'fwd'+str(h)+'h':>10} {'tNW':>6}"
        print(header)
        print("     " + "-" * (len(header) - 5))

        for name, sub in df.dropna(subset=["regime"]).groupby("regime"):
            if len(sub) < 10:
                continue
            line = f"     {name:>26} {len(sub):>6}"
            for h in args.horizons:
                col = sub[f"fwd_{h}"]
                line += f" {col.mean()*100:>+9.3f}% {nw_t_stat(col, h):>6.2f}"
            print(line)

        # --------------------------------------------------- combinatia extrema
        print("\n  C. COMBINATIA: funding fierbinte SI open interest in crestere")
        print("     Pozitionarea cea mai fragila teoretic - long-uri noi care platesc scump")
        print()
        hot = df[(df["funding_z"] > 1.0) & (df["oi_chg"] > 0)]
        cold = df[(df["funding_z"] < -1.0) & (df["oi_chg"] < 0)]

        for name, sub in [("funding+ & OI+", hot), ("funding- & OI-", cold)]:
            if len(sub) < 10:
                print(f"     {name:>18}: esantion prea mic ({len(sub)})")
                continue
            line = f"     {name:>18} {len(sub):>6}"
            for h in args.horizons:
                col = sub[f"fwd_{h}"]
                line += f" {col.mean()*100:>+9.3f}% {nw_t_stat(col, h):>6.2f}"
            print(line)

    print()
    print("=" * 86)
    print("  CUM SE CITESTE")
    print("=" * 86)
    print("  Coloana 't' este statistica t. Sub |2.0| rezultatul nu se distinge de")
    print("  zgomot, indiferent cat de mare pare procentul de langa el.")
    print()
    print("  Ce ar insemna un rezultat bun: randamente viitoare cu SEMNE DIFERITE")
    print("  intre bucket-uri extreme, cu t peste 2. Asta ar fi informatie reala,")
    print("  de pe care se poate construi o strategie.")
    print()
    print("  Ce ar insemna un rezultat prost: aceleasi randamente peste tot, sau")
    print("  t mic peste tot. Atunci funding-ul si OI-ul descriu trecutul fara sa")
    print("  spuna nimic despre viitor, si nu merita construita nicio strategie.")
    print()
    print("  ATENTIE la esantion: 30 de zile inseamna ~720 de ore suprapuse, nu 720")
    print("  de observatii independente. Randamentele care se suprapun sunt corelate,")
    print("  deci t-ul real e mai mic decat cel afisat. Trateaza pragul de 2.0 ca")
    print("  minim absolut, nu ca dovada.")
    print("=" * 86)
    return 0


if __name__ == "__main__":
    sys.exit(main())
