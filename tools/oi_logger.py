"""
Inregistreaza open interest, funding si pret. Datele care lipsesc azi nu se mai
pot recupera niciodata.

    python tools\\oi_logger.py                 # o singura citire, apoi iese
    python tools\\oi_logger.py --loop          # ruleaza continuu, la fiecare ora
    python tools\\oi_logger.py --stats         # ce s-a adunat pana acum

Nu are nevoie de chei API.

DE CE EXISTA

Studiul din tools\\funding_edge.py a aratat semne corecte pentru ipoteza
"pozitionarea aglomerata precede reversia", dar nimic nu a trecut pragul de
semnificatie. Motivul nu a fost ca ipoteza e gresita - a fost ca nu exista date
destule: BingX nu ofera deloc istoric de open interest, iar Binance da doar ~30
de zile.

Treizeci de zile inseamna cateva sute de observatii suprapuse. Sase luni ar
insemna un esantion cu care se poate decide. Diferenta dintre cele doua nu se
poate cumpara si nu se poate calcula - se poate doar astepta, si numai daca ai
inceput sa inregistrezi.

De asta acest fisier ar fi trebuit scris in prima zi a proiectului.

CE INREGISTREAZA, la fiecare ora, pentru fiecare simbol:
  - open interest de la BingX (locul unde tranzactionezi)
  - open interest de la Binance (piata dominanta, pentru context)
  - funding rate curent
  - pretul mark

FORMAT: JSONL, o linie per citire per simbol. Se adauga la sfarsit, nu se
rescrie niciodata nimic - un fisier care doar creste nu poate pierde date
printr-o intrerupere la momentul nepotrivit.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccxt

import config as cfg

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("oi")

C = cfg.CONFIG
DEFAULT_PATH = "logs/positioning.jsonl"


def _safe(fn, default=None):
    """Orice sursa poate pica. O sursa picata nu are voie sa opreasca logarea."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        log.debug("sursa indisponibila: %s", str(exc)[:80])
        return default


def snapshot(symbols: list[str]) -> list[dict]:
    """O citire completa pentru toate simbolurile."""
    bingx = ccxt.bingx({"options": {"defaultType": "swap"}})
    binance = ccxt.binance({"options": {"defaultType": "future"}})
    _safe(bingx.load_markets)
    _safe(binance.load_markets)

    now = datetime.now(timezone.utc)
    rows = []

    for symbol in symbols:
        bx_oi = _safe(lambda s=symbol: bingx.fetch_open_interest(s))
        bn_oi = _safe(lambda s=symbol: binance.fetch_open_interest(s))
        funding = _safe(lambda s=symbol: bingx.fetch_funding_rate(s))
        ticker = _safe(lambda s=symbol: bingx.fetch_ticker(s))

        price = (ticker or {}).get("last")

        # Normalizare, altfel datele nu vor fi comparabile peste sase luni:
        # BingX raporteaza open interest in USDT (`value`) si nu da `amount`,
        # Binance exact invers. Completam ce lipseste folosind pretul, ca fiecare
        # rand sa aiba ambele masuri indiferent de sursa.
        def both(raw: dict | None) -> tuple[float | None, float | None]:
            value = (raw or {}).get("openInterestValue")
            amount = (raw or {}).get("openInterestAmount")
            if value is None and amount is not None and price:
                value = amount * price
            if amount is None and value is not None and price:
                amount = value / price
            return value, amount

        bx_val, bx_amt = both(bx_oi)
        bn_val, bn_amt = both(bn_oi)

        row = {
            "ts": int(now.timestamp() * 1000),
            "datetime": now.isoformat(),
            "symbol": symbol,
            "bingx_oi_value": bx_val,
            "bingx_oi_amount": bx_amt,
            "binance_oi_value": bn_val,
            "binance_oi_amount": bn_amt,
            "funding": (funding or {}).get("fundingRate"),
            "next_funding": (funding or {}).get("fundingTimestamp"),
            "price": price,
        }
        rows.append(row)

        have = sum(1 for k in ("bingx_oi_value", "binance_oi_value", "funding", "price")
                   if row[k] is not None)
        log.info("  %-18s %d/4 campuri  pret=%s  funding=%s",
                 symbol, have, row["price"], row["funding"])

    return rows


def append(rows: list[dict], path: str = DEFAULT_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def show_stats(path: str = DEFAULT_PATH) -> int:
    if not os.path.exists(path):
        print(f"\n  Inca nu exista {path}.")
        print("  Porneste inregistrarea cu:  python tools\\oi_logger.py --loop\n")
        return 1

    import pandas as pd

    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    if not rows:
        print("  Fisier gol.")
        return 1

    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True, format="mixed")
    span_h = (df["datetime"].max() - df["datetime"].min()).total_seconds() / 3600

    print()
    print("=" * 70)
    print(f"  DATE DE POZITIONARE ADUNATE   ({path})")
    print("=" * 70)
    print(f"  Inregistrari : {len(df)}")
    print(f"  Perioada     : {df['datetime'].min():%Y-%m-%d %H:%M} -> "
          f"{df['datetime'].max():%Y-%m-%d %H:%M} UTC")
    print(f"  Acoperire    : {span_h/24:.1f} zile")
    print()
    print(f"  {'simbol':<20} {'obs':>6} {'OI BingX':>8} {'OI Binance':>11} {'funding':>8}")
    print("  " + "-" * 60)
    for sym, sub in df.groupby("symbol"):
        print(f"  {sym:<20} {len(sub):>6} "
              f"{sub['bingx_oi_value'].notna().sum():>8} "
              f"{sub['binance_oi_value'].notna().sum():>11} "
              f"{sub['funding'].notna().sum():>8}")

    print()
    need = 180 * 24 / max(len(df) / max(span_h, 1), 1) if span_h > 0 else 0
    if span_h < 24 * 30:
        print(f"  Ai {span_h/24:.1f} zile. Studiul de pozitionare devine util de la")
        print("  ~90 de zile si concludent pe la 180. Lasa loggerul sa ruleze.")
    else:
        print(f"  {span_h/24:.0f} zile adunate - deja peste ce ofera Binance public.")
        print("  Ruleaza tools\\funding_edge.py ca sa reevaluezi ipoteza.")
    print("=" * 70)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Logger de open interest si funding")
    p.add_argument("--symbol", action="append")
    p.add_argument("--loop", action="store_true", help="ruleaza continuu")
    p.add_argument("--interval", type=int, default=3600, help="secunde intre citiri")
    p.add_argument("--path", default=DEFAULT_PATH)
    p.add_argument("--stats", action="store_true", help="doar arata ce s-a adunat")
    args = p.parse_args()

    if args.stats:
        return show_stats(args.path)

    symbols = args.symbol or list(C.market.symbols)

    if not args.loop:
        log.info("Citire unica pentru %d simboluri...", len(symbols))
        append(snapshot(symbols), args.path)
        log.info("Scris in %s", args.path)
        return 0

    log.info("Inregistrez la fiecare %d secunde. Ctrl+C pentru oprire.", args.interval)
    log.info("Fisier: %s", args.path)
    try:
        while True:
            start = time.time()
            try:
                append(snapshot(symbols), args.path)
            except Exception as exc:  # noqa: BLE001
                # O eroare de retea nu are voie sa opreasca o inregistrare care
                # trebuie sa mearga luni de zile.
                log.error("citire esuata, continui: %s", str(exc)[:120])
            sleep_for = max(10, args.interval - (time.time() - start))
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        log.info("Oprit. Datele adunate raman in %s", args.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
