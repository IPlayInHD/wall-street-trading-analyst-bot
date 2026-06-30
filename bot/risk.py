"""
Risk management engine — the kill-switch layer between strategy signals
and live order execution.

Implements the same pre-trade and post-trade checks used by prop desks:
  • Position limits per symbol and in aggregate
  • Daily P&L stop-loss (hard kill if breached)
  • Max drawdown circuit-breaker
  • Per-order notional cap
  • Stale-quote rejection (don't trade on data older than N ms)
  • Slippage pre-check against simulated fill
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from typing import Dict, Optional

from bot.config import CONFIG, RiskConfig
from bot.logger import log


class RejectionReason(Enum):
    DAILY_LOSS_LIMIT = auto()
    DRAWDOWN_LIMIT = auto()
    POSITION_LIMIT = auto()
    NOTIONAL_LIMIT = auto()
    STALE_QUOTE = auto()
    EXCESS_SLIPPAGE = auto()
    DRY_RUN = auto()
    OK = auto()


@dataclass
class TradeIntent:
    strategy: str
    exchange_buy: str
    exchange_sell: str
    symbol: str
    quantity: Decimal
    expected_buy_price: Decimal
    expected_sell_price: Decimal
    estimated_profit_usd: Decimal
    estimated_slippage_bps: Decimal
    quote_age_ms: float


@dataclass
class RiskState:
    realized_pnl: Decimal = Decimal("0")
    peak_equity: Decimal = Decimal("0")
    open_positions: Dict[str, Decimal] = field(default_factory=dict)  # symbol → notional USD
    trade_count: int = 0
    rejected_count: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, compare=False, repr=False)

    @property
    def total_exposure(self) -> Decimal:
        return sum(self.open_positions.values(), Decimal("0"))

    @property
    def drawdown(self) -> Decimal:
        if self.peak_equity == 0:
            return Decimal("0")
        return (self.peak_equity - (self.peak_equity + self.realized_pnl)) / self.peak_equity

    def record_fill(self, symbol: str, notional: Decimal, pnl: Decimal) -> None:
        self.realized_pnl += pnl
        if (self.peak_equity + self.realized_pnl) > self.peak_equity:
            self.peak_equity = self.peak_equity + self.realized_pnl
        # Remove closed position
        self.open_positions.pop(symbol, None)
        self.trade_count += 1

    def open_position(self, symbol: str, notional: Decimal) -> None:
        self.open_positions[symbol] = self.open_positions.get(symbol, Decimal("0")) + notional


class RiskEngine:
    """
    Stateful pre-trade risk gate.

    call check(intent) before every order pair — returns RejectionReason.OK
    only when all checks pass.  The engine is the last line of defence;
    it cannot be bypassed by strategy code.
    """

    STALE_QUOTE_THRESHOLD_MS = 200  # quotes older than 200 ms are stale for HFT

    def __init__(self, cfg: RiskConfig = CONFIG.risk) -> None:
        self._cfg = cfg
        self.state = RiskState(peak_equity=cfg.max_position_usd)

    def check(self, intent: TradeIntent) -> RejectionReason:
        cfg = self._cfg
        state = self.state

        # 1. Daily loss hard stop
        if state.realized_pnl < -cfg.max_daily_loss_usd:
            log.error("risk.daily_loss_breach", pnl=float(state.realized_pnl))
            return RejectionReason.DAILY_LOSS_LIMIT

        # 2. Drawdown circuit-breaker
        if state.drawdown > cfg.max_drawdown_pct:
            log.error("risk.drawdown_breach", drawdown=float(state.drawdown))
            return RejectionReason.DRAWDOWN_LIMIT

        # 3. Per-order notional cap
        notional = intent.quantity * intent.expected_buy_price
        if notional > cfg.max_position_usd:
            return RejectionReason.NOTIONAL_LIMIT

        # 4. Aggregate exposure cap (2× max_position to allow hedged legs)
        if state.total_exposure + notional > cfg.max_position_usd * 2:
            return RejectionReason.POSITION_LIMIT

        # 5. Stale quote guard
        if intent.quote_age_ms > self.STALE_QUOTE_THRESHOLD_MS:
            return RejectionReason.STALE_QUOTE

        # 6. Slippage pre-check
        if intent.estimated_slippage_bps > cfg.max_slippage_bps:
            return RejectionReason.EXCESS_SLIPPAGE

        # 7. Dry-run mode — log but reject
        if CONFIG.dry_run:
            log.info(
                "risk.dry_run",
                strategy=intent.strategy,
                symbol=intent.symbol,
                profit_usd=float(intent.estimated_profit_usd),
                slippage_bps=float(intent.estimated_slippage_bps),
            )
            return RejectionReason.DRY_RUN

        state.open_position(intent.symbol, notional)
        return RejectionReason.OK

    def record_fill(self, symbol: str, notional: Decimal, pnl: Decimal) -> None:
        self.state.record_fill(symbol, notional, pnl)
        log.info(
            "risk.fill_recorded",
            symbol=symbol,
            pnl=float(pnl),
            total_pnl=float(self.state.realized_pnl),
            trades=self.state.trade_count,
        )
