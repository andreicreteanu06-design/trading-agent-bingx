"""
Frana de drawdown pentru cartea cross-sectionala.

De ce exista un fisier separat si nu inca un mecanism nou: KillSwitch din
`strategy/kill_switch.py` face deja exact ce trebuie - urmareste varful de
echitate, pierderea zilnica, persista pe disc si cere ridicare manuala. Aici
doar il configuram pentru o carte de portofoliu si ii dam un fisier de stare
propriu. Un al treilea mecanism de drawdown in acelasi proiect ar fi fost o
greseala: auditul a gasit deja doua (circuit_breaker.py si KillSwitch) cu
praguri diferite, ceea ce inseamna ca nimeni nu stie care opreste de fapt.

CE NU FOLOSIM, SI DE CE
KillSwitch numara si pierderi consecutive si tranzactii pe zi. Amandoua sunt
marimi de strategie per-tranzactie si nu au sens pentru o carte care deschide
40 de pozitii deodata la fiecare rebalansare: "3 pierderi consecutive" nu
inseamna nimic cand pozitiile se inchid toate in acelasi moment, iar "5
tranzactii pe zi" ar declansa la prima rebalansare. De aceea contoarele lor NU
sunt niciodata alimentate aici - nu apelam record_trade_opened /
record_trade_closed - deci pragurile lor nu se ating niciodata. Raman la valorile
implicite intentionat: puse pe zero ar declansa imediat (0 >= 0).

PRAGURILE
Verificate fata de statistica din certificatul validat, nu mostenite orbeste:

  drawdown 15%  = 1.26x cel mai rau drawdown out-of-sample (11.9%). Peste asta,
                  cartea se comporta mai rau decat orice a aratat walk-forward,
                  ceea ce e chiar definitia lui "s-a schimbat ceva".
  zilnic 3%     = 2.7 sigma zilnice (volatilitatea validata e 21%/an, adica
                  1.10% pe zi). Daca nimic nu s-a stricat, o zi asa apare cam o
                  data la 317 zile - o oprire falsa pe an, in schimbul prinderii
                  unei probleme reale.
"""

from __future__ import annotations

import os

from strategy.kill_switch import KillSwitch, KillSwitchConfig

STATE_PATH = "logs/killswitch_xs.json"

# Fisier separat de killswitch.json al strategiei BTC/ETH/SOL. Doua strategii
# cu echitati diferite nu pot imparti un contor de varf: un drawdown pe una ar
# opri-o pe cealalta, iar o zi buna pe una ar masca o zi proasta pe alta.
CONFIG = KillSwitchConfig(
    max_daily_loss_pct=0.03,
    max_total_drawdown_pct=0.15,
)


def book_brake(state_path: str = STATE_PATH) -> KillSwitch:
    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
    return KillSwitch(state_path, CONFIG)


def status_line(ks: KillSwitch, equity: float) -> str:
    """
    Starea franei in marimile care chiar o guverneaza.

    KillSwitch.status_line() e scrisa pentru strategii per-tranzactie si afiseaza
    "azi 0/5 trades, 0 pierderi consecutive" - contoare care aici nu sunt
    alimentate niciodata, deci arata ca niste limite active care de fapt nu pot
    declansa. Afisate asa, ascund exact cele doua praguri care conteaza.
    """
    s = ks.state
    if s.halted:
        return f"OPRIT - {s.halt_reason}"

    dd = (s.peak_equity - equity) / s.peak_equity if s.peak_equity > 0 else 0.0
    daily = (
        (equity - s.day_start_equity) / s.day_start_equity
        if s.day_start_equity > 0 else 0.0
    )
    return (
        f"activ | drawdown {dd:.2%} din max {CONFIG.max_total_drawdown_pct:.0%} "
        f"| azi {daily:+.2%}, limita -{CONFIG.max_daily_loss_pct:.0%}"
    )
