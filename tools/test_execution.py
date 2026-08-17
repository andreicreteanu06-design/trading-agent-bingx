"""
Teste pentru stratul de executie al cartii cross-sectionale:

    execution/rebalance.py      - planul: ce tranzactii, cu ce semne, ce minime
    execution/brake.py          - frana de drawdown si pierdere zilnica
    execution/live_executor.py  - portile care trebuie sa refuze trimiterea
    execution/paper_executor.py - lacatul si scadenta rebalansarii

Nu ating reteaua si NU pot trimite niciun ordin: clientul de bursa e un obiect
fals care ridica exceptie daca cineva incearca sa cheme create_*. Starea se
scrie intr-un director temporar.

    python tools\\test_execution.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from execution import rebalance  # noqa: E402
from execution.brake import (  # noqa: E402
    CONFIG as BRAKE_CFG,
    LIVE_STATE_PATH as LIVE_BRAKE_PATH,
    PAPER_STATE_PATH as PAPER_BRAKE_PATH,
    book_brake,
    status_line,
)
from execution.live_executor import (  # noqa: E402
    client_order_id,
    exchange_positions,
    preflight,
    _reconcile,
)
from execution.paper_executor import rebalance_due, single_instance  # noqa: E402
from execution.rebalance import RebalancePlan, build_plan  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")


# --------------------------------------------------------------- client fals
class FakeClient:
    """
    Bursa falsa. Orice incercare de a trimite un ordin ridica exceptie - daca
    un test trece prin create_*, testul insusi e gresit, nu codul.
    """

    def __init__(self, prices: dict[str, float], min_qty: float = 0.0):
        self.prices = prices
        self.min_qty = min_qty

    def fetch_last_prices(self, symbols):
        return {s: self.prices[s] for s in symbols if s in self.prices}

    def normalize_amount(self, symbol, amount, price=None, closing=False):
        qty = round(float(amount), 6)
        if qty <= 0:
            raise ValueError(f"{symbol}: se rotunjeste la zero")
        if not closing and self.min_qty and qty < self.min_qty:
            raise ValueError(f"{symbol}: {qty} sub minimul {self.min_qty}")
        return qty

    def create_market_order(self, *a, **k):
        raise AssertionError("un test a incercat sa trimita un ordin real")

    def create_stop_loss(self, *a, **k):
        raise AssertionError("un test a incercat sa trimita un stop real")


def fake_book(weights: dict[str, float]):
    """Inlocuieste build_target_book cu o carte fixa, ca planul sa fie testabil."""
    def _inner(client, factor, tf, universe, vol_scale):
        w = pd.Series(weights, dtype=float)
        return w, pd.Timestamp("2026-08-17 12:00", tz="UTC"), len(w)
    return _inner


# --------------------------------------------------------------------- plan
def test_plan(monkey):
    print("\n--- PLAN DE REBALANSARE ---")

    client = FakeClient({"A/USDT:USDT": 10.0, "B/USDT:USDT": 4.0})

    # carte goala -> deschide ambele picioare
    monkey(fake_book({"A/USDT:USDT": 0.5, "B/USDT:USDT": -0.5}))
    plan = build_plan(client, "range_pos", "4h", 50, False, positions={}, equity=1000.0)
    by = {t.symbol: t for t in plan.trades}
    check("deschide ambele picioare", len(plan.trades) == 2)
    check("longul are delta pozitiva", by["A/USDT:USDT"].delta_qty > 0)
    check("shortul are delta negativa", by["B/USDT:USDT"].delta_qty < 0)
    check("marimea longului = pondere x echitate / pret",
          abs(by["A/USDT:USDT"].new_qty - 50.0) < 1e-6)

    # inchiderea unui short nu inventeaza un long
    monkey(fake_book({"A/USDT:USDT": 1.0}))
    plan = build_plan(client, "range_pos", "4h", 50, False,
                      positions={"B/USDT:USDT": -125.0}, equity=1000.0)
    b = next(t for t in plan.trades if t.symbol == "B/USDT:USDT")
    check("inchiderea unui short da new_qty exact zero", b.new_qty == 0.0)
    check("inchiderea unui short cumpara inapoi", b.delta_qty > 0)
    check("inchiderea e marcata closing", b.closing)

    # diferenta sub prag nu produce tranzactie
    monkey(fake_book({"A/USDT:USDT": 1.0}))
    plan = build_plan(client, "range_pos", "4h", 50, False,
                      positions={"A/USDT:USDT": 99.7}, equity=1000.0)
    check("diferenta sub prag nu tranzactioneaza", not plan.trades)
    check("pozitia sub prag ramane neatinsa",
          plan.untouched.get("A/USDT:USDT") == 99.7)

    # minimul se aplica ORDINULUI, nu pozitiei tinta
    big = FakeClient({"A/USDT:USDT": 10.0}, min_qty=5.0)
    monkey(fake_book({"A/USDT:USDT": 1.0}))
    plan = build_plan(big, "range_pos", "4h", 50, False,
                      positions={"A/USDT:USDT": 98.0}, equity=1000.0)
    check("ordin mic pe pozitie mare e respins de minim",
          not plan.trades and plan.excluded_min)
    check("pozitia respinsa ramane deschisa, nu dispare",
          plan.untouched.get("A/USDT:USDT") == 98.0)


# -------------------------------------------------------------------- frana
def test_brake(tmp):
    print("\n--- FRANA DE DRAWDOWN ---")
    path = os.path.join(tmp, "brake.json")

    ks = book_brake(path)
    ks.sync(1000.0)
    check("porneste activa", ks.allowed)

    # pierdere zilnica sub prag
    ks = book_brake(path)
    ks.sync(980.0)
    check("pierdere zilnica 2% nu opreste", ks.allowed)

    # peste pragul zilnic
    ks = book_brake(path)
    ks.sync(960.0)
    check("pierdere zilnica 4% opreste", not ks.allowed)
    check("motivul spune ZILNIC", "ZILNIC" in ks.reason)

    # oprirea persista pe disc, nu se pierde la repornire
    ks2 = book_brake(path)
    check("oprirea supravietuieste repornirii", not ks2.allowed)

    # drawdown total
    path2 = os.path.join(tmp, "brake2.json")
    ks = book_brake(path2)
    ks.sync(1000.0)
    ks.state.day = "2000-01-01"      # forteaza o zi noua, ca sa nu se atinga pragul zilnic
    ks.sync(840.0)
    check("drawdown 16% de la varf opreste", not ks.allowed)
    check("motivul spune TOTAL", "TOTAL" in ks.reason)

    # ridicarea manuala
    ks.reset()
    check("resetarea manuala ridica blocarea", ks.allowed)

    check("pragurile sunt cele derivate din validare",
          BRAKE_CFG.max_daily_loss_pct == 0.03
          and BRAKE_CFG.max_total_drawdown_pct == 0.15)

    ks3 = book_brake(os.path.join(tmp, "brake3.json"))
    ks3.sync(1000.0)
    line = status_line(ks3, 950.0)
    check("linia de stare arata drawdown, nu contoare de tranzactii",
          "drawdown" in line and "trades" not in line)

    # Regresie: hartia si executia reala au impartit odata acelasi fisier, iar o
    # proba seaca pe un cont gol (echitate 0) a citit un drawdown de 100% fata de
    # varful cartii de hartie si a oprit-o pe nedrept.
    check("hartia si executia reala au fisiere diferite",
          PAPER_BRAKE_PATH != LIVE_BRAKE_PATH)

    paper = book_brake(os.path.join(tmp, "p.json"))
    paper.sync(500.0)
    live = book_brake(os.path.join(tmp, "l.json"))
    live.sync(0.0)
    check("un cont real gol nu opreste cartea de hartie",
          book_brake(os.path.join(tmp, "p.json")).allowed)
    check("un cont real gol nu-si inventeaza un drawdown", live.allowed)


# ----------------------------------------------------------------- scadenta
def test_due():
    print("\n--- SCADENTA REBALANSARII ---")
    now = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)

    check("fara istoric, e scadenta", rebalance_due(None, now, "4h", 30)[0])
    check("dupa 1h nu e scadenta",
          not rebalance_due((now - timedelta(hours=1)).isoformat(), now, "4h", 30)[0])
    check("dupa exact 30 bare de 4h e scadenta",
          rebalance_due((now - timedelta(hours=120)).isoformat(), now, "4h", 30)[0])
    check("orele ramase scad corect",
          abs(rebalance_due((now - timedelta(hours=20)).isoformat(),
                            now, "4h", 30)[1] - 100.0) < 1e-6)


# ------------------------------------------------------- porti de siguranta
def test_live_gates():
    print("\n--- PORTILE EXECUTORULUI REAL ---")

    class Args:
        armed = False

    ok, problems = preflight(Args())
    check("fara --armed, preflight refuza", not ok)
    check("refuzul spune de ce", any("armed" in p for p in problems))

    class Armed:
        armed = True

    ok, problems = preflight(Armed())
    # In mediul de test TRADING_MODE e 'paper' si cheile lipsesc, deci tot refuza.
    check("--armed singur nu e suficient", not ok)
    check("cere si TRADING_MODE=live", any("TRADING_MODE" in p for p in problems))

    # idempotenta: acelasi id pentru aceeasi rebalansare si simbol
    a = client_order_id("20260817T120000", "BTC/USDT:USDT")
    b = client_order_id("20260817T120000", "BTC/USDT:USDT")
    c = client_order_id("20260817T160000", "BTC/USDT:USDT")
    d = client_order_id("20260817T120000", "ETH/USDT:USDT")
    check("acelasi id la retry", a == b)
    check("id diferit pe alta rebalansare", a != c)
    check("id diferit pe alt simbol", a != d)
    check("id-ul incape in limitele bursei", len(a) <= 32)

    # pozitiile de la bursa: shorturile trebuie sa devina negative
    class Pos:
        @staticmethod
        def fetch_open_positions():
            return [
                {"symbol": "A/USDT:USDT", "side": "long", "contracts": 5.0},
                {"symbol": "B/USDT:USDT", "side": "short", "contracts": 3.0},
                {"symbol": "C/USDT:USDT", "side": "long", "contracts": 0.0},
            ]

    p = exchange_positions(Pos())
    check("longul de la bursa ramane pozitiv", p.get("A/USDT:USDT") == 5.0)
    check("shortul de la bursa devine negativ", p.get("B/USDT:USDT") == -3.0)
    check("pozitia zero e ignorata", "C/USDT:USDT" not in p)


def test_reconcile():
    print("\n--- RECONCILIERE ---")
    plan = RebalancePlan()
    plan.untouched = {"A/USDT:USDT": 10.0}
    plan.trades = [
        rebalance.PlannedTrade("B/USDT:USDT", 0.0, -4.0, -4.0, 2.0, -8.0, -0.1, False),
    ]

    check("potrivire perfecta nu raporteaza nimic",
          not _reconcile(plan, {"A/USDT:USDT": 10.0, "B/USDT:USDT": -4.0}))
    check("diferenta mica de rotunjire e tolerata",
          not _reconcile(plan, {"A/USDT:USDT": 10.002, "B/USDT:USDT": -4.0}))
    check("pozitie lipsa e raportata",
          any(s == "B/USDT:USDT" for s, _, _ in
              _reconcile(plan, {"A/USDT:USDT": 10.0})))
    check("pozitie in plus la bursa e raportata",
          any(s == "Z/USDT:USDT" for s, _, _ in
              _reconcile(plan, {"A/USDT:USDT": 10.0, "B/USDT:USDT": -4.0,
                                "Z/USDT:USDT": 1.0})))


def test_lock(tmp):
    print("\n--- LACAT ---")
    path = os.path.join(tmp, "x.lock")
    with single_instance(path):
        try:
            with single_instance(path):
                check("al doilea proces e respins", False)
        except RuntimeError:
            check("al doilea proces e respins", True)
    check("lacatul se elibereaza la iesire", not os.path.exists(path))

    # carti diferite nu se blocheaza intre ele
    with single_instance(os.path.join(tmp, "hartie.lock")):
        try:
            with single_instance(os.path.join(tmp, "real.lock")):
                check("hartia si executia reala nu se blocheaza reciproc", True)
        except RuntimeError:
            check("hartia si executia reala nu se blocheaza reciproc", False)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="xs_exec_test_")
    original = rebalance.build_target_book
    try:
        def monkey(fn):
            rebalance.build_target_book = fn

        test_plan(monkey)
        test_brake(tmp)
        test_due()
        test_live_gates()
        test_reconcile()
        test_lock(tmp)
    finally:
        rebalance.build_target_book = original
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 50)
    print(f"  {PASS} PASS / {FAIL} FAIL")
    print("=" * 50)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
