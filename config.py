"""
Configuratia centrala a agentului.

Toate limitele de risc de aici sunt HARD LIMITS. Risk engine-ul le trateaza ca
reguli deterministe: daca un semnal le incalca, este respins. Claude nu are
voie sa le modifice si nu are voie sa le negocieze.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------- credentiale
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TRADING_MODE = os.getenv("TRADING_MODE", "paper").lower()

CLAUDE_MODEL = "claude-opus-5"


# ------------------------------------------------------------------- universe
@dataclass(frozen=True)
class MarketConfig:
    """Ce scanam si pe ce timeframe-uri."""

    # Simboluri CCXT pentru perpetual USDT-M pe BingX.
    symbols: tuple[str, ...] = (
        "BTC/USDT:USDT",
        "ETH/USDT:USDT",
        "SOL/USDT:USDT",
    )

    # Timeframe-ul care decide directia permisa (trend filter).
    htf: str = "4h"
    # Timeframe-ul pe care cautam efectiv intrarea.
    ltf: str = "1h"

    # Cate lumanari citim. 400 acopera confortabil EMA200 + ATR + ADX.
    candles: int = 400


# ---------------------------------------------------------------- risk limits
@dataclass(frozen=True)
class RiskConfig:
    """
    Parametrii de risc. Acestea sunt cele mai importante numere din tot proiectul.

    Filozofia: riscul per tranzactie este FIX ca procent din capital, iar
    leverage-ul este o CONSECINTA a distantei pana la stop, nu o alegere.
    Un trader profesionist nu spune "intru cu 10x"; spune "risc 1% si stopul e
    la 2.4% distanta, deci marimea pozitiei rezulta din asta".
    """

    # Procent din echity riscat pe o singura tranzactie (0.01 = 1%).
    risk_per_trade: float = 0.01

    # Plafon absolut de leverage. Peste asta, semnalul e respins - indiferent
    # ce spune calculul sau Claude.
    max_leverage: float = 5.0

    # Expunerea notionala totala nu poate depasi acest multiplu din echity.
    max_total_notional_mult: float = 3.0

    # Numar maxim de pozitii deschise simultan.
    max_open_positions: int = 2

    # Raport risc/recompensa minim acceptat pentru take-profit 1.
    min_risk_reward: float = 1.5

    # Stopul trebuie sa fie la cel putin atata distanta procentuala de intrare.
    # Sub asta, zgomotul normal de piata te scoate din pozitie degeaba.
    min_stop_distance_pct: float = 0.004  # 0.4%

    # Si nu mai mult de atata - altfel pozitia devine prea mica ca sa merite.
    max_stop_distance_pct: float = 0.05  # 5%

    # Buffer de siguranta fata de pretul de lichidare. Stopul trebuie sa fie
    # mult inaintea lichidarii; daca nu e, leverage-ul e prea mare.
    # 3.0 = distanta pana la lichidare trebuie sa fie >= 3x distanta pana la stop.
    min_liquidation_buffer_mult: float = 3.0

    # Filtru de regim: sub acest ADX piata e in range si semnalele de trend
    # au rata de esec mult mai mare.
    min_adx: float = 20.0

    # Filtru de volatilitate: ATR ca procent din pret. Prea mic = fara miscare,
    # prea mare = risc de wick-uri violente.
    min_atr_pct: float = 0.003  # 0.3%
    max_atr_pct: float = 0.06  # 6%

    # Multiplicatorul ATR folosit pentru plasarea stopului.
    atr_stop_mult: float = 1.8

    # Tintele de profit, ca multiplu de R (R = distanta pana la stop).
    tp_r_multiples: tuple[float, ...] = (2.0, 4.0)

    # Dupa atatea semnale pierzatoare consecutive, agentul se opreste.
    max_consecutive_losses: int = 3

    # Nu emite un semnal nou pe acelasi simbol mai devreme de atatea minute.
    signal_cooldown_minutes: int = 120


# ------------------------------------------------------------------ strategie
@dataclass(frozen=True)
class StrategyConfig:
    """Parametrii indicatorilor. Modifica-i doar dupa backtest, nu dupa intuitie."""

    ema_fast: int = 50
    ema_slow: int = 200
    rsi_period: int = 14
    atr_period: int = 14
    adx_period: int = 14

    # Zone RSI. Nu intram pe supracumparare extrema intr-un long.
    rsi_long_min: float = 45.0
    rsi_long_max: float = 70.0
    rsi_short_min: float = 30.0
    rsi_short_max: float = 55.0

    # Volumul lumanarii de semnal fata de media ultimelor N.
    volume_ma_period: int = 20
    min_volume_ratio: float = 1.0

    # Cate lumanari inapoi cautam swing high/low pentru structura.
    swing_lookback: int = 20

    # Scor minim (0-100) pentru ca un setup sa fie propus.
    min_setup_score: float = 60.0


# ------------------------------------------------------------- regim de piata
@dataclass(frozen=True)
class RegimeConfig:
    """
    Filtru de context macro (risk/regime_analyzer.py).

    Nu spune ce sa cumperi. Spune doar daca merita sa cauti ceva de cumparat.
    Cand BTC e sub structura si funding-ul e euforic, statistica setup-urilor de
    trend se deterioreaza pe toate simbolurile deodata - deci filtrul e la
    nivel de ciclu, nu de simbol.
    """

    enabled: bool = True

    # De unde luam structura de trend. BTC conduce restul pietei.
    btc_symbol: str = "BTC/USDT:USDT"
    # regime_analyzer are nevoie de >= 220 inchideri zilnice (200 MA + 20 panta).
    daily_candles: int = 260

    # Perpetualele de pe care citim funding rate. Minim 2, altfel componenta
    # de funding se marcheaza indisponibila si scorul cade doar pe trend.
    funding_symbols: tuple[str, ...] = (
        "BTC/USDT:USDT",
        "ETH/USDT:USDT",
        "SOL/USDT:USDT",
    )

    # Datele sunt zilnice si funding-ul se schimba la 8h - nu are rost sa
    # interogam exchange-ul la fiecare scanare de 15 minute.
    refresh_minutes: int = 60

    # Zonele in care nu deschidem pozitii noi.
    blocked_zones: tuple[str, ...] = ("RISK_OFF",)

    # Daca datele lipsesc (zona UNKNOWN): oprim sau continuam?
    # Implicit continuam - regimul e un FILTRU, nu o limita de siguranta.
    # Limitele de siguranta sunt kill-switch-ul si circuit breaker-ul, si
    # acelea nu depind de retea. Un timeout la BingX nu trebuie sa opreasca
    # agentul, doar sa fie vizibil in status.
    block_on_unknown: bool = False


# ---------------------------------------------------------- circuit breaker
@dataclass(frozen=True)
class CircuitBreakerConfig:
    """
    Parametrii pentru risk/circuit_breaker.py.

    Se suprapune partial cu kill-switch-ul, dar masoara altceva: kill-switch-ul
    lucreaza pe echity (mark-to-market, se reseteaza zilnic), circuit breaker-ul
    lucreaza pe P&L realizat din jurnalul de tranzactii inchise, pe ferestre
    glisante de 24h / 7 zile / 30 zile. Doua masuratori independente ale
    aceluiasi lucru sunt intentionate: daca una se strica, cealalta tine.
    """

    enabled: bool = True

    max_daily_loss_pct: float = 2.0
    losing_streak_n: int = 3
    cooldown_hours: float = 12.0
    weekly_drawdown_pct: float = 5.0
    monthly_drawdown_pct: float = 10.0


@dataclass(frozen=True)
class AppConfig:
    market: MarketConfig = field(default_factory=MarketConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    circuit: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)

    log_dir: str = "logs"
    signal_log: str = "logs/signals.jsonl"
    state_file: str = "logs/state.json"
    trades_log: str = "logs/trades.jsonl"

    # Jurnalul citit de circuit breaker: o linie JSON per pozitie inchisa,
    # {"pnl": ..., "closed_at": ...}. Il scrie risk/trade_recorder.py.
    trade_log: str = "logs/trade_log.jsonl"
    # Ultimul rezultat de regim, cu timestamp, ca sa nu reinterogam la fiecare scanare.
    regime_cache: str = "logs/regime.json"
    # Snapshot al pozitiilor deschise, ca sa detectam cand una dispare (= s-a inchis).
    positions_state: str = "logs/positions.json"

    # Costuri reale de tranzactionare pe BingX perpetual - folosite in backtest
    # si in trade manager. Fee-urile sunt "taker" (agresor); daca folosesti
    # limite post-only, sunt mai mici.
    taker_fee: float = 0.0005  # 0.05%
    # Slippage estimat la intrare/iesire (o valoare per side). Pentru majors,
    # pe timeframe 1h+, ~0.05% e conservator. Pentru altcoin-uri mici, dubleaza.
    slippage: float = 0.0005  # 0.05%
    # Funding rate mediu absolut pe 8h. Pe BTC/ETH oscileaza ~0.01% pe cicu de 8h.
    # Folosit doar in backtest, ca aproximare - live se preia din exchange.
    funding_8h: float = 0.0001  # 0.01%


CONFIG = AppConfig()
