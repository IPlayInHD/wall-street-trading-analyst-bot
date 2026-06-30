"""
Main trading engine — wires all components together.

Runs fully without API keys using real live market data from public
WebSocket feeds and the paper trading simulator for order execution.

Architecture:
  ┌─────────────────────────────────────────────────────┐
  │               MarketDataFeed (public WS)            │
  │  Binance ─┐                                         │
  │  Bybit   ─┼──► quote callbacks (µs, non-blocking)  │
  │  Kraken  ─┤                                         │
  │  OKX     ─┘                                         │
  └──────────────────────┬──────────────────────────────┘
                         │ Quote
               ┌─────────▼──────────┐
               │   Strategy Bus     │
               │  ① CrossExchange   │
               │  ② Triangular      │
               │  ③ LatencyArb      │
               │  ④ StatisticalArb  │
               └─────────┬──────────┘
                         │ TradeIntent / Signal
               ┌─────────▼──────────┐
               │    RiskEngine      │  7-layer pre-trade gate
               └─────────┬──────────┘
                         │ approved
               ┌─────────▼──────────┐
               │  PaperTrading      │  simulated fills on live prices
               │  Simulator         │
               └─────────┬──────────┘
                         │ SimulatedFill
               ┌─────────▼──────────┐
               │  P&L Ledger +      │
               │  Prometheus /metrics│
               └────────────────────┘
"""

from __future__ import annotations

import asyncio
import signal as _signal
from typing import List

from bot.config import CONFIG
from bot.exchange_factory import build_exchanges
from bot.logger import log
from bot.market_data import MarketDataFeed, Quote
from bot.metrics import (
    DAILY_PNL,
    DRAWDOWN_PCT,
    SIGNAL_COUNT,
    SPREAD_BPS,
    start_metrics_server,
)
from bot.risk import RiskEngine
from bot.simulator import PaperTradingSimulator
from bot.strategies.cross_exchange import CrossExchangeArb
from bot.strategies.latency_arb import LatencyArb
from bot.strategies.statistical import StatisticalArb
from bot.strategies.triangular import TriangularArb

DEFAULT_SYMBOLS: List[str] = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "ETH/BTC",
    "SOL/ETH",
    "BNB/BTC",
    "XRP/BTC",
]


class TradingEngine:
    def __init__(self, symbols: List[str] = DEFAULT_SYMBOLS) -> None:
        self._symbols = symbols
        self._risk = RiskEngine()
        self._feed: MarketDataFeed | None = None
        self._simulator: PaperTradingSimulator | None = None
        self._signal_queue: asyncio.Queue = asyncio.Queue(maxsize=2000)

        self._cross_arb: CrossExchangeArb | None = None
        self._tri_arb = TriangularArb()
        self._lat_arb = LatencyArb(symbols)
        self._stat_arbs: List[StatisticalArb] = []

    async def start(self) -> None:
        log.info("engine.starting", mode="paper_trading_live_data")
        start_metrics_server()

        exchanges = build_exchanges()
        self._feed = MarketDataFeed(exchanges, self._symbols)
        self._simulator = PaperTradingSimulator(self._feed, self._risk)
        self._cross_arb = CrossExchangeArb(self._feed, self._symbols)
        self._stat_arbs = [StatisticalArb(name) for name in exchanges]

        self._feed.subscribe(self._on_quote)
        await self._feed.start()

        log.info(
            "engine.live",
            exchanges=list(exchanges.keys()),
            symbols=self._symbols,
            note="Paper trading — watching live prices, no real orders",
        )

        await self._process_signals()

    async def stop(self) -> None:
        if self._feed:
            await self._feed.stop()
        if self._simulator:
            self._simulator.print_summary()

    def _on_quote(self, quote: Quote) -> None:
        SPREAD_BPS.labels(exchange=quote.exchange, symbol=quote.symbol).set(
            float(quote.spread_bps)
        )

        if CONFIG.enable_cross_exchange_arb and self._cross_arb:
            intent = self._cross_arb.on_quote(quote)
            if intent:
                SIGNAL_COUNT.labels(strategy="cross_exchange_arb").inc()
                self._try_enqueue(("intent", intent))

        if CONFIG.enable_triangular_arb:
            signal = self._tri_arb.on_quote(quote)
            if signal:
                SIGNAL_COUNT.labels(strategy="triangular_arb").inc()
                self._try_enqueue(("tri", signal))

        if CONFIG.enable_latency_arb:
            intent = self._lat_arb.on_quote(quote)
            if intent:
                SIGNAL_COUNT.labels(strategy="latency_arb").inc()
                self._try_enqueue(("intent", intent))

        if CONFIG.enable_statistical_arb:
            for stat in self._stat_arbs:
                for intent in stat.on_quote(quote):
                    SIGNAL_COUNT.labels(strategy="statistical_arb").inc()
                    self._try_enqueue(("intent", intent))

    def _try_enqueue(self, item) -> None:
        try:
            self._signal_queue.put_nowait(item)
        except asyncio.QueueFull:
            pass  # drop signal if queue is saturated — risk > throughput

    async def _process_signals(self) -> None:
        while True:
            try:
                item = await asyncio.wait_for(self._signal_queue.get(), timeout=1.0)
                sig_type, payload = item

                if sig_type == "intent":
                    await self._simulator.execute(payload)
                elif sig_type == "tri":
                    await self._execute_triangle(payload)

                DAILY_PNL.set(float(self._risk.state.realized_pnl))
                DRAWDOWN_PCT.set(float(self._risk.state.drawdown))
                self._signal_queue.task_done()

            except asyncio.TimeoutError:
                DAILY_PNL.set(float(self._risk.state.realized_pnl))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("engine.signal_error", error=str(exc))

    async def _execute_triangle(self, signal) -> None:
        """
        Simulate a triangular arb by constructing a synthetic TradeIntent
        for the first two legs (the system logs the opportunity).
        """
        from bot.risk import TradeIntent
        from decimal import Decimal

        feed = self._feed
        ex = signal.exchange
        legs = signal.legs
        dirs = signal.directions

        # Fetch live quotes for each leg
        q0 = feed.get_quote(ex, legs[0])
        q1 = feed.get_quote(ex, legs[1])
        q2 = feed.get_quote(ex, legs[2])
        if not (q0 and q1 and q2):
            return

        # For simulation, model the triangle as a cross-exchange intent
        # (buy leg-0, sell leg-2 in USDT terms) so the simulator can fill it
        buy_px = q0.ask if dirs[0] == "buy" else q0.bid
        sell_px = q2.bid if dirs[2] == "sell" else q2.ask
        qty = signal.entry_usdt / buy_px

        intent = TradeIntent(
            strategy="triangular_arb",
            exchange_buy=ex,
            exchange_sell=ex,
            symbol=legs[0],
            quantity=qty,
            expected_buy_price=buy_px,
            expected_sell_price=sell_px,
            estimated_profit_usd=signal.entry_usdt * signal.profit_pct / Decimal("100"),
            estimated_slippage_bps=Decimal("2"),
            quote_age_ms=signal.quote_age_ms,
        )
        await self._simulator.execute(intent)
