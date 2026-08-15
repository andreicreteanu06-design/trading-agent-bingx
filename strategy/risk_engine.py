"""
Risk engine determinist.

Acesta este stratul care te tine in viata. Nu contine nicio urma de LLM, nicio
"parere", nicio interpretare. Numai aritmetica si reguli hard. Daca un semnal
trece de aici, riscul lui a fost calculat si limitat; daca nu trece, e respins
cu un motiv explicit.

Principiul central al dimensionarii pozitiei:

    risc_in_bani = echity * risk_per_trade
    marime_pozitie (unitati) = risc_in_bani / distanta_pana_la_stop
    notional = marime_pozitie * pret_intrare
    leverage_necesar = notional / echity

Leverage-ul NU este ales. Rezulta din cat de departe e stopul. Cu cat stopul e
mai aproape, cu atat pozitia poate fi mai mare la acelasi risc - dar plafonul de
leverage si buffer-ul de lichidare taie exagerarile.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from risk.position_sizer import SizingParams, calculate_position
from strategy.signal_builder import Signal


# Marje aproximative de mentinere pe BingX pentru USDT-M, in functie de leverage.
# Sunt conservatoare (usor mai mari decat realitatea) ca sa calculam un pret de
# lichidare mai apropiat decat cel real - adica sa fim mai prudenti, nu mai putin.
def _maintenance_margin_rate(leverage: float) -> float:
    if leverage <= 5:
        return 0.005
    if leverage <= 10:
        return 0.0075
    if leverage <= 20:
        return 0.01
    return 0.025


@dataclass
class SizedTrade:
    approved: bool
    symbol: str
    side: str
    entry: float
    stop_loss: float
    take_profits: list[float]
    position_size: float = 0.0
    notional: float = 0.0
    leverage: float = 0.0
    risk_amount: float = 0.0
    risk_pct: float = 0.0
    liquidation_price: float | None = None
    liquidation_buffer_mult: float | None = None
    rejections: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _estimate_liquidation(entry: float, side: str, leverage: float) -> float:
    """
    Pret de lichidare aproximativ pentru izolat, fara PnL-ul altor pozitii.
    Simplificat, dar suficient pentru un buffer de siguranta conservator.
    """
    mmr = _maintenance_margin_rate(leverage)
    if side == "long":
        return entry * (1 - 1 / leverage + mmr)
    return entry * (1 + 1 / leverage - mmr)


def evaluate(
    signal: Signal,
    equity: float | None,
    open_positions: list[dict[str, Any]],
    risk_cfg,
    *,
    assumed_equity: float = 1000.0,
    stats: dict[str, float] | None = None,
) -> SizedTrade:
    """
    Transforma un Signal intr-un SizedTrade validat. Nu trimite niciun ordin -
    doar calculeaza si verifica.

    `stats` optional: {"win_rate": 0.55, "avg_win": 120, "avg_loss": 80}. Daca e
    dat, dimensionarea trece pe half-Kelly in loc de fractie fixa. Nu-l trimite
    din backtest inainte sa ai expectanta pozitiva - Kelly pe expectanta negativa
    returneaza 0 si semnalul e respins, ceea ce e corect, dar surprinzator daca
    nu stii de ce.
    """
    rejections: list[str] = []
    notes: list[str] = []

    # Daca nu avem chei / balanta, lucram cu o echity presupusa doar ca sa
    # putem afisa un exemplu de dimensionare. Marcam clar acest lucru.
    if equity is None or equity <= 0:
        equity = assumed_equity
        notes.append(
            f"Fara balanta reala - dimensionare pe echity presupusa {assumed_equity} USDT"
        )

    trade = SizedTrade(
        approved=False,
        symbol=signal.symbol,
        side=signal.side,
        entry=signal.entry,
        stop_loss=signal.stop_loss,
        take_profits=list(signal.take_profits),
    )

    # ---------------------------------------------------- validari structurale
    stop_dist_pct = signal.stop_distance_pct
    if stop_dist_pct < risk_cfg.min_stop_distance_pct:
        rejections.append(
            f"Stop prea aproape ({stop_dist_pct:.2%} < {risk_cfg.min_stop_distance_pct:.2%})"
        )
    if stop_dist_pct > risk_cfg.max_stop_distance_pct:
        rejections.append(
            f"Stop prea departe ({stop_dist_pct:.2%} > {risk_cfg.max_stop_distance_pct:.2%})"
        )

    rr = signal.risk_reward
    if rr < risk_cfg.min_risk_reward:
        rejections.append(
            f"Risk/reward slab ({rr:.2f} < {risk_cfg.min_risk_reward:.2f})"
        )

    # ---------------------------------------------------- limite de portofoliu
    if len(open_positions) >= risk_cfg.max_open_positions:
        rejections.append(
            f"Prea multe pozitii deschise ({len(open_positions)} >= {risk_cfg.max_open_positions})"
        )

    already_open = any(p.get("symbol") == signal.symbol for p in open_positions)
    if already_open:
        rejections.append(f"Exista deja o pozitie deschisa pe {signal.symbol}")

    # ---------------------------------------------------------- dimensionare
    risk_per_unit = signal.risk_per_unit

    if risk_per_unit <= 0:
        rejections.append("Distanta pana la stop este zero - imposibil de dimensionat")
        trade.rejections = rejections
        trade.notes = notes
        return trade

    # Dimensionarea trece prin risk/position_sizer.py. Nu e o suma fixa: cantitatea
    # rezulta din echity reala, pretul de intrare si stopul planificat.
    #
    # `max_position_pct` primeste plafonul de leverage exprimat ca procent din
    # cont (5x = 500%). Asa, taierea pozitiei la plafon o face sizer-ul, cu
    # aceeasi aritmetica pe care o faceam aici manual - si o raporteaza prin
    # `binding_constraint`.
    sizing_params = SizingParams(
        account_balance_usdt=equity,
        entry_price=signal.entry,
        stop_price=signal.stop_loss,
        risk_pct=risk_cfg.risk_per_trade * 100,
        leverage=risk_cfg.max_leverage,
        max_position_pct=risk_cfg.max_leverage * 100,
        win_rate=stats.get("win_rate") if stats else None,
        avg_win=stats.get("avg_win") if stats else None,
        avg_loss=stats.get("avg_loss") if stats else None,
    )

    try:
        sizing = calculate_position(sizing_params)
    except ValueError as exc:
        rejections.append(f"Dimensionare imposibila: {exc}")
        trade.rejections = rejections
        trade.notes = notes
        return trade

    position_size = sizing["quantity"]
    notional = sizing["notional_usdt"]
    risk_amount = position_size * risk_per_unit
    leverage_needed = notional / equity

    if sizing["sizing_method"] == "half_kelly":
        notes.append(
            f"Dimensionare half-Kelly: {sizing['risk_pct_used']:.2f}% risc "
            f"(in loc de {risk_cfg.risk_per_trade:.2%} fix)"
        )
        if sizing["risk_pct_used"] <= 0:
            # Expectanta negativa: Kelly spune sa nu pariezi deloc. Nu e un
            # detaliu de raportat in note - e un refuz.
            rejections.append(
                "Half-Kelly = 0% (expectanta negativa pe statisticile date) - fara pozitie"
            )

    if sizing["binding_constraint"] == "max_position_pct":
        notes.append(
            f"Pozitie taiata la plafonul de leverage {risk_cfg.max_leverage:.0f}x "
            f"(risc real {risk_amount:.2f} USDT, sub tinta - e mai sigur)"
        )

    if sizing["liquidation_warning"]:
        notes.append(f"Sizer: {sizing['liquidation_warning']}")

    # ------------------------------------------------ expunere notionala totala
    existing_notional = sum(abs(p.get("notional") or 0) for p in open_positions)
    total_notional = existing_notional + notional
    max_notional = equity * risk_cfg.max_total_notional_mult
    if total_notional > max_notional:
        rejections.append(
            f"Expunere notionala totala {total_notional:.0f} > plafon {max_notional:.0f} USDT"
        )

    # ------------------------------------------------------- buffer lichidare
    liq_price = _estimate_liquidation(signal.entry, signal.side, max(leverage_needed, 1.0))
    liq_distance = abs(signal.entry - liq_price)
    buffer_mult = liq_distance / risk_per_unit if risk_per_unit else 0.0

    if buffer_mult < risk_cfg.min_liquidation_buffer_mult:
        rejections.append(
            f"Lichidarea e prea aproape de stop (buffer {buffer_mult:.2f}x < "
            f"{risk_cfg.min_liquidation_buffer_mult:.2f}x) - leverage prea agresiv"
        )

    # ------------------------------------------------------------------ verdict
    trade.position_size = round(position_size, 8)
    trade.notional = round(notional, 2)
    trade.leverage = round(leverage_needed, 2)
    trade.risk_amount = round(risk_amount, 2)
    trade.risk_pct = round(risk_amount / equity, 4)
    trade.liquidation_price = round(liq_price, 8)
    trade.liquidation_buffer_mult = round(buffer_mult, 2)
    trade.rejections = rejections
    trade.notes = notes
    trade.approved = not rejections

    return trade
