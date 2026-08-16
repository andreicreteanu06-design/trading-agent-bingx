"""
Dashboard web local pentru agent.

    python -m app.server                    # doar pe acest PC
    python -m app.server --host 0.0.0.0     # si de pe telefon, prin WiFi

Foloseste doar biblioteca standard - fara Flask, fara FastAPI, fara build de
frontend. Motivul e practic: un dashboard care depinde de trei framework-uri se
strica la fiecare update. Asta va merge si peste doi ani.

SECURITATE: nu are autentificare. Implicit asculta doar pe 127.0.0.1, deci e
accesibil numai de pe acest calculator. Daca folosesti --host 0.0.0.0, oricine
din reteaua ta locala poate deschide dashboard-ul. Nu-l expune pe internet.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg
from core.scanner import Scanner, read_signal_history
from execution.paper_executor import load_last_ledger_entry, load_state as load_paper_state

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
)
log = logging.getLogger("dashboard")

C = cfg.CONFIG
HERE = os.path.dirname(os.path.abspath(__file__))


class AgentService:
    """
    Starea partajata a dashboard-ului.

    O scanare dureaza cateva secunde (retea + eventual Claude), deci ruleaza pe
    un thread separat. UI-ul intreaba periodic daca s-a terminat. Un lock
    impiedica doua scanari simultane sa se calce in picioare.
    """

    def __init__(self, symbols: list[str] | None = None, use_claude: bool = True) -> None:
        self.scanner = Scanner(symbols, use_claude)
        self.lock = threading.Lock()
        self.scanning = False
        self.last_result: dict | None = None
        self.last_error: str = ""
        self.auto_enabled = False
        self.auto_interval_min = 15
        self._auto_thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ---------------------------------------------------------------- scanare
    def scan_async(self) -> bool:
        """Porneste o scanare daca nu ruleaza deja. True daca a pornit."""
        if not self.lock.acquire(blocking=False):
            return False

        def worker() -> None:
            self.scanning = True
            try:
                result = self.scanner.scan()
                self.last_result = result.to_dict()
                self.last_error = ""
                approved = len(result.approved)
                log.info(
                    "Scanare terminata - %d semnale aprobate din %d simboluri",
                    approved,
                    len(result.results),
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("Scanare esuata")
                self.last_error = str(exc)
            finally:
                self.scanning = False
                self.lock.release()

        threading.Thread(target=worker, daemon=True).start()
        return True

    # ------------------------------------------------------------ auto-scanare
    def set_auto(self, enabled: bool, interval_min: int = 15) -> None:
        self.auto_interval_min = max(1, interval_min)
        if enabled and not self.auto_enabled:
            self.auto_enabled = True
            self._stop.clear()
            self._auto_thread = threading.Thread(target=self._auto_loop, daemon=True)
            self._auto_thread.start()
            log.info("Auto-scanare pornita, la fiecare %d minute", self.auto_interval_min)
        elif not enabled:
            self.auto_enabled = False
            self._stop.set()
            log.info("Auto-scanare oprita")

    def _auto_loop(self) -> None:
        while self.auto_enabled and not self._stop.is_set():
            self.scan_async()
            # Asteptam in pasi mici, ca oprirea sa fie prompta.
            for _ in range(self.auto_interval_min * 60):
                if self._stop.is_set() or not self.auto_enabled:
                    return
                time.sleep(1)

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        ks = self.scanner.kill_switch
        equity = None
        try:
            equity = self.scanner.client.fetch_equity_usdt()
        except Exception:  # noqa: BLE001
            pass

        blackout_ok, blackout_reasons = self.scanner.blackout.check()
        until = self.scanner.blackout.next_clear_time()

        # Regimul se citeste din cache, nu se recalculeaza: statusul e cerut la
        # cateva secunde din browser, iar un recalcul inseamna 260 de lumanari
        # zilnice descarcate in mijlocul unui handler HTTP.
        regime = self.scanner.regime_gate.cached()
        circuit = self.scanner.circuit_gate.check(equity if equity else 1000.0)

        return {
            "scanning": self.scanning,
            "auto_enabled": self.auto_enabled,
            "auto_interval_min": self.auto_interval_min,
            "last_error": self.last_error,
            "symbols": self.scanner.symbols,
            "invalid_symbols": self.scanner.invalid_symbols,
            "claude_enabled": self.scanner.use_claude,
            "telegram_enabled": self.scanner.notifier.enabled,
            "bingx_authenticated": self.scanner.client.authenticated,
            "equity": equity,
            "kill_switch": {
                "allowed": ks.allowed,
                "reason": ks.reason,
                "status_line": ks.status_line(),
                "trades_today": ks.state.trades_today,
                "max_trades": ks.cfg.max_trades_per_day,
                "consecutive_losses": ks.state.consecutive_losses,
                "max_consecutive": ks.cfg.max_consecutive_losses,
                "pnl_today": ks.state.realized_pnl_today,
                "peak_equity": ks.state.peak_equity,
                # limitele de drawdown, ca UI-ul sa nu le hardcodeze
                "max_drawdown": ks.cfg.max_total_drawdown_pct,
                "max_daily_loss": ks.cfg.max_daily_loss_pct,
            },
            "blackout": {
                "clear": blackout_ok,
                "reasons": blackout_reasons,
                "until": until.strftime("%H:%M UTC") if until else "",
            },
            "regime": {
                "zone": regime.get("zone", ""),
                "score": regime.get("score"),
                "allowed": regime.get("allowed", True),
                "reason": regime.get("reason", ""),
                "status_line": self.scanner.regime_gate.status_line(regime),
                "stale": bool(regime.get("stale")),
                "data_error": regime.get("data_error", ""),
            },
            "circuit": {
                "recommendation": circuit.get("recommendation", ""),
                "allowed": circuit.get("allowed", True),
                "reason": circuit.get("reason", ""),
                "status_line": self.scanner.circuit_gate.status_line(circuit),
                "metrics": circuit.get("metrics", {}),
                "triggered_rules": circuit.get("triggered_rules", []),
                # limitele, ca UI-ul sa nu le hardcodeze
                "max_daily_loss_pct": C.circuit.max_daily_loss_pct,
                "weekly_drawdown_pct": C.circuit.weekly_drawdown_pct,
                "monthly_drawdown_pct": C.circuit.monthly_drawdown_pct,
                "losing_streak_n": C.circuit.losing_streak_n,
            },
            "risk": {
                "risk_per_trade": C.risk.risk_per_trade,
                "max_leverage": C.risk.max_leverage,
                "max_open_positions": C.risk.max_open_positions,
                "min_risk_reward": C.risk.min_risk_reward,
                "htf": C.market.htf,
                "ltf": C.market.ltf,
            },
            "server_time": datetime.now(timezone.utc).isoformat(),
        }


SERVICE: AgentService | None = None


class Handler(BaseHTTPRequestHandler):
    # ------------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            self._serve_file("index.html", "text/html; charset=utf-8")
            return

        if path == "/api/status":
            self._json(200, SERVICE.status())
            return

        if path == "/api/last-scan":
            self._json(200, SERVICE.last_result or {})
            return

        if path == "/api/history":
            self._json(200, {"signals": read_signal_history(40)})
            return

        if path == "/api/backtest":
            self._json(200, {"reports": _load_backtests()})
            return

        if path == "/api/paper":
            self._json(200, _paper_book())
            return

        self._json(404, {"error": "not found"})

    # ------------------------------------------------------------------ POST
    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_json()

        if path == "/api/scan":
            started = SERVICE.scan_async()
            self._json(200, {"started": started, "scanning": SERVICE.scanning})
            return

        if path == "/api/auto":
            enabled = bool(body.get("enabled"))
            interval = int(body.get("interval_min") or 15)
            SERVICE.set_auto(enabled, interval)
            self._json(200, {"auto_enabled": SERVICE.auto_enabled})
            return

        if path == "/api/killswitch/reset":
            SERVICE.scanner.kill_switch.reset()
            self._json(200, {"ok": True})
            return

        self._json(404, {"error": "not found"})

    # --------------------------------------------------------------- utilitare
    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > 32_000:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _serve_file(self, name: str, content_type: str) -> None:
        try:
            with open(os.path.join(HERE, name), "rb") as fh:
                data = fh.read()
        except FileNotFoundError:
            self._json(500, {"error": f"{name} lipseste"})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # browserul a inchis tabul intre timp

    def log_message(self, fmt: str, *args) -> None:
        pass


def _load_backtests() -> list[dict]:
    """Rapoartele de backtest salvate, fara lista completa de trades."""
    out: list[dict] = []
    try:
        names = sorted(os.listdir(C.log_dir))
    except FileNotFoundError:
        return out

    for name in names:
        if not (name.startswith("backtest_") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(C.log_dir, name), encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        data.pop("trades", None)  # prea mare pentru dashboard
        out.append(data)
    return out


def _paper_book() -> dict:
    """
    Cartea de hartie a strategiei cross-sectionale, pentru dashboard.

    Strategie separata de scanner-ul de mai sus (BTC/ETH/SOL, expectanta
    negativa) - cea validata walk-forward, rulata de
    execution/paper_executor.py fara ordine reale. Vezi README, sectiunea
    "Strategia cross-sectionala".
    """
    state = load_paper_state()
    if state is None:
        return {"exists": False}

    positions = [
        {
            "symbol": sym,
            "side": "long" if pos.qty > 0 else "short",
            "notional_usdt": abs(pos.qty * pos.mark_price),
            "mark_price": pos.mark_price,
        }
        for sym, pos in state.positions.items()
    ]
    positions.sort(key=lambda p: -p["notional_usdt"])

    return {
        "exists": True,
        "started_at": state.started_at,
        "last_updated_at": state.last_updated_at,
        "capital_usdt": state.capital_usdt,
        "equity_usdt": state.equity_usdt,
        "price_pnl_usdt": state.price_pnl_usdt,
        "funding_paid_usdt": state.funding_paid_usdt,
        "fees_paid_usdt": state.fees_paid_usdt,
        "trade_count": state.trade_count,
        "gross_exposure_usdt": sum(p["notional_usdt"] for p in positions),
        "positions": positions,
        "last_run": load_last_ledger_entry(),
    }


def _local_ip() -> str:
    """IP-ul din reteaua locala, pentru accesul de pe telefon."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


def main() -> int:
    global SERVICE

    parser = argparse.ArgumentParser(description="Dashboard web pentru agentul BingX")
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="127.0.0.1 = doar acest PC; 0.0.0.0 = accesibil din reteaua locala",
    )
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--no-claude", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    log.info("Pornesc agentul si ma conectez la BingX...")
    try:
        SERVICE = AgentService(args.symbol, use_claude=not args.no_claude)
    except Exception as exc:  # noqa: BLE001
        log.error("Nu m-am putut conecta la BingX: %s", exc)
        return 1

    if SERVICE.scanner.invalid_symbols:
        log.warning("Simboluri ignorate (inexistente): %s",
                    ", ".join(SERVICE.scanner.invalid_symbols))

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"

    print()
    print("=" * 62)
    print("  DASHBOARD AGENT BINGX")
    print("=" * 62)
    print(f"  Pe acest PC   : {url}")
    if args.host == "0.0.0.0":
        print(f"  De pe telefon : http://{_local_ip()}:{args.port}")
        print("                  (acelasi WiFi; nu expune portul pe internet)")
    else:
        print("  De pe telefon : reporneste cu --host 0.0.0.0")
    print("=" * 62)
    print(f"  Simboluri : {', '.join(SERVICE.scanner.symbols)}")
    print(f"  Claude    : {'pornit' if SERVICE.scanner.use_claude else 'oprit'}")
    print(f"  Telegram  : {'pornit' if SERVICE.scanner.notifier.enabled else 'oprit'}")
    print(f"  Cont BingX: {'conectat' if SERVICE.scanner.client.authenticated else 'date publice'}")
    print("=" * 62)
    print("  Agentul NU trimite ordine. Executia ramane manuala.")
    print("  Ctrl+C pentru oprire.")
    print("=" * 62)
    print()

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Oprit.")
        SERVICE.set_auto(False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
