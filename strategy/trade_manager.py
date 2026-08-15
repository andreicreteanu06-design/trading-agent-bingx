"""
Managementul pozitiei dupa intrare.

Aici se decide ce faci cu o pozitie DEJA deschisa: cand muti stopul, cand iei
profit partial, cand iesi complet. Este partea pe care majoritatea o
improvizeaza si exact motivul pentru care o strategie cu edge pozitiv poate
ajunge sa piarda bani.

Regulile implementate:

  1. Breakeven dupa TP1 - dupa ce ai luat jumatate din pozitie la 1.5R, stopul
     se muta la intrare + costurile. Nu la intrare exact: daca stopul e fix la
     entry, iesi in minus cu fee-urile si slippage-ul.

  2. Trailing pe ATR - dupa breakeven, stopul urmareste pretul la distanta de
     N x ATR, dar NU se muta niciodata inapoi. Un stop care coboara nu e stop,
     e o speranta.

  3. Timp maxim in pozitie - daca dupa X lumanari pozitia nu a ajuns nici la TP1
     nici la stop, iesi. Capitalul blocat intr-o pozitie moarta e capital care
     nu lucreaza, si pe perpetuals platesti funding pentru privilegiu.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Side = Literal["long", "short"]


@dataclass
class ManagedPosition:
    symbol: str
    side: Side
    entry: float
    initial_stop: float
    current_stop: float
    take_profits: list[float]
    size: float
    r_per_unit: float
    bars_held: int = 0
    fraction_closed: float = 0.0
    at_breakeven: bool = False
    highest_favorable: float = 0.0  # cel mai bun pret atins (MFE)

    @property
    def is_open(self) -> bool:
        return self.fraction_closed < 0.9999


@dataclass
class ManagementConfig:
    # Multiplicatorul ATR pentru trailing, dupa activare.
    trail_atr_mult: float = 2.0
    # Trailing-ul se activeaza doar dupa ce pretul a atins acest multiplu de R.
    trail_activate_r: float = 0.5
    # Fractiunea inchisa la TP1.
    tp1_close_fraction: float = 0.0
    # Costul dus-intors, folosit ca buffer peste breakeven.
    roundtrip_cost: float = 0.002  # 0.2% - acopera fee + slippage cu marja
    # Iesire fortata dupa atatea lumanari fara rezolutie.
    max_bars_in_trade: int = 48


class TradeManager:
    def __init__(self, cfg: ManagementConfig | None = None) -> None:
        self.cfg = cfg or ManagementConfig()

    def update(
        self, pos: ManagedPosition, high: float, low: float, close: float, atr: float
    ) -> list[dict]:
        """
        Proceseaza o lumanare noua pentru o pozitie deschisa.

        Returneaza lista de actiuni declansate, in ordinea in care s-au produs.
        Fiecare actiune e un dict cu cel putin {"type", "price", "reason"}.
        """
        actions: list[dict] = []
        pos.bars_held += 1

        favorable = high if pos.side == "long" else low
        if pos.highest_favorable == 0.0:
            pos.highest_favorable = favorable
        elif pos.side == "long":
            pos.highest_favorable = max(pos.highest_favorable, favorable)
        else:
            pos.highest_favorable = min(pos.highest_favorable, favorable)

        # --- 1. STOP: se verifica primul, mereu. Asumptia conservatoare.
        if self._stop_hit(pos, high, low):
            actions.append(
                {
                    "type": "close",
                    "price": pos.current_stop,
                    "fraction": 1.0 - pos.fraction_closed,
                    "reason": "breakeven" if pos.at_breakeven else "stop",
                }
            )
            pos.fraction_closed = 1.0
            return actions

        # --- 2. TP1: profit partial + mutare la breakeven
        tp1 = pos.take_profits[0]
        if pos.fraction_closed == 0.0 and self._level_hit(pos, tp1, high, low):
            actions.append(
                {
                    "type": "partial_close",
                    "price": tp1,
                    "fraction": self.cfg.tp1_close_fraction,
                    "reason": "tp1",
                }
            )
            pos.fraction_closed = self.cfg.tp1_close_fraction

            be = self._breakeven_level(pos)
            pos.current_stop = be
            pos.at_breakeven = True
            actions.append(
                {"type": "move_stop", "price": be, "reason": "breakeven dupa TP1"}
            )

        # --- 3. TP2: inchidere completa
        if len(pos.take_profits) > 1:
            tp2 = pos.take_profits[1]
            if pos.is_open and self._level_hit(pos, tp2, high, low):
                actions.append(
                    {
                        "type": "close",
                        "price": tp2,
                        "fraction": 1.0 - pos.fraction_closed,
                        "reason": "tp2",
                    }
                )
                pos.fraction_closed = 1.0
                return actions

        # --- 4. Trailing pe ATR, doar dupa ce am depasit pragul de activare
        if pos.is_open and atr > 0:
            r_reached = abs(pos.highest_favorable - pos.entry) / pos.r_per_unit
            if r_reached >= self.cfg.trail_activate_r:
                new_stop = self._trail_level(pos, close, atr)
                if self._is_improvement(pos, new_stop):
                    pos.current_stop = new_stop
                    actions.append(
                        {
                            "type": "move_stop",
                            "price": new_stop,
                            "reason": f"trailing {self.cfg.trail_atr_mult}xATR",
                        }
                    )

        # --- 5. Iesire pe timp
        if pos.is_open and pos.bars_held >= self.cfg.max_bars_in_trade:
            actions.append(
                {
                    "type": "close",
                    "price": close,
                    "fraction": 1.0 - pos.fraction_closed,
                    "reason": f"timp expirat ({pos.bars_held} lumanari)",
                }
            )
            pos.fraction_closed = 1.0

        return actions

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _stop_hit(pos: ManagedPosition, high: float, low: float) -> bool:
        return low <= pos.current_stop if pos.side == "long" else high >= pos.current_stop

    @staticmethod
    def _level_hit(pos: ManagedPosition, level: float, high: float, low: float) -> bool:
        return high >= level if pos.side == "long" else low <= level

    def _breakeven_level(self, pos: ManagedPosition) -> float:
        cost = self.cfg.roundtrip_cost
        return pos.entry * (1 + cost) if pos.side == "long" else pos.entry * (1 - cost)

    def _trail_level(self, pos: ManagedPosition, close: float, atr: float) -> float:
        offset = self.cfg.trail_atr_mult * atr
        return close - offset if pos.side == "long" else close + offset

    @staticmethod
    def _is_improvement(pos: ManagedPosition, new_stop: float) -> bool:
        """Stopul se muta doar in favoarea noastra. Niciodata inapoi."""
        return (
            new_stop > pos.current_stop
            if pos.side == "long"
            else new_stop < pos.current_stop
        )
