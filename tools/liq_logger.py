"""
Inregistreaza lichidarile fortate reale, in timp real, de la OKX.

    python tools\\liq_logger.py              # ruleaza pana la Ctrl+C
    python tools\\liq_logger.py --stats      # ce s-a adunat pana acum

Nu are nevoie de chei API.

DE CE EXISTA, SI DE CE NU COINGLASS

"Harta de lichidari" de pe Coinglass nu e o masuratoare, e un model: estimeaza
unde s-ar afla pozitiile cu levier presupunand o distributie de levier peste
open interest. Metodologia e proprietara si nepublicata, produsul e in spatele
unui paywall, iar graficul e randat pe canvas - deci nu exista serie istorica
bruta pe care sa se poata rula o validare walk-forward. Un semnal care nu poate
fi masurat nu are ce cauta intr-un agent care tranzactioneaza.

Lichidarile efective, in schimb, sunt publice si gratuite, difuzate de burse in
momentul in care se intampla. Sunt observate, nu estimate - strict superioare
unei presupuneri, si exact materia prima pe care o agrega si Coinglass.

Ca si la open interest: istoricul nu se poate cumpara. Se poate doar inregistra,
si numai de acum inainte.

DE CE OKX SI NU BINANCE

Binance are stream-ul cel mai bogat (`!forceOrder@arr`), dar `fstream.binance.com`
nu livreaza niciun cadru din reteaua de aici: handshake-ul reuseste, apoi tacere.
Verificat cu un stream de control aglomerat (btcusdt@aggTrade, zero mesaje in
90s), in timp ce Binance SPOT, Bybit si OKX raspund normal - deci nu e nici
codul, nici sandbox-ul, ci blocarea derivatelor Binance pe aceasta ruta. REST-ul
pe futures Binance merge in continuare, si e folosit mai departe de oi_logger.

OKX difuzeaza toate perpetuals-urile printr-un singur abonament si marcheaza
explicit `posSide`, deci nu exista capcana de semn.

CE INSEAMNA ASTA PENTRU DATE

OKX e o felie din piata, nu piata intreaga - volumele absolute inregistrate aici
vor fi sub totalul real. Pentru intrebarea care conteaza in acest proiect (care
monede vad lichidari disproportionate FATA DE CELELALTE) un esantion consecvent
e suficient, fiindca semnalul e cross-sectional si se compara ranguri, nu sume.
Nu folosi aceste cifre ca estimare a lichidarilor totale din piata.

CE INREGISTREAZA

Evenimentele brute sunt agregate in galeti de un minut per simbol. La orizontul
la care tranzactioneaza acest proiect (rebalansare la ~5 zile) o rezolutie mai
fina nu adauga nimic, dar ar inmulti volumul de o suta de ori. Se pastreaza
totusi pretul mediu ponderat si cel mai mare eveniment din fiecare galeata, ca
sa ramana posibila reconstructia nivelelor de pret unde s-a concentrat durerea.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("liq")

STREAM = "wss://ws.okx.com:8443/ws/v5/public"
INSTRUMENTS = "https://www.okx.com/api/v5/public/instruments"
SUBSCRIBE = json.dumps({
    "op": "subscribe",
    "args": [{"channel": "liquidation-orders", "instType": "SWAP"}],
})
DEFAULT_PATH = "logs/liquidations.jsonl"
FLUSH_AFTER_MS = 90_000
IDLE_TIMEOUT = 20


def contract_values() -> dict[str, float]:
    """
    instId -> cati bani de baza inseamna un contract.

    Fara asta cifrele sunt fara sens: un contract BTC e 0.01 BTC, unul DOGE e
    1000 DOGE. `sz` din stream vine in contracte, nu in moneda.
    """
    resp = requests.get(INSTRUMENTS, params={"instType": "SWAP"}, timeout=20)
    resp.raise_for_status()
    out = {}
    for inst in resp.json().get("data", []):
        try:
            out[inst["instId"]] = float(inst["ctVal"]) * float(inst.get("ctMult") or 1)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def to_ccxt_symbol(inst_id: str) -> str:
    """BTC-USDT-SWAP -> BTC/USDT:USDT, formatul folosit in restul proiectului."""
    base, quote, _ = inst_id.split("-", 2)
    return f"{base}/{quote}:{quote}"


class Bucket:
    """Agregat de un minut, pentru un simbol."""

    __slots__ = ("long_usdt", "short_usdt", "n_long", "n_short",
                 "px_notional", "notional", "max_usdt", "max_price",
                 "lo_price", "hi_price")

    def __init__(self) -> None:
        self.long_usdt = 0.0
        self.short_usdt = 0.0
        self.n_long = 0
        self.n_short = 0
        self.px_notional = 0.0
        self.notional = 0.0
        self.max_usdt = 0.0
        self.max_price = 0.0
        self.lo_price = float("inf")
        self.hi_price = 0.0

    def add(self, price: float, usdt: float, is_long: bool) -> None:
        if is_long:
            self.long_usdt += usdt
            self.n_long += 1
        else:
            self.short_usdt += usdt
            self.n_short += 1
        self.px_notional += price * usdt
        self.notional += usdt
        if usdt > self.max_usdt:
            self.max_usdt = usdt
            self.max_price = price
        self.lo_price = min(self.lo_price, price)
        self.hi_price = max(self.hi_price, price)

    def row(self, minute_ms: int, symbol: str) -> dict:
        return {
            "ts": minute_ms,
            "datetime": datetime.fromtimestamp(minute_ms / 1000, timezone.utc).isoformat(),
            "symbol": symbol,
            "long_liq_usdt": round(self.long_usdt, 2),
            "short_liq_usdt": round(self.short_usdt, 2),
            "n_long": self.n_long,
            "n_short": self.n_short,
            "vwap": round(self.px_notional / self.notional, 8) if self.notional else None,
            "max_liq_usdt": round(self.max_usdt, 2),
            "max_liq_price": self.max_price,
            "lo_price": self.lo_price if self.lo_price != float("inf") else None,
            "hi_price": self.hi_price,
        }


def parse_message(msg: dict, ctvals: dict[str, float]) -> list[tuple[int, str, float, float, bool]]:
    """(minut_ms, simbol, pret, notional_usdt, pozitia_lichidata_era_long)"""
    out = []
    for item in msg.get("data") or []:
        inst_id = item.get("instId") or ""
        if not inst_id.endswith("-USDT-SWAP"):
            continue
        ctval = ctvals.get(inst_id)
        if not ctval:
            continue

        symbol = to_ccxt_symbol(inst_id)
        for det in item.get("details") or []:
            try:
                price = float(det.get("bkPx") or 0)
                size = float(det.get("sz") or 0)
                ts = int(det.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0 or size <= 0 or ts <= 0:
                continue
            notional = size * ctval * price
            out.append((ts - (ts % 60_000), symbol, price, notional,
                        det.get("posSide") == "long"))
    return out


def append(rows: list[dict], path: str) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def flush(buckets: dict, path: str, now_ms: int, force: bool = False) -> int:
    """Scrie galetile suficient de vechi incat sa nu mai primeasca evenimente."""
    ready = [k for k in buckets if force or now_ms - k[0] > FLUSH_AFTER_MS]
    rows = [buckets.pop(k).row(k[0], k[1]) for k in sorted(ready)]
    append(rows, path)
    return len(rows)


async def run(path: str) -> None:
    ctvals = contract_values()
    log.info("Marimi de contract pentru %d instrumente.", len(ctvals))

    buckets: dict[tuple[int, str], Bucket] = defaultdict(Bucket)
    written = 0
    backoff = 1

    log.info("Ascult %s", STREAM)
    log.info("Fisier: %s", path)

    try:
        while True:
            try:
                async with websockets.connect(STREAM, ping_interval=20,
                                              open_timeout=15) as ws:
                    await ws.send(SUBSCRIBE)
                    backoff = 1
                    log.info("Conectat.")

                    while True:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), IDLE_TIMEOUT)
                        except asyncio.TimeoutError:
                            # OKX inchide conexiunea dupa 30s de tacere.
                            await ws.send("ping")
                            raw = None

                        if raw and raw != "pong":
                            msg = json.loads(raw)
                            for minute, sym, px, usdt, is_long in parse_message(msg, ctvals):
                                buckets[(minute, sym)].add(px, usdt, is_long)

                        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                        n = flush(buckets, path, now_ms)
                        if n:
                            written += n
                            log.info("scris %d galeti (total %d), in asteptare %d",
                                     n, written, len(buckets))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # O intrerupere de retea nu are voie sa opreasca o inregistrare
                # care trebuie sa mearga luni de zile.
                log.warning("conexiune pierduta (%s), reincerc in %ds",
                            str(exc)[:80], backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
    finally:
        # Fara asta, tot ce s-a adunat de la ultimul flush moare la oprire.
        n = flush(buckets, path, 0, force=True)
        if n:
            log.info("scris %d galeti ramase la oprire", n)


def show_stats(path: str) -> int:
    if not os.path.exists(path):
        print(f"\n  Inca nu exista {path}.")
        print("  Porneste inregistrarea cu:  python tools\\liq_logger.py\n")
        return 1

    import pandas as pd

    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    if not rows:
        print("  Fisier gol.")
        return 1

    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True, format="mixed")
    span_h = (df["datetime"].max() - df["datetime"].min()).total_seconds() / 3600
    df["total"] = df["long_liq_usdt"] + df["short_liq_usdt"]

    print()
    print("=" * 74)
    print(f"  LICHIDARI INREGISTRATE   ({path})")
    print("=" * 74)
    print(f"  Galeti       : {len(df):,}")
    print(f"  Simboluri    : {df['symbol'].nunique()}")
    print(f"  Perioada     : {df['datetime'].min():%Y-%m-%d %H:%M} -> "
          f"{df['datetime'].max():%Y-%m-%d %H:%M} UTC")
    print(f"  Acoperire    : {span_h/24:.2f} zile")
    print(f"  Volum total  : ${df['total'].sum():,.0f}   (doar OKX, nu piata intreaga)")
    print(f"    long-uri lichidate  : ${df['long_liq_usdt'].sum():,.0f}")
    print(f"    short-uri lichidate : ${df['short_liq_usdt'].sum():,.0f}")
    print()

    top = df.groupby("symbol")["total"].sum().nlargest(12)
    print(f"  {'simbol':<20} {'total lichidat':>18} {'galeti':>8}")
    print("  " + "-" * 50)
    for sym, tot in top.items():
        print(f"  {sym:<20} {tot:>18,.0f} {int((df['symbol'] == sym).sum()):>8}")

    print()
    if span_h < 24 * 90:
        print(f"  Ai {span_h/24:.1f} zile. Studiul devine util de la ~90 de zile.")
        print("  Lasa loggerul sa ruleze.")
    else:
        print(f"  {span_h/24:.0f} zile adunate - se poate testa o ipoteza.")
    print("=" * 74)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Logger de lichidari fortate (OKX)")
    p.add_argument("--path", default=DEFAULT_PATH)
    p.add_argument("--stats", action="store_true", help="doar arata ce s-a adunat")
    args = p.parse_args()

    if args.stats:
        return show_stats(args.path)

    try:
        asyncio.run(run(args.path))
    except KeyboardInterrupt:
        log.info("Oprit. Datele adunate raman in %s", args.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
