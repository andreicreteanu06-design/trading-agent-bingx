"""
Verificare a aritmeticii din risk engine, pe cazuri sintetice.

Rulare:  python tools/test_risk.py

Nu inlocuieste un backtest. Verifica doar ca dimensionarea, plafonul de
leverage, buffer-ul de lichidare si validarile resping ce trebuie sa respinga.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg
from strategy import risk_engine
from strategy.signal_builder import Signal

C = cfg.CONFIG
EQUITY = 1000.0

passed = failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


def make_signal(entry: float, stop: float, tp: float, side: str = "long") -> Signal:
    return Signal(
        symbol="BTC/USDT:USDT",
        side=side,  # type: ignore[arg-type]
        entry=entry,
        stop_loss=stop,
        take_profits=[tp],
        score=75.0,
    )


print("\n--- 1. Dimensionare normala: stop la 2%, risc 1% din 1000 USDT ---")
sig = make_signal(entry=100.0, stop=98.0, tp=104.0)
t = risk_engine.evaluate(sig, EQUITY, [], C.risk)

# risc 10 USDT / 2 USDT per unitate = 5 unitati; notional 500; leverage 0.5x
check("aprobat", t.approved, str(t.rejections))
check("risc = 10 USDT", abs(t.risk_amount - 10.0) < 0.01, f"got {t.risk_amount}")
check("marime = 5.0", abs(t.position_size - 5.0) < 1e-6, f"got {t.position_size}")
check("notional = 500", abs(t.notional - 500.0) < 0.01, f"got {t.notional}")
check("leverage = 0.5x", abs(t.leverage - 0.5) < 0.01, f"got {t.leverage}")
check("R:R = 2.0", abs(sig.risk_reward - 2.0) < 1e-9, f"got {sig.risk_reward}")

print("\n--- 2. Stop foarte apropiat -> leverage ar exploda, trebuie plafonat ---")
sig = make_signal(entry=100.0, stop=99.5, tp=101.5)  # stop 0.5%
t = risk_engine.evaluate(sig, EQUITY, [], C.risk)
# fara plafon: 10/0.5 = 20 unitati, notional 2000, leverage 2.0x -> sub plafon
check("leverage sub plafon", t.leverage <= C.risk.max_leverage, f"got {t.leverage}")

print("\n--- 3. Stop sub pragul minim -> respins ---")
sig = make_signal(entry=100.0, stop=99.8, tp=100.6)  # stop 0.2% < 0.4%
t = risk_engine.evaluate(sig, EQUITY, [], C.risk)
check("respins", not t.approved)
check("motiv corect", any("prea aproape" in r for r in t.rejections), str(t.rejections))

print("\n--- 4. Risk/reward slab -> respins ---")
sig = make_signal(entry=100.0, stop=98.0, tp=101.0)  # R:R 0.5
t = risk_engine.evaluate(sig, EQUITY, [], C.risk)
check("respins", not t.approved)
check("motiv corect", any("risk/reward" in r.lower() for r in t.rejections), str(t.rejections))

print("\n--- 5. Pozitie deja deschisa pe acelasi simbol -> respins ---")
sig = make_signal(entry=100.0, stop=98.0, tp=104.0)
t = risk_engine.evaluate(
    sig, EQUITY, [{"symbol": "BTC/USDT:USDT", "notional": 300}], C.risk
)
check("respins", not t.approved)
check("motiv corect", any("deja o pozitie" in r for r in t.rejections), str(t.rejections))

print("\n--- 6. Prea multe pozitii deschise -> respins ---")
sig = make_signal(entry=100.0, stop=98.0, tp=104.0)
t = risk_engine.evaluate(
    sig,
    EQUITY,
    [{"symbol": "ETH/USDT:USDT", "notional": 200}, {"symbol": "SOL/USDT:USDT", "notional": 200}],
    C.risk,
)
check("respins", not t.approved)
check("motiv corect", any("Prea multe" in r for r in t.rejections), str(t.rejections))

print("\n--- 7. Short: dimensionare simetrica ---")
sig = make_signal(entry=100.0, stop=102.0, tp=96.0, side="short")
t = risk_engine.evaluate(sig, EQUITY, [], C.risk)
check("aprobat", t.approved, str(t.rejections))
check("marime = 5.0", abs(t.position_size - 5.0) < 1e-6, f"got {t.position_size}")
check("lichidare peste intrare", (t.liquidation_price or 0) > sig.entry, f"got {t.liquidation_price}")
check("R:R = 2.0", abs(sig.risk_reward - 2.0) < 1e-9, f"got {sig.risk_reward}")

print("\n--- 8. Buffer de lichidare: long la leverage mare ---")
# Fortam leverage mare: stop foarte stramt + risc mare
import dataclasses

aggressive = dataclasses.replace(C.risk, risk_per_trade=0.05, max_leverage=25.0)
sig = make_signal(entry=100.0, stop=99.6, tp=101.2)
t = risk_engine.evaluate(sig, EQUITY, [], aggressive)
check(
    "respins sau buffer suficient",
    (not t.approved) or (t.liquidation_buffer_mult or 0) >= aggressive.min_liquidation_buffer_mult,
    f"lev {t.leverage}x buffer {t.liquidation_buffer_mult}x rej={t.rejections}",
)

print("\n--- 9. Expunere notionala totala peste plafon -> respins ---")
sig = make_signal(entry=100.0, stop=98.0, tp=104.0)
t = risk_engine.evaluate(sig, EQUITY, [{"symbol": "ETH/USDT:USDT", "notional": 2900}], C.risk)
check("respins", not t.approved)
check("motiv corect", any("notionala" in r for r in t.rejections), str(t.rejections))

print(f"\n{'=' * 50}")
print(f"  {passed} PASS / {failed} FAIL")
print("=" * 50)
sys.exit(1 if failed else 0)
