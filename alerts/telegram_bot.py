"""Notificari Telegram. Optional - daca lipsesc credentialele, tace elegant."""

from __future__ import annotations

import html
import logging

import requests

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            resp = requests.post(
                API_URL.format(token=self.token),
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Trimiterea catre Telegram a esuat: %s", exc)
            return False

    def send_signal(self, signal, trade, analysis: dict | None) -> bool:
        emoji = "🟢" if signal.side == "long" else "🔴"
        tps = " / ".join(f"{tp:g}" for tp in trade.take_profits)

        lines = [
            f"{emoji} <b>{html.escape(signal.symbol)} — {signal.side.upper()}</b>",
            f"Scor setup: <b>{signal.score:.0f}/100</b>",
            "",
            f"Intrare: <code>{trade.entry:g}</code>",
            f"Stop-loss: <code>{trade.stop_loss:g}</code>  ({signal.stop_distance_pct:.2%})",
            f"Take-profit: <code>{tps}</code>",
            f"R:R: <b>{signal.risk_reward:.2f}</b>",
            "",
            f"Marime: <code>{trade.position_size:.6f}</code>",
            f"Notional: <code>{trade.notional:.2f} USDT</code>",
            f"Leverage: <b>{trade.leverage:.2f}x</b>",
            f"Risc: <code>{trade.risk_amount:.2f} USDT ({trade.risk_pct:.2%})</code>",
            f"Lichidare est.: <code>{trade.liquidation_price:g}</code> "
            f"(buffer {trade.liquidation_buffer_mult:.1f}x)",
        ]

        if analysis:
            lines += [
                "",
                f"<b>Claude:</b> {html.escape(str(analysis.get('verdict', '?')).upper())} "
                f"({analysis.get('confidence', '?')}%)",
                html.escape(str(analysis.get("reasoning", ""))),
            ]
            risks = analysis.get("key_risks") or []
            if risks:
                lines.append("Riscuri: " + html.escape("; ".join(map(str, risks))))

        lines += ["", "<i>Confirmare manuala necesara. Nu s-a trimis niciun ordin.</i>"]
        return self.send("\n".join(lines))
