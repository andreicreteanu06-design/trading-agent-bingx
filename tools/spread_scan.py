"""
Masoara spread-ul bid/ask curent pe universul candidat, ca sa decidem daca
merita taiata coada iliquida din universul cross-sectional.

    python tools\\spread_scan.py                  # top 60 dupa volum (implicit)
    python tools\\spread_scan.py --pool 80

Nu are nevoie de chei API. O singura instantanee, nu un logger continuu -
spread-ul variaza in timpul zilei, dar pentru decizia "care treime e mult
mai scumpa decat restul" o instantanee e suficienta; diferenta intre coada
iliquida si restul e de ordinul a 5-10x, nu se schimba de la o ora la alta.

DE CE EXISTA

`tools/edge_scan.py::pick_universe` alege universul doar dupa volumul pe 24h.
Volumul mare nu garanteaza spread mic - o moneda poate avea volum de wash
trading sau volum concentrat pe cateva schimburi mari, dar lichiditate slaba
chiar pe BingX. Costul modelat in backtest (10bps: 5 fee + 5 slippage, vezi
[[liquidity-data-sources]]) e o ipoteza medie; pentru jumatatea iliquida a
universului e optimist.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchange.bingx_client import BingXClient  # noqa: E402
from tools.edge_scan import is_synthetic_product  # noqa: E402

DEFAULT_PATH = "logs/spread_scan.json"


def scan(pool: int) -> list[dict]:
    client = BingXClient()
    client.load_markets()
    tickers = client._exchange.fetch_tickers()

    rows = []
    for sym, t in tickers.items():
        if not sym.endswith("/USDT:USDT"):
            continue
        base = sym.split("/")[0]
        if is_synthetic_product(base):
            continue
        vol = t.get("quoteVolume") or 0.0
        bid = t.get("bid")
        ask = t.get("ask")
        if not vol or not bid or not ask or bid <= 0 or ask <= 0:
            continue
        mid = (bid + ask) / 2
        spread_bps = (ask - bid) / mid * 10_000
        rows.append({"symbol": sym, "volume_24h": float(vol),
                     "bid": bid, "ask": ask, "spread_bps": round(spread_bps, 2)})

    rows.sort(key=lambda r: r["volume_24h"], reverse=True)
    return rows[:pool]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pool", type=int, default=60,
                    help="cati candidati (dupa volum) sa evalueze")
    p.add_argument("--path", default=DEFAULT_PATH)
    args = p.parse_args()

    rows = scan(args.pool)
    if not rows:
        print("  Niciun simbol gasit.")
        return 1

    spreads = sorted(r["spread_bps"] for r in rows)
    n = len(spreads)
    median = spreads[n // 2]
    q3 = spreads[int(n * 0.75)]
    p90 = spreads[int(n * 0.90)]

    print()
    print("=" * 78)
    print(f"  SPREAD BID/ASK   (top {len(rows)} dupa volum 24h, instantanee)")
    print("=" * 78)
    print(f"  Median spread : {median:.1f}bps")
    print(f"  Cuartila 3    : {q3:.1f}bps")
    print(f"  Percentila 90 : {p90:.1f}bps")
    print()
    print(f"  {'simbol':<18} {'volum 24h':>16} {'spread':>10}")
    print("  " + "-" * 48)
    for r in sorted(rows, key=lambda r: r["spread_bps"], reverse=True):
        print(f"  {r['symbol']:<18} {r['volume_24h']:>16,.0f} {r['spread_bps']:>8.1f}bps")

    out = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "pool": args.pool,
        "median_bps": median,
        "q3_bps": q3,
        "p90_bps": p90,
        "rows": rows,
    }
    os.makedirs(os.path.dirname(args.path) or ".", exist_ok=True)
    with open(args.path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(f"\n  Salvat in {args.path}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
