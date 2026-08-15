"""
Inspectare si resetare kill-switch.

    python tools/killswitch.py            # arata starea
    python tools/killswitch.py --reset    # ridica blocarea

Inainte sa dai --reset, raspunde onest la intrebarea: s-a schimbat ceva in
strategie, sau doar vrei sa mai tranzactionezi?
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg
from strategy.kill_switch import KillSwitch, KillSwitchConfig

C = cfg.CONFIG


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    ks = KillSwitch(
        os.path.join(C.log_dir, "killswitch.json"),
        KillSwitchConfig(max_consecutive_losses=C.risk.max_consecutive_losses),
    )
    s = ks.state

    print("\n" + "=" * 60)
    print("  KILL-SWITCH")
    print("=" * 60)
    print(f"  Stare              : {'OPRIT' if s.halted else 'activ'}")
    if s.halted:
        print(f"  Motiv              : {s.halt_reason}")
        print(f"  De la              : {s.halted_at}")
    print(f"  Ziua               : {s.day or '-'}")
    print(f"  Echity start zi    : {s.day_start_equity:.2f}")
    print(f"  Varf echity        : {s.peak_equity:.2f}")
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
