"""
Filtru de stiri - implementat ca BLACKOUT, nu ca sursa de semnal.

De ce nu ca sursa de semnal
---------------------------
A tranzactiona pe baza stirilor inseamna sa concurezi cu firme care au:
  - fluxuri Reuters/Bloomberg structurate, livrate in microsecunde;
  - servere colocate langa matching engine;
  - parsare pe FPGA, fara sistem de operare la mijloc.

Cand tu CITESTI o stire, pretul s-a miscat deja de 30+ secunde. Nu esti in fata
nimanui. Esti ultimul din lant, si intri exact cand cei rapizi ies pe lichiditatea ta.

Mai rau: in fereastra unui eveniment macro, spread-ul se largeste de 10-50x,
iar stop-loss-ul nu se executa la pretul tau, ci mult mai jos. Cu leverage, asta
transforma o pierdere planificata de 1% intr-una de 5-10%.

Ce face acest modul
-------------------
Raspunde la o singura intrebare: "e un moment PROST sa deschid o pozitie?"
Blocheaza intrari in ferestre de risc cunoscut. Nu genereaza niciodata semnale.
Un filtru care doar respinge nu poate face overfitting pe direction.

Evenimentele recurente (FOMC, CPI, NFP) sunt hardcodate ca REGULI de recurenta,
nu ca date fixe - un calendar hardcodat expira si devine periculos tocmai pentru
ca esueaza in tacere.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

log = logging.getLogger(__name__)


@dataclass
class BlackoutWindow:
    name: str
    start: datetime
    end: datetime
    severity: str  # "high" | "medium"

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end

    def __str__(self) -> str:
        return (
            f"{self.name} ({self.severity}): "
            f"{self.start:%Y-%m-%d %H:%M} - {self.end:%H:%M} UTC"
        )


@dataclass
class BlackoutConfig:
    # Minute inainte/dupa un eveniment de impact mare.
    high_impact_before: int = 60
    high_impact_after: int = 90
    # Idem, pentru impact mediu.
    medium_impact_before: int = 30
    medium_impact_after: int = 30
    # Blocheaza in jurul deconturilor de funding (00:00, 08:00, 16:00 UTC).
    # In acele minute lichiditatea se subtiaza si apar wick-uri.
    funding_buffer_min: int = 5
    # Blocheaza in weekend? Lichiditatea crypto scade, wick-urile cresc.
    block_weekend: bool = False
    # Ore de lichiditate scazuta (UTC). 22:00-01:00 e trecerea NY -> Asia.
    low_liquidity_hours: tuple[int, ...] = (22, 23, 0)
    block_low_liquidity: bool = False


class NewsBlackout:
    """
    Determina daca momentul curent e intr-o fereastra de risc ridicat.

    Sursa evenimentelor este un set de reguli de recurenta pentru evenimentele
    macro care misca efectiv crypto. Nu depinde de niciun API extern - deci nu
    se rupe si nu are nevoie de chei.
    """

    def __init__(self, cfg: BlackoutConfig | None = None) -> None:
        self.cfg = cfg or BlackoutConfig()

    # ------------------------------------------------------- generare ferestre
    def windows_for_day(self, day: datetime) -> list[BlackoutWindow]:
        """Ferestrele de blackout pentru ziua data (UTC)."""
        windows: list[BlackoutWindow] = []
        d = day.astimezone(timezone.utc)

        for event_name, event_dt, severity in self._events_for_day(d):
            before = (
                self.cfg.high_impact_before
                if severity == "high"
                else self.cfg.medium_impact_before
            )
            after = (
                self.cfg.high_impact_after
                if severity == "high"
                else self.cfg.medium_impact_after
            )
            windows.append(
                BlackoutWindow(
                    name=event_name,
                    start=event_dt - timedelta(minutes=before),
                    end=event_dt + timedelta(minutes=after),
                    severity=severity,
                )
            )

        # Deconturi de funding pe perpetuals.
        for hour in (0, 8, 16):
            funding_dt = d.replace(hour=hour, minute=0, second=0, microsecond=0)
            windows.append(
                BlackoutWindow(
                    name="decont funding",
                    start=funding_dt - timedelta(minutes=self.cfg.funding_buffer_min),
                    end=funding_dt + timedelta(minutes=self.cfg.funding_buffer_min),
                    severity="medium",
                )
            )

        return windows

    def _events_for_day(self, d: datetime) -> list[tuple[str, datetime, str]]:
        """
        Reguli de recurenta pentru evenimentele macro care misca crypto.

        Sunt aproximari deliberate. Ora exacta a unui CPI se poate muta, iar
        FOMC nu e in fiecare luna. Fereastra e larga tocmai ca sa acopere
        imprecizia - scopul e sa NU tranzactionezi in jurul evenimentului, nu
        sa nimeresti minutul.
        """
        events: list[tuple[str, datetime, str]] = []
        weekday = d.weekday()  # 0=luni

        def at(hour: int, minute: int = 0) -> datetime:
            return d.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # --- CPI SUA: tipic in jurul zilei 10-15 a lunii, 13:30 UTC (8:30 ET)
        if 10 <= d.day <= 15 and weekday < 5:
            events.append(("CPI SUA (fereastra)", at(13, 30), "high"))

        # --- NFP: prima vineri a lunii, 13:30 UTC
        if weekday == 4 and d.day <= 7:
            events.append(("Non-Farm Payrolls", at(13, 30), "high"))

        # --- FOMC: sedintele cad tipic miercuri, 19:00 UTC (2pm ET).
        # Nu stim exact care miercuri fara calendar live, deci marcam toate
        # miercurile din a treia saptamana ca impact mediu.
        if weekday == 2 and 15 <= d.day <= 21:
            events.append(("FOMC (fereastra probabila)", at(19, 0), "high"))

        # --- Deschiderea pietei SUA: volatilitate crescuta, 14:30 UTC
        if weekday < 5:
            events.append(("deschidere sesiune SUA", at(14, 30), "medium"))

        # --- Expirare optiuni Deribit: ultima vineri a lunii, 08:00 UTC
        if weekday == 4 and self._is_last_weekday_of_month(d):
            events.append(("expirare optiuni BTC/ETH", at(8, 0), "high"))

        return events

    @staticmethod
    def _is_last_weekday_of_month(d: datetime) -> bool:
        next_week = d + timedelta(days=7)
        return next_week.month != d.month

    # ---------------------------------------------------------------- verdict
    def check(self, moment: datetime | None = None) -> tuple[bool, list[str]]:
        """
        Returneaza (permis, motive).

        permis=False inseamna: NU deschide pozitie acum.
        """
        now = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
        reasons: list[str] = []

        # Verificam si ziua precedenta/urmatoare, pentru ferestre care traverseaza
        # miezul noptii.
        for offset in (-1, 0, 1):
            day = now + timedelta(days=offset)
            for window in self.windows_for_day(day):
                if window.contains(now):
                    reasons.append(str(window))

        if self.cfg.block_weekend and now.weekday() >= 5:
            reasons.append("weekend - lichiditate redusa, wick-uri frecvente")

        if self.cfg.block_low_liquidity and now.hour in self.cfg.low_liquidity_hours:
            reasons.append(f"ora {now.hour:02d}:00 UTC - fereastra de lichiditate scazuta")

        return (not reasons), reasons

    def next_clear_time(self, moment: datetime | None = None) -> datetime | None:
        """Cand se termina blackout-ul curent. None daca nu suntem in blackout."""
        now = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
        ends: list[datetime] = []
        for offset in (-1, 0, 1):
            for window in self.windows_for_day(now + timedelta(days=offset)):
                if window.contains(now):
                    ends.append(window.end)
        return max(ends) if ends else None


class VolatilityGuard:
    """
    Detector de volatilitate anormala - complementul empiric al calendarului.

    Calendarul stie despre evenimente PROGRAMATE. Acest guard prinde socurile
    NEPROGRAMATE: un hack de exchange, o lichidare in cascada, un tweet.

    Nu trebuie sa stii CE s-a intamplat ca sa stii ca nu e momentul sa intri.
    Daca ultima lumanare are un range de 5x fata de normal, ceva se intampla si
    nu esti tu cel informat.
    """

    def __init__(self, spike_mult: float = 3.0, volume_spike_mult: float = 4.0) -> None:
        self.spike_mult = spike_mult
        self.volume_spike_mult = volume_spike_mult

    def check(self, df) -> tuple[bool, list[str]]:
        """df trebuie sa fie deja imbogatit cu atr si volume_ma."""
        reasons: list[str] = []
        if len(df) < 20:
            return True, reasons

        last = df.iloc[-1]
        atr_val = float(last.get("atr") or 0)

        # Range-ul ultimei lumanari fata de ATR.
        candle_range = float(last["high"]) - float(last["low"])
        if atr_val > 0 and candle_range > self.spike_mult * atr_val:
            reasons.append(
                f"lumanare anormala: range {candle_range / atr_val:.1f}x ATR "
                f"(prag {self.spike_mult}x) - probabil eveniment in desfasurare"
            )

        # Volum exploziv.
        vol_ratio = float(last.get("volume_ratio") or 0)
        if vol_ratio > self.volume_spike_mult:
            reasons.append(
                f"volum {vol_ratio:.1f}x fata de medie (prag {self.volume_spike_mult}x) "
                f"- flux anormal, nu esti tu cel informat"
            )

        return (not reasons), reasons
