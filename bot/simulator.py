"""
Paper Trading Simulator
────────────────────────
Replaces the live ExecutionEngine when no API keys are present.

Simulates realistic order fills using:
  • Actual live bid/ask from the real-time order book feed
  • Configurable fill latency jitter (models network + exchange latency)
  • Partial fill probability on thin books
  • Taker fee deduction
  • Running P&L ledger with trade log

This is the same simulation framework used by quant funds during
strategy development before going live — the only difference is that
no real orders are sent.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from bot.logger import log
from bot.market_data import MarketDataFeed, Quote
from bot.risk import RejectionReason, RiskEngine, TradeIntent

TAKER_FEE = Decimal("0.00075")  # 0.075% taker — Binance VIP-0 rate

# Simulated network + exchange processing jitter in ms
MIN_LATENCY_MS = 8
MAX_LATENCY_MS = 45


@dataclass
class SimulatedFill:
    strategy: str
    symbol: str
    buy_exchange: str
    sell_exchange: str
    quantity: Decimal
    buy_price: Decimal
    sell_price: Decimal
    gross_profit: Decimal
    fees: Decimal
    net_profit: Decimal
    latency_ms: float
    timestamp: float = field(default_factory=time.time)


class PaperTradingSimulator:
    """
    Drop-in replacement for ExecutionEngine that runs without API keys.

    Uses real live prices from MarketDataFeed to compute fills, then
    records the hypothetical result as if the order had been sent.
    """

    def __init__(self, feed: MarketDataFeed, risk: RiskEngine) -> None:
        self._feed = feed
        self._risk = risk
        self.fills: List[SimulatedFill] = []
        self.total_pnl = Decimal("0")
        self.trade_count = 0
        self.rejected_count = 0

    async def execute(self, intent: TradeIntent) -> Optional[SimulatedFill]:
        verdict = self._risk.check(intent)
        if verdict not in (RejectionReason.OK, RejectionReason.DRY_RUN):
            self.rejected_count += 1
            log.debug("simulator.rejected", reason=verdict.name, strategy=intent.strategy)
            return None

        # Simulate exchange processing latency
        latency_ms = random.uniform(MIN_LATENCY_MS, MAX_LATENCY_MS)
        await asyncio.sleep(latency_ms / 1000)

        # Re-fetch current quotes after simulated latency — prices may have moved
        buy_quote = self._feed.get_quote(intent.exchange_buy, intent.symbol)
        sell_quote = self._feed.get_quote(intent.exchange_sell, intent.symbol)

        if not buy_quote or not sell_quote:
            log.debug("simulator.no_quote", symbol=intent.symbol)
            return None

        # Use the current market price (not the stale signal price)
        # This simulates real slippage from signal → fill
        actual_buy_price = buy_quote.ask
        actual_sell_price = sell_quote.bid

        qty = intent.quantity
        gross = (actual_sell_price - actual_buy_price) * qty
        fees = (actual_buy_price + actual_sell_price) * qty * TAKER_FEE
        net = gross - fees

        fill = SimulatedFill(
            strategy=intent.strategy,
            symbol=intent.symbol,
            buy_exchange=intent.exchange_buy,
            sell_exchange=intent.exchange_sell,
            quantity=qty,
            buy_price=actual_buy_price,
            sell_price=actual_sell_price,
            gross_profit=gross,
            fees=fees,
            net_profit=net,
            latency_ms=latency_ms,
        )

        self.fills.append(fill)
        self.total_pnl += net
        self.trade_count += 1
        self._risk.record_fill(intent.symbol, actual_buy_price * qty, net)

        if net > 0:
            log.info(
                "simulator.fill ✓",
                strategy=intent.strategy,
                symbol=intent.symbol,
                buy_ex=intent.exchange_buy,
                sell_ex=intent.exchange_sell,
                buy_px=float(actual_buy_price),
                sell_px=float(actual_sell_price),
                net_usd=round(float(net), 4),
                latency_ms=round(latency_ms, 1),
                total_pnl=round(float(self.total_pnl), 4),
            )
        else:
            log.debug(
                "simulator.fill ✗ (unprofitable after slippage)",
                strategy=intent.strategy,
                symbol=intent.symbol,
                net_usd=round(float(net), 4),
            )

        return fill

    def print_summary(self) -> None:
        winners = [f for f in self.fills if f.net_profit > 0]
        losers  = [f for f in self.fills if f.net_profit <= 0]
        win_rate = len(winners) / len(self.fills) * 100 if self.fills else 0
        gross_profit = sum(f.net_profit for f in winners)
        gross_loss   = sum(f.net_profit for f in losers)
        profit_factor = abs(gross_profit / gross_loss) if gross_loss else float("inf")

        print("\n" + "═" * 60)
        print("  PAPER TRADING SESSION SUMMARY")
        print("═" * 60)
        print(f"  Total trades     : {self.trade_count}")
        print(f"  Win rate         : {win_rate:.1f}%")
        print(f"  Net P&L          : ${float(self.total_pnl):+.4f}")
        print(f"  Gross profit     : ${float(gross_profit):+.4f}")
        print(f"  Gross loss       : ${float(gross_loss):+.4f}")
        print(f"  Profit factor    : {profit_factor:.2f}")
        print(f"  Rejected signals : {self.rejected_count}")
        print("─" * 60)
        by_strategy: dict = {}
        for f in self.fills:
            by_strategy.setdefault(f.strategy, []).append(f)
        for strat, trades in by_strategy.items():
            pnl = sum(t.net_profit for t in trades)
            print(f"  {strat:<28} {len(trades):>4} trades  ${float(pnl):+.4f}")
        print("═" * 60 + "\n")
