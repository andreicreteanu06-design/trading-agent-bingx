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


# ------------------------------------------------- strategie intraday (<= 1h)
@dataclass(frozen=True)
class ScalpConfig:
    """
    Profilul pentru pozitii tinute sub o ora.

    De ce exista separat de StrategyConfig: acela descrie o strategie de trend
    pe 1h/4h, unde o tranzactie respira 1-2 zile. Nu e o chestiune de parametri
    diferiti, ci de aritmetica diferita.

    Aritmetica, pe scurt, pentru ca ea dicteaza tot restul fisierului:
    un TP la 2R cu stop de 1.8xATR cere o miscare de 3.6xATR. Pe 1h, unde o
    lumanare are un range mediu de ~1 ATR, asta nu se intampla intr-o ora.
    Pe 5m, insa, 60 de minute inseamna 12 lumanari, iar daca stopul e stramt
    (sub structura unui sweep, nu la 1.8xATR), 2R devine o distanta de ~0.5%
    pe BTC - o miscare absolut banala in 12 lumanari.

    Concluzia care conteaza: orizontul scurt nu se obtine grabind o strategie
    lenta, ci strangand stopul. R-ul mare vine din numitor, nu din numarator.
    """

    # --- timeframe-uri
    # Contextul (directie permisa, niveluri de lichiditate majore).
    context_tf: str = "1h"
    # Executia. 5m: 1h = 12 lumanari. 3m: 1h = 20 lumanari, dar mai mult zgomot.
    exec_tf: str = "5m"
    # Cate lumanari de executie citim (600 x 5m = ~2 zile, acopera EMA200 + VWAP).
    exec_candles: int = 600
    context_candles: int = 300

    # Bugetul de timp, in lumanari de executie. 36 x 5m = 3 ore.
    #
    # A fost 12 (o ora). Masuratoarea din tools\feasibility.py a aratat de ce
    # nu functiona: distanta pe care o poate parcurge pretul creste cu sqrt(timp)
    # in timp ce costurile raman fixe, deci bugetul scurt strange R:R-ul din
    # ambele parti. Rata de succes ceruta doar pentru break-even pe BTC 5m:
    #   60 min -> 45.2%   |   120 min -> 34.2%   |   240 min -> 25.5%
    # Trecerea la 3 ore este cea mai ieftina imbunatatire din tot proiectul:
    # nu cere nicio idee noua de trading, doar rabdare.
    max_bars_in_trade: int = 36

    # --- indicatori de baza (necesari pentru indicators.enrich)
    ema_fast: int = 21
    ema_slow: int = 200
    rsi_period: int = 14
    atr_period: int = 14
    adx_period: int = 14
    volume_ma_period: int = 20
    swing_lookback: int = 20

    # --- oscilatori
    stoch_period: int = 14
    stoch_k: int = 3
    stoch_d: int = 3
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    cci_period: int = 20
    williams_period: int = 14
    mfi_period: int = 14
    bb_period: int = 20
    bb_mult: float = 2.0
    kc_mult: float = 1.5
    vwap_reset: str = "D"

    # --- detectia sweep-ului de lichiditate
    # Cate lumanari inapoi cautam nivelul care va fi maturat.
    liquidity_lookback: int = 60
    # Confirmarea pivotului: left/right lumanari de fiecare parte.
    pivot_left: int = 3
    pivot_right: int = 3
    # In cate lumanari de la depasire trebuie sa se produca reclaim-ul.
    # Peste 3, nu mai e o capcana - e o schimbare reala de directie.
    reclaim_within: int = 3
    # Cat de mult trebuie sa depaseasca wick-ul nivelul, in ATR. Sub pragul asta
    # e doar atingere, nu maturare de stopuri.
    min_sweep_atr: float = 0.15
    # Volumul lumanarii de sweep fata de medie. Un stop-run adevarat are volum:
    # se executa ordinele stop ale altora. Fara volum, e doar drift.
    min_sweep_volume: float = 1.4

    # --- setup 2: squeeze breakout (Bollinger in Keltner, apoi expansiune)
    #
    # Singurul setup de CONTINUARE din cele trei. Cand deviatia standard se
    # strange sub ATR, piata a incetat sa se deplaseze desi inca se agita:
    # energie acumulata fara directie. Eliberarea da miscarea rapida de care
    # are nevoie un orizont scurt. Directia nu vine din squeeze - vine din
    # lumanarea care il rupe.
    squeeze_enabled: bool = True
    # Cate lumanari trebuie sa fi stat comprimat. Sub 6, e doar o pauza.
    min_squeeze_bars: int = 6
    # Volumul lumanarii care rupe, fata de medie. Un breakout fara volum este
    # cea mai scumpa capcana din analiza tehnica.
    min_breakout_volume: float = 1.5
    # Cat trebuie sa depaseasca inchiderea banda Bollinger, in ATR.
    min_breakout_atr: float = 0.20

    # --- setup 3: reversie la VWAP
    #
    # Cand pretul se intinde mult peste/sub pretul mediu ponderat cu volum al
    # sesiunii, iar oscilatorii arata epuizare, VWAP-ul functioneaza ca magnet.
    # Rata de succes mai mare decat celelalte doua, dar R:R mai mic - tinta e
    # VWAP-ul, nu un multiplu arbitrar de R.
    vwap_reversion_enabled: bool = True
    # De la cate deviatii standard consideram ca merita fadeuit.
    vwap_z_entry: float = 2.2
    # Nu fadeuim intr-un trend puternic pe contextul mare: acolo "intins" poate
    # sta intins ore intregi. Peste acest ADX pe context, setup-ul se dezactiveaza.
    vwap_max_context_adx: float = 30.0

    # --- praguri oscilatori
    stoch_oversold: float = 20.0
    stoch_overbought: float = 80.0
    mfi_oversold: float = 25.0
    mfi_overbought: float = 75.0
    williams_oversold: float = -80.0
    williams_overbought: float = -20.0
    # Cat de intins fata de VWAP consideram "exces" (in deviatii standard).
    vwap_z_stretch: float = 1.8

    # --- divergente
    div_lookback: int = 80
    div_min_bars_apart: int = 4
    div_max_bars_since: int = 6
    div_min_osc_gap: float = 2.0

    # --- scor si calitate
    # Scor minim (0-100) pentru a propune setup-ul.
    min_setup_score: float = 65.0
    # R:R minim la TP1. Sub asta nu merita comisionul.
    min_risk_reward: float = 2.0
    # Buffer peste extremul sweep-ului pentru stop, in ATR.
    stop_buffer_atr: float = 0.25
    # Podea absoluta pentru stop, calibrata pe date reale: ATR-ul median pe 5m
    # este ~0.08% pe BTC si ~0.10% pe SOL, deci un stop de 1.5xATR inseamna
    # 0.12-0.15%. O podea de 0.3% ar respinge fix setup-urile normale.
    # Verifica singur cu: python tools\feasibility.py
    min_stop_distance_pct: float = 0.001  # 0.1%
    max_stop_distance_pct: float = 0.006  # 0.6%

    # Tinte, ca multiplu de R BRUT. Par mari fata de un TP clasic la 2R, si
    # trebuie sa fie: pe 5m costurile consuma ~1R (vezi max_cost_r), deci 4R
    # brut inseamna ~3R net.
    #
    # Verificarea distantei, la buget de 36 de lumanari: excursia asteptata este
    # ATR x sqrt(36) = 6 x ATR = 0.47% pe BTC. TP1 la 4R cu stop de 1.5xATR cade
    # tot la ~0.47%. Adica tinta este exact la marginea a ce face pretul in mod
    # normal in 3 ore - nu ceva ce trebuie sa speram.
    tp_r_multiples: tuple[float, ...] = (4.0, 7.0)

    # --- costuri: numarul care decide daca strategia are voie sa existe
    #
    # Costurile sunt exprimate in PRET, iar R-ul unui scalp este mic in pret.
    # Raportul dintre ele este cea mai importanta cifra a strategiei:
    #
    #     cost_R = cost_dus_intors / distanta_stop
    #
    # Cu ordine market pe ambele capete (0.05% fee + 0.05% slippage x2 = 0.2%)
    # si un stop de 0.3%, cost_R = 0.67. Adica pe un trade in care risti 1R,
    # dai doua treimi din el brokerului inainte sa se miste pretul. Nicio rata
    # de succes nu repara asta.
    #
    # De aceea intrarea este LIMIT (post-only) la nivelul recucerit: platesti
    # maker, nu taker, si nu platesti slippage la intrare. Setup-ul o cere
    # oricum - vrei retestul nivelului, nu sa alergi dupa pret.
    entry_order_type: str = "limit_post_only"
    # Cat de departe de nivelul recucerit punem limita, in ATR. 0 = exact pe
    # nivel. Prea aproape de pretul curent si ordinul se executa ca taker.
    limit_offset_atr: float = 0.10
    # Daca limita nu se umple in atatea lumanari, setup-ul a expirat.
    limit_valid_bars: int = 3

    # Plafon de avarie, NU filtrul principal.
    #
    # Prima versiune a acestui fisier avea aici 0.25, pornind de la intuitia ca
    # un cost peste un sfert din R e inacceptabil. Masuratoarea pe date reale
    # (tools\feasibility.py) a aratat ca intuitia era gresita: pe 5m cost_R este
    # ~1.0 si asta e in regula, pentru ca R:R-ul brut accesibil in 12 lumanari
    # este ~3.5, deci raman ~2.5R net. Poarta la 0.25 respingea exact
    # setup-urile viabile.
    #
    # Filtrul care conteaza este `min_risk_reward`, aplicat pe R:R-ul NET.
    # Asta ramane doar ca sa prinda cazurile absurde - stop atat de stramt incat
    # comisionul depaseste riscul asumat.
    max_cost_r: float = 1.2


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
    scalp: ScalpConfig = field(default_factory=ScalpConfig)
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
    # Comisionul cand ESTI lichiditate (ordin limit post-only care nu ia din
    # carte). Pe BingX perpetual e ~0.02%. Diferenta pare mica, dar pentru un
    # scalp cu stop de 0.5% inseamna 0.06R vs 0.15R doar din fee-ul de intrare.
    maker_fee: float = 0.0002  # 0.02%
    # Slippage estimat la intrare/iesire (o valoare per side). Pentru majors,
    # pe timeframe 1h+, ~0.05% e conservator. Pentru altcoin-uri mici, dubleaza.
    slippage: float = 0.0005  # 0.05%
    # Funding rate mediu absolut pe 8h. Pe BTC/ETH oscileaza ~0.01% pe cicu de 8h.
    # Folosit doar in backtest, ca aproximare - live se preia din exchange.
    funding_8h: float = 0.0001  # 0.01%


CONFIG = AppConfig()
