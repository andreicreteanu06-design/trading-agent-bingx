"""
Ce tranzactii trebuie facute. Nu cum se umplu.

Separarea asta e singurul motiv pentru care executorul de hartie si cel real
pot fi comparati: amandoua consuma EXACT acelasi plan, si difera doar in ce fac
cu el - unul simuleaza umplerea la bid/ask, celalalt trimite ordine. Daca
fiecare si-ar calcula propriile tranzactii, hartia ar masura o strategie iar
banii ar tranzactiona alta, si nimeni n-ar observa pana la extras de cont.

E a treia oara in acest proiect cand duplicarea logicii produce exact acest
gen de divergenta tacuta: intai GRID-ul, apoi build_target_book, apoi cadenta
de rebalansare (hold) care lipsea din certificat. De data asta seama e trasa
de la inceput.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from tools.xs_signals import build_target_book

log = logging.getLogger(__name__)

# Sub acest prag de pondere, o diferenta e zgomot de rotunjire, nu o decizie.
REBALANCE_THRESHOLD = 0.005


@dataclass(frozen=True)
class PlannedTrade:
    """Un singur ordin care ar trebui trimis, cu tot ce trebuie ca sa fie verificabil."""

    symbol: str
    old_qty: float           # semnat: pozitiv = long
    new_qty: float           # semnat, dupa rotunjire la precizia pietei
    delta_qty: float         # semnat: ce se cumpara (+) sau se vinde (-)
    ref_price: float         # pretul la care s-a facut planul, nu pretul de umplere
    delta_notional: float    # semnat, la ref_price
    target_weight: float
    closing: bool            # inchide complet pozitia

    @property
    def side(self) -> str:
        """Directia ordinului, in limbajul lui BingXClient.create_market_order."""
        return "long" if self.delta_qty > 0 else "short"


@dataclass
class RebalancePlan:
    trades: list[PlannedTrade] = field(default_factory=list)
    # simbol -> qty, pozitiile lasate neatinse (sub prag, sau respinse de minime)
    untouched: dict[str, float] = field(default_factory=dict)
    # simboluri pe care bursa nu le-ar accepta la acest capital, cu ponderea tinta
    excluded_min: list[tuple[str, float]] = field(default_factory=list)
    asof: pd.Timestamp | None = None
    n_symbols: int = 0

    @property
    def gross_traded_usdt(self) -> float:
        return sum(abs(t.delta_notional) for t in self.trades)

    def summary(self) -> str:
        return (f"{len(self.trades)} tranzactii, {self.gross_traded_usdt:.2f} USDT rulaj, "
                f"{len(self.excluded_min)} sub minim")


def build_plan(
    client,
    factor: str,
    tf: str,
    universe: int,
    vol_scale: bool,
    positions: dict[str, float],
    equity: float,
    prices: dict[str, float] | None = None,
) -> RebalancePlan:
    """
    Planul de rebalansare pentru cartea cross-sectionala.

    `positions` e simbol -> cantitate semnata detinuta acum. `equity` e
    echitatea DUPA marcarea la piata, pentru ca ponderile tinta se aplica la
    capitalul real, nu la cel de la ultima rebalansare.

    Ridica ValueError daca nu se poate construi cartea tinta - apelantul decide
    daca asta inseamna "sari peste rulare" sau "opreste-te".
    """
    w, asof, n_symbols = build_target_book(client, factor, tf, universe, vol_scale)

    plan = RebalancePlan(asof=asof, n_symbols=n_symbols)
    prices = dict(prices or {})

    union_symbols = set(w.index) | set(positions)
    missing = union_symbols - set(prices)
    if missing:
        prices.update(client.fetch_last_prices(list(missing)))

    for sym in sorted(union_symbols):
        px = prices.get(sym)
        if px is None or px <= 0:
            log.warning("Fara pret pentru %s - simbol sarit la rebalansare.", sym)
            if positions.get(sym):
                plan.untouched[sym] = positions[sym]
            continue

        old_qty = positions.get(sym, 0.0)
        current_notional = old_qty * px
        target_weight = float(w.get(sym, 0.0))
        target_notional = target_weight * equity
        current_weight = current_notional / equity if equity else 0.0

        if abs(target_weight - current_weight) <= REBALANCE_THRESHOLD:
            if old_qty:
                plan.untouched[sym] = old_qty
            continue

        # Rotunjim mereu la precizia pietei. closing=True aici sare doar peste
        # verificarea minimelor, nu si peste rotunjire - minimul se verifica mai
        # jos, pe ORDINUL efectiv.
        closing = abs(target_notional) < 1e-9
        trade_amount = abs(old_qty) if closing else abs(target_notional) / px
        try:
            qty_unsigned = client.normalize_amount(sym, trade_amount, closing=True)
        except ValueError as exc:
            plan.excluded_min.append((sym, target_weight))
            if old_qty:
                plan.untouched[sym] = old_qty
            log.info("  %s: %s", sym, exc)
            continue

        # La inchidere pozitia rezultata e exact 0, nu marimea (fara semn) a
        # tranzactiei de inchidere - altfel un short inchis (old_qty negativ) ar
        # iesi cu new_qty pozitiv, o inversare de pozitie inventata din nimic.
        new_qty = 0.0 if closing else (qty_unsigned if target_notional >= 0 else -qty_unsigned)
        delta_qty = new_qty - old_qty
        delta_notional = delta_qty * px

        if abs(delta_notional) < 1e-9:
            if old_qty:
                plan.untouched[sym] = old_qty
            continue

        if not closing:
            # Minimul bursei se aplica ORDINULUI care s-ar trimite (delta fata de
            # pozitia veche), nu pozitiei finale. La un rebalans care doar
            # ajusteaza o pozitie deja deschisa cele doua difera: tinta poate fi
            # $45 (peste minim) cand ordinul e doar $5 din delta (sub minim).
            try:
                client.normalize_amount(sym, abs(delta_qty), price=px, closing=False)
            except ValueError as exc:
                plan.excluded_min.append((sym, target_weight))
                if old_qty:
                    plan.untouched[sym] = old_qty
                log.info("  %s: %s", sym, exc)
                continue

        plan.trades.append(PlannedTrade(
            symbol=sym,
            old_qty=old_qty,
            new_qty=new_qty,
            delta_qty=delta_qty,
            ref_price=px,
            delta_notional=delta_notional,
            target_weight=target_weight,
            closing=closing,
        ))

    return plan
