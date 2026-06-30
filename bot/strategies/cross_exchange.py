"""
Cross-Exchange Arbitrage Strategy
──────────────────────────────────
The most fundamental arb play on Wall Street: the same asset trades at
different prices on different venues simultaneously.

Algorithm:
  For every quote update on any exchange:
    1. Compare best-ask on cheapest exchange vs best-bid on most expensive.
    2. Net profit = sell_price − buy_price − fees − slippage.
    3. If net profit > MIN_PROFIT_THRESHOLD_BPS, fire a simultaneous
       buy (cheap venue) + sell (expensive venue).

Fee model uses maker/taker tier appropriate for institutional volume.
Slippage is pre-estimated from the local order-book snapshot.

Historical precedent: Getco, Virtu, and Citadel Securities ran this
strategy across equities in 2003-2010 capturing $0.001–0.003 per share
at millions of shares/day.  The crypto markets still offer wider gaps
due to fragmented liquidity.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Dict, List, Optional

from bot.config import CONFIG
from bot.logger import log
from bot.market_data import MarketDataFeed, Quote
from bot.risk import TradeIntent

# Taker fee in BPS for each supported exchange (institutional tier)
TAKER_FEE_BPS: Dict[str, Decimal] = {
    "binance": Decimal("7"),    # 0.07%
    "coinbase": Decimal("5"),   # 0.05%
    "kraken": Decimal("10"),    # 0.10%
    "bybit": Decimal("6"),      # 0.06%
    "okx": Decimal("8"),        # 0.08%
}

DEFAULT_ORDER_SIZE_USD = Decimal("5000")


class CrossExchangeArb:
    """
    Fires arbitrage intents when the same symbol misprices across venues.

    Designed to be called synchronously from the market-data callback
    path so latency is minimised — no async overhead in the hot path.
    """

    def __init__(self, feed: MarketDataFeed, symbols: List[str]) -> None:
        self._feed = feed
        self._symbols = symbols
        self._last_signal_ts: Dict[str, float] = {}
        # Minimum cool-down between signals for the same symbol (ms)
        self._cooldown_ms = 50

    def on_quote(self, quote: Quote) -> Optional[TradeIntent]:
        """Called for every incoming quote update.  Returns a TradeIntent or None."""
        symbol = quote.symbol
        now = time.time()
        last = self._last_signal_ts.get(symbol, 0.0)
        if (now - last) * 1000 < self._cooldown_ms:
            return None

        quotes = self._feed.all_quotes_for_symbol(symbol)
        if len(quotes) < 2:
            return None

        # Find the exchange with the lowest ask (where we'd buy)
        best_buy = min(quotes, key=lambda q: q.ask)
        # Find the exchange with the highest bid (where we'd sell)
        best_sell = max(quotes, key=lambda q: q.bid)

        if best_buy.exchange == best_sell.exchange:
            return None

        buy_fee = TAKER_FEE_BPS.get(best_buy.exchange, Decimal("10"))
        sell_fee = TAKER_FEE_BPS.get(best_sell.exchange, Decimal("10"))
        total_fees_bps = buy_fee + sell_fee

        gross_spread_bps = (best_sell.bid - best_buy.ask) / best_buy.ask * Decimal("10000")
        net_profit_bps = gross_spread_bps - total_fees_bps

        if net_profit_bps < CONFIG.risk.min_profit_threshold_bps:
            return None

        # Size the trade to the lesser of the two book sizes
        max_qty_usd = min(
            best_buy.ask_size * best_buy.ask,
            best_sell.bid_size * best_sell.bid,
            DEFAULT_ORDER_SIZE_USD,
        )
        if max_qty_usd < Decimal("100"):
            return None

        quantity = max_qty_usd / best_buy.ask
        profit_usd = quantity * (best_sell.bid - best_buy.ask) * (1 - total_fees_bps / Decimal("10000"))
        quote_age_ms = (now - min(best_buy.ts, best_sell.ts)) * 1000

        self._last_signal_ts[symbol] = now

        log.debug(
            "cross_exchange.signal",
            symbol=symbol,
            buy_ex=best_buy.exchange,
            sell_ex=best_sell.exchange,
            gross_bps=float(gross_spread_bps),
            net_bps=float(net_profit_bps),
            profit_usd=float(profit_usd),
        )

        return TradeIntent(
            strategy="cross_exchange_arb",
            exchange_buy=best_buy.exchange,
            exchange_sell=best_sell.exchange,
            symbol=symbol,
            quantity=quantity,
            expected_buy_price=best_buy.ask,
            expected_sell_price=best_sell.bid,
            estimated_profit_usd=profit_usd,
            estimated_slippage_bps=Decimal("1"),  # top-of-book IOC ≈ 0 slippage
            quote_age_ms=quote_age_ms,
        )
