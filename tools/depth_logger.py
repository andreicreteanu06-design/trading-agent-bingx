"""
Inregistreaza adancimea cartii de ordine (top 5 nivele bid/ask), in timp real,
de la OKX, pentru cercetarea de scalping pe BTC/ETH/SOL.

    python tools\\depth_logger.py              # ruleaza pana la Ctrl+C
    python tools\\depth_logger.py --stats      # ce s-a adunat pana acum

Nu are nevoie de chei API.

DE CE EXISTA

Analiza tehnica clasica (RSI/MACD/StochRSI pe OHLCV) nu are edge pe 5m -
4111 semnale, corelatie scor-rezultat +0.026 (vezi no-edge-in-classic-ta).
OHLCV e o rezumare prea grosiera pentru orizontul de scalping: arata unde a
fost pretul, nu cine sta gata sa-l miste. Adancimea cartii de ordine
(dezechilibrul bid/ask) e materia prima pe care se bazeaza orice edge de
microstructura - daca exista unul, aici ar trebui cautat, nu in inca un
indicator derivat din pret.

Ca si la lichidari si open interest: istoricul nu se poate cumpara
retroactiv. Se poate doar inregistra, si numai de acum inainte. Studiul
devine posibil abia dupa ce se aduna suficiente zile - nu exista scurtatura.

DE CE OKX SI NU BINANCE

Binance futures WebSocket e blocat din reteaua asta (vezi liq_logger.py
pentru diagnosticul complet). OKX raspunde normal si difuzeaza `books5`
(top 5 nivele) cu actualizari la fiecare schimbare, fara sa ceara chei.

DE CE 1 SECUNDA, NU 1 MINUT

liq_logger si oi_logger agrega in galeti de un minut pentru ca strategia lor
tinta rebalanseaza la ore/zile. Scalpingul opereaza pe orizont de secunde
pana la cateva minute - o galeata de un minut ar sterge exact semnalul cautat.
Se scrie cel mult o instantanee pe secunda per simbol (OKX poate impinge mai
des; excesul e aruncat, nu agregat) - suficient de fin pentru studiu, fara sa
umple discul cu sute de randuri pe secunda.

CE INREGISTREAZA

Per instantanee: cel mai bun bid/ask, marimile insumate pe top 5 nivele pe
fiecare parte, dezechilibrul (bid-ask)/(bid+ask) intre -1 si 1, spread-ul in
bps si micropretul (media ponderata cu marimea opusa). Marimile raman in
contracte, nu in USDT - pentru un raport intre cele doua parti ale aceluiasi
simbol, multiplicatorul de contract se simplifica si nu conteaza.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("depth")

STREAM = "wss://ws.okx.com:8443/ws/v5/public"
SYMBOLS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
SUBSCRIBE = json.dumps({
    "op": "subscribe",
    "args": [{"channel": "books5", "instId": s} for s in SYMBOLS],
})
DEFAULT_PATH = "logs/depth.jsonl"
IDLE_TIMEOUT = 20


def to_ccxt_symbol(inst_id: str) -> str:
    base, quote, _ = inst_id.split("-", 2)
    return f"{base}/{quote}:{quote}"


def parse_snapshot(item: dict, inst_id: str) -> dict | None:
    bids = item.get("bids") or []
    asks = item.get("asks") or []
    if not bids or not asks:
        return None

    try:
        best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
        bid_sz = sum(float(lvl[1]) for lvl in bids[:5])
        ask_sz = sum(float(lvl[1]) for lvl in asks[:5])
        ts = int(item.get("ts") or 0)
    except (TypeError, ValueError, IndexError):
        return None
    if best_bid <= 0 or best_ask <= 0 or ts <= 0 or (bid_sz + ask_sz) <= 0:
        return None

    mid = (best_bid + best_ask) / 2
    microprice = (best_bid * ask_sz + best_ask * bid_sz) / (bid_sz + ask_sz)
    imbalance = (bid_sz - ask_sz) / (bid_sz + ask_sz)
    spread_bps = (best_ask - best_bid) / mid * 10_000

    return {
        "ts": ts,
        "datetime": datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat(),
        "symbol": to_ccxt_symbol(inst_id),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": round(mid, 8),
        "microprice": round(microprice, 8),
        "bid_sz_top5": round(bid_sz, 6),
        "ask_sz_top5": round(ask_sz, 6),
        "imbalance": round(imbalance, 6),
        "spread_bps": round(spread_bps, 3),
    }


def append(rows: list[dict], path: str) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


async def run(path: str) -> None:
    log.info("Ascult %s pentru %s", STREAM, ", ".join(SYMBOLS))
    log.info("Fisier: %s", path)

    last_written_sec: dict[str, int] = {}
    buffer: list[dict] = []
    written = 0
    backoff = 1

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

                        if not raw or raw == "pong":
                            continue

                        msg = json.loads(raw)
                        inst_id = (msg.get("arg") or {}).get("instId")
                        if not inst_id:
                            continue

                        for item in msg.get("data") or []:
                            row = parse_snapshot(item, inst_id)
                            if row is None:
                                continue
                            sec = row["ts"] // 1000
                            # Cel mult o instantanee pe secunda per simbol -
                            # restul actualizarilor din aceeasi secunda sunt
                            # aruncate, nu agregate.
                            if last_written_sec.get(inst_id) == sec:
                                continue
                            last_written_sec[inst_id] = sec
                            buffer.append(row)

                        if len(buffer) >= 20:
                            append(buffer, path)
                            written += len(buffer)
                            buffer = []
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # O intrerupere de retea nu are voie sa opreasca o inregistrare
                # care trebuie sa mearga zile/saptamani.
                log.warning("conexiune pierduta (%s), reincerc in %ds",
                            str(exc)[:80], backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
    finally:
        if buffer:
            append(buffer, path)
            written += len(buffer)
        log.info("Scrise %d instantanee in total.", written)


def show_stats(path: str) -> int:
    if not os.path.exists(path):
        print(f"\n  Inca nu exista {path}.")
        print("  Porneste inregistrarea cu:  python tools\\depth_logger.py\n")
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
    print(f"  ADANCIME CARTE DE ORDINE   ({path})")
    print("=" * 70)
    print(f"  Instantanee  : {len(df):,}")
    print(f"  Simboluri    : {sorted(df['symbol'].unique())}")
    print(f"  Perioada     : {df['datetime'].min():%Y-%m-%d %H:%M} -> "
          f"{df['datetime'].max():%Y-%m-%d %H:%M} UTC")
    print(f"  Acoperire    : {span_h/24:.2f} zile")
    print()

    for sym, g in df.groupby("symbol"):
        print(f"  {sym:<16} instantanee={len(g):>7}  "
              f"spread median={g['spread_bps'].median():.2f}bps  "
              f"|imbalance| median={g['imbalance'].abs().median():.3f}")

    print()
    if span_h < 24 * 14:
        print(f"  Ai {span_h/24:.1f} zile. Un prim test orientativ e posibil "
              "de la ~14 zile, unul serios de la ~60.")
        print("  Lasa loggerul sa ruleze.")
    else:
        print(f"  {span_h/24:.0f} zile adunate - se poate testa o ipoteza.")
    print("=" * 70)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Logger de adancime carte de ordine (OKX)")
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
