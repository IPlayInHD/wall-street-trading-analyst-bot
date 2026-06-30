"""
In-memory limit order book with O(log n) insert/cancel.

Used internally by strategies to simulate fill logic and compute
realistic slippage before committing a live order.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, Iterator, List, Optional, Tuple

from sortedcontainers import SortedList


class Side:
    """One side (bids or asks) of the order book."""

    def __init__(self, ascending: bool) -> None:
        # bids: descending (negate key), asks: ascending
        self._asc = ascending
        self._levels: SortedList = SortedList(key=lambda x: x[0] if ascending else -x[0])
        self._index: Dict[Decimal, int] = {}  # price → index pointer (approximate)

    def update(self, price: Decimal, size: Decimal) -> None:
        self.remove(price)
        if size > 0:
            self._levels.add((price, size))

    def remove(self, price: Decimal) -> None:
        for i, (p, _) in enumerate(self._levels):
            if p == price:
                self._levels.pop(i)
                return

    def best(self) -> Optional[Tuple[Decimal, Decimal]]:
        return self._levels[0] if self._levels else None

    def levels(self, depth: int = 10) -> List[Tuple[Decimal, Decimal]]:
        return list(self._levels[:depth])

    def simulate_fill(self, qty: Decimal) -> Tuple[Decimal, Decimal]:
        """
        Walk the book and return (avg_fill_price, filled_qty).
        Partial fills are possible if the book is thin.
        """
        remaining = qty
        cost = Decimal("0")
        filled = Decimal("0")
        for price, size in self._levels:
            take = min(remaining, size)
            cost += take * price
            filled += take
            remaining -= take
            if remaining <= 0:
                break
        avg_price = cost / filled if filled else Decimal("0")
        return avg_price, filled

    def __len__(self) -> int:
        return len(self._levels)

    def __iter__(self) -> Iterator[Tuple[Decimal, Decimal]]:
        return iter(self._levels)


class LocalOrderBook:
    """Maintains a local snapshot of an exchange order book."""

    def __init__(self, exchange: str, symbol: str) -> None:
        self.exchange = exchange
        self.symbol = symbol
        self.bids = Side(ascending=False)
        self.asks = Side(ascending=True)
        self.last_update_id: int = 0

    def apply_snapshot(self, bids: List, asks: List, update_id: int = 0) -> None:
        self.bids = Side(ascending=False)
        self.asks = Side(ascending=True)
        for p, s in bids:
            self.bids.update(Decimal(str(p)), Decimal(str(s)))
        for p, s in asks:
            self.asks.update(Decimal(str(p)), Decimal(str(s)))
        self.last_update_id = update_id

    def apply_delta(self, bids: List, asks: List, update_id: int = 0) -> None:
        if update_id and update_id <= self.last_update_id:
            return
        for p, s in bids:
            self.bids.update(Decimal(str(p)), Decimal(str(s)))
        for p, s in asks:
            self.asks.update(Decimal(str(p)), Decimal(str(s)))
        self.last_update_id = update_id

    def spread(self) -> Optional[Decimal]:
        b = self.bids.best()
        a = self.asks.best()
        if b and a:
            return a[0] - b[0]
        return None

    def mid(self) -> Optional[Decimal]:
        b = self.bids.best()
        a = self.asks.best()
        if b and a:
            return (b[0] + a[0]) / 2
        return None

    def slippage_bps(self, side: str, qty: Decimal) -> Optional[Decimal]:
        """Estimate slippage in bps for a market order of given qty."""
        mid = self.mid()
        if not mid:
            return None
        if side == "buy":
            avg, _ = self.asks.simulate_fill(qty)
        else:
            avg, _ = self.bids.simulate_fill(qty)
        if avg == 0:
            return None
        return abs(avg - mid) / mid * Decimal("10000")
