"""
Receptor de alerte TradingView.

Ce este si ce nu este
---------------------
TradingView NU are API public de date pentru retail. Ce are sunt WEBHOOK-uri:
o alerta Pine Script poate trimite un POST catre un URL al tau. Asta e singura
integrare oficiala si stabila.

Important: datele OHLCV de pe TradingView sunt aceleasi cu cele de la BingX -
de fapt, pentru tranzactionare pe BingX, datele BingX sunt mai bune, pentru ca
sunt de pe venue-ul unde chiar executi. TradingView nu aduce date mai bune.

Ce aduce TradingView e Pine Script: daca ai un indicator propriu in care ai
incredere si care nu e usor de reprodus in Python, alertele lui pot deveni
sursa de semnal.

Cheia arhitecturii: alerta TradingView este tratata ca o SUGESTIE, exact ca
semnalul intern. Trece prin ACELASI risk engine. Nu exista cale prin care un
webhook sa ocoleasca validarile - altfel un JSON gresit iti goleste contul.

Rulare
------
    python -m webhook.tradingview --port 8080

Expunere catre internet (TradingView trebuie sa te poata apela):
    ngrok http 8080
si pui URL-ul ngrok in alerta TradingView.

Formatul mesajului de alerta (in campul "Message" din TradingView):

    {
      "secret": "pune-aici-secretul-din-.env",
      "symbol": "BTC/USDT:USDT",
      "side": "long",
      "entry": {{close}},
      "stop_loss": {{plot_0}},
      "take_profits": [0, 0],
      "comment": "cross EMA + volum"
    }

Daca stop_loss lipseste sau e 0, il calculam noi din ATR - dar semnalul primeste
un avertisment, pentru ca un stop venit din strategia care a generat semnalul e
aproape mereu mai bun decat unul generic.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg
from exchange.bingx_client import BingXClient
from news.blackout import NewsBlackout
from strategy import indicators, risk_engine
from strategy.signal_builder import Signal

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
)
log = logging.getLogger("webhook")

C = cfg.CONFIG
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

_client: BingXClient | None = None
_blackout = NewsBlackout()


def _get_client() -> BingXClient:
    global _client
    if _client is None:
        _client = BingXClient(cfg.BINGX_API_KEY, cfg.BINGX_SECRET_KEY)
        _client.load_markets()
    return _client


def process_alert(payload: dict) -> dict:
    """
    Valideaza o alerta TradingView si o trece prin risk engine.

    Returneaza un dict cu rezultatul. NU trimite niciun ordin.
    """
    result: dict = {"received_at": datetime.now(timezone.utc).isoformat()}

    # --- 1. autentificare
    secret = str(payload.get("secret", ""))
    if not WEBHOOK_SECRET:
        return {**result, "status": "rejected", "error": "WEBHOOK_SECRET nu e configurat pe server"}
    if not hmac.compare_digest(secret, WEBHOOK_SECRET):
        log.warning("Alerta cu secret invalid respinsa")
        return {**result, "status": "rejected", "error": "secret invalid"}

    # --- 2. campuri obligatorii
    symbol = str(payload.get("symbol", "")).strip()
    side = str(payload.get("side", "")).strip().lower()

    if side not in ("long", "short"):
        return {**result, "status": "rejected", "error": f"side invalid: {side!r}"}

    client = _get_client()
    if not client.market_exists(symbol):
        return {**result, "status": "rejected", "error": f"simbol inexistent pe BingX: {symbol}"}

    # --- 3. blackout de stiri/volatilitate
    allowed, reasons = _blackout.check()
    if not allowed:
        return {
            **result,
            "status": "blocked",
            "error": "fereastra de blackout",
            "reasons": reasons,
        }

    # --- 4. pret si ATR curent, de la BingX (nu ne bazam pe ce trimite alerta)
    ltf = indicators.enrich(
        client.fetch_ohlcv(symbol, C.market.ltf, C.market.candles), C.strategy
    )
    last = ltf.iloc[-1]
    atr_val = float(last["atr"])

    entry = float(payload.get("entry") or 0) or client.fetch_last_price(symbol)

    # --- 5. stop-loss: preferam ce trimite strategia, altfel il calculam
    warnings: list[str] = []
    stop = float(payload.get("stop_loss") or 0)
    if stop <= 0:
        stop = (
            entry - C.risk.atr_stop_mult * atr_val
            if side == "long"
            else entry + C.risk.atr_stop_mult * atr_val
        )
        warnings.append(
            "Alerta nu a trimis stop-loss - calculat din ATR. "
            "Un stop din strategia proprie e aproape mereu mai bun."
        )

    # Sanity check pe directie: un stop de partea gresita e o alerta gresit
    # configurata, nu un setup exotic.
    if side == "long" and stop >= entry:
        return {**result, "status": "rejected", "error": "long cu stop peste intrare"}
    if side == "short" and stop <= entry:
        return {**result, "status": "rejected", "error": "short cu stop sub intrare"}

    # --- 6. take-profits: din alerta, altfel din multiplii de R
    r = abs(entry - stop)
    tps = [float(x) for x in (payload.get("take_profits") or []) if float(x) > 0]
    if not tps:
        sign = 1 if side == "long" else -1
        tps = [entry + sign * m * r for m in C.risk.tp_r_multiples]

    signal = Signal(
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        entry=entry,
        stop_loss=stop,
        take_profits=tps,
        score=0.0,  # scorul intern nu se aplica alertelor externe
        reasons=[f"TradingView: {payload.get('comment', 'fara comentariu')}"],
        warnings=warnings,
        context={"source": "tradingview", "atr": atr_val},
    )

    # --- 7. ACELASI risk engine ca semnalele interne. Fara exceptii.
    equity = client.fetch_equity_usdt()
    positions = client.fetch_open_positions()
    trade = risk_engine.evaluate(signal, equity, positions, C.risk)

    result.update(
        {
            "status": "approved" if trade.approved else "rejected",
            "signal": signal.to_dict(),
            "trade": trade.to_dict(),
        }
    )

    # --- 8. logare, indiferent de verdict
    os.makedirs(C.log_dir, exist_ok=True)
    with open(C.signal_log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({**result, "source": "tradingview"}, ensure_ascii=False, default=str) + "\n")

    return result


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > 64_000:
            self._reply(400, {"error": "corp lipsa sau prea mare"})
            return

        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._reply(400, {"error": "JSON invalid"})
            return

        try:
            result = process_alert(payload)
        except Exception as exc:  # noqa: BLE001
            log.exception("Eroare la procesarea alertei")
            self._reply(500, {"error": str(exc)})
            return

        status = result.get("status")
        icon = {"approved": "OK", "rejected": "RESPINS", "blocked": "BLOCAT"}.get(status, "?")
        log.info(
            "%s  %s %s",
            icon,
            payload.get("symbol", "?"),
            result.get("error", ""),
        )
        if status == "approved":
            t = result["trade"]
            log.info(
                "   marime %.6f | leverage %.2fx | risc %.2f USDT",
                t["position_size"], t["leverage"], t["risk_amount"],
            )
            log.info("   CONFIRMARE MANUALA NECESARA - niciun ordin trimis")

        self._reply(200, result)

    def do_GET(self) -> None:  # noqa: N802
        self._reply(200, {"status": "receptorul TradingView ruleaza"})

    def _reply(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        pass  # tacem logul default al HTTPServer


def main() -> int:
    parser = argparse.ArgumentParser(description="Receptor webhook TradingView")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    if not WEBHOOK_SECRET:
        log.error("WEBHOOK_SECRET lipseste din .env - refuz sa pornesc fara autentificare")
        return 1

    server = HTTPServer((args.host, args.port), Handler)
    log.info("Ascult pe http://%s:%d", args.host, args.port)
    log.info("Pentru TradingView, expune public: ngrok http %d", args.port)
    log.info("Alertele trec prin acelasi risk engine. Niciun ordin nu se trimite automat.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Oprit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
