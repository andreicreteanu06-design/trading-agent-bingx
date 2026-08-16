"""
Executor de hartie pentru strategia cross-sectionala. Fara ordine reale.

    python execution\\paper_executor.py --capital 500       # prima rulare
    python execution\\paper_executor.py                     # o rulare in plus
    python execution\\paper_executor.py --status             # doar starea curenta, fara retea
    python execution\\paper_executor.py --loop               # ruleaza continuu
    python execution\\paper_executor.py --reset              # sterge starea

Ruleaza-l periodic (manual sau printr-un task programat), la acelasi interval
la care rebalanseaza strategia (`hold` din certificatul validat, de obicei
zile). Fiecare rulare: marcheaza pozitiile existente la piata cu preturi
REALE, aplica funding-ul REAL acumulat de la ultima rulare, si daca poarta e
deschisa, calculeaza tranzactiile necesare si le "umple" la bid/ask REAL din
carte - fara sa trimita niciun ordin.

NICIUN APEL DE SCRIERE CATRE BURSA. Singurele metode BingXClient folosite aici
sunt fetch_* (citire). create_market_order si create_stop_loss nu sunt
importate, nu sunt apelate, nu apar nicaieri in acest fisier - verificabil cu
`grep create_ execution/paper_executor.py`.

DE CE EXISTA

Intre "semnal validat" si "bani reali" lipseste o verificare: cat de aproape
e randamentul de hartie de cel din backtest? Slippage-ul presupus (10bps pe
parte) e o estimare; fill-urile reale la bid/ask macar arata daca estimarea a
fost optimista sau pesimista, inainte sa conteze cu bani adevarati.

CE NU FACE

Nu deschide conturi, nu semneaza cereri autentificate, nu are nevoie de chei
API - foloseste doar date publice de piata. Nu inlocuieste validarea
walk-forward; o completeaza cu observatii din timp real.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CONFIG
from exchange.bingx_client import BingXClient
from strategy import xs_gate
from backtest.validate_xs import GRID
from tools.edge_scan import fetch_funding_panel
from tools.xs_signals import build_target_book

log = logging.getLogger("paper_executor")

STATE_PATH = "logs/paper_state.json"
LEDGER_PATH = "logs/paper_ledger.jsonl"

# Sub acest prag de pondere, o diferenta e zgomot de rotunjire, nu o decizie -
# aceeasi valoare ca in tools/xs_signals.py, ca cele doua unelte sa fie de
# acord asupra a ce inseamna "s-a schimbat cartea".
REBALANCE_THRESHOLD = 0.005


@dataclass
class Position:
    qty: float          # semnat: pozitiv = long, negativ = short
    mark_price: float    # pretul folosit ultima data ca sa marcheze pozitia


@dataclass
class PaperState:
    capital_usdt: float
    equity_usdt: float
    positions: dict[str, Position]
    last_updated_at: str | None
    started_at: str
    price_pnl_usdt: float = 0.0
    funding_paid_usdt: float = 0.0
    fees_paid_usdt: float = 0.0
    trade_count: int = 0


def load_state() -> PaperState | None:
    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)
    raw["positions"] = {s: Position(**p) for s, p in raw["positions"].items()}
    return PaperState(**raw)


def save_state(state: PaperState) -> None:
    os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
    raw = asdict(state)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, indent=2, ensure_ascii=False)


def append_ledger(row: dict) -> None:
    os.makedirs(os.path.dirname(LEDGER_PATH) or ".", exist_ok=True)
    with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def load_last_ledger_entry() -> dict | None:
    """
    Ultima rulare din jurnal, pentru afisare (dashboard, --status).

    Traieste aici, nu duplicat in app/server.py, din acelasi motiv pentru care
    GRID si build_target_book au fost extrase mai devreme in acest proiect:
    doua cititoare ale aceluiasi fisier diverg tacut la prima schimbare de
    format.
    """
    if not os.path.exists(LEDGER_PATH):
        return None
    try:
        with open(LEDGER_PATH, encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()]
        return json.loads(lines[-1]) if lines else None
    except (json.JSONDecodeError, OSError):
        return None


def funding_accrued_since(
    symbols: list[str], since: datetime | None, until: datetime
) -> dict[str, float]:
    """
    Suma exacta a decontarilor reale pentru fiecare simbol, in (since, until].

    Nu o rata presupusa - istoricul chiar intamplat, de la Binance, la fel ca
    in validare. `since=None` (prima rulare cu pozitii, teoretic imposibil
    pentru ca pozitiile abia se deschid) intoarce zero pentru toate.
    """
    if since is None or not symbols:
        return {s: 0.0 for s in symbols}

    days = max(1, int((until - since).total_seconds() // 86400) + 2)
    # cache_hours mic: executorul poate rula la cateva ore, iar cache-ul
    # implicit de 24h din fetch_funding_panel ar putea rata decontari recente.
    raw = fetch_funding_panel(symbols, days, cache_hours=1.0)

    out: dict[str, float] = {}
    for sym in symbols:
        df = raw.get(sym)
        if df is None or df.empty:
            out[sym] = 0.0
            continue
        mask = (df["datetime"] > since) & (df["datetime"] <= until)
        out[sym] = float(df.loc[mask, "funding"].sum())
    return out


def print_status(state: PaperState) -> None:
    """Starea curenta, direct din disc - fara niciun apel de retea."""
    print()
    print("=" * 78)
    print("  STARE HARTIE (din logs/paper_state.json, fara date live)")
    print("=" * 78)
    print(f"  pornit         : {state.started_at[:16]}")
    print(f"  ultima rulare  : {(state.last_updated_at or '-')[:16]}")
    print(f"  capital initial: {state.capital_usdt:.2f} USDT")
    print(f"  echitate acum  : {state.equity_usdt:.2f} USDT  "
          f"({state.equity_usdt / state.capital_usdt - 1:+.2%})")
    print(f"  P&L de pret cumulat    : {state.price_pnl_usdt:+.2f} USDT")
    print(f"  funding platit cumulat : {state.funding_paid_usdt:+.2f} USDT")
    print(f"  comisioane cumulate    : {state.fees_paid_usdt:.2f} USDT")
    print(f"  tranzactii de hartie   : {state.trade_count}")
    print()
    if not state.positions:
        print("  Nicio pozitie deschisa.")
    else:
        print(f"  {len(state.positions)} pozitii (marcate la ultima rulare, nu live):")
        for sym, pos in sorted(state.positions.items(), key=lambda kv: -abs(kv[1].qty * kv[1].mark_price)):
            side = "long " if pos.qty > 0 else "short"
            notional = abs(pos.qty * pos.mark_price)
            print(f"    {side} {sym:<22} {notional:>10.2f} USDT  @ {pos.mark_price:.6g}")
    print("=" * 78)


def run_once(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    state = load_state()

    if state is None:
        if not args.capital:
            print("Prima rulare: da --capital <suma_USDT> ca sa pornesti hartia.")
            return 1
        state = PaperState(
            capital_usdt=args.capital,
            equity_usdt=args.capital,
            positions={},
            last_updated_at=None,
            started_at=now.isoformat(),
        )
        print(f"Pornit cu {args.capital:.2f} USDT de hartie (fara bani reali).")

    client = BingXClient()
    last_dt = (
        datetime.fromisoformat(state.last_updated_at) if state.last_updated_at else None
    )

    held_symbols = list(state.positions.keys())

    print()
    print("=" * 78)
    print(f"  EXECUTOR DE HARTIE   {args.factor}   {args.tf}   "
          f"{now:%Y-%m-%d %H:%M} UTC")
    print("=" * 78)

    # --------------------------------------------- 1. marcheaza ce exista deja
    price_pnl = 0.0
    funding_pnl = 0.0
    prices_now: dict[str, float] = {}

    if held_symbols:
        prices_now = client.fetch_last_prices(held_symbols)
        missing = [s for s in held_symbols if s not in prices_now]
        if missing:
            log.warning("Fara pret curent pentru %s - marcate la ultimul pret cunoscut.",
                        ", ".join(missing))

        for sym, pos in state.positions.items():
            px = prices_now.get(sym, pos.mark_price)
            price_pnl += pos.qty * (px - pos.mark_price)

        funding = funding_accrued_since(held_symbols, last_dt, now)
        for sym, pos in state.positions.items():
            px = prices_now.get(sym, pos.mark_price)
            notional_signed = pos.qty * px
            # Aceeasi formula ca in backtest/validate_xs.py::simulate: rata
            # pozitiva inseamna long-urile platesc, deci -notional*rata.
            funding_pnl += -notional_signed * funding.get(sym, 0.0)

    equity_after_marks = state.equity_usdt + price_pnl + funding_pnl

    print(f"  echitate inainte  : {state.equity_usdt:.2f} USDT")
    if held_symbols:
        print(f"  P&L de pret       : {price_pnl:+.2f} USDT")
        print(f"  P&L de funding    : {funding_pnl:+.2f} USDT")
    print(f"  echitate dupa marcaj : {equity_after_marks:.2f} USDT")

    # --------------------------------------------------- 2. verifica poarta
    gate = xs_gate.check(args.factor, args.tf, args.universe, GRID,
                          path=xs_gate.path_for(args.factor))
    print()
    if gate.tradeable:
        print(f"  Poarta: TRANZACTIONABIL - {gate.reason}")
    else:
        print(f"  Poarta: INCHISA - {gate.reason}")
        if held_symbols:
            print("  Pozitiile existente RAMAN deschise (nu se lichideaza automat),")
            print("  dar nu se adauga risc nou cat poarta e inchisa.")

    trades: list[dict] = []
    fees_this_run = 0.0
    new_positions: dict[str, Position] = {}

    if gate.tradeable:
        # ------------------------------------------- 3. cartea tinta si delta
        try:
            w, asof, n_symbols = build_target_book(client, args.factor, args.tf, args.universe)
        except ValueError as exc:
            print(f"  Nu am putut construi cartea tinta: {exc}")
            w = None

        if w is not None:
            print(f"  cartea tinta: {n_symbols} simboluri, ultima lumanare {asof:%Y-%m-%d %H:%M} UTC")

            excluded_min: list[tuple[str, float]] = []
            target_symbols = set(w.index)
            union_symbols = target_symbols | set(held_symbols)
            missing_px = union_symbols - set(prices_now)
            if missing_px:
                prices_now.update(client.fetch_last_prices(list(missing_px)))

            for sym in union_symbols:
                px = prices_now.get(sym)
                if px is None or px <= 0:
                    log.warning("Fara pret pentru %s - simbol sarit la rebalansare.", sym)
                    continue

                old_qty = state.positions.get(sym, Position(0.0, px)).qty
                current_notional = old_qty * px
                target_weight = float(w.get(sym, 0.0))
                target_notional = target_weight * equity_after_marks
                current_weight = current_notional / equity_after_marks if equity_after_marks else 0.0

                if abs(target_weight - current_weight) <= REBALANCE_THRESHOLD:
                    if old_qty != 0.0:
                        new_positions[sym] = Position(qty=old_qty, mark_price=px)
                    continue

                # Marimea REALA acceptata de bursa, cu acelasi normalize_amount
                # care va valida si ordinele live - un simbol respins aici e
                # respins si acolo, deci hartia arata din prima ce e neexecutabil
                # la acest capital, nu doar dupa ce esueaza cu bani reali.
                closing = abs(target_notional) < 1e-9
                trade_amount = abs(old_qty) if closing else abs(target_notional) / px
                try:
                    qty_unsigned = client.normalize_amount(
                        sym, trade_amount, price=px, closing=closing,
                    )
                except ValueError as exc:
                    excluded_min.append((sym, target_weight))
                    if old_qty != 0.0:
                        new_positions[sym] = Position(qty=old_qty, mark_price=px)
                    log.info("  %s: %s", sym, exc)
                    continue

                # La inchidere pozitia rezultata e exact 0, nu marimea (fara
                # semn) a tranzactiei de inchidere - altfel un short inchis
                # (old_qty negativ) ar iesi cu new_qty pozitiv, o inversare de
                # pozitie inventata din nimic.
                new_qty = 0.0 if closing else (qty_unsigned if target_notional >= 0 else -qty_unsigned)
                delta_notional = new_qty * px - current_notional
                if abs(delta_notional) < 1e-9:
                    if old_qty != 0.0:
                        new_positions[sym] = Position(qty=old_qty, mark_price=px)
                    continue

                try:
                    book = client.fetch_order_book(sym, limit=20)
                    best_bid = float(book["bids"][0][0])
                    best_ask = float(book["asks"][0][0])
                except Exception as exc:  # noqa: BLE001
                    log.warning("Fara carte de ordine pentru %s (%s) - umplu la ultimul pret.",
                                sym, str(exc)[:60])
                    best_bid = best_ask = px

                # Cumparam -> luam din ask (platim mai mult). Vindem -> dam in
                # bid (primim mai putin). Asta e fill-ul REAL al unui ordin de
                # piata, nu pretul mid pe care il presupune backtestul.
                fill_price = best_ask if delta_notional > 0 else best_bid
                mid = (best_bid + best_ask) / 2
                slippage_bps = abs(fill_price - mid) / mid * 1e4 if mid else 0.0

                fee = abs(delta_notional) * CONFIG.taker_fee
                fees_this_run += fee

                new_positions[sym] = Position(qty=new_qty, mark_price=px)

                trades.append({
                    "symbol": sym,
                    "delta_notional_usdt": round(delta_notional, 2),
                    "fill_price": fill_price,
                    "mid_price": mid,
                    "slippage_bps": round(slippage_bps, 2),
                    "fee_usdt": round(fee, 4),
                })

            if excluded_min:
                excluded_weight = sum(abs(wt) for _, wt in excluded_min)
                print(f"\n  {len(excluded_min)} simboluri sub minimul bursei la acest capital "
                      f"({excluded_weight:.1%} din expunerea bruta tinta):")
                for sym, wt in sorted(excluded_min, key=lambda x: -abs(x[1]))[:10]:
                    print(f"    {sym:<22} pondere tinta {wt:+.2%}")
                if len(excluded_min) > 10:
                    print(f"    ... si inca {len(excluded_min) - 10}")
                print("    Cartea de hartie e mai concentrata decat cea validata -")
                print("    capital mai mare ar reduce diferenta.")

            # simbolurile ramase din vechea carte, neatinse de bucla de mai sus
            # (target 0 si sub prag ar fi trecut deja prin ramura "continue")
        else:
            new_positions = {s: Position(p.qty, prices_now.get(s, p.mark_price))
                              for s, p in state.positions.items()}
    else:
        new_positions = {s: Position(p.qty, prices_now.get(s, p.mark_price))
                          for s, p in state.positions.items()}

    equity_final = equity_after_marks - fees_this_run

    if trades:
        print()
        print(f"  {len(trades)} tranzactii de hartie:")
        for t in trades:
            verb = "cumpara" if t["delta_notional_usdt"] > 0 else "vinde  "
            print(f"    {verb} {t['symbol']:<22} {t['delta_notional_usdt']:>+10.2f} USDT"
                  f"  fill={t['fill_price']:.6g}  slippage={t['slippage_bps']:.1f}bps"
                  f"  fee={t['fee_usdt']:.4f}")
        print(f"  comisioane rulare : {fees_this_run:.2f} USDT")

    print()
    print(f"  ECHITATE FINALA   : {equity_final:.2f} USDT")
    total_return = equity_final / state.capital_usdt - 1.0 if state.capital_usdt else 0.0
    print(f"  randament de la start ({state.started_at[:10]}): {total_return:+.2%}")
    if equity_final <= 0:
        print("  ATENTIE: echitatea de hartie a atins zero sau sub - carte")
        print("  supra-levierata pentru capitalul declarat. Verifica sizing-ul.")
    print("=" * 78)

    new_state = PaperState(
        capital_usdt=state.capital_usdt,
        equity_usdt=equity_final,
        positions={s: pos for s, pos in new_positions.items() if abs(pos.qty) > 0},
        last_updated_at=now.isoformat(),
        started_at=state.started_at,
        price_pnl_usdt=state.price_pnl_usdt + price_pnl,
        funding_paid_usdt=state.funding_paid_usdt - funding_pnl,
        fees_paid_usdt=state.fees_paid_usdt + fees_this_run,
        trade_count=state.trade_count + len(trades),
    )
    save_state(new_state)

    append_ledger({
        "ts": now.isoformat(),
        "equity_before": state.equity_usdt,
        "price_pnl": price_pnl,
        "funding_pnl": funding_pnl,
        "fees": fees_this_run,
        "equity_after": equity_final,
        "gate_tradeable": gate.tradeable,
        "gate_reason": gate.reason,
        "trades": trades,
    })

    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Executor de hartie - fara ordine reale")
    p.add_argument("--capital", type=float, default=None,
                   help="capital initial USDT (necesar doar la prima rulare)")
    p.add_argument("--tf", default="4h")
    p.add_argument("--universe", type=int, default=50)
    p.add_argument("--factor", default="range_pos")
    p.add_argument("--reset", action="store_true",
                   help="sterge starea si ledger-ul, reincepe de la zero")
    p.add_argument("--status", action="store_true",
                   help="arata starea curenta din disc, fara nicio cerere de retea")
    p.add_argument("--loop", action="store_true",
                   help="ruleaza continuu, la fiecare --interval secunde")
    p.add_argument("--interval", type=int, default=4 * 3600,
                   help="secunde intre rulari in modul --loop (implicit 4h, cat tf-ul strategiei)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")

    if args.reset:
        for path in (STATE_PATH, LEDGER_PATH):
            if os.path.exists(path):
                os.remove(path)
        print("Stare de hartie stearsa.")

    if args.status:
        state = load_state()
        if state is None:
            print("Nu exista inca stare de hartie. Porneste cu --capital <suma>.")
            return 1
        print_status(state)
        return 0

    if not args.loop:
        return run_once(args)

    log.info("Executor de hartie in bucla, la fiecare %d secunde. Ctrl+C pentru oprire.",
             args.interval)
    try:
        while True:
            start = time.time()
            try:
                run_once(args)
            except Exception as exc:  # noqa: BLE001
                # O rulare picata (retea, date lipsa) nu are voie sa opreasca
                # o bucla care trebuie sa mearga zile in sir.
                log.error("rulare esuata, continui: %s", str(exc)[:160])
            sleep_for = max(30, args.interval - (time.time() - start))
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        log.info("Oprit. Starea ramane in %s", STATE_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
