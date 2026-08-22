"""
Wrapper peste CCXT pentru BingX perpetual futures (USDT-M).

Tot ce tine de exchange trece prin aici. Restul aplicatiei nu stie ca exista
CCXT - daca maine schimbi exchange-ul, singurul fisier care se modifica e asta.
"""

from __future__ import annotations

import logging
from typing import Any

import ccxt
import pandas as pd

log = logging.getLogger(__name__)

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class BingXClient:
    def __init__(self, api_key: str = "", secret: str = "") -> None:
        self._exchange = ccxt.bingx(
            {
                "apiKey": api_key,
                "secret": secret,
                "enableRateLimit": True,
                "options": {
                    # "swap" = perpetual futures. Fara asta, CCXT ar folosi spot.
                    "defaultType": "swap",
                },
            }
        )
        self._markets_loaded = False
        self.authenticated = bool(api_key and secret)

    # ------------------------------------------------------------------ setup
    def load_markets(self) -> None:
        if not self._markets_loaded:
            self._exchange.load_markets()
            self._markets_loaded = True

    def market_exists(self, symbol: str) -> bool:
        self.load_markets()
        return symbol in self._exchange.markets

    def market(self, symbol: str) -> dict[str, Any]:
        self.load_markets()
        return self._exchange.market(symbol)

    # ------------------------------------------------------------- date piata
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """
        Returneaza un DataFrame OHLCV, cu ultima lumanare (inca in formare)
        eliminata. Asta e important: daca calculezi indicatori pe lumanarea
        curenta, semnalul se schimba de la un tick la altul si backtestul devine
        o minciuna.
        """
        self.load_markets()
        raw = self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if not raw:
            raise RuntimeError(f"Fara date OHLCV pentru {symbol} {timeframe}")

        df = pd.DataFrame(raw, columns=OHLCV_COLUMNS)
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.iloc[:-1].reset_index(drop=True)  # scoatem lumanarea incompleta
        return df

    def fetch_ohlcv_history(
        self, symbol: str, timeframe: str, total: int, page: int = 1000
    ) -> pd.DataFrame:
        """
        Aduce `total` lumanari paginand inapoi in timp.

        BingX limiteaza raspunsul (uzual 1000 lumanari), deci pentru backtest pe
        1-2 ani trebuie sa cerem repetat, mergand tot mai in urma. Deduplicam pe
        timestamp pentru ca marginile paginilor se suprapun.
        """
        self.load_markets()
        tf_ms = self._exchange.parse_timeframe(timeframe) * 1000
        now_ms = self._exchange.milliseconds()
        since = now_ms - total * tf_ms

        rows: list[list] = []
        cursor = since

        while len(rows) < total:
            try:
                batch = self._exchange.fetch_ohlcv(
                    symbol, timeframe=timeframe, since=cursor, limit=page
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Paginare intrerupta pentru %s %s: %s", symbol, timeframe, exc)
                break

            if not batch:
                break

            rows.extend(batch)
            next_cursor = batch[-1][0] + tf_ms
            if next_cursor <= cursor:  # exchange-ul nu mai avanseaza
                break
            cursor = next_cursor

            if cursor >= now_ms:
                break

        if not rows:
            raise RuntimeError(f"Fara istoric OHLCV pentru {symbol} {timeframe}")

        df = pd.DataFrame(rows, columns=OHLCV_COLUMNS)
        df = df.drop_duplicates(subset="timestamp").sort_values("timestamp")
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.iloc[:-1].reset_index(drop=True)  # lumanarea curenta, incompleta
        return df

    def fetch_daily_closes(self, symbol: str, days: int) -> list[float]:
        """
        Inchiderile zilnice, cea mai veche prima - formatul cerut de
        regime_analyzer.score_btc_trend().

        Ultima lumanare (ziua in curs, inca in formare) e deja eliminata de
        fetch_ohlcv_history, deci ultima valoare e o zi inchisa. Asta conteaza:
        un 200DMA calculat pe ziua curenta se schimba la fiecare tick.
        """
        df = self.fetch_ohlcv_history(symbol, "1d", days)
        return [float(c) for c in df["close"].tolist()]

    def fetch_funding_rates(self, symbols: list[str]) -> dict[str, float]:
        """
        Funding rate-ul curent pe 8h, ca zecimala (0.0001 = 0.01%), per simbol.

        Simbolurile care esueaza sunt omise, nu inlocuite cu zero: un zero
        inventat ar deplasa media spre "neutru" si ar ascunde exact semnalul pe
        care il cautam. regime_analyzer marcheaza singur componenta ca
        indisponibila daca raman sub 2 simboluri.
        """
        self.load_markets()
        out: dict[str, float] = {}

        for symbol in symbols:
            try:
                data = self._exchange.fetch_funding_rate(symbol)
            except Exception as exc:  # noqa: BLE001
                log.warning("Fara funding rate pentru %s: %s", symbol, exc)
                continue

            rate = data.get("fundingRate")
            if rate is None:
                info = data.get("info") or {}
                rate = info.get("fundingRate") or info.get("lastFundingRate")
            if rate is None:
                log.warning("Raspuns de funding fara rata pentru %s", symbol)
                continue

            try:
                out[symbol] = float(rate)
            except (TypeError, ValueError):
                log.warning("Funding rate neinterpretabil pentru %s: %r", symbol, rate)

        return out

    def fetch_order_book(self, symbol: str, limit: int = 50) -> dict[str, Any]:
        return self._exchange.fetch_order_book(symbol, limit=limit)

    def fetch_last_price(self, symbol: str) -> float:
        ticker = self._exchange.fetch_ticker(symbol)
        price = ticker.get("last") or ticker.get("close")
        if price is None:
            raise RuntimeError(f"Fara pret pentru {symbol}")
        return float(price)

    def fetch_last_prices(self, symbols: list[str]) -> dict[str, float]:
        """Pretul curent pentru mai multe simboluri, intr-o singura cerere."""
        self.load_markets()
        tickers = self._exchange.fetch_tickers(symbols)
        out: dict[str, float] = {}
        for sym in symbols:
            t = tickers.get(sym) or {}
            price = t.get("last") or t.get("close")
            if price is not None:
                out[sym] = float(price)
        return out

    # ------------------------------------------------------------------- cont
    def fetch_equity_usdt(self) -> float | None:
        """Echity total in USDT pe contul de futures. None daca nu avem chei."""
        if not self.authenticated:
            return None
        try:
            balance = self._exchange.fetch_balance()
        except Exception as exc:  # noqa: BLE001
            log.warning("Nu am putut citi balanta: %s", exc)
            return None

        usdt = balance.get("USDT") or {}
        total = usdt.get("total")
        return float(total) if total is not None else None

    def fetch_open_positions(self) -> list[dict[str, Any]]:
        """Pozitiile deschise, normalizate. Lista goala daca nu avem chei."""
        if not self.authenticated:
            return []
        try:
            positions = self._exchange.fetch_positions()
        except Exception as exc:  # noqa: BLE001
            log.warning("Nu am putut citi pozitiile: %s", exc)
            return []

        out: list[dict[str, Any]] = []
        for pos in positions:
            contracts = pos.get("contracts") or 0
            if not contracts:
                continue
            out.append(
                {
                    "symbol": pos.get("symbol"),
                    "side": pos.get("side"),
                    "contracts": float(contracts),
                    "notional": float(pos.get("notional") or 0),
                    "entry_price": float(pos.get("entryPrice") or 0),
                    "leverage": float(pos.get("leverage") or 0),
                    "unrealized_pnl": float(pos.get("unrealizedPnl") or 0),
                    "liquidation_price": pos.get("liquidationPrice"),
                }
            )
        return out

    def fetch_realized_pnl(self, symbol: str, since_ms: int) -> float | None:
        """
        P&L-ul realizat pe un simbol de la `since_ms` incoace, insumat din
        istoricul de executii. None daca exchange-ul nu ni-l da - cine apeleaza
        decide ce face cu necunoscutul, nu presupunem zero.
        """
        if not self.authenticated:
            return None
        try:
            trades = self._exchange.fetch_my_trades(symbol, since=since_ms)
        except Exception as exc:  # noqa: BLE001
            log.warning("Nu am putut citi executiile pentru %s: %s", symbol, exc)
            return None

        if not trades:
            return None

        total = 0.0
        found = False
        for t in trades:
            info = t.get("info") or {}
            raw = (
                info.get("realizedPnl")
                or info.get("realisedPNL")
                or info.get("profit")
                or t.get("realizedPnl")
            )
            if raw in (None, ""):
                continue
            try:
                total += float(raw)
                found = True
            except (TypeError, ValueError):
                continue

        if not found:
            return None

        # Comisioanele nu sunt incluse in realizedPnl pe BingX - le scadem,
        # altfel jurnalul circuit breaker-ului arata mai bine decat contul.
        for t in trades:
            fee = t.get("fee") or {}
            cost = fee.get("cost")
            if cost:
                try:
                    total -= abs(float(cost))
                except (TypeError, ValueError):
                    pass

        return total

    # -------------------------------------------------------------- executie
    # Nota: aceste metode exista, dar main.py NU le apeleaza automat. Executia
    # ramane manuala pana cand ai backtest si paper trading in spate.
    def set_leverage(self, symbol: str, leverage: float, side: str) -> None:
        params = {"side": "LONG" if side == "long" else "SHORT"}
        self._exchange.set_leverage(int(leverage), symbol, params)

    def normalize_amount(
        self,
        symbol: str,
        amount: float,
        price: float | None = None,
        closing: bool = False,
    ) -> float:
        """
        Rotunjeste cantitatea la precizia pietei si verifica minimele bursei.

        Fiecare contract are alta precizie si alt minim - masurat pe BingX:
        BTC accepta 0.0001, SOL cere minim 0.03, HOLO cere 34.29 unitati, iar
        costul minim e 2 USDT peste tot. O cantitate brută trimisa asa cum a
        calculat-o sizerul e respinsa de bursa sau, mai rau, rotunjita tacut de
        ea in altceva decat riscul pe care l-ai dimensionat.

        `closing=True` sare peste verificarea minimelor: un ordin care INCHIDE o
        pozitie nu are voie sa fie blocat de o limita de marime. Daca minimele
        s-au schimbat de la deschidere, alternativa la un stop prea mic nu e un
        stop respins, ci o pozitie ramasa fara protectie.
        """
        self.load_markets()
        try:
            qty = float(self._exchange.amount_to_precision(symbol, amount))
        except Exception as exc:  # noqa: BLE001
            # ccxt arunca InvalidOrder cand cantitatea e sub un pas de precizie.
            # E aceeasi problema ca minimele de mai jos, deci merita acelasi tip
            # de eroare - altfel apelantul trebuie sa prinda doua exceptii pentru
            # un singur motiv.
            raise ValueError(f"{symbol}: cantitate {amount} invalida - {exc}") from exc

        if closing:
            return qty

        limits = self.market(symbol).get("limits") or {}
        min_amount = (limits.get("amount") or {}).get("min")
        min_cost = (limits.get("cost") or {}).get("min")

        if qty <= 0:
            raise ValueError(
                f"{symbol}: cantitatea {amount} se rotunjeste la zero la precizia pietei"
            )
        if min_amount is not None and qty < float(min_amount):
            raise ValueError(
                f"{symbol}: cantitate {qty} sub minimul bursei {min_amount}"
            )
        if min_cost is not None and price is not None and qty * price < float(min_cost):
            raise ValueError(
                f"{symbol}: notional {qty * price:.2f} USDT sub minimul "
                f"bursei {min_cost} USDT"
            )
        return qty

    def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        params: dict[str, Any] | None = None,
        price: float | None = None,
        closing: bool = False,
    ) -> dict[str, Any]:
        """`price` e folosit doar ca sa verifice notionalul minim, nu ca limita."""
        qty = self.normalize_amount(symbol, amount, price, closing)
        order_side = "buy" if side == "long" else "sell"
        return self._exchange.create_order(
            symbol, "market", order_side, qty, None, params or {}
        )

    def create_stop_loss(
        self, symbol: str, side: str, amount: float, stop_price: float
    ) -> dict[str, Any]:
        qty = self.normalize_amount(symbol, amount, closing=True)
        stop = float(self._exchange.price_to_precision(symbol, stop_price))
        close_side = "sell" if side == "long" else "buy"
        return self._exchange.create_order(
            symbol,
            "stop_market",
            close_side,
            qty,
            None,
            {"stopPrice": stop, "reduceOnly": True},
        )
