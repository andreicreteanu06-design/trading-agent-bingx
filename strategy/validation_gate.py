"""
Poarta de validare: nicio strategie nevalidata nu produce semnale tranzactionabile.

Aceasta este singura componenta din proiect care apara impotriva celei mai
scumpe greseli posibile - nu o pierdere dintr-un trade prost, ci tranzactionarea
sistematica a unei strategii care nu a demonstrat niciodata ca are edge.

Istoria proiectului o justifica: prima strategie a pierdut 127 de tranzactii
inainte ca cineva sa masoare expectancy. A doua familie de strategii (trei
setup-uri, cu oscilatori si divergente) a fost respinsa de validator INAINTE sa
coste ceva. Diferenta dintre cele doua situatii este exact acest fisier.

CUM FUNCTIONEAZA

`backtest/validate.py` scrie un certificat JSON cand ruleaza. Poarta il citeste
si raspunde la trei intrebari:

  1. Exista certificat pentru strategia activa?
  2. Spune ca a TRECUT?
  3. Este suficient de recent, si pentru configuratia curenta?

Daca oricare raspuns e nu, semnalele continua sa fie generate si afisate - sunt
utile ca sa vezi ce face agentul - dar sunt marcate `tradeable=False`, cu motivul
la vedere. Nimic nu se ascunde, nimic nu se blocheaza in tacere.

DE CE VERIFICA SI CONFIGURATIA

Un certificat obtinut pe `tp_r_multiples=(4.0, 7.0)` nu spune nimic despre
`(2.0, 3.0)`. Sunt strategii diferite care se intampla sa imparta acelasi cod.
Poarta calculeaza o amprenta a parametrilor care schimba comportamentul si
invalideaza certificatul cand se modifica vreunul. Altfel ar deveni exact ce
pare ca previne: o stampila pusa o data si respectata la nesfarsit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_PATH = "logs/validation.json"

# Parametrii care schimba efectiv comportamentul strategiei. Modificarea
# oricaruia invalideaza certificatul.
FINGERPRINT_FIELDS = (
    "exec_tf", "context_tf", "max_bars_in_trade",
    "tp_r_multiples", "min_setup_score", "min_risk_reward",
    "stop_buffer_atr", "min_stop_distance_pct", "max_stop_distance_pct",
    "max_cost_r", "entry_order_type", "limit_offset_atr", "limit_valid_bars",
    "min_sweep_atr", "min_sweep_volume", "reclaim_within",
    "squeeze_enabled", "vwap_reversion_enabled",
)


def fingerprint(scfg) -> str:
    """Amprenta parametrilor care conteaza. Se schimba unul, se schimba ea."""
    parts = []
    for name in FINGERPRINT_FIELDS:
        value = getattr(scfg, name, None)
        parts.append(f"{name}={value!r}")
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class Certificate:
    """Rezultatul unei rulari de validare, salvat pe disc."""

    passed: bool
    fingerprint: str
    created_at: str
    expectancy_r: float
    t_stat: float
    total_trades: int
    windows_passing: int
    symbols_passing: int
    period_start: str = ""
    period_end: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GateResult:
    """Verdictul portii pentru configuratia curenta."""

    tradeable: bool
    reason: str
    certificate: Certificate | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tradeable": self.tradeable,
            "reason": self.reason,
            "certificate": self.certificate.to_dict() if self.certificate else None,
        }


def save_certificate(cert: Certificate, path: str = DEFAULT_PATH) -> None:
    """Scrie certificatul. Apelat de backtest/validate.py la final."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cert.to_dict(), fh, indent=2, ensure_ascii=False)
    log.info("Certificat de validare scris: %s (passed=%s)", path, cert.passed)


def load_certificate(path: str = DEFAULT_PATH) -> Certificate | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return Certificate(**data)
    except Exception as exc:  # noqa: BLE001
        log.warning("Certificat ilizibil (%s): %s", path, exc)
        return None


def check(scfg, path: str = DEFAULT_PATH, max_age_days: int = 30) -> GateResult:
    """
    Are strategia curenta voie sa produca semnale tranzactionabile?

    `max_age_days`: un certificat vechi de luni de zile descrie un alt regim de
    piata. Nu il invalidam din superstitie, ci pentru ca volatilitatea si
    costurile se schimba, iar amandoua intra in aritmetica edge-ului.
    """
    cert = load_certificate(path)

    if cert is None:
        return GateResult(
            tradeable=False,
            reason=(
                "Nu exista certificat de validare. Ruleaza "
                "`python -m backtest.validate` inainte de a trata semnalele "
                "ca tranzactionabile."
            ),
        )

    if not cert.passed:
        return GateResult(
            tradeable=False,
            certificate=cert,
            reason=(
                f"Validarea a fost RESPINSA (expectancy {cert.expectancy_r:+.3f}R, "
                f"t={cert.t_stat:+.2f}, {cert.total_trades} trades). "
                "Strategia nu a demonstrat edge - semnalele sunt informative, "
                "nu tranzactionabile."
            ),
        )

    current = fingerprint(scfg)
    if cert.fingerprint != current:
        return GateResult(
            tradeable=False,
            certificate=cert,
            reason=(
                "Configuratia s-a schimbat de la ultima validare "
                f"(amprenta {cert.fingerprint} -> {current}). "
                "Certificatul descrie alta strategie. Revalideaza."
            ),
        )

    try:
        created = datetime.fromisoformat(cert.created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - created
        if age > timedelta(days=max_age_days):
            return GateResult(
                tradeable=False,
                certificate=cert,
                reason=(
                    f"Certificat expirat ({age.days} zile, limita {max_age_days}). "
                    "Volatilitatea si costurile s-au putut schimba. Revalideaza."
                ),
            )
    except Exception:  # noqa: BLE001
        return GateResult(
            tradeable=False,
            certificate=cert,
            reason="Data certificatului nu poate fi interpretata. Revalideaza.",
        )

    return GateResult(
        tradeable=True,
        certificate=cert,
        reason=(
            f"Validare TRECUTA: expectancy {cert.expectancy_r:+.3f}R, "
            f"t={cert.t_stat:+.2f}, {cert.total_trades} trades pe "
            f"{cert.windows_passing} ferestre si {cert.symbols_passing} simboluri "
            f"({cert.period_start} -> {cert.period_end})"
        ),
    )
