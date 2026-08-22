"""
Punctul de intrare al agentului.

Fluxul unei rulari:

    BingX (OHLCV 4h + 1h)
        -> indicatori
        -> signal_builder  (exista un setup?)
        -> risk_engine     (e dimensionabil in limitele mele?)
        -> Claude          (ce nu vad in cifre?)
        -> terminal + Telegram + logs/signals.jsonl
        -> TU decizi si executi manual pe BingX

Agentul nu trimite ordine. Niciodata, in aceasta versiune.

Utilizare:
    python main.py              # o singura scanare
    python main.py --watch      # scaneaza la fiecare 15 minute
    python main.py --symbol BTC/USDT:USDT
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import config as cfg
from ai.claude_analyzer import ClaudeAnalyzer
from alerts.telegram_bot import TelegramNotifier
from exchange.bingx_client import BingXClient
from news.blackout import NewsBlackout, VolatilityGuard
from risk.gate import CircuitGate, RegimeGate
from risk.trade_recorder import TradeRecorder
from strategy import indicators, risk_engine, signal_builder
from strategy.kill_switch import KillSwitch, KillSwitchConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("agent")

C = cfg.CONFIG


# --------------------------------------------------------------------- helpers
def _ensure_dirs() -> None:
    os.makedirs(C.log_dir, exist_ok=True)


def _load_state() -> dict:
    try:
        with open(C.state_file, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_signal": {}}


def _save_state(state: dict) -> None:
    _ensure_dirs()
    with open(C.state_file, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def _in_cooldown(state: dict, symbol: str) -> bool:
    ts = state.get("last_signal", {}).get(symbol)
    if not ts:
        return False
    last = datetime.fromisoformat(ts)
    return datetime.now(timezone.utc) - last < timedelta(
        minutes=C.risk.signal_cooldown_minutes
    )


def _log_signal(record: dict) -> None:
    _ensure_dirs()
    with open(C.signal_log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# ------------------------------------------------------------------- afisare
def _print_trade(signal, trade, analysis) -> None:
    bar = "=" * 68
    arrow = "▲ LONG" if signal.side == "long" else "▼ SHORT"

    print(f"\n{bar}")
    print(f"  {signal.symbol}   {arrow}   scor setup {signal.score:.0f}/100")
    print(bar)

    print(f"  Intrare        : {signal.entry:g}")
    print(f"  Stop-loss      : {signal.stop_loss:g}   ({signal.stop_distance_pct:.2%})")
    for i, tp in enumerate(signal.take_profits, start=1):
        r_mult = C.risk.tp_r_multiples[i - 1]
        print(f"  Take-profit {i}  : {tp:g}   ({r_mult:g}R)")
    print(f"  Risk / Reward  : {signal.risk_reward:.2f}")

    print("  " + "-" * 64)
    print(f"  Marime pozitie : {trade.position_size:.6f}")
    print(f"  Notional       : {trade.notional:.2f} USDT")
    print(f"  Leverage       : {trade.leverage:.2f}x")
    print(f"  Risc asumat    : {trade.risk_amount:.2f} USDT ({trade.risk_pct:.2%})")
    print(
        f"  Lichidare est. : {trade.liquidation_price:g} "
        f"(buffer {trade.liquidation_buffer_mult:.1f}x fata de stop)"
    )

    if signal.reasons:
        print("  " + "-" * 64)
        print("  Motive:")
        for r in signal.reasons:
            print(f"    + {r}")
    if signal.warnings:
        print("  Atentionari:")
        for w in signal.warnings:
            print(f"    ! {w}")
    if trade.notes:
        print("  Note risk engine:")
        for n in trade.notes:
            print(f"    · {n}")

    if analysis:
        print("  " + "-" * 64)
        verdict = str(analysis.get("verdict", "?")).upper()
        print(f"  Claude: {verdict}  (incredere {analysis.get('confidence', '?')}%)")
        print(f"  {analysis.get('reasoning', '')}")
        for risk in analysis.get("key_risks") or []:
            print(f"    ! {risk}")
        if analysis.get("invalidation"):
            print(f"  Invalidare: {analysis['invalidation']}")

    print(bar)
    print("  Executie manuala. Agentul NU a trimis niciun ordin.")
    print(f"{bar}\n")


def _print_rejection(signal, trade) -> None:
    print(f"\n  [RESPINS] {signal.symbol} {signal.side.upper()} (scor {signal.score:.0f})")
    for r in trade.rejections:
        print(f"      × {r}")


# ---------------------------------------------------------------------- scan
def scan(client: BingXClient, analyzer: ClaudeAnalyzer, notifier: TelegramNotifier,
         symbols: list[str], use_claude: bool, kill_switch: KillSwitch,
         blackout: NewsBlackout, vol_guard: VolatilityGuard,
         regime_gate: RegimeGate, circuit_gate: CircuitGate,
         recorder: TradeRecorder) -> None:
    state = _load_state()

    equity = client.fetch_equity_usdt()
    positions = client.fetch_open_positions()

    if equity is not None:
        log.info("Echity: %.2f USDT | pozitii deschise: %d", equity, len(positions))
    else:
        log.info("Fara chei API valide - rulez pe date publice, dimensionare orientativa")

    # --------------------------------------------- inchideri de la ciclul trecut
    # Inaintea kill-switch-ului si a circuit breaker-ului: amandoua citesc ce
    # scrie recorder-ul aici.
    for closed in recorder.sync(positions):
        log.info(
            "Pozitie inchisa: %s %s  P&L %+.2f USDT (%s)",
            closed["symbol"],
            closed["side"],
            closed["pnl"],
            closed["source"],
        )

    # ------------------------------------------------------------ kill-switch
    kill_switch.sync(equity if equity is not None else 1000.0)
    log.info("Kill-switch: %s", kill_switch.status_line())
    if not kill_switch.allowed:
        print(f"\n  [AGENT OPRIT] {kill_switch.reason}")
        print("  Nu se cauta semnale. Ridicare: python tools\\killswitch.py --reset\n")
        return

    # ------------------------------------------------- blackout de evenimente
    clear, reasons = blackout.check()
    if not clear:
        log.info("Blackout activ - nu se deschid pozitii:")
        for r in reasons:
            log.info("   · %s", r)
        until = blackout.next_clear_time()
        if until:
            log.info("   liber dupa: %s UTC", until.strftime("%H:%M"))
        return

    # ------------------------------------------------------- regim de piata
    regime = regime_gate.evaluate()
    log.info("Regim de piata: %s", regime_gate.status_line(regime))
    if regime.get("data_error"):
        log.warning("   date de regim incomplete: %s", regime["data_error"])
    if not regime.get("allowed", True):
        print(f"\n  [CICLU SARIT] {regime.get('reason', '')}")
        print("  Nu se cauta setup-uri cand contextul e ostil pe toata piata.\n")
        return

    # ----------------------------------------------------- circuit breaker
    circuit = circuit_gate.check(equity if equity is not None else 1000.0)
    log.info("Circuit breaker: %s", circuit_gate.status_line(circuit))
    if not circuit.get("allowed", True):
        print(f"\n  [FARA POZITII NOI] {circuit.get('recommendation')}")
        for rule in circuit.get("triggered_rules") or []:
            print(f"      × {rule}")
        print()
        return

    found = 0

    for symbol in symbols:
        if _in_cooldown(state, symbol):
            log.info("%s - in cooldown, sar peste", symbol)
            continue

        try:
            htf = client.fetch_ohlcv(symbol, C.market.htf, C.market.candles)
            ltf = client.fetch_ohlcv(symbol, C.market.ltf, C.market.candles)
        except Exception as exc:  # noqa: BLE001
            log.error("%s - eroare la citirea datelor: %s", symbol, exc)
            continue

        # Volatilitate anormala = eveniment in desfasurare despre care nu stim.
        # Verificam inainte de a construi semnalul.
        vol_ok, vol_reasons = vol_guard.check(indicators.enrich(ltf, C.strategy))
        if not vol_ok:
            log.info("%s - sarit din cauza volatilitatii:", symbol)
            for r in vol_reasons:
                log.info("     · %s", r)
            continue

        signal = signal_builder.build_signal(symbol, htf, ltf, C.strategy, C.risk)
        if signal is None:
            log.info("%s - niciun setup valabil", symbol)
            continue

        trade = risk_engine.evaluate(signal, equity, positions, C.risk)

        if not trade.approved:
            _print_rejection(signal, trade)
            _log_signal(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "rejected",
                    "signal": signal.to_dict(),
                    "trade": trade.to_dict(),
                }
            )
            continue

        analysis = analyzer.analyze(signal.to_dict(), trade.to_dict()) if use_claude else None

        # Claude poate doar sa franeze, niciodata sa accelereze.
        if analysis and str(analysis.get("verdict")).lower() == "skip":
            print(f"\n  [CLAUDE: SKIP] {symbol} {signal.side.upper()}")
            print(f"      {analysis.get('reasoning', '')}")
            _log_signal(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "claude_skip",
                    "signal": signal.to_dict(),
                    "trade": trade.to_dict(),
                    "analysis": analysis,
                }
            )
            continue

        found += 1
        _print_trade(signal, trade, analysis)
        notifier.send_signal(signal, trade, analysis)
        kill_switch.record_trade_opened()

        state.setdefault("last_signal", {})[symbol] = datetime.now(
            timezone.utc
        ).isoformat()
        _log_signal(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "status": "approved",
                "signal": signal.to_dict(),
                "trade": trade.to_dict(),
                "analysis": analysis,
            }
        )

    _save_state(state)

    if found == 0:
        log.info("Scanare incheiata - niciun semnal aprobat. Rabdarea e o pozitie.")


# ---------------------------------------------------------------------- main
def main() -> int:
    parser = argparse.ArgumentParser(description="Agent de semnale BingX futures")
    parser.add_argument("--watch", action="store_true", help="scanare continua")
    parser.add_argument("--interval", type=int, default=15, help="minute intre scanari")
    parser.add_argument("--symbol", action="append", help="suprascrie lista de simboluri")
    parser.add_argument("--no-claude", action="store_true", help="dezactiveaza analiza LLM")
    args = parser.parse_args()

    if cfg.TRADING_MODE != "paper":
        # Respingere, nu avertisment. Un avertisment intr-un log pe care nimeni
        # nu-l citeste lasa impresia ca TRADING_MODE=live face ceva aici - nu
        # face, si strategia asta are expectanta negativa masurata (-0.331R pe
        # 73 de tranzactii). Executia reala traieste exclusiv in
        # execution/live_executor.py, pentru cartea cross-sectionala validata.
        log.error(
            "TRADING_MODE=%s nu e acceptat de main.py. Aceasta cale genereaza "
            "doar semnale pentru strategia BTC/ETH/SOL, care are expectanta "
            "negativa masurata. Pentru executie reala foloseste "
            "execution/live_executor.py. Porneste cu TRADING_MODE=paper.",
            cfg.TRADING_MODE,
        )
        return 2

    symbols = args.symbol or list(C.market.symbols)

    client = BingXClient(cfg.BINGX_API_KEY, cfg.BINGX_SECRET_KEY)
    try:
        client.load_markets()
    except Exception as exc:  # noqa: BLE001
        log.error("Nu m-am putut conecta la BingX: %s", exc)
        return 1

    valid = [s for s in symbols if client.market_exists(s)]
    for s in set(symbols) - set(valid):
        log.error("Simbol inexistent pe BingX swap: %s", s)
    if not valid:
        log.error("Niciun simbol valid. Verifica formatul, ex: BTC/USDT:USDT")
        return 1

    analyzer = ClaudeAnalyzer(cfg.ANTHROPIC_API_KEY, cfg.CLAUDE_MODEL)
    notifier = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    use_claude = analyzer.enabled and not args.no_claude

    kill_switch = KillSwitch(
        os.path.join(C.log_dir, "killswitch.json"),
        KillSwitchConfig(max_consecutive_losses=C.risk.max_consecutive_losses),
    )
    blackout = NewsBlackout()
    vol_guard = VolatilityGuard()

    regime_gate = RegimeGate(client, C.regime, C.regime_cache)
    circuit_gate = CircuitGate(C.circuit, C.trade_log)
    recorder = TradeRecorder(
        client, C.trade_log, C.positions_state, kill_switch=kill_switch
    )

    log.info(
        "Agent pornit | simboluri: %s | HTF %s / LTF %s | Claude: %s | Telegram: %s",
        ", ".join(valid),
        C.market.htf,
        C.market.ltf,
        "on" if use_claude else "off",
        "on" if notifier.enabled else "off",
    )

    if not args.watch:
        scan(
            client, analyzer, notifier, valid, use_claude, kill_switch,
            blackout, vol_guard, regime_gate, circuit_gate, recorder,
        )
        return 0

    while True:
        try:
            scan(
                client, analyzer, notifier, valid, use_claude, kill_switch,
                blackout, vol_guard, regime_gate, circuit_gate, recorder,
            )
        except KeyboardInterrupt:
            log.info("Oprit de utilizator.")
            return 0
        except Exception as exc:  # noqa: BLE001
            log.exception("Eroare in bucla de scanare: %s", exc)

        log.info("Urmatoarea scanare in %d minute...", args.interval)
        try:
            time.sleep(args.interval * 60)
        except KeyboardInterrupt:
            log.info("Oprit de utilizator.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
