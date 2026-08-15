"""
Rulare backtest pe date istorice BingX.

    python -m backtest.run
    python -m backtest.run --symbol "BTC/USDT:USDT" --candles 4000
    python -m backtest.run --equity 500 --save

Nu are nevoie de chei API - foloseste date publice.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg
from backtest.engine import Backtester, save_report
from exchange.bingx_client import BingXClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("backtest")

C = cfg.CONFIG


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest strategia pe istoric BingX")
    parser.add_argument("--symbol", action="append", help="implicit: toate din config")
    parser.add_argument("--candles", type=int, default=3000, help="lumanari LTF de adus")
    parser.add_argument("--equity", type=float, default=1000.0)
    parser.add_argument("--save", action="store_true", help="salveaza raportul JSON")
    parser.add_argument("--trades", action="store_true", help="listeaza fiecare trade")
    args = parser.parse_args()

    symbols = args.symbol or list(C.market.symbols)

    client = BingXClient()
    client.load_markets()
    bt = Backtester(C)

    # HTF are nevoie de mai putine lumanari, dar sa acopere aceeasi perioada.
    tf_ratio = 4  # 4h / 1h
    htf_candles = max(400, args.candles // tf_ratio + 250)

    portfolio_start = args.equity
    portfolio_end = 0.0
    all_reports = []

    for symbol in symbols:
        if not client.market_exists(symbol):
            log.error("Simbol inexistent: %s", symbol)
            continue

        log.info("Aduc istoric pentru %s...", symbol)
        try:
            htf = client.fetch_ohlcv_history(symbol, C.market.htf, htf_candles)
            ltf = client.fetch_ohlcv_history(symbol, C.market.ltf, args.candles)
        except Exception as exc:  # noqa: BLE001
            log.error("%s - eroare la istoric: %s", symbol, exc)
            continue

        log.info("  HTF %d lumanari | LTF %d lumanari", len(htf), len(ltf))
        log.info("  Perioada: %s -> %s", str(ltf.iloc[0]["datetime"])[:16],
                 str(ltf.iloc[-1]["datetime"])[:16])

        report = bt.run(symbol, htf, ltf, starting_equity=args.equity)
        all_reports.append(report)
        portfolio_end += report.ending_equity

        print("\n" + report.summary())

        if args.trades and report.trades:
            print(f"\n  {'Data':<20} {'Dir':<6} {'Motiv':<12} {'R':>7} {'PnL':>10} {'Echity':>10}")
            print("  " + "-" * 68)
            for t in report.trades:
                print(
                    f"  {t.exit_time[:19]:<20} {t.side:<6} {t.exit_reason:<12} "
                    f"{t.r_multiple:>+7.2f} {t.pnl_usdt:>+10.2f} {t.equity_after:>10.2f}"
                )

        if args.save:
            os.makedirs("logs", exist_ok=True)
            slug = symbol.replace("/", "_").replace(":", "_")
            path = f"logs/backtest_{slug}.json"
            save_report(report, path)
            log.info("  Raport salvat: %s", path)

    # ------------------------------------------------------------- concluzie
    if len(all_reports) > 1:
        print("\n" + "=" * 68)
        print("  AGREGAT PE TOATE SIMBOLURILE")
        print("=" * 68)
        total_trades = sum(r.total_trades for r in all_reports)
        total_wins = sum(r.wins for r in all_reports)
        total_r = sum(r.avg_r * r.total_trades for r in all_reports)
        avg_r = total_r / total_trades if total_trades else 0.0
        wr = total_wins / total_trades if total_trades else 0.0
        print(f"  Trades totale : {total_trades}")
        print(f"  Win rate      : {wr:.1%}")
        print(f"  R mediu       : {avg_r:+.3f}R")
        print(f"  Echity        : {portfolio_start * len(all_reports):.0f} -> {portfolio_end:.0f}")
        print("=" * 68)
        if avg_r <= 0:
            print("  VERDICT: strategia nu are edge pe aceasta perioada.")
            print("  NU trece pe bani reali. Schimba strategia, nu risk-ul.")
        else:
            print("  VERDICT: edge pozitiv pe aceasta perioada.")
            print("  Testeaza si pe alte perioade inainte de a concluziona ceva.")
        print("=" * 68)

    return 0


if __name__ == "__main__":
    sys.exit(main())
