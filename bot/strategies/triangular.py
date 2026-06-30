"""
Triangular Arbitrage Strategy
───────────────────────────────
Exploits pricing inconsistencies within a single exchange across three
currency pairs that form a cycle.

Classic triangle:
    BTC/USDT → ETH/BTC → ETH/USDT → back to USDT
    If  (1 / ask_BTC_USDT) * (1 / ask_ETH_BTC) * bid_ETH_USDT > 1 + fees
    then there is a riskless profit.

This was common in FX desks in the 1990s (Deutsche Mark / Dollar / Yen
triangles), and is extremely competitive today in both FX and crypto.
Speed of execution is everything — the window can close in <10 ms.

The algorithm:
  1. On each quote update, check every predefined triangle.
  2. Compute the product of conversion rates around the loop.
  3. If the product > 1 + min_profit, construct three sequential market
     orders and queue them for immediate execution.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from bot.config import CONFIG
from bot.logger import log
from bot.market_data import Quote

# Each triangle is a 3-tuple of (base_pair, quote_pair, derived_pair)
# representing the cycle A→B→C→A
TRIANGLES: List[Tuple[str, str, str]] = [
    ("BTC/USDT", "ETH/BTC", "ETH/USDT"),
    ("BTC/USDT", "BNB/BTC", "BNB/USDT"),
    ("ETH/USDT", "SOL/ETH", "SOL/USDT"),
    ("BTC/USDT", "SOL/BTC", "SOL/USDT"),
    ("BTC/USDT", "XRP/BTC", "XRP/USDT"),
    ("ETH/USDT", "MATIC/ETH", "MATIC/USDT"),
]

TAKER_FEE = Decimal("0.00075")  # 0.075% per leg × 3 legs


@dataclass
class TriangleSignal:
    exchange: str
    legs: Tuple[str, str, str]  # symbols
    directions: Tuple[str, str, str]  # buy/sell per leg
    profit_pct: Decimal
    entry_usdt: Decimal
    quote_age_ms: float


class TriangularArb:
    """
    Scans all defined triangles on a single exchange for circular mispricing.

    Quotes are cached per-exchange per-symbol; on each update the affected
    triangles are re-evaluated.  Signals are returned as TriangleSignal
    objects rather than TradeIntents because tri-arb requires 3 sequential
    orders on the same exchange, not 2 simultaneous cross-exchange legs.
    """

    def __init__(self) -> None:
        # (exchange, symbol) → Quote
        self._quotes: Dict[Tuple[str, str], Quote] = {}
        self._last_signal_ts: Dict[Tuple[str, str, str, str], float] = {}
        self._cooldown_ms = 100  # tri-arb needs sequential orders, allow more time

    def on_quote(self, quote: Quote) -> Optional[TriangleSignal]:
        self._quotes[(quote.exchange, quote.symbol)] = quote
        return self._evaluate(quote.exchange, quote.symbol)

    def _evaluate(self, exchange: str, updated_symbol: str) -> Optional[TriangleSignal]:
        for triangle in TRIANGLES:
            if updated_symbol not in triangle:
                continue
            signal = self._check_triangle(exchange, triangle)
            if signal:
                return signal
        return None

    def _check_triangle(
        self, exchange: str, triangle: Tuple[str, str, str]
    ) -> Optional[TriangleSignal]:
        s1, s2, s3 = triangle
        q1 = self._quotes.get((exchange, s1))
        q2 = self._quotes.get((exchange, s2))
        q3 = self._quotes.get((exchange, s3))
        if not (q1 and q2 and q3):
            return None

        key = (exchange, s1, s2, s3)
        now = time.time()
        if (now - self._last_signal_ts.get(key, 0.0)) * 1000 < self._cooldown_ms:
            return None

        quote_age_ms = (now - min(q1.ts, q2.ts, q3.ts)) * 1000

        # Forward path: buy s1 (USDT→BTC), buy s2 (BTC→ETH), sell s3 (ETH→USDT)
        # Start with 1 USDT
        usdt_start = Decimal("1")

        # Leg 1: buy BTC with USDT  →  divide by ask
        btc = usdt_start / q1.ask * (1 - TAKER_FEE)
        # Leg 2: buy ETH with BTC   →  divide by ask
        eth = btc / q2.ask * (1 - TAKER_FEE)
        # Leg 3: sell ETH for USDT  →  multiply by bid
        usdt_end = eth * q3.bid * (1 - TAKER_FEE)

        profit_pct = (usdt_end - usdt_start) / usdt_start * 100

        if profit_pct > float(CONFIG.risk.min_profit_threshold_bps) / 100:
            self._last_signal_ts[key] = now
            entry_usdt = Decimal("5000")
            log.debug(
                "triangular.signal",
                exchange=exchange,
                triangle=triangle,
                profit_pct=float(profit_pct),
            )
            return TriangleSignal(
                exchange=exchange,
                legs=triangle,
                directions=("buy", "buy", "sell"),
                profit_pct=profit_pct,
                entry_usdt=entry_usdt,
                quote_age_ms=quote_age_ms,
            )

        # Reverse path: sell s3 (USDT→ETH), sell s2 (ETH→BTC), sell s1 (BTC→USDT)
        eth2 = usdt_start / q3.ask * (1 - TAKER_FEE)
        btc2 = eth2 * q2.bid * (1 - TAKER_FEE)
        usdt_end2 = btc2 * q1.bid * (1 - TAKER_FEE)
        profit_pct2 = (usdt_end2 - usdt_start) / usdt_start * 100

        if profit_pct2 > float(CONFIG.risk.min_profit_threshold_bps) / 100:
            self._last_signal_ts[key] = now
            log.debug(
                "triangular.signal_reverse",
                exchange=exchange,
                triangle=triangle,
                profit_pct=float(profit_pct2),
            )
            return TriangleSignal(
                exchange=exchange,
                legs=triangle,
                directions=("sell", "sell", "buy"),
                profit_pct=profit_pct2,
                entry_usdt=Decimal("5000"),
                quote_age_ms=quote_age_ms,
            )

        return None
