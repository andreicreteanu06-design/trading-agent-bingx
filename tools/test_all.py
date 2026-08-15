"""
Teste pentru kill-switch, trade manager si blackout.

    python tools/test_all.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from news.blackout import BlackoutConfig, NewsBlackout, VolatilityGuard
from strategy.kill_switch import KillSwitch, KillSwitchConfig
from strategy.trade_manager import ManagedPosition, ManagementConfig, TradeManager

passed = failed = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


tmpdir = tempfile.mkdtemp()

# ============================================================== KILL-SWITCH
print("\n--- KILL-SWITCH ---")

ks = KillSwitch(os.path.join(tmpdir, "ks.json"), KillSwitchConfig())
ks.sync(1000.0)
check("pornire: permis", ks.allowed)

# Drawdown zilnic de 3%
ks.sync(1000.0)
ks.sync(969.0)  # -3.1%
check("blocare la pierdere zilnica 3%", not ks.allowed, ks.reason)
check("motiv marcat ZILNIC", ks.reason.startswith("ZILNIC"), ks.reason)

# Reset si test pierderi consecutive
ks2 = KillSwitch(os.path.join(tmpdir, "ks2.json"), KillSwitchConfig())
ks2.sync(1000.0)
for _ in range(3):
    ks2.record_trade_closed(-5.0)
ks2.sync(985.0)
check("blocare la 3 pierderi consecutive", not ks2.allowed, ks2.reason)

# O tranzactie castigatoare reseteaza contorul
ks3 = KillSwitch(os.path.join(tmpdir, "ks3.json"), KillSwitchConfig())
ks3.sync(1000.0)
ks3.record_trade_closed(-5.0)
ks3.record_trade_closed(-5.0)
ks3.record_trade_closed(+10.0)
check("castig reseteaza contorul", ks3.state.consecutive_losses == 0,
      str(ks3.state.consecutive_losses))

# Drawdown total
ks4 = KillSwitch(os.path.join(tmpdir, "ks4.json"), KillSwitchConfig())
ks4.sync(1000.0)
ks4.sync(1200.0)  # varf nou
ks4.sync(1000.0)  # -16.7% de la varf
check("blocare la drawdown total 15%", not ks4.allowed, ks4.reason)
check("motiv marcat TOTAL", ks4.reason.startswith("TOTAL"), ks4.reason)

# Persistenta pe disc
path5 = os.path.join(tmpdir, "ks5.json")
ks5 = KillSwitch(path5, KillSwitchConfig())
ks5.sync(1000.0)
ks5.sync(950.0)
was_halted = not ks5.allowed
ks5_reloaded = KillSwitch(path5, KillSwitchConfig())
check("starea supravietuieste restartului", was_halted and ks5_reloaded.state.halted)

# Overtrading
ks6 = KillSwitch(os.path.join(tmpdir, "ks6.json"), KillSwitchConfig(max_trades_per_day=2))
ks6.sync(1000.0)
ks6.record_trade_opened()
ks6.record_trade_opened()
ks6.sync(1000.0)
check("blocare la overtrading", not ks6.allowed, ks6.reason)


# ============================================================ TRADE MANAGER
print("\n--- TRADE MANAGER ---")

tm = TradeManager(ManagementConfig())


def make_pos(side="long", entry=100.0, stop=98.0):
    return ManagedPosition(
        symbol="BTC/USDT:USDT",
        side=side,
        entry=entry,
        initial_stop=stop,
        current_stop=stop,
        take_profits=[103.0, 106.0] if side == "long" else [97.0, 94.0],
        size=5.0,
        r_per_unit=abs(entry - stop),
    )


# Stop atins
pos = make_pos()
acts = tm.update(pos, high=101, low=97.5, close=98, atr=1.0)
check("stop declanseaza inchidere", any(a["type"] == "close" for a in acts))
check("pozitia e inchisa", not pos.is_open)

# TP1 -> partial + breakeven
pos = make_pos()
acts = tm.update(pos, high=103.5, low=99.5, close=103, atr=1.0)
check("TP1 declanseaza partial", any(a["type"] == "partial_close" for a in acts))
check("stopul se muta la breakeven", pos.at_breakeven)
check("breakeven peste intrare (long)", pos.current_stop > pos.entry,
      f"stop {pos.current_stop} vs entry {pos.entry}")
check("jumatate inchisa", abs(pos.fraction_closed - 0.5) < 1e-9)

# Stopul nu se muta inapoi
pos = make_pos()
tm.update(pos, high=103.5, low=99.5, close=103, atr=1.0)
stop_after_be = pos.current_stop
tm.update(pos, high=103.6, low=100.0, close=100.5, atr=1.0)  # pret in scadere
check("stopul nu coboara niciodata", pos.current_stop >= stop_after_be,
      f"{stop_after_be} -> {pos.current_stop}")

# Trailing urca
pos = make_pos()
tm.update(pos, high=103.5, low=99.5, close=103, atr=1.0)
before = pos.current_stop
tm.update(pos, high=105.0, low=103.0, close=104.8, atr=1.0)
check("trailing ridica stopul", pos.current_stop > before,
      f"{before} -> {pos.current_stop}")

# Short simetric
pos = make_pos(side="short", entry=100.0, stop=102.0)
acts = tm.update(pos, high=100.5, low=96.5, close=97.0, atr=1.0)
check("short: TP1 declanseaza partial", any(a["type"] == "partial_close" for a in acts))
check("short: breakeven sub intrare", pos.current_stop < pos.entry,
      f"stop {pos.current_stop}")

# Iesire pe timp
tm_fast = TradeManager(ManagementConfig(max_bars_in_trade=3))
pos = make_pos()
for _ in range(3):
    acts = tm_fast.update(pos, high=100.5, low=99.5, close=100.0, atr=1.0)
check("iesire fortata pe timp", not pos.is_open)


# ================================================================= BLACKOUT
print("\n--- BLACKOUT STIRI ---")

bo = NewsBlackout()

# Decont funding la 08:00 UTC
at_funding = datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc)
clear, reasons = bo.check(at_funding)
check("blocare la decont funding", not clear, str(reasons))

# Ora linistita
quiet = datetime(2026, 3, 10, 5, 30, tzinfo=timezone.utc)
clear, reasons = bo.check(quiet)
check("ora linistita e permisa", clear, str(reasons))

# NFP: prima vineri, 13:30 UTC. 2026-03-06 e vineri.
nfp = datetime(2026, 3, 6, 13, 30, tzinfo=timezone.utc)
clear, reasons = bo.check(nfp)
check("blocare la NFP", not clear, str(reasons))
check("NFP identificat", any("Payrolls" in r for r in reasons), str(reasons))

# Weekend, cu optiunea activata
bo_we = NewsBlackout(BlackoutConfig(block_weekend=True))
sat = datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc)
clear, reasons = bo_we.check(sat)
check("blocare weekend cand e activata", not clear, str(reasons))

# next_clear_time
clear, _ = bo.check(at_funding)
nxt = bo.next_clear_time(at_funding)
check("next_clear_time returneaza un moment", nxt is not None and nxt > at_funding)


# ======================================================== VOLATILITY GUARD
print("\n--- VOLATILITY GUARD ---")

import pandas as pd

vg = VolatilityGuard(spike_mult=3.0, volume_spike_mult=4.0)

normal = pd.DataFrame(
    {
        "high": [101.0] * 25,
        "low": [99.0] * 25,
        "close": [100.0] * 25,
        "atr": [1.0] * 25,
        "volume_ratio": [1.0] * 25,
    }
)
ok, reasons = vg.check(normal)
check("piata normala e permisa", ok, str(reasons))

spike = normal.copy()
spike.loc[24, "high"] = 110.0
spike.loc[24, "low"] = 99.0  # range 11 = 11x ATR
ok, reasons = vg.check(spike)
check("lumanare anormala blocheaza", not ok, str(reasons))

volspike = normal.copy()
volspike.loc[24, "volume_ratio"] = 8.0
ok, reasons = vg.check(volspike)
check("volum exploziv blocheaza", not ok, str(reasons))


shutil.rmtree(tmpdir, ignore_errors=True)

print(f"\n{'=' * 50}")
print(f"  {passed} PASS / {failed} FAIL")
print("=" * 50)
sys.exit(1 if failed else 0)
