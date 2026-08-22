"""
Executor REAL pentru cartea cross-sectionala. Trimite ordine cu bani adevarati.

    python execution\\live_executor.py                    # doar arata ce ar trimite
    python execution\\live_executor.py --armed            # TRIMITE ORDINE REALE
    python execution\\live_executor.py --flatten --armed  # inchide TOT, urgenta
    python execution\\live_executor.py --clear-open       # dupa o rebalansare intrerupta

CE TREBUIE SA FIE ADEVARAT SIMULTAN CA SA PLECE UN ORDIN

  1. TRADING_MODE=live in mediu
  2. --armed pe linia de comanda
  3. chei API prezente
  4. frana de drawdown neactivata
  5. poarta de validare deschisa, cu certificat care contine hold si vol_scale
  6. rebalansarea sa fie scadenta

Lipseste oricare, nu pleaca nimic. Implicit, fara niciun flag, e o rulare
seaca: afiseaza exact ordinele pe care le-ar trimite si iese. Asta e starea
normala; --armed e exceptia deliberata.

DE CE NU EXISTA STOP-LOSS

Strategia validata nu are stopuri si nu poate avea. E o carte dollar-neutral cu
40 de picioare: daca stopurile ies pe rand, ce ramane nu mai e neutru, e o
pozitie directionala pe care nimeni n-a masurat-o. Protectia nu e per pozitie,
e la nivel de carte - frana de drawdown din execution/brake.py.

Dar o frana doar OPRESTE RISCUL NOU; pozitiile deschise raman. De aceea exista
`--flatten`: iesirea de urgenta, apasata de om, care inchide tot la piata.

SURSA DE ADEVAR E BURSA, NU DISCUL

Spre deosebire de executorul de hartie, aici pozitiile si echitatea se citesc de
la bursa la fiecare rulare. Un fisier local care crede ca detine 40 de pozitii
cand bursa are 39 e exact felul in care se pierd bani fara sa observe nimeni.
Discul tine doar ce bursa nu poate spune: cand a fost ultima rebalansare.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg
from backtest.validate_xs import GRID
from exchange.bingx_client import BingXClient
from execution.brake import (
    LIVE_STATE_PATH as BRAKE_STATE_PATH,
    book_brake,
    status_line as brake_status,
)
from execution.paper_executor import rebalance_due, single_instance
from execution.rebalance import build_plan
from strategy import xs_gate

log = logging.getLogger("live_executor")

STATE_PATH = "logs/live_state.json"
JOURNAL_PATH = "logs/live_journal.jsonl"
LOCK_PATH = "logs/live_executor.lock"

# Costul minim pe ordin la BingX e 2 USDT. O carte de 40-50 de picioare cu
# expunere bruta 1x are nevoie de mult mai mult ca fiecare picior sa treaca de
# el; sub 20 USDT nu trece niciunul, deci nu are rost nici sa aducem datele.
# Deasupra pragului lasam planul sa raporteze onest cate picioare cad sub minim -
# acela e semnalul ca respectivul capital e prea mic pentru cartea validata.
MIN_VIABLE_EQUITY = 20.0


@dataclass
class LiveState:
    """
    Doar ce bursa NU poate spune. Pozitiile si echitatea nu stau aici niciodata -
    se citesc de acolo, ca sa nu existe doua adevaruri care pot diverge.
    """

    started_at: str
    last_rebalance_at: str | None = None
    # Rebalansarea in curs, daca una a fost intrerupta. Prezenta ei blocheaza
    # pornirea unei noi rebalansari pana cand omul confirma ce s-a intamplat.
    open_rebalance: dict | None = None
    rebalance_count: int = 0
    notes: list = field(default_factory=list)


def load_state() -> LiveState | None:
    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH, encoding="utf-8") as fh:
        return LiveState(**json.load(fh))


def save_state(state: LiveState) -> None:
    os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(asdict(state), fh, indent=2, ensure_ascii=False)
    # Scriere atomica: un proces omorat la mijloc lasa fisierul vechi intact,
    # nu unul pe jumatate scris pe care load_state() l-ar respinge la pornire.
    os.replace(tmp, STATE_PATH)


def journal(event: str, **fields) -> None:
    """
    Jurnalul pleaca pe disc INAINTE de ordin, nu dupa.

    Daca procesul moare intre scriere si confirmare, ramane o intrare "sent"
    fara "filled" - ceea ce e exact informatia de care ai nevoie ca sa stii unde
    sa te uiti. Un jurnal scris dupa fill nu ar contine nimic despre ordinul care
    a plecat si nu s-a intors.
    """
    os.makedirs(os.path.dirname(JOURNAL_PATH) or ".", exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    with open(JOURNAL_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def client_order_id(rebalance_id: str, symbol: str) -> str:
    """
    Acelasi id pentru aceeasi (rebalansare, simbol), mereu.

    Daca reteaua cade dupa ce ordinul a plecat dar inainte de confirmare, un
    retry trimite ACELASI id, iar bursa il respinge ca duplicat in loc sa
    deschida a doua pozitie. Fara asta, orice retry e un pariu.
    """
    raw = f"{rebalance_id}:{symbol}".encode()
    return "xs" + hashlib.sha256(raw).hexdigest()[:20]


def exchange_positions(client: BingXClient) -> dict[str, float]:
    """Pozitiile reale, ca simbol -> cantitate semnata."""
    out: dict[str, float] = {}
    for p in client.fetch_open_positions():
        sym = p.get("symbol")
        qty = float(p.get("contracts") or 0.0)
        if not sym or not qty:
            continue
        out[sym] = -qty if str(p.get("side", "")).lower() == "short" else qty
    return out


def preflight(args) -> tuple[bool, list[str]]:
    """Toate conditiile care trebuie adevarate ca sa plece un ordin."""
    problems: list[str] = []

    if cfg.TRADING_MODE != "live":
        problems.append(f"TRADING_MODE={cfg.TRADING_MODE} (trebuie 'live')")
    if not args.armed:
        problems.append("lipseste --armed")
    if not cfg.BINGX_API_KEY or not cfg.BINGX_SECRET_KEY:
        problems.append("chei API absente")

    return (not problems), problems


def run_once(args) -> int:
    now = datetime.now(timezone.utc)
    state = load_state() or LiveState(started_at=now.isoformat())

    armed, blockers = preflight(args)

    print()
    print("=" * 78)
    print(f"  EXECUTOR REAL   {args.factor}   {args.tf}   {now:%Y-%m-%d %H:%M} UTC")
    print(f"  {'ARMAT - ORDINELE PLEACA' if armed else 'RULARE SEACA - nu pleaca nimic'}")
    if blockers:
        print(f"  blocat de: {'; '.join(blockers)}")
    print("=" * 78)

    client = BingXClient(cfg.BINGX_API_KEY, cfg.BINGX_SECRET_KEY)

    # ------------------------------------------------ 1. adevarul de la bursa
    if not client.authenticated:
        print("  Fara chei API nu pot citi pozitiile reale. Ma opresc.")
        return 1

    positions = exchange_positions(client)
    equity = client.fetch_equity_usdt()
    if equity is None:
        print("  Nu am putut citi echitatea contului. Ma opresc.")
        return 1

    print(f"  echitate reala : {equity:.2f} USDT")
    print(f"  pozitii la bursa: {len(positions)}")

    if equity < MIN_VIABLE_EQUITY and not positions:
        # Nu e o limita de risc, e o gardă împotriva muncii inutile: sub acest
        # prag fiecare picior al cartii cade sub costul minim al bursei (2 USDT),
        # deci planul ar aduce 50 de simboluri ca sa produca zero tranzactii.
        #
        # Pragul NU e zero pentru ca un cont "gol" nu are echitate zero: acesta
        # avea 0.0004 USDT ramase, si o verificare pe zero exact nu a prins-o.
        print(f"  Sub {MIN_VIABLE_EQUITY:.0f} USDT nu se poate construi cartea - "
              f"fiecare picior ar cadea sub minimul bursei.")
        print("  (pe BingX portofelul spot si cel de futures sunt separate;")
        print("   daca ai fonduri, verifica daca sunt in cel de futures)")
        return 0

    # ------------------------------- 2. rebalansare intrerupta ramasa in aer?
    if state.open_rebalance:
        print()
        print("  REBALANSARE INTRERUPTA gasita in stare:")
        print(f"    id {state.open_rebalance.get('id')} "
              f"pornita {str(state.open_rebalance.get('started_at'))[:19]}")
        print(f"    {state.open_rebalance.get('sent', 0)} ordine trimise din "
              f"{state.open_rebalance.get('total', 0)}")
        print("  Nu pornesc alta rebalansare peste ea. Verifica jurnalul")
        print(f"  ({JOURNAL_PATH}) si contul, apoi ruleaza --clear-open cand e limpede.")
        return 1

    # --------------------------------------------------------- 3. frana
    brake = book_brake(BRAKE_STATE_PATH)
    brake.sync(equity)
    print(f"  frana: {brake_status(brake, equity)}")
    if not brake.allowed:
        print(f"  OPRIT DE FRANA - {brake.reason}")
        print("  Nu se adauga risc nou. Pentru iesire completa: --flatten --armed")
        return 1

    # ---------------------------------------------------------- 4. poarta
    gate = xs_gate.check(args.factor, args.tf, args.universe, GRID,
                         path=xs_gate.path_for(args.factor))
    cert = gate.certificate
    if not gate.tradeable:
        print(f"  Poarta INCHISA - {gate.reason}")
        return 1
    if not cert or not cert.hold or cert.vol_scale is None:
        print("  Certificat fara hold/vol_scale - nu tranzactionez.")
        return 1
    print(f"  Poarta: {gate.reason}")

    # ------------------------------------------------------- 5. scadenta
    due, hours_left = rebalance_due(state.last_rebalance_at, now, args.tf, cert.hold)
    if not due:
        print(f"  Nu e scadenta - {hours_left:.1f}h pana la urmatoarea rebalansare.")
        return 0
    print(f"  Rebalansare scadenta (hold {cert.hold} bare pe {args.tf}).")

    # ------------------------------------------------------------ 6. planul
    try:
        plan = build_plan(
            client, args.factor, args.tf, args.universe, cert.vol_scale,
            positions=positions, equity=equity,
        )
    except ValueError as exc:
        print(f"  Nu am putut construi cartea tinta: {exc}")
        return 1

    print(f"  plan: {plan.summary()}")
    if not plan.trades:
        if plan.excluded_min:
            excluded_weight = sum(abs(w) for _, w in plan.excluded_min)
            print(f"  Nicio tranzactie posibila: {len(plan.excluded_min)} simboluri "
                  f"({excluded_weight:.0%} din expunerea tinta) cad sub minimul bursei")
            print(f"  la {equity:.2f} USDT. Cartea validata are nevoie de mai mult capital.")
        else:
            print("  Nimic de tranzactionat - cartea tinta coincide cu cea detinuta.")
        return 0

    rebalance_id = f"{now:%Y%m%dT%H%M%S}"
    print()
    for t in plan.trades:
        verb = "CUMPARA" if t.delta_qty > 0 else "VINDE  "
        print(f"    {verb} {t.symbol:<22} {t.delta_qty:>+14.6g} "
              f"({t.delta_notional:>+9.2f} USDT)  id={client_order_id(rebalance_id, t.symbol)}")

    if not armed:
        print()
        print("  Rulare seaca - niciun ordin nu a plecat.")
        print("  Ca sa trimiti: TRADING_MODE=live si --armed.")
        print("=" * 78)
        return 0

    return _send(client, plan, state, rebalance_id, now)


def _send(client, plan, state: LiveState, rebalance_id: str, now) -> int:
    """Trimiterea propriu-zisa. Ajunge aici doar dupa ce toate portile au trecut."""
    state.open_rebalance = {
        "id": rebalance_id,
        "started_at": now.isoformat(),
        "total": len(plan.trades),
        "sent": 0,
    }
    save_state(state)
    journal("rebalance_start", id=rebalance_id, total=len(plan.trades))

    sent = 0
    failed: list[tuple[str, str]] = []

    for t in plan.trades:
        coid = client_order_id(rebalance_id, t.symbol)
        # Jurnalul INAINTE de ordin: daca murim aici, ramane dovada ca ordinul
        # putea sa fi plecat, si stim exact ce sa cautam in cont.
        journal("order_sent", id=rebalance_id, symbol=t.symbol, coid=coid,
                side=t.side, qty=abs(t.delta_qty), ref_price=t.ref_price,
                delta_notional=t.delta_notional, closing=t.closing)
        try:
            order = client.create_market_order(
                t.symbol,
                t.side,
                abs(t.delta_qty),
                params={"clientOrderId": coid},
                price=t.ref_price,
                closing=t.closing,
            )
            sent += 1
            state.open_rebalance["sent"] = sent
            save_state(state)
            journal("order_ack", id=rebalance_id, symbol=t.symbol, coid=coid,
                    order_id=order.get("id"), status=order.get("status"),
                    filled=order.get("filled"), average=order.get("average"))
            print(f"    ok  {t.symbol:<22} {t.delta_qty:>+14.6g}")
        except Exception as exc:  # noqa: BLE001
            failed.append((t.symbol, str(exc)[:120]))
            journal("order_error", id=rebalance_id, symbol=t.symbol, coid=coid,
                    error=str(exc)[:400])
            log.error("ordin esuat %s: %s", t.symbol, str(exc)[:160])

    # ------------------------------------------------------ reconciliere
    after = exchange_positions(client)
    drift = _reconcile(plan, after)

    journal("rebalance_end", id=rebalance_id, sent=sent, failed=len(failed),
            drift=len(drift))

    state.open_rebalance = None
    state.last_rebalance_at = now.isoformat()
    state.rebalance_count += 1
    save_state(state)

    print()
    print(f"  trimise {sent}/{len(plan.trades)}, esuate {len(failed)}")
    for sym, err in failed[:10]:
        print(f"    ESEC {sym:<22} {err}")
    if drift:
        print(f"  DIVERGENTA fata de plan pe {len(drift)} simboluri:")
        for sym, want, got in drift[:10]:
            print(f"    {sym:<22} asteptat {want:>+12.6g}, la bursa {got:>+12.6g}")
        print("  Verifica manual inainte de urmatoarea rebalansare.")
    else:
        print("  Reconciliere: pozitiile de la bursa se potrivesc cu planul.")
    print("=" * 78)
    return 0 if not failed else 1


def _reconcile(plan, actual: dict[str, float]) -> list[tuple[str, float, float]]:
    """
    Ce ar trebui sa fie la bursa dupa plan, fata de ce e chiar acolo.

    Toleranta e relativa la marimea pozitiei: fill-urile partiale si rotunjirile
    bursei produc mereu diferente mici, iar o toleranta absoluta ar fi ori prea
    stricta pe monede ieftine (milioane de unitati) ori prea laxa pe BTC.
    """
    expected: dict[str, float] = dict(plan.untouched)
    for t in plan.trades:
        if abs(t.new_qty) > 0:
            expected[t.symbol] = t.new_qty

    out: list[tuple[str, float, float]] = []
    for sym in set(expected) | set(actual):
        want = expected.get(sym, 0.0)
        got = actual.get(sym, 0.0)
        scale = max(abs(want), abs(got), 1e-12)
        if abs(want - got) / scale > 0.01:
            out.append((sym, want, got))
    return sorted(out)


def flatten(args) -> int:
    """Iesirea de urgenta: inchide toate pozitiile la piata."""
    armed, blockers = preflight(args)
    client = BingXClient(cfg.BINGX_API_KEY, cfg.BINGX_SECRET_KEY)
    if not client.authenticated:
        print("Fara chei API nu pot citi pozitiile. Ma opresc.")
        return 1

    positions = exchange_positions(client)
    if not positions:
        print("Nicio pozitie deschisa la bursa.")
        return 0

    print()
    print("=" * 78)
    print(f"  INCHIDERE TOTALA   {len(positions)} pozitii")
    print(f"  {'ARMAT' if armed else 'RULARE SEACA'}")
    print("=" * 78)
    for sym, qty in sorted(positions.items()):
        print(f"    inchide {sym:<22} {qty:>+14.6g}")

    if not armed:
        print(f"\n  Rulare seaca. Blocat de: {'; '.join(blockers)}")
        return 0

    fid = f"flat{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    failed = 0
    for sym, qty in sorted(positions.items()):
        coid = client_order_id(fid, sym)
        side = "short" if qty > 0 else "long"  # opusul pozitiei
        journal("flatten_sent", symbol=sym, coid=coid, qty=abs(qty), side=side)
        try:
            client.create_market_order(
                sym, side, abs(qty), params={"clientOrderId": coid}, closing=True,
            )
            print(f"    ok  {sym}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            journal("flatten_error", symbol=sym, error=str(exc)[:400])
            print(f"    ESEC {sym}: {str(exc)[:120]}")

    left = exchange_positions(client)
    print(f"\n  Ramase deschise: {len(left)}")
    print("=" * 78)
    return 0 if not left else 1


def main() -> int:
    p = argparse.ArgumentParser(
        description="Executor REAL pentru cartea cross-sectionala")
    p.add_argument("--armed", action="store_true",
                   help="TRIMITE ORDINE REALE. Fara el, doar afiseaza planul.")
    p.add_argument("--flatten", action="store_true",
                   help="inchide toate pozitiile la piata (iesire de urgenta)")
    p.add_argument("--clear-open", action="store_true",
                   help="sterge marcajul de rebalansare intrerupta, dupa verificare manuala")
    p.add_argument("--tf", default="4h")
    p.add_argument("--universe", type=int, default=50)
    p.add_argument("--factor", default="range_pos")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")

    if args.clear_open:
        state = load_state()
        if not state or not state.open_rebalance:
            print("Nu exista nicio rebalansare marcata ca intrerupta.")
            return 0
        print(f"Sterg marcajul pentru {state.open_rebalance.get('id')}.")
        state.open_rebalance = None
        save_state(state)
        return 0

    try:
        with single_instance(LOCK_PATH):
            if args.flatten:
                return flatten(args)
            return run_once(args)
    except RuntimeError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
