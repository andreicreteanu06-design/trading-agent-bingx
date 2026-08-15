"""
Detecteaza inchiderile de pozitii si le scrie in jurnalul circuit breaker-ului.

Agentul asta nu trimite ordine - executia e manuala. Deci nu exista un moment
"am inchis pozitia" pe care sa-l prindem din cod. Singura sursa de adevar e
contul: la fiecare ciclu comparam pozitiile deschise cu snapshot-ul de la ciclul
anterior. Ce era acolo si nu mai e, s-a inchis.

P&L-ul realizat se cere exchange-ului (fetch_realized_pnl). Daca nu ni-l da -
cont fara chei, endpoint indisponibil, executii fara camp de profit - NU
inventam zero. Cadem pe ultimul P&L nerealizat vazut inainte de disparitie si
marcam intrarea cu `source: "estimated"`. Circuit breaker-ul citeste doar
`pnl` si `closed_at`, deci campul in plus e strict pentru audit: cand te uiti
peste jurnal peste doua luni, vrei sa stii care cifre sunt masurate si care
sunt aproximate.

Fara chei API nu exista pozitii de urmarit, deci modulul devine no-op.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from risk.circuit_breaker import append_trade

log = logging.getLogger(__name__)


class TradeRecorder:
    """
    Tine un snapshot al pozitiilor deschise si scrie o linie in trade_log.jsonl
    pentru fiecare pozitie care dispare intre doua cicluri.
    """

    def __init__(
        self,
        client,
        trade_log_path: str,
        state_path: str,
        kill_switch=None,
    ) -> None:
        self.client = client
        self.trade_log = Path(trade_log_path)
        self.state_path = state_path
        self.kill_switch = kill_switch

    # ------------------------------------------------------------------ stare
    def _load_snapshot(self) -> dict[str, dict]:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        positions = data.get("positions")
        return positions if isinstance(positions, dict) else {}

    def _save_snapshot(self, positions: dict[str, dict]) -> None:
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "positions": positions,
        }
        with open(self.state_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    @staticmethod
    def _key(pos: dict) -> str:
        """O pozitie e identificata de simbol + directie (hedge mode le separa)."""
        return f"{pos.get('symbol')}|{pos.get('side')}"

    # ------------------------------------------------------------- sincronizare
    def sync(self, positions: list[dict[str, Any]] | None = None) -> list[dict]:
        """
        Apeleaza la fiecare ciclu, INAINTE de verificarea circuit breaker-ului -
        altfel breaker-ul ia decizia pe un jurnal invechit cu o tranzactie.

        Returneaza lista inchiderilor inregistrate acum (goala de obicei).
        """
        if not getattr(self.client, "authenticated", False):
            return []

        if positions is None:
            try:
                positions = self.client.fetch_open_positions()
            except Exception as exc:  # noqa: BLE001
                log.warning("Nu am putut citi pozitiile pentru recorder: %s", exc)
                return []

        now = datetime.now(timezone.utc)
        previous = self._load_snapshot()

        current: dict[str, dict] = {}
        for pos in positions:
            key = self._key(pos)
            prev = previous.get(key, {})
            current[key] = {
                "symbol": pos.get("symbol"),
                "side": pos.get("side"),
                "contracts": pos.get("contracts"),
                "entry_price": pos.get("entry_price"),
                "unrealized_pnl": pos.get("unrealized_pnl"),
                # Pastram momentul primei aparitii, ca sa stim de unde sa cerem
                # executiile cand pozitia se inchide.
                "first_seen": prev.get("first_seen") or now.isoformat(),
                "last_seen": now.isoformat(),
            }

        closed_keys = set(previous) - set(current)
        recorded: list[dict] = []

        for key in sorted(closed_keys):
            entry = self._record_close(previous[key], now)
            if entry:
                recorded.append(entry)

        self._save_snapshot(current)
        return recorded

    # ------------------------------------------------------------- inregistrare
    def _record_close(self, snapshot: dict, now: datetime) -> dict | None:
        symbol = snapshot.get("symbol")
        if not symbol:
            return None

        since_ms = self._since_ms(snapshot)
        pnl = None
        source = "exchange"

        try:
            pnl = self.client.fetch_realized_pnl(symbol, since_ms)
        except Exception as exc:  # noqa: BLE001
            log.warning("Fara P&L realizat pentru %s: %s", symbol, exc)

        if pnl is None:
            fallback = snapshot.get("unrealized_pnl")
            if fallback is None:
                log.error(
                    "Pozitia %s s-a inchis dar nu am nicio valoare de P&L - "
                    "nu o inregistrez, jurnalul ar deveni fals.",
                    symbol,
                )
                return None
            pnl = float(fallback)
            source = "estimated"
            log.warning(
                "P&L estimat pentru %s (%.2f) - exchange-ul nu a returnat executii.",
                symbol,
                pnl,
            )

        append_trade(self.trade_log, float(pnl), closed_at=now)

        # Kill-switch-ul masoara acelasi lucru pe alta fereastra. Pana acum
        # record_trade_closed nu era apelat nicaieri in productie; aici e
        # singurul loc din agent care stie ca o pozitie s-a inchis.
        if self.kill_switch is not None:
            try:
                self.kill_switch.record_trade_closed(float(pnl))
            except Exception as exc:  # noqa: BLE001
                log.warning("Kill-switch nu a putut inregistra inchiderea: %s", exc)

        entry = {
            "symbol": symbol,
            "side": snapshot.get("side"),
            "pnl": round(float(pnl), 2),
            "source": source,
            "closed_at": now.isoformat(),
        }
        log.info(
            "Pozitie inchisa: %s %s, P&L %+.2f USDT (%s)",
            symbol,
            snapshot.get("side"),
            pnl,
            source,
        )
        return entry

    @staticmethod
    def _since_ms(snapshot: dict) -> int:
        """Momentul de la care cerem executiile: cand am vazut prima data pozitia."""
        raw = snapshot.get("first_seen")
        if raw:
            try:
                return int(datetime.fromisoformat(raw).timestamp() * 1000)
            except ValueError:
                pass
        # Fallback conservator: ultimele 7 zile.
        return int((datetime.now(timezone.utc).timestamp() - 7 * 86400) * 1000)
