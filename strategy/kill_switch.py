"""
Kill-switch: intrerupatorul care opreste agentul cand lucrurile merg prost.

Motivul pentru care exista: dupa 3 pierderi consecutive, majoritatea oamenilor
nu devin mai prudenti - devin mai agresivi. Vor sa "recupereze". Acesta este
mecanismul prin care pierderea unui cont devine ireversibila.

Un kill-switch nu te face profitabil. Doar limiteaza cat de repede poti pierde
in timp ce afli ca strategia ta nu functioneaza.

Starea se persista pe disc, deci restartul agentului NU reseteaza limitele.
Asta e intentionat - altfel ai fi tentat sa dai restart cand te blocheaza.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

log = logging.getLogger(__name__)


@dataclass
class KillSwitchState:
    # Ziua curenta (UTC), ca sa stim cand sa resetam contoarele zilnice.
    day: str = ""
    day_start_equity: float = 0.0
    realized_pnl_today: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    # Blocarea manuala sau automata; se ridica doar explicit.
    halted: bool = False
    halt_reason: str = ""
    halted_at: str = ""
    # Recordul de echity, pentru drawdown-ul total (nu doar zilnic).
    peak_equity: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class KillSwitchConfig:
    # Pierdere zilnica maxima ca fractiune din echity de la inceputul zilei.
    max_daily_loss_pct: float = 0.03  # 3%
    # Drawdown total maxim de la varf. Peste asta, agentul se opreste definitiv
    # pana intervii tu.
    max_total_drawdown_pct: float = 0.15  # 15%
    # Pierderi consecutive dupa care ne oprim pe ziua respectiva.
    max_consecutive_losses: int = 3
    # Numar maxim de tranzactii pe zi - previne overtrading-ul.
    max_trades_per_day: int = 5


class KillSwitch:
    def __init__(self, state_path: str, cfg: KillSwitchConfig | None = None) -> None:
        self.path = state_path
        self.cfg = cfg or KillSwitchConfig()
        self.state = self._load()

    # ------------------------------------------------------------- persistenta
    def _load(self) -> KillSwitchState:
        try:
            with open(self.path, encoding="utf-8") as fh:
                return KillSwitchState(**json.load(fh))
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return KillSwitchState()

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.state.to_dict(), fh, indent=2)

    # ------------------------------------------------------------------ ciclu
    def sync(self, equity: float) -> None:
        """Apeleaza la fiecare scanare, inainte de a verifica permisiunea."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if self.state.day != today:
            # Zi noua: resetam contoarele zilnice, dar NU si halt-ul total.
            self.state.day = today
            self.state.day_start_equity = equity
            self.state.realized_pnl_today = 0.0
            self.state.trades_today = 0
            self.state.consecutive_losses = 0
            if self.state.halted and self.state.halt_reason.startswith("ZILNIC"):
                log.info("Zi noua - ridic blocarea zilnica")
                self.state.halted = False
                self.state.halt_reason = ""

        if equity > self.state.peak_equity:
            self.state.peak_equity = equity
        if self.state.day_start_equity <= 0:
            self.state.day_start_equity = equity

        self._evaluate(equity)
        self.save()

    def _evaluate(self, equity: float) -> None:
        if self.state.halted:
            return

        # 1. Drawdown total de la varf - cel mai sever.
        if self.state.peak_equity > 0:
            dd = (self.state.peak_equity - equity) / self.state.peak_equity
            if dd >= self.cfg.max_total_drawdown_pct:
                self._halt(
                    f"TOTAL: drawdown {dd:.1%} >= {self.cfg.max_total_drawdown_pct:.1%} "
                    f"(varf {self.state.peak_equity:.2f} -> acum {equity:.2f})"
                )
                return

        # 2. Pierdere zilnica.
        if self.state.day_start_equity > 0:
            daily = (self.state.day_start_equity - equity) / self.state.day_start_equity
            if daily >= self.cfg.max_daily_loss_pct:
                self._halt(
                    f"ZILNIC: pierdere {daily:.1%} >= {self.cfg.max_daily_loss_pct:.1%}"
                )
                return

        # 3. Pierderi consecutive.
        if self.state.consecutive_losses >= self.cfg.max_consecutive_losses:
            self._halt(
                f"ZILNIC: {self.state.consecutive_losses} pierderi consecutive"
            )
            return

        # 4. Overtrading.
        if self.state.trades_today >= self.cfg.max_trades_per_day:
            self._halt(
                f"ZILNIC: {self.state.trades_today} tranzactii azi "
                f">= {self.cfg.max_trades_per_day}"
            )

    def _halt(self, reason: str) -> None:
        self.state.halted = True
        self.state.halt_reason = reason
        self.state.halted_at = datetime.now(timezone.utc).isoformat()
        log.warning("KILL-SWITCH ACTIVAT: %s", reason)

    # ------------------------------------------------------------- interogare
    @property
    def allowed(self) -> bool:
        return not self.state.halted

    @property
    def reason(self) -> str:
        return self.state.halt_reason

    def status_line(self) -> str:
        s = self.state
        if s.halted:
            return f"OPRIT - {s.halt_reason}"
        daily_pct = 0.0
        if s.day_start_equity > 0:
            daily_pct = (s.realized_pnl_today / s.day_start_equity) * 100
        return (
            f"activ | azi {s.trades_today}/{self.cfg.max_trades_per_day} trades, "
            f"PnL {daily_pct:+.2f}%, {s.consecutive_losses} pierderi consecutive"
        )

    # ------------------------------------------------------------- inregistrare
    def record_trade_opened(self) -> None:
        self.state.trades_today += 1
        self.save()

    def record_trade_closed(self, pnl: float) -> None:
        self.state.realized_pnl_today += pnl
        if pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0
        self.save()

    def reset(self) -> None:
        """Ridicare manuala a blocarii. Foloseste-o constient, nu reflex."""
        log.info("Kill-switch resetat manual (motiv anterior: %s)", self.state.halt_reason)
        self.state.halted = False
        self.state.halt_reason = ""
        self.state.consecutive_losses = 0
        self.save()
