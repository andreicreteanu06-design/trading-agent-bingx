"""
Logica de scanare, extrasa ca sa fie folosita identic din terminal si din
dashboard-ul web.

Regula de aur a acestui fisier: intoarce DATE, nu afiseaza nimic. Cine il
apeleaza decide cum le prezinta - print in terminal, JSON in browser, mesaj pe
Telegram. Daca ar contine print-uri, dashboard-ul si terminalul ar diverge in
timp si ai avea doua comportamente diferite pentru acelasi agent.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import config as cfg
from ai.claude_analyzer import ClaudeAnalyzer
from alerts.telegram_bot import TelegramNotifier
from exchange.bingx_client import BingXClient
from news.blackout import NewsBlackout, VolatilityGuard
from news import sentiment
from news.sentiment import SentimentConfig
from risk.gate import CircuitGate, RegimeGate
from risk.trade_recorder import TradeRecorder
from strategy import indicators, risk_engine, signal_builder, validation_gate
from strategy.kill_switch import KillSwitch, KillSwitchConfig

log = logging.getLogger(__name__)
C = cfg.CONFIG


@dataclass
class SymbolResult:
    """Ce s-a intamplat cu un simbol intr-o scanare."""

    symbol: str
    status: str  # "approved" | "rejected" | "no_setup" | "skipped" | "error" | "claude_skip"
    detail: str = ""
    signal: dict | None = None
    trade: dict | None = None
    analysis: dict | None = None


@dataclass
class ScanResult:
    started_at: str
    finished_at: str = ""
    equity: float | None = None
    equity_is_real: bool = False
    open_positions: list[dict] = field(default_factory=list)
    kill_switch_ok: bool = True
    kill_switch_status: str = ""
    kill_switch_reason: str = ""
    blackout_ok: bool = True
    blackout_reasons: list[str] = field(default_factory=list)
    blackout_until: str = ""
    # Regim de piata (risk/regime_analyzer.py) - filtru de context la nivel de ciclu.
    regime_ok: bool = True
    regime_zone: str = ""
    regime_score: float | None = None
    regime_status: str = ""
    regime_reason: str = ""
    # Circuit breaker (risk/circuit_breaker.py) - limita pe P&L realizat.
    circuit_ok: bool = True
    circuit_status: str = ""
    # Sentiment de piata (news/sentiment.py) - blocheaza DIRECTII, nu scanarea.
    sentiment_available: bool = False
    sentiment_blocked_sides: list[str] = field(default_factory=list)
    sentiment_reasons: list[str] = field(default_factory=list)
    sentiment_data: dict = field(default_factory=dict)
    # Poarta de validare (strategy/validation_gate.py). Cand e False, semnalele
    # sunt informative, NU tranzactionabile.
    tradeable: bool = False
    tradeable_reason: str = ""
    circuit_reason: str = ""
    circuit_metrics: dict = field(default_factory=dict)
    # Pozitii detectate ca inchise in acest ciclu si scrise in jurnal.
    closed_trades: list[dict] = field(default_factory=list)
    results: list[SymbolResult] = field(default_factory=list)
    error: str = ""

    @property
    def approved(self) -> list[SymbolResult]:
        return [r for r in self.results if r.status == "approved"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "equity": self.equity,
            "equity_is_real": self.equity_is_real,
            "open_positions": self.open_positions,
            "kill_switch_ok": self.kill_switch_ok,
            "kill_switch_status": self.kill_switch_status,
            "kill_switch_reason": self.kill_switch_reason,
            "blackout_ok": self.blackout_ok,
            "blackout_reasons": self.blackout_reasons,
            "blackout_until": self.blackout_until,
            "regime_ok": self.regime_ok,
            "regime_zone": self.regime_zone,
            "regime_score": self.regime_score,
            "regime_status": self.regime_status,
            "regime_reason": self.regime_reason,
            "circuit_ok": self.circuit_ok,
            "circuit_status": self.circuit_status,
            "sentiment_available": self.sentiment_available,
            "sentiment_blocked_sides": self.sentiment_blocked_sides,
            "sentiment_reasons": self.sentiment_reasons,
            "sentiment_data": self.sentiment_data,
            "tradeable": self.tradeable,
            "tradeable_reason": self.tradeable_reason,
            "circuit_reason": self.circuit_reason,
            "circuit_metrics": self.circuit_metrics,
            "closed_trades": self.closed_trades,
            "error": self.error,
            "results": [
                {
                    "symbol": r.symbol,
                    "status": r.status,
                    "detail": r.detail,
                    "signal": r.signal,
                    "trade": r.trade,
                    "analysis": r.analysis,
                }
                for r in self.results
            ],
        }


class Scanner:
    """
    Detine conexiunile si starea partajata. Se creeaza o singura data si se
    reutilizeaza - load_markets() e scump si nu are rost repetat la fiecare
    scanare.
    """

    def __init__(self, symbols: list[str] | None = None, use_claude: bool = True) -> None:
        self.client = BingXClient(cfg.BINGX_API_KEY, cfg.BINGX_SECRET_KEY)
        self.client.load_markets()

        requested = symbols or list(C.market.symbols)
        self.symbols = [s for s in requested if self.client.market_exists(s)]
        self.invalid_symbols = [s for s in requested if s not in self.symbols]

        self.analyzer = ClaudeAnalyzer(cfg.ANTHROPIC_API_KEY, cfg.CLAUDE_MODEL)
        self.notifier = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
        self.use_claude = use_claude and self.analyzer.enabled

        self.kill_switch = KillSwitch(
            os.path.join(C.log_dir, "killswitch.json"),
            KillSwitchConfig(max_consecutive_losses=C.risk.max_consecutive_losses),
        )
        self.blackout = NewsBlackout()
        self.sentiment_cfg = SentimentConfig()
        self._sentiment = None
        self.vol_guard = VolatilityGuard()

        # Modulele de risc adaugate peste agent.
        self.regime_gate = RegimeGate(self.client, C.regime, C.regime_cache)
        self.circuit_gate = CircuitGate(C.circuit, C.trade_log)
        self.trade_recorder = TradeRecorder(
            self.client, C.trade_log, C.positions_state, kill_switch=self.kill_switch
        )

    # ------------------------------------------------------------------ stare
    def _load_state(self) -> dict:
        try:
            with open(C.state_file, encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"last_signal": {}}

    def _save_state(self, state: dict) -> None:
        os.makedirs(C.log_dir, exist_ok=True)
        with open(C.state_file, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)

    def _in_cooldown(self, state: dict, symbol: str) -> bool:
        ts = state.get("last_signal", {}).get(symbol)
        if not ts:
            return False
        try:
            last = datetime.fromisoformat(ts)
        except ValueError:
            return False
        return datetime.now(timezone.utc) - last < timedelta(
            minutes=C.risk.signal_cooldown_minutes
        )

    def _log_signal(self, record: dict) -> None:
        os.makedirs(C.log_dir, exist_ok=True)
        with open(C.signal_log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    # ---------------------------------------------------------------- scanare
    def scan(self) -> ScanResult:
        now = datetime.now(timezone.utc)
        out = ScanResult(started_at=now.isoformat())

        try:
            equity = self.client.fetch_equity_usdt()
            positions = self.client.fetch_open_positions()
        except Exception as exc:  # noqa: BLE001
            out.error = f"Eroare la citirea contului: {exc}"
            out.finished_at = datetime.now(timezone.utc).isoformat()
            return out

        out.equity_is_real = equity is not None
        out.equity = equity if equity is not None else 1000.0
        out.open_positions = positions

        # --- inchideri de pozitii de la ciclul anterior
        # Trebuie PRIMUL: si kill-switch-ul, si circuit breaker-ul citesc jurnalul
        # pe care il scrie recorder-ul. Daca ruleaza dupa ei, o pierdere proaspata
        # e ignorata exact in ciclul in care conteaza cel mai mult.
        out.closed_trades = self.trade_recorder.sync(positions)

        # --- kill-switch
        self.kill_switch.sync(out.equity)
        out.kill_switch_status = self.kill_switch.status_line()
        out.kill_switch_ok = self.kill_switch.allowed
        out.kill_switch_reason = self.kill_switch.reason
        if not out.kill_switch_ok:
            out.finished_at = datetime.now(timezone.utc).isoformat()
            return out

        # --- blackout
        clear, reasons = self.blackout.check()
        out.blackout_ok = clear
        out.blackout_reasons = reasons
        if not clear:
            until = self.blackout.next_clear_time()
            out.blackout_until = until.strftime("%H:%M UTC") if until else ""
            out.finished_at = datetime.now(timezone.utc).isoformat()
            return out

        # --- regim de piata: merita sa caut ceva acum?
        regime = self.regime_gate.evaluate()
        out.regime_zone = regime.get("zone", "")
        out.regime_score = regime.get("score")
        out.regime_status = self.regime_gate.status_line(regime)
        out.regime_ok = regime.get("allowed", True)
        out.regime_reason = regime.get("reason", "")
        if not out.regime_ok:
            out.finished_at = datetime.now(timezone.utc).isoformat()
            return out

        # --- circuit breaker: am voie sa mai deschid ceva?
        circuit = self.circuit_gate.check(out.equity)
        out.circuit_status = self.circuit_gate.status_line(circuit)
        out.circuit_metrics = circuit.get("metrics", {})
        out.circuit_ok = circuit.get("allowed", True)
        out.circuit_reason = circuit.get("reason", "")
        if not out.circuit_ok:
            out.finished_at = datetime.now(timezone.utc).isoformat()
            return out

        # --- poarta de validare: are strategia dreptul sa produca semnale
        # pe care sa pui bani? Nu opreste scanarea - semnalele raman utile ca
        # informatie - dar le marcheaza explicit.
        gate = validation_gate.check(C.scalp)
        out.tradeable = gate.tradeable
        out.tradeable_reason = gate.reason
        if not gate.tradeable:
            log.warning("Semnale NEtranzactionabile: %s", gate.reason)

        # --- sentiment: ce DIRECTII sunt permise acum?
        #
        # Spre deosebire de portile de mai sus, asta nu opreste scanarea. Blocheaza
        # o directie, nu activitatea: intr-o piata cu funding fierbinte long-urile
        # sunt periculoase, dar short-urile devin mai atractive, nu mai putin.
        #
        # Se evalueaza O SINGURA DATA pe scanare, nu per simbol - Fear & Greed si
        # funding-ul sunt marimi de piata, iar apelurile de retea nu au ce cauta
        # intr-o bucla peste simboluri.
        self._sentiment = None
        if self.sentiment_cfg.enabled:
            try:
                funding = self.client.fetch_funding_rates(
                    list(C.regime.funding_symbols)
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Funding indisponibil pentru sentiment: %s", exc)
                funding = {}

            self._sentiment = sentiment.evaluate(
                self.sentiment_cfg, funding_rates=funding
            )
            out.sentiment_available = self._sentiment.available
            out.sentiment_blocked_sides = sorted(self._sentiment.blocked_sides)
            out.sentiment_reasons = self._sentiment.reasons
            out.sentiment_data = self._sentiment.data

        state = self._load_state()

        for symbol in self.symbols:
            out.results.append(self._scan_symbol(symbol, state, equity, positions))

        self._save_state(state)
        out.finished_at = datetime.now(timezone.utc).isoformat()
        return out

    def _scan_symbol(self, symbol: str, state: dict, equity, positions) -> SymbolResult:
        if self._in_cooldown(state, symbol):
            return SymbolResult(symbol, "skipped", "in cooldown dupa un semnal recent")

        try:
            htf = self.client.fetch_ohlcv(symbol, C.market.htf, C.market.candles)
            ltf = self.client.fetch_ohlcv(symbol, C.market.ltf, C.market.candles)
        except Exception as exc:  # noqa: BLE001
            return SymbolResult(symbol, "error", str(exc))

        vol_ok, vol_reasons = self.vol_guard.check(indicators.enrich(ltf, C.strategy))
        if not vol_ok:
            return SymbolResult(symbol, "skipped", "; ".join(vol_reasons))

        signal = signal_builder.build_signal(symbol, htf, ltf, C.strategy, C.risk)
        if signal is None:
            return SymbolResult(symbol, "no_setup", "niciun setup valabil")

        # --- poarta de sentiment, aplicata DUPA ce stim directia.
        # Blocheaza o directie, nu simbolul: cand funding-ul e fierbinte,
        # long-urile sunt periculoase iar short-urile nu sunt.
        if self._sentiment is not None and not self._sentiment.allows(signal.side):
            motive = "; ".join(self._sentiment.reasons) or "sentiment nefavorabil"
            return SymbolResult(
                symbol, "skipped", f"sentiment blocheaza {signal.side}: {motive}"
            )

        trade = risk_engine.evaluate(signal, equity, positions, C.risk)

        if not trade.approved:
            self._log_signal(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "rejected",
                    "signal": signal.to_dict(),
                    "trade": trade.to_dict(),
                }
            )
            return SymbolResult(
                symbol,
                "rejected",
                "; ".join(trade.rejections),
                signal=signal.to_dict(),
                trade=trade.to_dict(),
            )

        analysis = (
            self.analyzer.analyze(signal.to_dict(), trade.to_dict())
            if self.use_claude
            else None
        )

        if analysis and str(analysis.get("verdict", "")).lower() == "skip":
            self._log_signal(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "claude_skip",
                    "signal": signal.to_dict(),
                    "trade": trade.to_dict(),
                    "analysis": analysis,
                }
            )
            return SymbolResult(
                symbol,
                "claude_skip",
                str(analysis.get("reasoning", "")),
                signal=signal.to_dict(),
                trade=trade.to_dict(),
                analysis=analysis,
            )

        # Semnal aprobat.
        self.notifier.send_signal(signal, trade, analysis)
        self.kill_switch.record_trade_opened()
        state.setdefault("last_signal", {})[symbol] = datetime.now(timezone.utc).isoformat()

        self._log_signal(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "status": "approved",
                "signal": signal.to_dict(),
                "trade": trade.to_dict(),
                "analysis": analysis,
            }
        )

        return SymbolResult(
            symbol,
            "approved",
            "",
            signal=signal.to_dict(),
            trade=trade.to_dict(),
            analysis=analysis,
        )


def read_signal_history(limit: int = 50) -> list[dict]:
    """Ultimele semnale din jurnal, cele mai recente primele."""
    try:
        with open(C.signal_log, encoding="utf-8") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return []

    out: list[dict] = []
    for line in reversed(lines[-limit * 2 :]):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out
