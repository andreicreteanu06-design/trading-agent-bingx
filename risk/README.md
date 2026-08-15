# Module de risc extrase din claude-trading-skills

Adaptate din `tradermonty/claude-trading-skills` (gândit pentru trading manual pe
acțiuni) pentru un bot automat de futures crypto pe BingX. Fiecare fișier
rulează independent (`python3 nume_fisier.py`) ca demo.

## 1. `position_sizer.py`
Calculează câte contracte deschizi, pe baza riscului pe care vrei să-l asumi.

```python
from position_sizer import SizingParams, calculate_position

result = calculate_position(SizingParams(
    account_balance_usdt=1000.0,
    entry_price=65000.0,
    stop_price=63700.0,   # unde e stop-loss-ul
    risk_pct=1.0,          # riști 1% din cont per tranzacție
    leverage=5.0,
    max_position_pct=20.0, # nicio poziție > 20% din cont
))
# result["quantity"] = câte BTC să deschizi
```

Apelează-l înainte de fiecare ordin nou, cu prețul de intrare și stop-loss-ul
planificate.

## 2. `circuit_breaker.py`
Oprește automat botul dacă pierderile depășesc limitele — protecție esențială
pentru futures cu leverage.

```python
from pathlib import Path
from circuit_breaker import check_circuit_breaker, append_trade

# După ce închizi o poziție (cu profit sau pierdere):
append_trade(Path("state/trade_log.jsonl"), pnl=-15.30)

# Înainte de a deschide una nouă:
decision = check_circuit_breaker(
    trade_log_path=Path("state/trade_log.jsonl"),
    account_balance_usdt=1000.0,
)
if decision["recommendation"] != "TRADING_ALLOWED":
    print("Nu deschide poziții noi:", decision["triggered_rules"])
    # skip / exit
```

Reguli implicite (ajustabile prin `CircuitConfig`):
- Pierdere zilnică > 2% din cont → HALTED
- Pierdere săptămânală > 5% → HALTED
- Pierdere lunară > 10% → HALTED
- 3 pierderi consecutive → COOLDOWN 12 ore

## 3. `regime_analyzer.py`
Scor 0-100 al "sănătății" pieței crypto, din trend BTC + funding rate.

```python
from regime_analyzer import calculate_regime

# closes = ultimele 220+ prețuri zilnice de închidere BTC, oldest first
# funding_rates = ratele curente de funding, ex. de la API-ul BingX/Binance
regime = calculate_regime(closes, funding_rates)
if regime["zone"] == "RISK_OFF":
    print("Condiții defensive — redu size-ul sau nu deschide poziții noi")
```

**Notă**: în demo folosește date fake. Trebuie conectat la date reale — prețuri
zilnice BTC și funding rate de la API-ul BingX (sau Binance public, ca proxy).

## Cum se leagă între ele într-un ciclu al botului

```python
regime = calculate_regime(btc_closes, funding_rates)
if regime["zone"] == "RISK_OFF":
    continue  # skip acest ciclu

decision = check_circuit_breaker(trade_log_path, account_balance)
if decision["recommendation"] != "TRADING_ALLOWED":
    continue  # skip, oprit de circuit breaker

sizing = calculate_position(SizingParams(...))
# plasează ordinul cu sizing["quantity"], folosind ccxt/API-ul BingX

# după ce poziția se închide:
append_trade(trade_log_path, pnl=realized_pnl)
```

## Sursă originală
https://github.com/tradermonty/claude-trading-skills (MIT-style, vezi LICENSE
din repo pentru termeni exacți). Skill-urile originale (`position-sizer`,
`drawdown-circuit-breaker`, `crypto-regime-analyzer`) sunt gândite pentru
trading manual, cu om în buclă — aceste module păstrează formulele, dar
elimină dependența de sistemul lor de jurnal manual (`trader-memory-core`).
