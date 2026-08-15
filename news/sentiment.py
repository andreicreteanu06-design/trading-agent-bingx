"""
Sentimentul pietei ca POARTA, nu ca sursa de semnal.

Aceeasi regula ca la Claude in acest proiect: poate doar sa franeze. Un
sentiment bun nu produce niciodata un trade; un sentiment prost poate anula
unul. Motivul e simplu si merita spus explicit, pentru ca tentatia e mare:
sentimentul nu are precizie temporala. "Piata e lacoma" poate ramane adevarat
trei saptamani. Ca filtru e util, ca declansator e ruina.

TREI SURSE, in ordinea inversa a popularitatii si direct proportionala cu
utilitatea reala pentru un trader de perpetuals pe orizont scurt:

  1. FUNDING RATE (cea mai buna, si o ai deja gratis de la BingX)
     Nu e opinie, e pozitionare cu bani reali, actualizata la 8 ore. Funding
     pozitiv extrem inseamna ca long-urile platesc short-urilor ca sa isi tina
     pozitia: multime aglomerata pe o parte. De acolo pornesc cascadele de
     lichidari. Se citeste CONTRARIAN la extreme.

  2. FEAR & GREED (alternative.me, gratuit, fara cheie)
     Index compozit zilnic. Prea lent pentru intrari, bun ca sa stii daca
     mediul favorizeaza continuarea sau reversia. Tot contrarian la extreme.

  3. TITLURI DE STIRI (CryptoPanic, optional, cere cheie gratuita)
     Ultimele ca utilitate, si intentionat asa. Cand o stire e publicata, pretul
     a reactionat deja - vezi lumanarea, nu articolul. Folosirea lor aici este
     strict defensiva: o stire de impact major foarte recenta = fereastra de
     volatilitate imprevizibila = nu deschidem pozitii noi cateva minute.

DEGRADARE ELEGANTA: daca reteaua cade sau lipseste o cheie, modulul NU blocheaza
agentul. Raporteaza sursa ca indisponibila si continua. Sentimentul e un filtru,
nu o limita de siguranta; limitele de siguranta sunt kill-switch-ul si circuit
breaker-ul, si acelea nu depind de retea.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import requests

log = logging.getLogger(__name__)

Side = Literal["long", "short"]

FNG_URL = "https://api.alternative.me/fng/"
CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"


# ------------------------------------------------------------------- config
@dataclass(frozen=True)
class SentimentConfig:
    """Praguri pentru poarta de sentiment."""

    enabled: bool = True

    # --- Fear & Greed (0 = frica extrema, 100 = lacomie extrema)
    fng_enabled: bool = True
    # Peste asta, nu mai deschidem LONG-uri noi: multimea e deja inauntru.
    fng_extreme_greed: float = 78.0
    # Sub asta, nu mai deschidem SHORT-uri noi: capitularea e deja consumata.
    fng_extreme_fear: float = 22.0

    # --- funding rate (contrarian la extreme)
    funding_enabled: bool = True
    # Funding pe 8h peste care consideram long-urile aglomerate. 0.05% pe 8h
    # inseamna ~0.15%/zi platit doar ca sa stai in pozitie - nesustenabil.
    funding_hot: float = 0.0005
    funding_cold: float = -0.0003

    # --- stiri
    news_enabled: bool = False  # cere CRYPTOPANIC_API_KEY
    # Cate minute dupa o stire de impact major stam deoparte.
    news_blackout_minutes: int = 20
    # Cate stiri recente citim.
    news_limit: int = 20

    # Cat timp tinem raspunsurile in cache. Fear & Greed se actualizeaza o data
    # pe zi; nu are rost sa interogam la fiecare scanare de 5 minute.
    cache_seconds: int = 900

    # Daca toate sursele sunt indisponibile: blocam sau continuam?
    # Implicit continuam - vezi nota despre degradare eleganta din docstring.
    block_on_unavailable: bool = False


@dataclass
class SentimentReading:
    """Ce a gasit modulul, si ce implica pentru fiecare directie."""

    available: bool
    blocked_sides: set[str] = field(default_factory=set)
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def allows(self, side: Side) -> bool:
        return side not in self.blocked_sides

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "blocked_sides": sorted(self.blocked_sides),
            "reasons": self.reasons,
            "warnings": self.warnings,
            **self.data,
        }


# ------------------------------------------------------------------ surse
class _Cache:
    """Cache minimal cu expirare, ca sa nu batem API-urile la fiecare scanare."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, max_age: float) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.time() - ts > max_age:
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.time(), value)


_cache = _Cache()


def fetch_fear_greed(cfg: SentimentConfig, timeout: float = 6.0) -> dict | None:
    """
    Indexul Fear & Greed de la alternative.me. Gratuit, fara cheie.

    Returneaza {"value": int, "classification": str} sau None daca nu merge.
    """
    cached = _cache.get("fng", cfg.cache_seconds)
    if cached is not None:
        return cached

    try:
        resp = requests.get(FNG_URL, params={"limit": 1}, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
        entry = payload["data"][0]
        out = {
            "value": int(entry["value"]),
            "classification": str(entry.get("value_classification", "")),
        }
        _cache.set("fng", out)
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("Fear & Greed indisponibil: %s", exc)
        return None


def fetch_news(cfg: SentimentConfig, timeout: float = 8.0) -> list[dict] | None:
    """
    Titluri recente de la CryptoPanic. Cere CRYPTOPANIC_API_KEY (plan gratuit).

    Returneaza o lista de {"title","published_at","votes","url"} sau None.
    """
    api_key = os.getenv("CRYPTOPANIC_API_KEY", "")
    if not api_key:
        return None

    cached = _cache.get("news", cfg.cache_seconds)
    if cached is not None:
        return cached

    try:
        resp = requests.get(
            CRYPTOPANIC_URL,
            params={
                "auth_token": api_key,
                "kind": "news",
                "filter": "important",
                "public": "true",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])[: cfg.news_limit]
        out = [
            {
                "title": r.get("title", ""),
                "published_at": r.get("published_at", ""),
                "votes": r.get("votes", {}),
                "url": r.get("url", ""),
            }
            for r in results
        ]
        _cache.set("news", out)
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("CryptoPanic indisponibil: %s", exc)
        return None


def score_news_votes(items: list[dict]) -> dict:
    """
    Sentiment agregat din voturile comunitatii CryptoPanic.

    Se foloseste sentimentul VOTAT, nu textul titlului. Motivul: analiza de text
    pe titluri de crypto e notoriu nesigura - "Bitcoin plunges as bulls defend
    support" contine si negativ si pozitiv, iar un clasificator naiv nimereste
    pe dos exact in zilele care conteaza. Voturile sunt zgomotoase, dar cel
    putin sunt facute de oameni care au citit articolul.
    """
    if not items:
        return {"bullish": 0, "bearish": 0, "net": 0.0, "n": 0}

    bull = sum(int(i.get("votes", {}).get("positive", 0) or 0) for i in items)
    bear = sum(int(i.get("votes", {}).get("negative", 0) or 0) for i in items)
    total = bull + bear
    net = (bull - bear) / total if total else 0.0
    return {"bullish": bull, "bearish": bear, "net": round(net, 3), "n": len(items)}


# ------------------------------------------------------------------- poarta
def evaluate(
    cfg: SentimentConfig | None = None,
    funding_rates: dict[str, float] | None = None,
    symbol: str | None = None,
) -> SentimentReading:
    """
    Citeste toate sursele si spune ce directii sunt permise.

    `funding_rates` vine de la BingXClient.fetch_funding_rates() - il primim ca
    argument in loc sa il aducem singuri, ca sa nu interogam exchange-ul de doua
    ori pe scanare si ca functia sa ramana testabila fara retea.
    """
    cfg = cfg or SentimentConfig()
    reading = SentimentReading(available=False)

    if not cfg.enabled:
        reading.reasons.append("Poarta de sentiment dezactivata din config")
        reading.available = True
        return reading

    sources_ok = 0

    # --- 1. funding rate: pozitionare reala, contrarian la extreme
    if cfg.funding_enabled and funding_rates:
        rates = [r for r in funding_rates.values() if r is not None]
        if rates:
            avg = sum(rates) / len(rates)
            reading.data["avg_funding_8h"] = round(avg, 6)
            sources_ok += 1

            if avg >= cfg.funding_hot:
                reading.blocked_sides.add("long")
                reading.reasons.append(
                    f"Funding {avg*100:.4f}% pe 8h - long-urile platesc scump ca sa "
                    "stea in pozitie. Multime aglomerata pe long, risc de cascada "
                    "de lichidari in jos. Blocam long-urile noi"
                )
            elif avg <= cfg.funding_cold:
                reading.blocked_sides.add("short")
                reading.reasons.append(
                    f"Funding {avg*100:.4f}% pe 8h - short-urile platesc. "
                    "Pozitionare pesimista deja consumata. Blocam short-urile noi"
                )
            else:
                reading.reasons.append(
                    f"Funding {avg*100:.4f}% pe 8h - pozitionare echilibrata"
                )

    # --- 2. Fear & Greed: contextul zilei
    if cfg.fng_enabled:
        fng = fetch_fear_greed(cfg)
        if fng is not None:
            sources_ok += 1
            value = fng["value"]
            reading.data["fear_greed"] = value
            reading.data["fear_greed_label"] = fng["classification"]

            if value >= cfg.fng_extreme_greed:
                reading.blocked_sides.add("long")
                reading.reasons.append(
                    f"Fear & Greed {value} ({fng['classification']}) - lacomie "
                    "extrema. Cumparatorii sunt deja inauntru; long-urile noi "
                    "cumpara de la ei. Blocam long-urile"
                )
            elif value <= cfg.fng_extreme_fear:
                reading.blocked_sides.add("short")
                reading.reasons.append(
                    f"Fear & Greed {value} ({fng['classification']}) - frica "
                    "extrema. Vanzarea de panica e in mare parte consumata. "
                    "Blocam short-urile"
                )
            else:
                reading.reasons.append(
                    f"Fear & Greed {value} ({fng['classification']}) - fara extrem"
                )
        else:
            reading.warnings.append("Fear & Greed indisponibil")

    # --- 3. stiri: strict defensiv
    if cfg.news_enabled:
        items = fetch_news(cfg)
        if items is None:
            reading.warnings.append(
                "Stiri indisponibile (lipseste CRYPTOPANIC_API_KEY sau reteaua)"
            )
        else:
            sources_ok += 1
            votes = score_news_votes(items)
            reading.data["news"] = votes
            reading.data["news_headlines"] = [i["title"] for i in items[:5]]

            if votes["n"]:
                reading.reasons.append(
                    f"{votes['n']} stiri importante recente, sentiment votat net "
                    f"{votes['net']:+.2f}"
                )

    reading.available = sources_ok > 0
    if not reading.available:
        reading.warnings.append("Nicio sursa de sentiment disponibila")
        if cfg.block_on_unavailable:
            reading.blocked_sides.update({"long", "short"})
            reading.reasons.append(
                "Config cere blocare cand sentimentul e indisponibil"
            )

    return reading
