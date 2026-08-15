"""
Teste pentru stratul de risc adaugat peste agent:

    risk/gate.py            - RegimeGate, CircuitGate
    risk/trade_recorder.py  - detectia inchiderilor de pozitii

Nu ating reteaua. Clientii BingX sunt inlocuiti cu obiecte false, iar jurnalele
se scriu intr-un director temporar, ca rulatul testelor sa nu murdareasca logs/.

    python tools\\test_gates.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg  # noqa: E402
from risk.circuit_breaker import append_trade  # noqa: E402
from risk.gate import CircuitGate, RegimeGate  # noqa: E402
from risk.trade_recorder import TradeRecorder  # noqa: E402

C = cfg.CONFIG

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


# ------------------------------------------------------------------ clienti fals
class TrendClient:
    """BTC in trend ascendent curat, funding usor pozitiv."""

    authenticated = True

    def fetch_daily_closes(self, symbol, days):
        return [100.0 + i for i in range(260)]

    def fetch_funding_rates(self, symbols):
        return {s: 0.0001 for s in symbols}


class DeadClient:
    """Exchange-ul nu raspunde."""

    authenticated = True

    def fetch_daily_closes(self, *args):
        raise RuntimeError("timeout")

    def fetch_funding_rates(self, *args):
        raise RuntimeError("timeout")


class PnlClient:
    authenticated = True

    def __init__(self, pnl):
        self.pnl = pnl

    def fetch_realized_pnl(self, symbol, since_ms):
        return self.pnl


class AnonClient:
    authenticated = False


def _pos(symbol="BTC/USDT:USDT", side="long", upnl=-3.0):
    return {
        "symbol": symbol,
        "side": side,
        "contracts": 1,
        "entry_price": 60000.0,
        "unrealized_pnl": upnl,
    }


# ---------------------------------------------------------------------- teste
def test_circuit(tmp: str) -> None:
    print("\n--- CIRCUIT BREAKER (poarta) ---")

    empty = os.path.join(tmp, "trades_empty.jsonl")
    gate = CircuitGate(C.circuit, empty)
    r = gate.check(1000.0)
    check("jurnal gol -> tranzactionare permisa", r["allowed"])
    check("recomandare TRADING_ALLOWED", r["recommendation"] == "TRADING_ALLOWED")

    streak = os.path.join(tmp, "trades_streak.jsonl")
    for _ in range(C.circuit.losing_streak_n):
        append_trade(Path(streak), -5.0)
    r = CircuitGate(C.circuit, streak).check(1000.0)
    check("pierderi consecutive -> blocat", not r["allowed"])
    check("motiv explicit", bool(r["reason"]))

    daily = os.path.join(tmp, "trades_daily.jsonl")
    # Pierdere zilnica peste plafon, dar alternata ca sa nu declanseze streak-ul.
    append_trade(Path(daily), 1.0)
    append_trade(Path(daily), -50.0)
    r = CircuitGate(C.circuit, daily).check(1000.0)
    check("pierdere zilnica 5% > 2% -> blocat", not r["allowed"])

    corrupt = os.path.join(tmp, "trades_bad.jsonl")
    with open(corrupt, "w", encoding="utf-8") as fh:
        fh.write("{asta nu e json\n")
    r = CircuitGate(C.circuit, corrupt).check(1000.0)
    check("jurnal corupt NU trece drept permis", not r["allowed"])
    check("marcat ca ERROR", r["recommendation"] == "ERROR")


def test_regime(tmp: str) -> None:
    print("\n--- REGIM DE PIATA (poarta) ---")

    cache = os.path.join(tmp, "regime.json")
    gate = RegimeGate(TrendClient(), C.regime, cache)

    res = gate.evaluate()
    check("trend ascendent -> RISK_ON", res["zone"] == "RISK_ON")
    check("ciclul e permis", res["allowed"])
    check("scor calculat", isinstance(res.get("score"), (int, float)))
    check("prima citire nu vine din cache", res["from_cache"] is False)

    res2 = gate.evaluate()
    check("a doua citire vine din cache", res2["from_cache"] is True)

    cached = gate.cached()
    check("cached() nu atinge reteaua", cached["zone"] == "RISK_ON")

    fresh = RegimeGate(TrendClient(), C.regime, os.path.join(tmp, "gol.json"))
    check("cached() fara cache -> STALE, dar permis", fresh.cached()["allowed"])

    dead = RegimeGate(DeadClient(), C.regime, os.path.join(tmp, "mort.json"))
    res3 = dead.evaluate()
    check("fara date -> zona UNKNOWN", res3["zone"] == "UNKNOWN")
    check("fara date NU opreste agentul (e filtru, nu limita)", res3["allowed"] is True)
    check("eroarea de date e raportata", bool(res3["data_error"]))


def test_recorder(tmp: str) -> None:
    print("\n--- INREGISTRAREA INCHIDERILOR ---")

    state = os.path.join(tmp, "positions.json")
    journal = os.path.join(tmp, "rec.jsonl")
    rec = TradeRecorder(PnlClient(-12.5), journal, state)

    opened = rec.sync([_pos()])
    check("pozitie noua -> nimic de inregistrat", opened == [])

    still = rec.sync([_pos()])
    check("pozitie inca deschisa -> nimic de inregistrat", still == [])

    closed = rec.sync([])
    check("pozitia disparuta e detectata", len(closed) == 1)
    check("P&L luat de la exchange", closed and closed[0]["pnl"] == -12.5)
    check("sursa marcata exchange", closed and closed[0]["source"] == "exchange")
    check("scris in jurnal", Path(journal).exists())

    r = CircuitGate(C.circuit, journal).check(1000.0)
    check("circuit breaker vede pierderea proaspata", r["metrics"]["pnl_today"] == -12.5)

    # Exchange-ul nu returneaza executii: estimam, dar o marcam.
    state2 = os.path.join(tmp, "positions2.json")
    journal2 = os.path.join(tmp, "rec2.jsonl")
    rec2 = TradeRecorder(PnlClient(None), journal2, state2)
    rec2.sync([_pos("ETH/USDT:USDT", "short", upnl=7.25)])
    closed2 = rec2.sync([])
    check("fara P&L real -> cade pe ultimul nerealizat", closed2[0]["pnl"] == 7.25)
    check("marcat ca estimare", closed2[0]["source"] == "estimated")

    # Fara nicio valoare de P&L nu inventam: nu scriem deloc.
    state3 = os.path.join(tmp, "positions3.json")
    journal3 = os.path.join(tmp, "rec3.jsonl")
    rec3 = TradeRecorder(PnlClient(None), journal3, state3)
    rec3.sync([{"symbol": "SOL/USDT:USDT", "side": "long", "unrealized_pnl": None}])
    closed3 = rec3.sync([])
    check("fara nicio valoare -> nu se scrie nimic fals", closed3 == [])

    # Fara chei API nu exista pozitii de urmarit.
    anon = TradeRecorder(AnonClient(), journal3, state3)
    check("fara chei API -> no-op", anon.sync([_pos()]) == [])


def test_kill_switch_link(tmp: str) -> None:
    print("\n--- LEGATURA CU KILL-SWITCH-UL ---")

    class SpyKill:
        def __init__(self):
            self.seen = []

        def record_trade_closed(self, pnl):
            self.seen.append(pnl)

    spy = SpyKill()
    rec = TradeRecorder(
        PnlClient(-8.0),
        os.path.join(tmp, "ks.jsonl"),
        os.path.join(tmp, "ks_state.json"),
        kill_switch=spy,
    )
    rec.sync([_pos()])
    rec.sync([])
    check("kill-switch-ul e anuntat de inchidere", spy.seen == [-8.0])


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="gates_test_")
    try:
        test_circuit(tmp)
        test_regime(tmp)
        test_recorder(tmp)
        test_kill_switch_link(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 50)
    print(f"  {PASS} PASS / {FAIL} FAIL")
    print("=" * 50)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
