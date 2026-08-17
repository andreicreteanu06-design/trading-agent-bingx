"""
Inspectare si resetare kill-switch.

    python tools/killswitch.py                 # cartea BTC/ETH/SOL
    python tools/killswitch.py --reset         # ridica blocarea
    python tools/killswitch.py --xs            # cartea cross-sectionala
    python tools/killswitch.py --xs --reset

Doua carti, doua frane separate, doua fisiere de stare. Nu pot fi una singura:
un drawdown pe o carte ar opri-o pe cealalta, iar varful de echitate al uneia
n-are nicio legatura cu al celeilalte.

Inainte sa dai --reset, raspunde onest la intrebarea: s-a schimbat ceva in
strategie, sau doar vrei sa mai tranzactionezi?
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg
from execution.brake import STATE_PATH as XS_BRAKE_PATH, book_brake
from strategy.kill_switch import KillSwitch, KillSwitchConfig

C = cfg.CONFIG


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--xs", action="store_true",
                        help="frana cartii cross-sectionale, nu cea BTC/ETH/SOL")
    args = parser.parse_args()

    if args.xs:
        ks = book_brake(XS_BRAKE_PATH)
        title = "FRANA CARTE CROSS-SECTIONALA"
    else:
        ks = KillSwitch(
            os.path.join(C.log_dir, "killswitch.json"),
            KillSwitchConfig(max_consecutive_losses=C.risk.max_consecutive_losses),
        )
        title = "KILL-SWITCH  (BTC/ETH/SOL)"
    s = ks.state

    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)
    print(f"  Stare              : {'OPRIT' if s.halted else 'activ'}")
    if s.halted:
        print(f"  Motiv              : {s.halt_reason}")
        print(f"  De la              : {s.halted_at}")
    print(f"  Ziua               : {s.day or '-'}")
    print(f"  Echity start zi    : {s.day_start_equity:.2f}")
    print(f"  Varf echity        : {s.peak_equity:.2f}")
    if args.xs:
        # Contoarele per-tranzactie nu sunt alimentate pentru o carte de
        # portofoliu (vezi execution/brake.py) - afisate ar parea limite active.
        print("  (contoarele de tranzactii/pierderi consecutive nu se aplica)")
    else:
        print(f"  PnL realizat azi   : {s.realized_pnl_today:+.2f}")
        print(f"  Trades azi         : {s.trades_today}/{ks.cfg.max_trades_per_day}")
        print(f"  Pierderi consec.   : {s.consecutive_losses}/{ks.cfg.max_consecutive_losses}")
    print("=" * 60)
    print("  Limite configurate:")
    print(f"    pierdere zilnica max : {ks.cfg.max_daily_loss_pct:.1%}")
    print(f"    drawdown total max   : {ks.cfg.max_total_drawdown_pct:.1%}")
    print("=" * 60 + "\n")

    if args.reset:
        if not s.halted:
            print("  Nu era blocat. Nimic de resetat.\n")
            return 0
        ks.reset()
        print("  Blocare ridicata.\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
