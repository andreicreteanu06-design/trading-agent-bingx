# Agent de semnale — BingX Futures (USDT-M)

Agent care scanează piața, generează setup-uri de tranzacționare cu leverage,
le validează printr-un motor de risc determinist și ți le trimite spre
**confirmare manuală**.

> **Agentul nu trimite ordine.** Nici în modul `live`. Execuția rămâne la tine,
> pe BingX. Metodele de execuție există în `exchange/bingx_client.py`, dar nu
> sunt apelate de nicăieri — sunt acolo pentru mai târziu, după backtest.

---

## Cum gândește agentul

**1. Trend-following pe două timeframe-uri.** Timeframe-ul mare (4h) decide ce
direcție e permisă; cel mic (1h) decide dacă și unde intri. Nu se
tranzacționează împotriva trendului mare — acolo se pierd conturile cu leverage.

**2. Stopul e plasat de piață, nu de tine.** Se ia varianta *mai largă* dintre
structura recentă (swing low/high + buffer ATR) și zgomotul măsurat
(1.8 × ATR). Un stop la „2%, cifră rotundă" e o invitație la stop-hunt.

**3. Leverage-ul este o consecință, nu o alegere.**

```
risc_în_bani     = echity × 1%
mărime_poziție   = risc_în_bani / distanța_până_la_stop
notional         = mărime_poziție × preț_intrare
leverage         = notional / echity
```

Nu spui „intru cu 10x". Spui „risc 1%, stopul e la 2.4% distanță" — restul
rezultă. Dacă leverage-ul calculat depășește plafonul, poziția se reduce, nu
riscul se mărește.

**4. Claude poate doar să frâneze.** Primește un setup *deja validat* și
răspunde la o singură întrebare: „ce nu văd în cifrele astea?". Un verdict
`skip` anulează semnalul. Nu poate mări poziția, muta stopul, ridica
leverage-ul sau reînvia un semnal respins.

---

## Instalare

```powershell
cd C:\Users\Dell\trading-agent-bingx
python -m pip install -r requirements.txt
copy .env.example .env
```

Apoi completează `.env`. **Toate cheile sunt opționale la început** — fără ele,
agentul rulează pe date publice și dimensionează pe o echity presupusă de 1000 USDT.

### Cheia BingX

BingX → API Management → Create API.

- **NU** bifa `Withdraw`. Niciodată.
- Pentru faza de semnale, `Read` este suficient.
- Pune IP whitelist dacă ai IP static.

---

## Utilizare

```powershell
python main.py                      # o singură scanare
python main.py --watch              # scanează la fiecare 15 minute
python main.py --watch --interval 5
python main.py --symbol "BTC/USDT:USDT"
python main.py --no-claude          # fără analiză LLM

python -m backtest.run              # backtest pe toate simbolurile
python -m backtest.run --candles 6000 --trades --save

python tools\diagnose.py            # de ce nu există semnal acum
python tools\test_risk.py           # aritmetica de risc (22 teste)
python tools\test_all.py            # kill-switch, trade manager, blackout (29 teste)
python tools\killswitch.py          # starea kill-switch-ului
```

Formatul simbolurilor e cel CCXT pentru perpetual: `BTC/USDT:USDT`.

---

## Structura

```
config.py                    toate limitele de risc (HARD LIMITS)
exchange/bingx_client.py     CCXT; singurul fișier care știe de BingX
strategy/indicators.py       EMA, RSI, ATR, ADX (netezire Wilder)
strategy/signal_builder.py   logica multi-timeframe + scor 0-100
strategy/risk_engine.py      dimensionare, plafoane, lichidare — zero LLM
strategy/kill_switch.py      drawdown zilnic/total, pierderi consecutive
strategy/trade_manager.py    breakeven după TP1, trailing ATR, ieșire pe timp
news/blackout.py             ferestre de risc macro + detector de șocuri
backtest/engine.py           simulare fără lookahead, cu fees/funding/slippage
ai/claude_analyzer.py        analiză critică; poate doar respinge
alerts/telegram_bot.py       notificări
webhook/tradingview.py       receptor alerte Pine Script (opțional)
main.py                      orchestrare
tools/                       diagnose, test_risk, test_all, killswitch
```

## Kill-switch

Se persistă pe disc — **restartul nu resetează limitele**, intenționat.

| Limită | Prag | Resetare |
|---|---|---|
| Pierdere zilnică | 3% | automat, la zi nouă |
| Drawdown total de la vârf | 15% | doar manual |
| Pierderi consecutive | 3 | la zi nouă sau la un câștig |
| Tranzacții pe zi | 5 | la zi nouă |

```powershell
python tools\killswitch.py           # stare
python tools\killswitch.py --reset   # ridică blocarea
```

## Știrile: blackout, nu alpha

`news/blackout.py` **blochează intrări**, nu generează semnale. Motivul e în
capul fișierului, dar pe scurt: când tu citești o știre, prețul s-a mișcat de
30+ secunde, iar în fereastra unui eveniment spread-ul se lărgește de 10–50×,
deci stopul tău nu se execută la prețul cerut.

Ferestre blocate: CPI, NFP, FOMC, deschiderea sesiunii SUA, expirări de opțiuni,
deconturile de funding (00/08/16 UTC). Plus `VolatilityGuard`, care prinde
șocurile *neprogramate* — dacă ultima lumânare are range 3× ATR sau volum 4×,
ceva se întâmplă și nu ești tu cel informat.

## TradingView

TradingView **nu are API public de date** pentru retail — doar webhooks (plan
Pro+). Iar datele lui OHLCV sunt aceleași cu ale BingX; pentru tranzacționare pe
BingX, datele BingX sunt mai bune, fiind de pe venue-ul unde execuți.

Ce aduce TradingView e Pine Script. Dacă ai un indicator propriu în care ai
încredere:

```powershell
# în .env: WEBHOOK_SECRET=ceva-lung-si-aleatoriu
python -m webhook.tradingview --port 8080
ngrok http 8080     # ca TradingView să te poată apela
```

Mesajul alertei:
```json
{
  "secret": "secretul-din-.env",
  "symbol": "BTC/USDT:USDT",
  "side": "long",
  "entry": {{close}},
  "stop_loss": {{plot_0}}
}
```

Alerta trece prin **același risk engine**. Nu există cale prin care un webhook
să ocolească validările.

---

## Parametrii de risc (`config.py`)

| Parametru | Implicit | Ce face |
|---|---|---|
| `risk_per_trade` | 1% | din echity, per tranzacție |
| `max_leverage` | 5x | plafon absolut |
| `max_total_notional_mult` | 3x | expunere totală maximă |
| `max_open_positions` | 2 | poziții simultane |
| `min_risk_reward` | 1.5 | sub asta, respins |
| `min_liquidation_buffer_mult` | 3x | lichidarea la ≥3× distanța stopului |
| `min_adx` | 20 | sub asta, piața e în range |
| `atr_stop_mult` | 1.8 | lățimea stopului |
| `tp_r_multiples` | 1.5R, 3R | țintele |

Un semnal e respins dacă: stopul e prea aproape (<0.4%) sau prea departe (>5%),
R:R < 1.5, lichidarea e prea aproape de stop, expunerea totală depășește
plafonul, sau există deja poziție pe simbol.

---

## ⛔ Rezultatul backtestului: strategia curentă PIERDE bani

Rulat pe 8 luni (dec 2025 – aug 2026), 1h/4h, cu fees + funding + slippage:

| Simbol | Trades | Win rate | R mediu | Randament |
|---|---|---|---|---|
| BTC/USDT | 31 | 45.2% | **−0.21R** | −8.6% |
| ETH/USDT | 34 | 35.3% | **−0.37R** | −13.6% |
| SOL/USDT | 62 | 30.6% | **−0.48R** | −28.0% |
| **Total** | **127** | **35.4%** | **−0.386R** | — |

Reprodu singur: `python -m backtest.run --candles 6000`

**Nu tranzacționa cu strategia asta.** 127 de tranzacții e un eșantion
suficient cât să nu fie ghinion. Pierderea e consistentă pe toate cele trei
simboluri, ceea ce arată o problemă de strategie, nu de simbol.

Ce spun cifrele mai exact:
- Win rate 35% ar fi acceptabil dacă R-ul mediu pe câștig ar fi >2. Nu este.
- Profit factor 0.29–0.51 înseamnă că pentru fiecare dolar câștigat se pierd 2–3.
- **Funding a fost pozitiv** (+26 USDT total) — deci nici măcar costurile nu
  explică pierderea. Strategia pur și simplu intră prost.

Ipoteza cea mai probabilă: pe 1h, un pullback la EMA50 în direcția trendului 4h
e un setup prea comun. Este exact locul unde se adună stopurile tuturor, deci
exact unde se duce prețul înainte să continue. Filtrul ADX>20 nu compensează.

### Ce ai de făcut înainte de bani reali

1. **Schimbă strategia**, nu parametrii de risc. Un risk engine bun nu salvează
   un edge negativ — doar întârzie pierderea.
2. Testează idei diferite pe backtester: timeframe mai mare (4h/1d), breakout
   pe volum în loc de pullback, mean-reversion când ADX e mic.
3. Cere `expectancy_r > +0.15` pe **cel puțin două perioade diferite** și pe
   mai multe simboluri. Dacă merge doar pe una, e noroc.
4. Abia apoi paper trading. Apoi sume mici.

Un backtester care îți spune „nu are edge" ți-a economisit deja mai mulți bani
decât îți va aduce vreodată un semnal.

Alte lucruri pe care le vei descoperi altfel pe pielea ta:

- **Funding rate** nu e modelat aici. Pe perpetuals ținute peste noapte,
  contează.
- **Slippage și comisioane** nu sunt modelate. La stopuri strânse, mănâncă R-ul.
- **Prețul de lichidare e o estimare conservatoare**, calculată pentru marjă
  izolată. Cel real de pe BingX diferă. Nu te baza pe el ca pe un stop.
- **ADX < 20 filtrează majoritatea timpului.** E intenționat. Zilele fără
  semnal sunt zilele în care nu pierzi bani.
- Un agent care nu găsește nimic două săptămâni **nu e stricat**. Rulează
  `tools\diagnose.py` ca să vezi exact ce filtru taie.

---

## Ce urmează, dacă vrei să continui

- Backtester peste `logs/signals.jsonl` + date istorice
- Modelare funding + comisioane
- Trailing stop după atingerea TP1
- Execuție semi-automată cu confirmare în terminal
- Kill-switch la drawdown zilnic
