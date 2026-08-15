"""
Poarta de risc dinaintea fiecarei scanari.

Doua verificari, in ordinea in care conteaza:

  1. REGIM DE PIATA (risk/regime_analyzer.py) - "merita sa caut ceva acum?"
     Structura BTC pe MA50/MA200 + funding-ul mediu pe perpetuale. Daca zona e
     RISK_OFF, ciclul se sare complet. E un filtru de context, nu o limita de
     siguranta: cand datele lipsesc, implicit lasa agentul sa mearga mai departe
     si marcheaza asta in status.

  2. CIRCUIT BREAKER (risk/circuit_breaker.py) - "am voie sa mai deschid?"
     P&L realizat pe ferestre de 24h / 7 zile / 30 zile, plus cooldown dupa
     pierderi consecutive. Asta E o limita de siguranta: citeste un fisier
     local, nu depinde de retea, si nu are voie sa esueze in "permis".

Datele de regim se pastreaza in cache pe disc. Inchiderile zilnice si funding-ul
pe 8h nu se schimba la fiecare 15 minute; a le reinteroga la fiecare scanare ar
insemna zeci de apeluri inutile pe zi si un motiv in plus de rate limit.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from risk.circuit_breaker import CircuitConfig, check_circuit_breaker
from risk.regime_analyzer import calculate_regime

log = logging.getLogger(__name__)


class RegimeGate:
    """Calculeaza regimul de piata din date reale BingX, cu cache pe disc."""

    def __init__(self, client, cfg, cache_path: str) -> None:
        self.client = client
        self.cfg = cfg
        self.cache_path = cache_path

    # ------------------------------------------------------------------ cache
    def _read_cache(self) -> dict | None:
        try:
            with open(self.cache_path, encoding="utf-8") as fh:
                cached = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

        ts = cached.get("fetched_at")
        if not ts:
            return None
        try:
            fetched = datetime.fromisoformat(ts)
        except ValueError:
            return None

        age = datetime.now(timezone.utc) - fetched
        if age > timedelta(minutes=self.cfg.refresh_minutes):
            return None
        return cached

    def _write_cache(self, payload: dict) -> None:
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)

    # --------------------------------------------------------------- evaluare
    def evaluate(self, force: bool = False) -> dict:
        """
        Returneaza rezultatul regime_analyzer.calculate_regime(), imbogatit cu:
            allowed     - True daca ciclul poate continua
            reason      - de ce a fost blocat, daca a fost
            from_cache  - daca valoarea vine din cache
            data_error  - mesajul erorii de date, daca a fost una
        """
        if not self.cfg.enabled:
            return {
                "score": None,
                "zone": "DISABLED",
                "allowed": True,
                "reason": "",
                "from_cache": False,
            }

        if not force:
            cached = self._read_cache()
            if cached is not None:
                cached["from_cache"] = True
                return cached

        closes: list[float] = []
        funding: dict[str, float] = {}
        data_error = ""

        try:
            closes = self.client.fetch_daily_closes(
                self.cfg.btc_symbol, self.cfg.daily_candles
            )
        except Exception as exc:  # noqa: BLE001
            data_error = f"inchideri zilnice {self.cfg.btc_symbol}: {exc}"
            log.warning("Regim - %s", data_error)

        try:
            funding = self.client.fetch_funding_rates(list(self.cfg.funding_symbols))
        except Exception as exc:  # noqa: BLE001
            msg = f"funding rates: {exc}"
            data_error = f"{data_error}; {msg}" if data_error else msg
            log.warning("Regim - %s", msg)

        result: dict[str, Any] = calculate_regime(closes, funding)
        result["fetched_at"] = datetime.now(timezone.utc).isoformat()
        result["from_cache"] = False
        result["data_error"] = data_error
        result["closes_used"] = len(closes)
        result["funding_symbols_used"] = sorted(funding)

        zone = result.get("zone", "UNKNOWN")
        if zone in self.cfg.blocked_zones:
            result["allowed"] = False
            result["reason"] = (
                f"Regim {zone} (scor {result.get('score')}/100) - "
                f"{self._components_summary(result)}"
            )
        elif zone == "UNKNOWN" and self.cfg.block_on_unknown:
            result["allowed"] = False
            result["reason"] = f"Regim necunoscut - fara date ({data_error or 'sursa indisponibila'})"
        else:
            result["allowed"] = True
            result["reason"] = ""

        self._write_cache(result)
        return result

    def cached(self) -> dict:
        """
        Ultimul regim calculat, fara niciun apel de retea.

        Pentru dashboard: statusul e cerut la fiecare cateva secunde din browser,
        iar `evaluate()` poate porni o descarcare de 260 de lumanari zilnice cand
        cache-ul a expirat. Handler-ul HTTP ar astepta dupa BingX. Aici raportam
        doar ce stim deja; recalcularea ramane treaba scanarii.
        """
        if not self.cfg.enabled:
            return {"zone": "DISABLED", "score": None, "allowed": True, "reason": ""}

        cached = self._read_cache()
        if cached is None:
            return {
                "zone": "STALE",
                "score": None,
                "allowed": True,
                "reason": "",
                "stale": True,
            }
        cached["from_cache"] = True
        return cached

    @staticmethod
    def _components_summary(result: dict) -> str:
        comps = result.get("components") or {}
        parts = [
            c.get("signal", "")
            for c in (comps.get("btc_trend"), comps.get("funding"))
            if isinstance(c, dict) and c.get("signal")
        ]
        return " | ".join(parts)

    def status_line(self, result: dict) -> str:
        zone = result.get("zone", "?")
        score = result.get("score")
        if zone == "DISABLED":
            return "dezactivat"
        if zone == "STALE":
            return "necalculat inca"
        score_txt = f"{score}/100" if score is not None else "fara scor"
        suffix = " (cache)" if result.get("from_cache") else ""
        if result.get("data_error"):
            suffix += " [date incomplete]"
        return f"{zone} {score_txt}{suffix}"


class CircuitGate:
    """Wrapper subtire peste circuit_breaker.check_circuit_breaker()."""

    def __init__(self, cfg, trade_log_path: str) -> None:
        self.cfg = cfg
        self.path = Path(trade_log_path)
        self.config = CircuitConfig(
            max_daily_loss_pct=cfg.max_daily_loss_pct,
            losing_streak_n=cfg.losing_streak_n,
            cooldown_hours=cfg.cooldown_hours,
            weekly_drawdown_pct=cfg.weekly_drawdown_pct,
            monthly_drawdown_pct=cfg.monthly_drawdown_pct,
        )

    def check(self, account_balance_usdt: float) -> dict:
        """
        Verificarea dinaintea oricarui ordin nou. Rezultatul contine
        `allowed` = True doar pentru TRADING_ALLOWED.
        """
        if not self.cfg.enabled:
            return {
                "recommendation": "DISABLED",
                "allowed": True,
                "reason": "",
                "metrics": {},
                "triggered_rules": [],
            }

        try:
            result = check_circuit_breaker(self.path, account_balance_usdt, self.config)
        except Exception as exc:  # noqa: BLE001
            # Un jurnal corupt nu are voie sa treaca drept "e in regula".
            log.error("Circuit breaker nu a putut fi evaluat: %s", exc)
            return {
                "recommendation": "ERROR",
                "allowed": False,
                "reason": f"Jurnalul de tranzactii nu poate fi citit: {exc}",
                "metrics": {},
                "triggered_rules": [f"read_error: {exc}"],
            }

        result["allowed"] = result["recommendation"] == "TRADING_ALLOWED"
        result["reason"] = "; ".join(result.get("triggered_rules") or [])
        return result

    @staticmethod
    def status_line(result: dict) -> str:
        rec = result.get("recommendation", "?")
        if rec == "DISABLED":
            return "dezactivat"
        m = result.get("metrics") or {}
        return (
            f"{rec} | 24h {m.get('pnl_today', 0):+.2f} | "
            f"7z {m.get('pnl_7d', 0):+.2f} | 30z {m.get('pnl_30d', 0):+.2f} | "
            f"{m.get('consecutive_losses', 0)} pierderi consecutive"
        )
