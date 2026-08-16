"""
Backtester event-driven, fara lookahead.

Regulile de simulare - cele care fac diferenta intre un backtest util si o
minciuna frumoasa:

1. Semnalul se genereaza pe lumanarea INCHISA t.
2. Intrarea se face la open-ul lumanarii t+1, cu slippage adaugat.
3. Stop / TP se verifica intrabar pe lumanarile t+2, t+3, ... folosind high/low.
4. Daca in aceeasi lumanare sunt atinse SI stopul SI TP1, presupunem ca stopul
   s-a atins primul. E asumptia conservatoare - realitatea depinde de ordinea
   ticksurilor pe care nu o avem. Presupunerea inversa infrumuseteaza artificial
   rezultatele.
5. La TP1 se inchide jumatate din pozitie si stopul se muta la breakeven +
   fee-uri (nu vrei ca a doua jumatate sa se inchida cu comision total in minus
   dupa un breakeven "curat").
6. Fee-uri pe fiecare intrare/iesire, funding la fiecare 8h de detinere.
7. Doar o pozitie deschisa pe simbol; daca aparea semnal in acelasi timp cu o
   pozitie in curs, e ignorat.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from typing import Any

import pandas as pd

from strategy import signal_builder
from strategy.trade_manager import TP1_CLOSE_FRACTION


@dataclass
class ClosedTrade:
    symbol: str
    side: str
    entry_time: str
    exit_time: str
    entry: float
    exit: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    position_size: float
    r_multiple: float
    pnl_usdt: float
    pnl_pct_equity: float
    fees_paid: float
    funding_paid: float
    exit_reason: str
    equity_after: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestReport:
    symbol: str
    period_start: str
    period_end: str
    starting_equity: float
    ending_equity: float
    total_trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float
    avg_r: float
    expectancy_r: float
    total_return_pct: float
    max_drawdown_pct: float
    max_drawdown_r: float
    sharpe_daily: float
    profit_factor: float
    total_fees: float
    total_funding: float
    trades: list[ClosedTrade] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 68,
            f"  Backtest {self.symbol}  |  {self.period_start} -> {self.period_end}",
            "=" * 68,
            f"  Echity          : {self.starting_equity:.2f}  ->  {self.ending_equity:.2f}",
            f"  Randament total : {self.total_return_pct:+.2f}%",
            f"  Trades          : {self.total_trades}  ({self.wins}W / {self.losses}L / {self.breakeven}BE)",
            f"  Win rate        : {self.win_rate:.1%}",
            f"  R mediu         : {self.avg_r:+.2f}R    (expectancy {self.expectancy_r:+.2f}R)",
            f"  Profit factor   : {self.profit_factor:.2f}",
            f"  Max drawdown    : {self.max_drawdown_pct:.2f}%   ({self.max_drawdown_r:.1f}R)",
            f"  Sharpe (zilnic) : {self.sharpe_daily:.2f}",
            f"  Fees platite    : {self.total_fees:.2f} USDT",
            f"  Funding platit  : {self.total_funding:+.2f} USDT",
            "=" * 68,
        ]
        if self.total_trades == 0:
            lines.append("  Zero trades. Perioada prea scurta sau filtre prea stricte.")
        elif self.expectancy_r <= 0:
            lines.append("  Expectancy negativa. Strategia PIERDE pe termen lung.")
        elif self.expectancy_r < 0.1:
            lines.append("  Expectancy marginala. Un slippage mai mare o scoate din bani.")
        else:
            lines.append("  Expectancy pozitiva. Verifica robustetea pe alta perioada/simbol.")
        lines.append("=" * 68)
        return "\n".join(lines)


class Backtester:
    """
    Ruleaza strategia pe date istorice. NU are nevoie de chei API - foloseste
    doar OHLCV public.

    Modul de operare:
      - HTF-ul se pregateste o singura data, indexat pe timestamp;
      - iteram pe LTF lumanare cu lumanare, folosind DOAR datele pana la t;
      - la fiecare lumanare fara pozitie deschisa: cautam semnal;
      - cu pozitie deschisa: verificam stop/TP pe high/low ale lumanarii curente.
    """

    def __init__(
        self,
        cfg,
        signal_fn=None,
        strategy_cfg=None,
        min_bars: int | None = None,
        max_bars_in_trade: int | None = None,
        limit_valid_bars: int | None = None,
    ) -> None:
        """
        `signal_fn(symbol, htf_slice, ltf_slice) -> Signal | None` permite
        rularea altei strategii decat cea implicita, fara sa duplicam motorul.
        Contractul e intentionat ingust: functia primeste DOAR felii de date
        care se termina la bara curenta, deci nu are cum sa vada in viitor.

        `max_bars_in_trade` impune un buget de timp. Implicit None = fara limita,
        cum a fost pana acum. Pentru strategiile intraday e obligatoriu: fara el
        masori o strategie care poate tine pozitia zile intregi, nu pe cea pe
        care chiar intentionezi sa o tranzactionezi.
        """
        self.cfg = cfg
        self.strategy_cfg = strategy_cfg if strategy_cfg is not None else cfg.strategy
        self.signal_fn = signal_fn if signal_fn is not None else self._default_signal_fn
        self._min_bars_override = min_bars
        self.max_bars_in_trade = max_bars_in_trade
        # Cate lumanari asteapta un ordin limit inainte sa fie anulat. None =
        # executie la piata pe open-ul urmatoarei lumanari (comportamentul vechi).
        self.limit_valid_bars = limit_valid_bars

    def _default_signal_fn(self, symbol, htf_slice, ltf_slice):
        return signal_builder.build_signal(
            symbol, htf_slice, ltf_slice, self.strategy_cfg, self.cfg.risk
        )

    # -------------------------------------------------------- runner principal
    def run(self, symbol: str, htf: pd.DataFrame, ltf: pd.DataFrame,
            starting_equity: float = 1000.0) -> BacktestReport:
        risk = self.cfg.risk
        strat = self.strategy_cfg
        min_bars = self._min_bars_override or (
            max(strat.ema_slow, strat.adx_period * 4) + 5
        )

        htf_sorted = htf.sort_values("timestamp").reset_index(drop=True)
        ltf_sorted = ltf.sort_values("timestamp").reset_index(drop=True)

        equity = starting_equity
        equity_curve: list[tuple[int, float]] = [(int(ltf_sorted.iloc[0]["timestamp"]), equity)]
        trades: list[ClosedTrade] = []
        open_trade: dict | None = None
        pending: dict | None = None  # ordin limit care asteapta in carte

        # Ca sa nu spargem incapsularea, folosim aceleasi functii ca live.
        for i in range(min_bars, len(ltf_sorted) - 1):
            bar = ltf_sorted.iloc[i]
            ts = int(bar["timestamp"])

            # --- 1. daca avem pozitie deschisa, testam iesirea pe ACEASTA lumanare
            # Buclam pentru ca o singura bara poate declansa TP1 partial si apoi,
            # in bara urmatoare, restul. In aceeasi bara insa oprim dupa primul
            # eveniment - nu stim ordinea ticksurilor.
            if open_trade is not None:
                exit_info = self._check_exit(open_trade, bar)
                if exit_info is not None:
                    closed, equity = self._close_trade(open_trade, exit_info, equity)
                    equity_curve.append((ts, equity))
                    if closed is not None:  # pozitia s-a inchis complet
                        trades.append(closed)
                        open_trade = None

            # --- 1b. bugetul de timp. Se verifica DUPA stop/TP, pentru ca daca
            # ambele s-ar declansa pe aceeasi lumanare, stopul are prioritate -
            # varianta pesimista este singura onesta cand nu stim ordinea ticks.
            if open_trade is not None and self.max_bars_in_trade is not None:
                open_trade["bars_held"] = open_trade.get("bars_held", 0) + 1
                if open_trade["bars_held"] >= self.max_bars_in_trade:
                    exit_info = {
                        "exit_time": bar["datetime"],
                        "exit_price": float(bar["close"]),
                        "reason": f"timp expirat ({open_trade['bars_held']} lumanari)",
                        "fraction_closed": 1.0 - open_trade["fraction_closed"],
                    }
                    closed, equity = self._close_trade(
                        open_trade, exit_info, equity, force_full=True
                    )
                    equity_curve.append((ts, equity))
                    if closed is not None:
                        trades.append(closed)
                    open_trade = None

            # --- 1c. ordinul limit in asteptare: se executa daca pretul chiar
            # revine la nivel. Verificarea vine DUPA iesiri, ca un trade sa nu
            # se deschida si sa se inchida in aceeasi lumanare - nu stim ordinea
            # ticksurilor, iar presupunerea optimista ar inventa castiguri.
            if open_trade is None and pending is not None:
                sig = pending["signal"]
                price = pending["price"]
                touched = (
                    float(bar["low"]) <= price
                    if sig.side == "long"
                    else float(bar["high"]) >= price
                )
                if touched:
                    open_trade = self._open_trade(
                        sig, bar, equity, fill_price=price, is_maker=True
                    )
                    pending = None
                    if open_trade is not None:
                        equity -= open_trade["fees_paid"]
                else:
                    pending["bars_left"] -= 1
                    if pending["bars_left"] <= 0:
                        pending = None  # setup expirat, pretul a plecat fara noi

            # --- 2. daca suntem flat, cautam semnal folosind datele pana la ACEASTA lumanare
            if open_trade is None and pending is None:
                htf_slice = htf_sorted[htf_sorted["timestamp"] <= ts]
                ltf_slice = ltf_sorted.iloc[: i + 1]

                if len(htf_slice) < min_bars:
                    continue

                signal = self.signal_fn(symbol, htf_slice, ltf_slice)
                if signal is None:
                    continue

                if self.limit_valid_bars is not None:
                    # Ordin limit: nu intram acum, punem ordinul in carte si
                    # asteptam ca pretul sa vina la el. Daca nu vine, nu
                    # tranzactionam - a nu alerga dupa pret e jumatate din setup.
                    pending = {
                        "signal": signal,
                        "price": float(signal.entry),
                        "bars_left": self.limit_valid_bars,
                    }
                else:
                    # Intrare la open-ul lumanarii URMATOARE, cu slippage.
                    next_bar = ltf_sorted.iloc[i + 1]
                    open_trade = self._open_trade(signal, next_bar, equity)
                    if open_trade is not None:
                        # Comisionul de intrare se plateste imediat, nu la iesire.
                        equity -= open_trade["fees_paid"]

        # Daca la sfarsit ramane o pozitie deschisa, o inchidem la ultimul close
        # (marcare la piata, ca sa nu falsificam metricile ignorand-o).
        if open_trade is not None:
            last = ltf_sorted.iloc[-1]
            exit_info = {
                "exit_time": last["datetime"],
                "exit_price": float(last["close"]),
                "reason": "eod",
                "fraction_closed": 1.0 - open_trade["fraction_closed"],
            }
            closed, equity = self._close_trade(open_trade, exit_info, equity, force_full=True)
            if closed is not None:
                trades.append(closed)
            equity_curve.append((int(last["timestamp"]), equity))

        return self._make_report(
            symbol=symbol,
            starting_equity=starting_equity,
            ending_equity=equity,
            trades=trades,
            equity_curve=equity_curve,
            ltf=ltf_sorted,
        )

    # --------------------------------------------------------------- executie
    def _open_trade(self, signal, entry_bar, equity: float,
                    fill_price: float | None = None, is_maker: bool = False) -> dict:
        """
        `fill_price` dat = executie pe ordin LIMIT: pretul e garantat (de aceea
        am si asteptat pentru el) si nu exista slippage - noi am fost
        lichiditatea, nu am luat-o pe a altcuiva.

        Fara `fill_price` = executie la piata pe open-ul urmatoarei lumanari, cu
        slippage advers. Comportamentul de dinainte, pastrat pentru strategiile
        care chiar intra la piata.
        """
        risk = self.cfg.risk
        app = self.cfg

        if fill_price is not None:
            entry = float(fill_price)
        else:
            # Slippage adverse: long plateste mai mult, short primeste mai putin.
            raw_entry = float(entry_bar["open"])
            slip = raw_entry * app.slippage
            entry = raw_entry + slip if signal.side == "long" else raw_entry - slip

        # Distanta de risc RAMANE cea din semnal (calculata pe close-ul t).
        # Nu recalculam mai bine dupa slippage - realitatea nu iti da acest cadou.
        r_per_unit = abs(signal.entry - signal.stop_loss)
        if r_per_unit <= 0:
            return None  # type: ignore[return-value]

        risk_amount = equity * risk.risk_per_trade
        size = risk_amount / r_per_unit
        notional = size * entry

        # Plafon de leverage (aceeasi regula ca in live).
        leverage = notional / equity
        if leverage > risk.max_leverage:
            leverage = risk.max_leverage
            notional = equity * leverage
            size = notional / entry

        entry_fee = notional * (app.maker_fee if is_maker else app.taker_fee)

        return {
            "symbol": signal.symbol,
            "side": signal.side,
            "entry_time": entry_bar["datetime"],
            "entry_timestamp": int(entry_bar["timestamp"]),
            "last_funding_ts": int(entry_bar["timestamp"]),
            "entry": entry,
            "stop_loss": signal.stop_loss,
            "initial_stop": signal.stop_loss,  # pastrat pentru raport
            "take_profits": list(signal.take_profits),
            "position_size": size,
            "notional": notional,
            "leverage": leverage,
            "r_per_unit": r_per_unit,
            "risk_amount": risk_amount,
            "equity_at_entry": equity,
            "fees_paid": entry_fee,
            "funding_paid": 0.0,
            "realized_pnl": -entry_fee,  # comisionul de intrare e deja pierdut
            "r_accumulated": 0.0,
            "fraction_closed": 0.0,  # cat din pozitie am inchis deja (partial la TP1)
            "moved_to_breakeven": False,
            "exits": [],
        }

    def _check_exit(self, trade: dict, bar: pd.Series) -> dict | None:
        """Returneaza infoul de iesire sau None daca pozitia continua."""
        high = float(bar["high"])
        low = float(bar["low"])

        stop = trade["stop_loss"]
        tp1 = trade["take_profits"][0]
        tp2 = trade["take_profits"][1] if len(trade["take_profits"]) > 1 else None

        if trade["side"] == "long":
            hit_stop = low <= stop
            hit_tp1 = high >= tp1 and trade["fraction_closed"] < TP1_CLOSE_FRACTION
            hit_tp2 = tp2 is not None and high >= tp2
        else:
            hit_stop = high >= stop
            hit_tp1 = low <= tp1 and trade["fraction_closed"] < TP1_CLOSE_FRACTION
            hit_tp2 = tp2 is not None and low <= tp2

        # Regula conservatoare: cand stopul si TP-ul sunt in aceeasi bara, stopul castiga.
        if hit_stop:
            return {
                "exit_time": bar["datetime"],
                "exit_price": stop,
                "reason": "stop",
                "fraction_closed": 1.0 - trade["fraction_closed"],
            }

        # TP1 partial: inchidem fractiunea configurata si mutam stopul la
        # breakeven + fee-uri.
        if hit_tp1:
            fee = self.cfg.taker_fee
            slip = self.cfg.slippage
            # Breakeven "real" trebuie sa acopere fee-urile pe restul pozitiei.
            if trade["side"] == "long":
                trade["stop_loss"] = trade["entry"] * (1 + 2 * (fee + slip))
            else:
                trade["stop_loss"] = trade["entry"] * (1 - 2 * (fee + slip))
            trade["moved_to_breakeven"] = True
            trade["fraction_closed"] = TP1_CLOSE_FRACTION
            return {
                "exit_time": bar["datetime"],
                "exit_price": tp1,
                "reason": "tp1",
                "fraction_closed": TP1_CLOSE_FRACTION,
            }

        if hit_tp2:
            return {
                "exit_time": bar["datetime"],
                "exit_price": tp2,
                "reason": "tp2",
                "fraction_closed": 1.0 - trade["fraction_closed"],
            }

        return None

    def _close_trade(self, trade: dict, exit_info: dict, equity: float,
                     force_full: bool = False) -> tuple[ClosedTrade | None, float]:
        """
        Aplica o iesire (partiala sau totala) si actualizeaza echity.

        Returneaza un ClosedTrade DOAR cand pozitia s-a inchis complet. O
        inchidere partiala la TP1 nu este o tranzactie separata - este o etapa
        din aceeasi pozitie. Daca am raporta-o separat, am umfla artificial
        numarul de trades si am distorsiona win rate-ul.
        """
        # Cine plateste ce, la iesire:
        #
        #   TP  -> ordin LIMIT care asteapta in carte. Noi suntem lichiditatea,
        #          deci maker si ZERO slippage: pretul e cel pe care l-am cerut,
        #          altfel ordinul pur si simplu nu se executa.
        #   stop-> ordin de piata declansat, luam din carte in graba. Taker si
        #          slippage, iar in miscari violente chiar mai mult.
        #
        # Distinctia nu e cosmetica: pentru un scalp cu R mic in pret, ea
        # valoreaza ~0.2R per tranzactie. Pana acum modelul presupunea taker pe
        # ambele iesiri, adica platea de doua ori un cost pe care il plateste
        # o singura data.
        reason = str(exit_info.get("reason", ""))
        exit_is_maker = reason.startswith("tp")

        fee = self.cfg.maker_fee if exit_is_maker else self.cfg.taker_fee
        slip = 0.0 if exit_is_maker else self.cfg.slippage

        fraction = exit_info["fraction_closed"]
        size_closed = trade["position_size"] * fraction
        raw_exit = exit_info["exit_price"]

        # Slippage la iesire: adverse pentru amandoua directiile.
        if trade["side"] == "long":
            exit_price = raw_exit - raw_exit * slip
            pnl = (exit_price - trade["entry"]) * size_closed
        else:
            exit_price = raw_exit + raw_exit * slip
            pnl = (trade["entry"] - exit_price) * size_closed

        exit_fee = exit_price * size_closed * fee

        # Funding: rata absoluta aplicata pe notionalul inca deschis, pe durata
        # scursa de la ultimul decont.
        exit_ms = int(pd.Timestamp(exit_info["exit_time"]).value // 10**6)
        held_ms = exit_ms - trade["last_funding_ts"]
        periods_8h = max(held_ms, 0) / (8 * 3600 * 1000)
        open_notional = trade["notional"] * (1.0 - trade["fraction_closed"])
        funding = open_notional * self.cfg.funding_8h * periods_8h
        trade["last_funding_ts"] = exit_ms

        net = pnl - exit_fee - funding
        equity += net

        trade["fees_paid"] += exit_fee
        trade["funding_paid"] += funding
        trade["realized_pnl"] += net
        trade["fraction_closed"] += fraction

        # R-ul acestei transe, ponderat cu fractiunea inchisa.
        if trade["r_per_unit"] > 0:
            delta = (
                exit_price - trade["entry"]
                if trade["side"] == "long"
                else trade["entry"] - exit_price
            )
            trade["r_accumulated"] += (delta / trade["r_per_unit"]) * fraction

        trade["exits"].append(f"{exit_info['reason']}@{exit_price:.4f}")

        is_final = trade["fraction_closed"] >= 0.9999 or force_full
        if not is_final:
            return None, equity

        trade["_finished"] = True

        closed = ClosedTrade(
            symbol=trade["symbol"],
            side=trade["side"],
            entry_time=str(trade["entry_time"]),
            exit_time=str(exit_info["exit_time"]),
            entry=round(trade["entry"], 8),
            exit=round(exit_price, 8),
            stop_loss=round(trade["initial_stop"], 8),
            take_profit_1=round(trade["take_profits"][0], 8),
            take_profit_2=round(
                trade["take_profits"][1] if len(trade["take_profits"]) > 1 else 0, 8
            ),
            position_size=round(trade["position_size"], 8),
            r_multiple=round(trade["r_accumulated"], 3),
            pnl_usdt=round(trade["realized_pnl"], 4),
            pnl_pct_equity=round(trade["realized_pnl"] / trade["equity_at_entry"], 5),
            fees_paid=round(trade["fees_paid"], 4),
            funding_paid=round(trade["funding_paid"], 4),
            exit_reason="+".join(trade["exits"]),
            equity_after=round(equity, 4),
        )
        return closed, equity

    # ----------------------------------------------------------------- raport
    def _make_report(self, *, symbol, starting_equity, ending_equity, trades,
                     equity_curve, ltf) -> BacktestReport:
        # Filtram trade-urile "partial" ca sa raportam pe unitati economice reale.
        wins = [t for t in trades if t.r_multiple > 0.05]
        losses = [t for t in trades if t.r_multiple < -0.05]
        be = [t for t in trades if -0.05 <= t.r_multiple <= 0.05]

        gross_win = sum(t.pnl_usdt for t in wins)
        gross_loss = abs(sum(t.pnl_usdt for t in losses))

        total_r = sum(t.r_multiple for t in trades)
        avg_r = total_r / len(trades) if trades else 0.0
        win_rate = len(wins) / len(trades) if trades else 0.0
        # Expectancy pe trade, in R.
        expectancy = avg_r

        # Drawdown pe curba de echity.
        peak = starting_equity
        max_dd_abs = 0.0
        for _, eq in equity_curve:
            peak = max(peak, eq)
            dd = peak - eq
            max_dd_abs = max(max_dd_abs, dd)
        max_dd_pct = 100 * max_dd_abs / peak if peak > 0 else 0.0
        max_dd_r = max_dd_abs / (starting_equity * self.cfg.risk.risk_per_trade) if starting_equity > 0 else 0.0

        # Sharpe zilnic simplu: agregam PnL pe zi calendaristica.
        by_day: dict[str, float] = {}
        for t in trades:
            day = str(t.exit_time)[:10]
            by_day[day] = by_day.get(day, 0.0) + t.pnl_usdt
        daily = list(by_day.values())
        sharpe = 0.0
        if len(daily) > 1:
            mean = sum(daily) / len(daily)
            variance = sum((x - mean) ** 2 for x in daily) / (len(daily) - 1)
            std = math.sqrt(variance)
            if std > 0:
                sharpe = (mean / std) * math.sqrt(365)

        total_fees = sum(t.fees_paid for t in trades)
        total_funding = sum(t.funding_paid for t in trades)
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0

        period_start = str(ltf.iloc[0]["datetime"])[:19]
        period_end = str(ltf.iloc[-1]["datetime"])[:19]

        return BacktestReport(
            symbol=symbol,
            period_start=period_start,
            period_end=period_end,
            starting_equity=starting_equity,
            ending_equity=round(ending_equity, 2),
            total_trades=len(trades),
            wins=len(wins),
            losses=len(losses),
            breakeven=len(be),
            win_rate=win_rate,
            avg_r=round(avg_r, 3),
            expectancy_r=round(expectancy, 3),
            total_return_pct=round(100 * (ending_equity - starting_equity) / starting_equity, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            max_drawdown_r=round(max_dd_r, 1),
            sharpe_daily=round(sharpe, 2),
            profit_factor=round(pf, 2) if pf != float("inf") else 999.0,
            total_fees=round(total_fees, 2),
            total_funding=round(total_funding, 2),
            trades=trades,
        )


def save_report(report: BacktestReport, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        data = asdict(report)
        json.dump(data, fh, indent=2, default=str)
