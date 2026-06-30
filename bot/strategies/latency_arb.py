"""
Latency Arbitrage Strategy
───────────────────────────
One of the most profitable—and controversial—HFT strategies.  Pioneered
by firms like Jump Trading and IMC in the early 2010s.

Core insight: some exchanges update their prices faster than others.
When exchange A's price moves, exchange B's stale quote creates a
momentary, near-riskless trade:
  • If A's ask just jumped UP, B's ask is still low → buy on B, sell on A.
  • If A's bid just dropped DOWN, B's bid is still high → sell on B, buy on A.

The "latency" being exploited is the propagation delay between venues,
typically 1–50 ms in crypto (much shorter in equities co-location).

Implementation:
  1. Maintain a price-update timestamp per exchange.
  2. When a fast feed updates, mark other feeds as "potentially stale."
  3. Compute cross-venue edge using the stale price.
  4. Fire the trade immediately — the window is measured in milliseconds.

Risk: the stale price may already be updating.  Cooldown guards prevent
over-firing on the same staleness event.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from bot.config import CONFIG
from bot.logger import log
from bot.market_data import Quote
from bot.risk import TradeIntent

# How stale a quote must be (in ms) before we consider it exploitable
STALE_THRESHOLD_MS = 15.0
# Maximum age of the "fast" quote — if it's old, the edge may have closed
FAST_QUOTE_MAX_AGE_MS = 5.0
# How long to suppress signals for the same (symbol, exchange_pair) after firing
COOLDOWN_MS = 200.0
# Min edge in BPS after fees to bother trading
MIN_EDGE_BPS = Decimal("4")


@dataclass
class QuoteRecord:
    quote: Quote
    received_at: float  # wall-clock time we received it


class LatencyArb:
    """
    Detects and exploits stale quotes on slower exchanges.

    Must receive quotes from ALL exchanges to function — it compares
    update recency across venues for the same symbol.
    """

    def __init__(self, symbols: List[str]) -> None:
        self._symbols = symbols
        # (exchange, symbol) → most recent QuoteRecord
        self._latest: Dict[Tuple[str, str], QuoteRecord] = {}
        # (symbol, fast_ex, slow_ex) → last fire time
        self._last_fire: Dict[Tuple[str, str, str], float] = defaultdict(float)

    def on_quote(self, quote: Quote) -> Optional[TradeIntent]:
        now = time.time()
        key = (quote.exchange, quote.symbol)
        self._latest[key] = QuoteRecord(quote=quote, received_at=now)
        return self._scan(quote.symbol, now)

    def _scan(self, symbol: str, now: float) -> Optional[TradeIntent]:
        records: List[QuoteRecord] = []
        for ex in self._exchange_names():
            rec = self._latest.get((ex, symbol))
            if rec:
                records.append(rec)

        if len(records) < 2:
            return None

        # Identify the freshest quote as the "price leader"
        records.sort(key=lambda r: r.received_at, reverse=True)
        fast = records[0]

        if (now - fast.received_at) * 1000 > FAST_QUOTE_MAX_AGE_MS:
            return None

        for slow_rec in records[1:]:
            age_ms = (now - slow_rec.received_at) * 1000
            if age_ms < STALE_THRESHOLD_MS:
                continue

            fast_q = fast.quote
            slow_q = slow_rec.quote

            cooldown_key = (symbol, fast_q.exchange, slow_q.exchange)
            if (now - self._last_fire[cooldown_key]) * 1000 < COOLDOWN_MS:
                continue

            intent = self._compute_edge(fast_q, slow_q, age_ms)
            if intent:
                self._last_fire[cooldown_key] = now
                return intent

        return None

    def _compute_edge(
        self, fast: Quote, slow: Quote, slow_age_ms: float
    ) -> Optional[TradeIntent]:
        """
        Fast exchange moved up → slow exchange ask still low → buy on slow, sell on fast.
        Fast exchange moved down → slow exchange bid still high → sell on slow, buy on fast.
        """
        # Case 1: fast ask > slow ask  (fast moved up, slow is cheap)
        if fast.ask > slow.ask:
            spread_bps = (fast.bid - slow.ask) / slow.ask * Decimal("10000")
            fees_bps = Decimal("14")  # ~0.07% + 0.07%
            edge_bps = spread_bps - fees_bps
            if edge_bps < MIN_EDGE_BPS:
                return None
            qty_usd = min(slow.ask_size * slow.ask, fast.bid_size * fast.bid, Decimal("4000"))
            if qty_usd < Decimal("100"):
                return None
            qty = qty_usd / slow.ask
            profit = qty * (fast.bid - slow.ask) * (1 - Decimal("0.0014"))
            log.debug(
                "latency_arb.signal",
                symbol=fast.symbol,
                buy_ex=slow.exchange,
                sell_ex=fast.exchange,
                edge_bps=float(edge_bps),
                slow_age_ms=round(slow_age_ms, 1),
            )
            return TradeIntent(
                strategy="latency_arb",
                exchange_buy=slow.exchange,
                exchange_sell=fast.exchange,
                symbol=fast.symbol,
                quantity=qty,
                expected_buy_price=slow.ask,
                expected_sell_price=fast.bid,
                estimated_profit_usd=profit,
                estimated_slippage_bps=Decimal("1"),
                quote_age_ms=slow_age_ms,
            )

        # Case 2: fast bid < slow bid  (fast moved down, slow bid still elevated)
        if fast.bid < slow.bid:
            spread_bps = (slow.bid - fast.ask) / fast.ask * Decimal("10000")
            fees_bps = Decimal("14")
            edge_bps = spread_bps - fees_bps
            if edge_bps < MIN_EDGE_BPS:
                return None
            qty_usd = min(fast.ask_size * fast.ask, slow.bid_size * slow.bid, Decimal("4000"))
            if qty_usd < Decimal("100"):
                return None
            qty = qty_usd / fast.ask
            profit = qty * (slow.bid - fast.ask) * (1 - Decimal("0.0014"))
            log.debug(
                "latency_arb.signal_reverse",
                symbol=fast.symbol,
                buy_ex=fast.exchange,
                sell_ex=slow.exchange,
                edge_bps=float(edge_bps),
                slow_age_ms=round(slow_age_ms, 1),
            )
            return TradeIntent(
                strategy="latency_arb",
                exchange_buy=fast.exchange,
                exchange_sell=slow.exchange,
                symbol=fast.symbol,
                quantity=qty,
                expected_buy_price=fast.ask,
                expected_sell_price=slow.bid,
                estimated_profit_usd=profit,
                estimated_slippage_bps=Decimal("1"),
                quote_age_ms=slow_age_ms,
            )

        return None

    def _exchange_names(self) -> List[str]:
        seen = set()
        names = []
        for ex, _ in self._latest:
            if ex not in seen:
                seen.add(ex)
                names.append(ex)
        return names
