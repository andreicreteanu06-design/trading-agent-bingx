"""
Poarta de validare pentru strategia cross-sectionala pe altcoins.

Acelasi principiu ca `strategy/validation_gate.py`: nicio strategie nevalidata
nu produce semnale marcate ca tranzactionabile. Un fisier separat, pentru ca
marimile masurate sunt altele - un portofoliu rebalansat nu are "expectancy in
R pe tranzactie", are randament anualizat, Sharpe si folduri out-of-sample.

Certificatul e scris de `backtest/validate_xs.py` si citit de
`tools/xs_signals.py`. Intre ele nu exista alta cale de comunicare: daca
validarea nu a rulat, semnalele apar dar sunt marcate netranzactionabile.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

DEFAULT_PATH = "logs/validation_xs.json"


def path_for(factor: str, vol_target: float = 0.0, funding_cost: bool = True) -> str:
    """
    Un certificat per configuratie (factor, tinta de volatilitate, cost de
    funding), in fisiere separate.

    Cu un singur fisier comun, validarea factorului B stergea certificatul
    factorului A. Amprenta prindea substituirea si poarta se inchidea corect,
    deci nu era periculos - dar insemna ca fiecare experiment nou anula un
    rezultat castigat cinstit, si trebuia refacut. Numele contine factorul
    tocmai ca rezultatele sa se acumuleze, nu sa se calce in picioare.

    Tinta de volatilitate si costul de funding intra in nume din acelasi
    motiv: schimba randamentul si economia strategiei, deci sunt configuratii
    diferite, nu variante ale aceleiasi rulari.
    """
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in factor)
    suffix = ""
    if vol_target > 0:
        suffix += f"_vt{round(vol_target * 100)}"
    if not funding_cost:
        suffix += "_nofunding"
    return f"logs/validation_xs_{safe}{suffix}.json"


def fingerprint(
    factor: str,
    tf: str,
    universe: int,
    grid: tuple,
    vol_target: float = 0.0,
    funding_cost: bool = True,
) -> str:
    """
    Amprenta a ceea ce defineste strategia.

    Universul intra in amprenta pentru ca un certificat obtinut pe 40 de
    simboluri nu spune nimic despre 10 - latimea sectiunii transversale ESTE
    strategia, nu un detaliu de configurare.

    La fel si tinta de volatilitate si costul de funding: fiecare schimba
    randamentul si drawdown-ul raportat. Fara ele in amprenta, doua rulari cu
    economii diferite scriu peste acelasi certificat si poarta nu mai poate
    spune care configuratie a fost de fapt validata. `funding_cost` mai ales:
    inainte de aceasta modificare costul nu era deloc modelat, iar un
    certificat vechi (nemodelat) nu are voie sa treaca drept echivalent cu
    unul nou doar pentru ca restul parametrilor coincid.
    """
    raw = (f"factor={factor}|tf={tf}|universe={universe}|grid={sorted(grid)!r}"
           f"|vol_target={vol_target:g}|funding_cost={funding_cost}")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class XSCertificate:
    passed: bool
    fingerprint: str
    created_at: str
    factor: str
    tf: str
    universe: int
    ann_return: float
    sharpe: float
    t_stat: float
    max_dd: float
    regime_beta: float
    folds_total: int
    folds_positive: int
    period_start: str = ""
    period_end: str = ""
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class XSGateResult:
    tradeable: bool
    reason: str
    certificate: XSCertificate | None = None


def save_certificate(cert: XSCertificate, path: str = DEFAULT_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cert.to_dict(), fh, indent=2, ensure_ascii=False)
    log.info("Certificat XS scris: %s (passed=%s)", path, cert.passed)


def load_certificate(path: str = DEFAULT_PATH) -> XSCertificate | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return XSCertificate(**json.load(fh))
    except Exception as exc:  # noqa: BLE001
        log.warning("Certificat XS ilizibil (%s): %s", path, exc)
        return None


def check(
    factor: str,
    tf: str,
    universe: int,
    grid: tuple,
    path: str = DEFAULT_PATH,
    max_age_days: int = 30,
    vol_target: float = 0.0,
    funding_cost: bool = True,
) -> XSGateResult:
    cert = load_certificate(path)
    if cert is None:
        return XSGateResult(False, "Nu exista certificat - ruleaza validate_xs.py")

    if not cert.passed:
        return XSGateResult(
            False,
            f"Validarea a fost RESPINSA ({cert.ann_return:+.1%}/an, "
            f"t={cert.t_stat:.2f}, {cert.folds_positive}/{cert.folds_total} folduri)",
            cert,
        )

    want = fingerprint(factor, tf, universe, grid, vol_target, funding_cost)
    if cert.fingerprint != want:
        return XSGateResult(
            False,
            "Configuratia s-a schimbat de la validare - certificatul nu se aplica",
            cert,
        )

    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(cert.created_at)
    except Exception:  # noqa: BLE001
        return XSGateResult(False, "Data certificatului e ilizibila", cert)

    if age > timedelta(days=max_age_days):
        return XSGateResult(
            False,
            f"Certificat vechi de {age.days} zile (limita {max_age_days})",
            cert,
        )

    return XSGateResult(
        True,
        f"Validat: {cert.ann_return:+.1%}/an out-of-sample, Sharpe {cert.sharpe:.2f}, "
        f"t={cert.t_stat:.2f}",
        cert,
    )
