"""
Stratul de analiza cu Claude.

Rolul lui Claude aici este ingust si deliberat: primeste un setup DEJA validat
de risk engine si raspunde la o singura intrebare - "ce nu vad in cifrele
astea?". Poate reduce increderea, poate semnala un context nefavorabil, poate
recomanda sa sarim peste tranzactie.

Ce NU poate face:
  - nu poate creste marimea pozitiei
  - nu poate muta stopul mai departe
  - nu poate ridica leverage-ul
  - nu poate aproba un semnal respins de risk engine

Daca lipseste ANTHROPIC_API_KEY sau apelul esueaza, agentul continua sa
functioneze - pierde doar comentariul, nu semnalul.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Esti un analist de risc pentru tranzactionare crypto futures cu leverage.

Primesti un setup tehnic care a trecut deja de un motor de risc determinist.
Marimea pozitiei, stopul si leverage-ul sunt DEJA calculate si fixate. Nu le
poti modifica si nu ti se cere sa le modifici.

Sarcina ta este sa evaluezi critic contextul si sa raspunzi la:
1. Exista un motiv evident pentru care acest setup ar esua?
2. Structura pietei sustine directia propusa sau o contrazice?
3. Semnalele sunt aliniate intre ele sau se contrazic?

Fii sceptic. Un analist bun respinge mai des decat aproba. Daca datele sunt
ambigue, spune ca sunt ambigue - nu inventa o naratiune coerenta din zgomot.
Nu ai acces la stiri, sentiment sau date fundamentale; nu pretinde ca ai.

Raspunde EXCLUSIV cu un obiect JSON valid, fara text inainte sau dupa, fara
blocuri de cod markdown, cu exact aceasta structura:

{
  "verdict": "confirm" | "caution" | "skip",
  "confidence": <numar intreg 0-100>,
  "reasoning": "<2-4 propozitii in limba romana>",
  "key_risks": ["<risc 1>", "<risc 2>"],
  "invalidation": "<ce anume ar dovedi ca acest setup e gresit>"
}"""


class ClaudeAnalyzer:
    def __init__(self, api_key: str, model: str) -> None:
        self.model = model
        self._client = None
        if not api_key:
            log.info("ANTHROPIC_API_KEY lipseste - analiza Claude e dezactivata")
            return
        try:
            import anthropic

            self._client = anthropic.Anthropic(api_key=api_key)
        except Exception as exc:  # noqa: BLE001
            log.warning("Nu am putut initializa clientul Anthropic: %s", exc)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def analyze(self, signal_dict: dict[str, Any], trade_dict: dict[str, Any]) -> dict[str, Any] | None:
        if not self.enabled:
            return None

        payload = {
            "setup_tehnic": signal_dict,
            "dimensionare_dupa_risk_engine": trade_dict,
        }

        prompt = (
            "Evalueaza urmatorul setup de trading.\n\n"
            f"```json\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n```\n\n"
            "Raspunde doar cu obiectul JSON cerut."
        )

        try:
            with self._client.messages.stream(
                model=self.model,
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                message = stream.get_final_message()
        except Exception as exc:  # noqa: BLE001
            log.warning("Apelul catre Claude a esuat: %s", exc)
            return None

        text = "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        ).strip()

        return _parse_json(text)


def _parse_json(text: str) -> dict[str, Any] | None:
    """Parsare toleranta: accepta si raspunsuri invelite in ```json ... ```."""
    if not text:
        return None

    candidate = text
    if candidate.startswith("```"):
        candidate = candidate.split("```")[1]
        if candidate.startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                pass

    log.warning("Raspunsul Claude nu a putut fi parsat ca JSON")
    return None
