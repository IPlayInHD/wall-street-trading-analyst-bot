"""
Real-time market data layer.

Maintains a normalised order-book and ticker cache for every subscribed
symbol across all connected exchanges.  Data is pushed via WebSocket and
surfaced through an async pub/sub bus so strategy engines can react in
microseconds without polling.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Tuple

import ccxt.pro as ccxtpro

from bot.logger import log


@dataclass
class Quote:
    exchange: str
    symbol: str
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    ts: float = field(default_factory=time.time)

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> Decimal:
        if self.mid == 0:
            return Decimal("0")
        return (self.ask - self.bid) / self.mid * Decimal("10000")


@dataclass
class OrderBook:
    exchange: str
    symbol: str
    bids: List[Tuple[Decimal, Decimal]]  # (price, size) sorted desc
    asks: List[Tuple[Decimal, Decimal]]  # (price, size) sorted asc
    ts: float = field(default_factory=time.time)

    def best_bid(self) -> Optional[Tuple[Decimal, Decimal]]:
        return self.bids[0] if self.bids else None

    def best_ask(self) -> Optional[Tuple[Decimal, Decimal]]:
        return self.asks[0] if self.asks else None

    def to_quote(self) -> Optional[Quote]:
        bb = self.best_bid()
        ba = self.best_ask()
        if not bb or not ba:
            return None
        return Quote(
            exchange=self.exchange,
            symbol=self.symbol,
            bid=bb[0],
            ask=ba[0],
            bid_size=bb[1],
            ask_size=ba[1],
            ts=self.ts,
        )


# Type alias for subscribers
QuoteCallback = Callable[[Quote], None]


class MarketDataFeed:
    """
    Aggregates WebSocket order-book feeds from multiple exchanges.

    Each exchange runs its own asyncio task that calls ccxt.pro's
    watch_order_book() in a tight loop.  On every update the normalised
    Quote is pushed to all registered callbacks synchronously (they must be
    non-blocking; heavy processing should be dispatched to a queue).
    """

    def __init__(self, exchanges: Dict[str, ccxtpro.Exchange], symbols: List[str]) -> None:
        self._exchanges = exchanges
        self._symbols = symbols
        self._books: Dict[Tuple[str, str], OrderBook] = {}
        self._subscribers: List[QuoteCallback] = []
        self._tasks: List[asyncio.Task] = []

    def subscribe(self, cb: QuoteCallback) -> None:
        self._subscribers.append(cb)

    def get_quote(self, exchange: str, symbol: str) -> Optional[Quote]:
        book = self._books.get((exchange, symbol))
        return book.to_quote() if book else None

    def all_quotes_for_symbol(self, symbol: str) -> List[Quote]:
        quotes = []
        for ex in self._exchanges:
            q = self.get_quote(ex, symbol)
            if q:
                quotes.append(q)
        return quotes

    async def start(self) -> None:
        for name, exchange in self._exchanges.items():
            for symbol in self._symbols:
                task = asyncio.create_task(
                    self._watch_loop(name, exchange, symbol),
                    name=f"feed-{name}-{symbol}",
                )
                self._tasks.append(task)
        log.info("market_data.started", exchanges=list(self._exchanges.keys()), symbols=self._symbols)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for ex in self._exchanges.values():
            await ex.close()

    async def _watch_loop(self, name: str, exchange: ccxtpro.Exchange, symbol: str) -> None:
        while True:
            try:
                raw = await exchange.watch_order_book(symbol, limit=5)
                book = OrderBook(
                    exchange=name,
                    symbol=symbol,
                    bids=[(Decimal(str(p)), Decimal(str(s))) for p, s in raw["bids"]],
                    asks=[(Decimal(str(p)), Decimal(str(s))) for p, s in raw["asks"]],
                    ts=raw.get("timestamp", time.time() * 1000) / 1000,
                )
                self._books[(name, symbol)] = book
                quote = book.to_quote()
                if quote:
                    for cb in self._subscribers:
                        try:
                            cb(quote)
                        except Exception as exc:
                            log.error("feed.callback_error", error=str(exc))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("feed.reconnect", exchange=name, symbol=symbol, error=str(exc))
                await asyncio.sleep(1)
