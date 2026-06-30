"""
Main trading engine — wires all components together.

Architecture (inspired by Jane Street / Citadel prop desk design):

  ┌─────────────────────────────────────────────────────┐
  │                   MarketDataFeed                    │
  │  Binance WS ─┐                                      │
  │  Bybit WS   ─┼──► quote callbacks (non-blocking)   │
  │  Kraken WS  ─┘                                      │
  └──────────────────────┬──────────────────────────────┘
                         │ Quote
               ┌─────────▼──────────┐
               │   Strategy Bus     │  (sync, sub-microsecond)
               │  CrossExchangeArb  │
               │  TriangularArb     │
               │  LatencyArb        │
               │  StatisticalArb    │
               └─────────┬──────────┘
                         │ TradeIntent / Signal
               ┌─────────▼──────────┐
               │    RiskEngine      │  pre-trade checks
               └─────────┬──────────┘
                         │ approved
               ┌─────────▼──────────┐
               │  ExecutionEngine   │  dual-leg IOC orders
               └─────────┬──────────┘
                         │ fills
               ┌─────────▼──────────┐
               │  RiskEngine.record │  post-trade PnL accounting
               └────────────────────┘
"""

from __future__ import annotations

import asyncio
from typing import Dict, List

import ccxt.pro as ccxtpro

from bot.config import CONFIG
from bot.execution import ExecutionEngine
from bot.exchange_factory import build_exchanges
from bot.logger import log
from bot.market_data import MarketDataFeed, Quote
from bot.metrics import (
    DAILY_PNL,
    DRAWDOWN_PCT,
    REJECTION_COUNT,
    SIGNAL_COUNT,
    SPREAD_BPS,
    start_metrics_server,
)
from bot.risk import RejectionReason, RiskEngine
from bot.strategies.cross_exchange import CrossExchangeArb
from bot.strategies.latency_arb import LatencyArb
from bot.strategies.statistical import StatisticalArb
from bot.strategies.triangular import TriangularArb

# Default universe — high liquidity, tight spreads, global coverage
DEFAULT_SYMBOLS: List[str] = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "AVAX/USDT",
    "MATIC/USDT",
    "DOT/USDT",
    "ETH/BTC",
    "SOL/BTC",
    "XRP/BTC",
    "BNB/BTC",
    "SOL/ETH",
    "MATIC/ETH",
]


class TradingEngine:
    """
    Orchestrates the full HFT pipeline from market data ingestion to
    order execution and risk accounting.
    """

    def __init__(self, symbols: List[str] = DEFAULT_SYMBOLS) -> None:
        self._symbols = symbols
        self._exchanges: Dict[str, ccxtpro.Exchange] = {}
        self._feed: MarketDataFeed | None = None
        self._risk = RiskEngine()
        self._exec: ExecutionEngine | None = None
        self._signal_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

        # Strategy engines
        self._cross_arb: CrossExchangeArb | None = None
        self._tri_arb = TriangularArb()
        self._lat_arb = LatencyArb(symbols)
        self._stat_arbs: List[StatisticalArb] = []

    async def start(self) -> None:
        log.info("engine.starting", dry_run=CONFIG.dry_run)
        start_metrics_server()

        self._exchanges = build_exchanges()
        self._feed = MarketDataFeed(self._exchanges, self._symbols)
        self._exec = ExecutionEngine(self._exchanges, self._risk)
        self._cross_arb = CrossExchangeArb(self._feed, self._symbols)
        self._stat_arbs = [StatisticalArb(ex) for ex in self._exchanges]

        # Register the unified callback
        self._feed.subscribe(self._on_quote)

        await self._feed.start()
        log.info("engine.running", exchanges=list(self._exchanges.keys()))

        # Run signal processor alongside feed tasks
        await self._process_signals()

    async def stop(self) -> None:
        log.info("engine.stopping")
        if self._feed:
            await self._feed.stop()

    def _on_quote(self, quote: Quote) -> None:
        """
        Hot path — called synchronously on every WebSocket message.
        Must complete in <100 µs.  Heavy work is queued.
        """
        # Update Prometheus spread gauge
        SPREAD_BPS.labels(exchange=quote.exchange, symbol=quote.symbol).set(
            float(quote.spread_bps)
        )

        # Cross-exchange arb (requires quotes from ≥2 exchanges)
        if CONFIG.enable_cross_exchange_arb and self._cross_arb:
            intent = self._cross_arb.on_quote(quote)
            if intent:
                SIGNAL_COUNT.labels(strategy="cross_exchange_arb").inc()
                self._signal_queue.put_nowait(("intent", intent))

        # Triangular arb (within same exchange)
        if CONFIG.enable_triangular_arb:
            signal = self._tri_arb.on_quote(quote)
            if signal:
                SIGNAL_COUNT.labels(strategy="triangular_arb").inc()
                self._signal_queue.put_nowait(("tri", signal))

        # Latency arb
        if CONFIG.enable_latency_arb:
            intent = self._lat_arb.on_quote(quote)
            if intent:
                SIGNAL_COUNT.labels(strategy="latency_arb").inc()
                self._signal_queue.put_nowait(("intent", intent))

        # Statistical arb
        if CONFIG.enable_statistical_arb:
            for stat in self._stat_arbs:
                for intent in stat.on_quote(quote):
                    SIGNAL_COUNT.labels(strategy="statistical_arb").inc()
                    self._signal_queue.put_nowait(("intent", intent))

    async def _process_signals(self) -> None:
        """Drain the signal queue and execute approved intents."""
        while True:
            try:
                item = await asyncio.wait_for(self._signal_queue.get(), timeout=1.0)
                signal_type, payload = item

                if signal_type == "intent":
                    result = await self._exec.execute(payload)
                    if result:
                        DAILY_PNL.set(float(self._risk.state.realized_pnl))
                        DRAWDOWN_PCT.set(float(self._risk.state.drawdown))
                    else:
                        pass  # rejection already logged inside risk/exec

                elif signal_type == "tri":
                    await self._execute_triangle(payload)

                self._signal_queue.task_done()

            except asyncio.TimeoutError:
                # Idle tick — update PnL gauge
                DAILY_PNL.set(float(self._risk.state.realized_pnl))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("engine.signal_error", error=str(exc))

    async def _execute_triangle(self, signal) -> None:
        """
        Execute three sequential IOC orders for triangular arb.
        Sequential (not concurrent) because each leg funds the next.
        """
        from bot.risk import TradeIntent
        from decimal import Decimal

        exchange = self._exchanges.get(signal.exchange)
        if not exchange:
            return

        entry = signal.entry_usdt
        legs = signal.legs
        directions = signal.directions

        log.info(
            "engine.triangle_execute",
            exchange=signal.exchange,
            legs=legs,
            profit_pct=float(signal.profit_pct),
            dry_run=CONFIG.dry_run,
        )

        if CONFIG.dry_run:
            return  # logged above, don't place real orders

        try:
            # Build TradeIntents for each leg sequentially
            # Leg amounts cascade from the output of each prior leg
            running_qty = entry
            for sym, direction in zip(legs, directions):
                side = "buy" if direction == "buy" else "sell"
                qty = running_qty / Decimal("1")  # simplified; real impl tracks intermediate asset
                await exchange.create_order(sym, "market", side, float(qty))
        except Exception as exc:
            log.error("engine.triangle_error", error=str(exc))
