"""
Backtest pentru captura de funding (delta-neutru: spot long + perp short).

    python -m backtest.basis
    python -m backtest.basis --days 700 --mode conditional
    python -m backtest.basis --symbol "BTC/USDT" --perp-leverage 3

Nu are nevoie de chei API.

CE ESTE, SI DE CE E ALTCEVA DECAT RESTUL PROIECTULUI

Toate celelalte strategii din repo incearca sa GHICEASCA unde merge pretul.
Masuratorile din aceasta sesiune au aratat ca nu reusesc: 4111 semnale cu
corelatie scor-rezultat de +0.026, adica exact cat ar da niste intrari
aleatoare.

Aceasta nu ghiceste nimic. Cumperi spot si vinzi perpetual in aceeasi cantitate.
Pretul poate merge oriunde - castigi pe o parte exact cat pierzi pe cealalta.
Ce ramane este plata de funding, pe care long-urile din perpetual o fac
short-urilor, si pe care o incasezi pentru ca esti short pe perpetual.

Nu e o predictie, e o taxa pe care o colectezi pentru un serviciu real: cineva
vrea expunere cu levier si e dispus sa plateasca pentru ea.

DE UNDE VINE RISCUL, pentru ca exista

  1. Funding NEGATIV. Cand piata e pesimista, short-urile platesc long-urilor,
     deci platesti tu. In 2.5 ani s-a intamplat ~16% din timp pe BTC, cu episoade
     de pana la 144 de ore consecutive.
  2. BAZA. Spot si perpetual nu sunt lipite perfect. Diferenta oscileaza, si
     desi revine la medie, pe termen scurt misca P&L-ul. Modelata explicit aici,
     cu preturi reale de spot si de perpetual, nu presupusa zero.
  3. LICHIDARE pe piciorul short, si aici e capcana care surprinde pe toata
     lumea: desi esti delta-neutru pe hartie, cele doua picioare stau in conturi
     cu marje SEPARATE. Daca pretul urca 10% si ai levier 10x pe perpetual,
     short-ul se lichideaza - castigul de pe spot NU alimenteaza automat marja
     de pe perpetual. Ramai long pe spot, fara acoperire, exact in momentul
     nepotrivit. Neutralitatea e reala doar cat timp ambele picioare traiesc.

     Randamentul creste cu levierul, dar cu randamente descrescatoare (masurat
     pe BTC: 1x -> +2.82%, 2x -> +3.76%, 5x -> +4.70%, 10x -> +5.13%), in timp
     ce distanta pana la lichidare scade liniar. Zona rezonabila e 2-3x.
  4. RISC DE EXCHANGE. Tii bani pe platforma luni de zile. Nu se poate modela.

CAPITALUL, pentru ca aici se ascunde adevarata rentabilitate: pentru expunere de
1000 USDT ai nevoie de 1000 in spot PLUS marja pentru short-ul de 1000 pe
perpetual. La levier 1x pe perpetual, asta inseamna 2000 de capital blocat
pentru a incasa funding-ul unei singure pozitii de 1000 - adica jumatate din
randamentul brut. Raportul de mai jos calculeaza pe capitalul TOTAL imobilizat,
nu pe notional, pentru ca doar primul e banul tau.
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("basis")

C = cfg.CONFIG


def _paginate_ohlcv(ex, symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    cursor = ex.milliseconds() - days * 24 * 3600 * 1000
    rows: list[list] = []
    for _ in range(80):
        batch = ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=1000)
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
    return df[["timestamp", "datetime", "close"]]


def _paginate_funding(ex, symbol: str, days: int) -> pd.DataFrame:
    cursor = ex.milliseconds() - days * 24 * 3600 * 1000
    rows: list[dict] = []
    for _ in range(80):
        batch = ex.fetch_funding_rate_history(symbol, since=cursor, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        last = batch[-1]["timestamp"]
        if last <= cursor:
            break
        cursor = last + 1
        if last > ex.milliseconds() - 8 * 3600 * 1000:
            break

    df = pd.DataFrame(
        [{"timestamp": r["timestamp"], "funding": float(r["fundingRate"])} for r in rows]
    ).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def build_panel(base: str, days: int) -> pd.DataFrame:
    """
    Aliniaza pret spot, pret perpetual si funding pe grila de decontare (8h).

    Folosim Binance pentru toate trei: are si spot si perpetual pe acelasi loc,
    cu istoric adanc, iar baza calculata intre doua preturi de pe acelasi
    exchange este cea reala, nu un artefact al comparatiei intre platforme.
    """
    spot_ex = ccxt.binance()
    perp_ex = ccxt.binance({"options": {"defaultType": "future"}})
    spot_ex.load_markets()
    perp_ex.load_markets()

    spot_sym = f"{base}/USDT"
    perp_sym = f"{base}/USDT:USDT"

    log.info("  aduc spot, perpetual si funding pentru %s...", base)
    spot = _paginate_ohlcv(spot_ex, spot_sym, "1h", days).rename(columns={"close": "spot"})
    perp = _paginate_ohlcv(perp_ex, perp_sym, "1h", days).rename(columns={"close": "perp"})
    fund = _paginate_funding(perp_ex, perp_sym, days)

    panel = pd.merge(spot[["datetime", "spot"]], perp[["datetime", "perp"]], on="datetime")
    # Pastram doar orele de decontare - acolo se intampla tot ce conteaza.
    panel = pd.merge(panel, fund[["datetime", "funding"]], on="datetime", how="inner")
    panel["basis"] = (panel["perp"] - panel["spot"]) / panel["spot"]
    return panel.reset_index(drop=True)


def simulate(
    panel: pd.DataFrame,
    mode: str,
    notional: float,
    perp_leverage: float,
    spot_fee: float,
    perp_fee: float,
    enter_above: float,
    exit_below: float,
) -> dict:
    """
    Simuleaza pozitia delta-neutra decont cu decont.

    `mode`:
      "always"      - intri o data si tii pana la final. Referinta.
      "conditional" - intri cand funding-ul e peste `enter_above`, iesi cand
                      scade sub `exit_below`. Evita episoadele in care platesti,
                      dar plateste comisioane la fiecare reintrare.

    Convenția de semn: fiind SHORT pe perpetual, incasezi cand funding > 0.
    """
    # Capital imobilizat: spot integral + marja pentru short-ul de pe perpetual.
    capital = notional + notional / perp_leverage

    in_position = False
    entry_spot = entry_perp = 0.0
    cash = 0.0            # funding incasat minus comisioane
    round_trips = 0
    total_fees = 0.0
    total_funding = 0.0
    bars_in = 0

    equity_curve: list[float] = []
    entry_cost = notional * (spot_fee + perp_fee)

    for _, row in panel.iterrows():
        f = float(row["funding"])
        spot = float(row["spot"])
        perp = float(row["perp"])

        # --- decizia de intrare / iesire, luata pe funding-ul CURENT (cunoscut
        # la momentul decontarii, deci fara privire in viitor)
        if mode == "always":
            want = True
        else:
            want = f > enter_above if not in_position else f > exit_below

        if want and not in_position:
            in_position = True
            entry_spot, entry_perp = spot, perp
            cash -= entry_cost
            total_fees += entry_cost
            round_trips += 1
        elif not want and in_position:
            in_position = False
            # Inchidem ambele picioare: P&L-ul directional se anuleaza, ce ramane
            # este variatia BAZEI intre intrare si iesire.
            pnl_spot = (spot - entry_spot) / entry_spot * notional
            pnl_perp = -(perp - entry_perp) / entry_perp * notional
            cash += pnl_spot + pnl_perp
            exit_cost = notional * (spot_fee + perp_fee)
            cash -= exit_cost
            total_fees += exit_cost

        if in_position:
            # Short pe perpetual: funding pozitiv = incasare.
            received = f * notional
            cash += received
            total_funding += received
            bars_in += 1
            # Marcare la piata a bazei, ca sa vedem drawdown-ul real pe parcurs
            mtm = ((spot - entry_spot) / entry_spot - (perp - entry_perp) / entry_perp) * notional
        else:
            mtm = 0.0

        equity_curve.append(capital + cash + mtm)

    # Inchidere fortata la final, daca am ramas in pozitie.
    if in_position:
        last = panel.iloc[-1]
        pnl_spot = (float(last["spot"]) - entry_spot) / entry_spot * notional
        pnl_perp = -(float(last["perp"]) - entry_perp) / entry_perp * notional
        exit_cost = notional * (spot_fee + perp_fee)
        cash += pnl_spot + pnl_perp - exit_cost
        total_fees += exit_cost

    eq = pd.Series(equity_curve)
    periods = len(panel)
    years = periods * 8 / (24 * 365) if periods else 0.0

    total_return = cash / capital if capital else 0.0
    annualized = ((1 + total_return) ** (1 / years) - 1) if years > 0 else 0.0

    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd = float(dd.min()) if len(dd) else 0.0

    rets = eq.pct_change().dropna()
    # 3 deconturi pe zi -> 1095 pe an
    sharpe = float(rets.mean() / rets.std() * math.sqrt(1095)) if rets.std() > 0 else 0.0

    return {
        "mode": mode,
        "capital": capital,
        "notional": notional,
        "total_return": total_return,
        "annualized": annualized,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "funding_collected": total_funding,
        "fees_paid": total_fees,
        "round_trips": round_trips,
        "time_in_market": bars_in / periods if periods else 0.0,
        "years": years,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest captura de funding")
    p.add_argument("--symbol", action="append", help="baza, ex: BTC (implicit BTC ETH SOL)")
    p.add_argument("--days", type=int, default=700)
    p.add_argument("--notional", type=float, default=1000.0)
    p.add_argument("--perp-leverage", type=float, default=2.0,
                   help="levier pe piciorul short; mai mare = capital mai eficient, lichidare mai aproape")
    p.add_argument("--spot-fee", type=float, default=0.001, help="taker spot (0.10%%)")
    p.add_argument("--perp-fee", type=float, default=0.0005, help="taker perp (0.05%%)")
    p.add_argument("--enter-above", type=float, default=0.00005,
                   help="intra cand funding depaseste pragul (mod conditional)")
    p.add_argument("--exit-below", type=float, default=-0.00005,
                   help="iesi cand funding scade sub prag (mod conditional)")
    args = p.parse_args()

    bases = args.symbol or ["BTC", "ETH", "SOL"]

    print()
    print("=" * 88)
    print("  CAPTURA DE FUNDING - delta-neutru (spot long + perpetual short)")
    print(f"  Notional {args.notional:.0f} USDT per picior | levier perp {args.perp_leverage}x")
    print(f"  Capital imobilizat: {args.notional + args.notional/args.perp_leverage:.0f} USDT")
    print(f"  Comisioane: spot {args.spot_fee*100:.3f}% | perp {args.perp_fee*100:.3f}% (taker, per capat)")
    print("=" * 88)

    rows = []
    for base in bases:
        try:
            panel = build_panel(base, args.days)
        except Exception as exc:  # noqa: BLE001
            log.error("  %s esuat: %s", base, str(exc)[:120])
            continue
        if len(panel) < 50:
            log.warning("  %s: prea putine deconturi (%d)", base, len(panel))
            continue

        log.info("  %s: %d deconturi, %s -> %s", base, len(panel),
                 panel.iloc[0]["datetime"].date(), panel.iloc[-1]["datetime"].date())

        for mode in ("always", "conditional"):
            res = simulate(
                panel, mode, args.notional, args.perp_leverage,
                args.spot_fee, args.perp_fee, args.enter_above, args.exit_below,
            )
            res["symbol"] = base
            res["basis_mean"] = float(panel["basis"].mean())
            res["funding_mean"] = float(panel["funding"].mean())
            rows.append(res)

    if not rows:
        print("\n  Niciun rezultat. Verifica reteaua.")
        return 1

    print()
    print(f"  {'simbol':<8} {'mod':<12} {'ani':>5} {'randament':>10} {'anualizat':>10} "
          f"{'max DD':>8} {'Sharpe':>7} {'in piata':>9} {'cicluri':>8}")
    print("  " + "-" * 84)
    for r in rows:
        print(f"  {r['symbol']:<8} {r['mode']:<12} {r['years']:>5.2f} "
              f"{r['total_return']*100:>+9.2f}% {r['annualized']*100:>+9.2f}% "
              f"{r['max_drawdown']*100:>7.2f}% {r['sharpe']:>7.2f} "
              f"{r['time_in_market']*100:>8.1f}% {r['round_trips']:>8}")

    print()
    print("  Detaliu funding vs comisioane (USDT, pe intreaga perioada):")
    print(f"  {'simbol':<8} {'mod':<12} {'funding':>12} {'comisioane':>12} {'net':>12}")
    print("  " + "-" * 60)
    for r in rows:
        net = r["funding_collected"] - r["fees_paid"]
        print(f"  {r['symbol']:<8} {r['mode']:<12} {r['funding_collected']:>+12.2f} "
              f"{r['fees_paid']:>12.2f} {net:>+12.2f}")

    best = max(rows, key=lambda r: r["annualized"])
    print()
    print("=" * 88)
    print("  CITIRE")
    print("=" * 88)
    print(f"  Cel mai bun: {best['symbol']} in mod '{best['mode']}' -> "
          f"{best['annualized']*100:+.2f}% anualizat, max drawdown {best['max_drawdown']*100:.2f}%")
    print()
    print("  Compara cu alternativa fara efort si fara risc de exchange: un cont")
    print("  de economii sau obligatiuni. Daca diferenta nu justifica riscul de")
    print("  platforma si munca de intretinere, strategia e corecta matematic si")
    print("  inutila practic - ambele lucruri pot fi adevarate simultan.")
    print()
    print("  Ce NU e modelat: riscul ca exchange-ul sa aiba probleme cat timp")
    print("  tii banii acolo luni de zile, si lichidarea piciorului short intr-o")
    print("  miscare brusca in sus daca levierul e prea mare.")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
