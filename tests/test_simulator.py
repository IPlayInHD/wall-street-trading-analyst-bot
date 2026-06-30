"""Tests for the paper trading simulator."""

import asyncio
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from bot.market_data import Quote
from bot.risk import RiskEngine, TradeIntent
from bot.simulator import PaperTradingSimulator


def _make_feed(bid, ask, exchange="binance"):
    q = Quote(
        exchange=exchange,
        symbol="BTC/USDT",
        bid=Decimal(str(bid)),
        ask=Decimal(str(ask)),
        bid_size=Decimal("1"),
        ask_size=Decimal("1"),
    )
    feed = MagicMock()
    feed.get_quote.return_value = q
    return feed


def _intent(buy_price=49900, sell_price=50100, qty=Decimal("0.1")):
    return TradeIntent(
        strategy="test",
        exchange_buy="binance",
        exchange_sell="bybit",
        symbol="BTC/USDT",
        quantity=qty,
        expected_buy_price=Decimal(str(buy_price)),
        expected_sell_price=Decimal(str(sell_price)),
        estimated_profit_usd=Decimal("20"),
        estimated_slippage_bps=Decimal("1"),
        quote_age_ms=10.0,
    )


@pytest.mark.asyncio
async def test_profitable_fill():
    from bot.config import RiskConfig
    cfg = RiskConfig(
        max_position_usd=Decimal("10000"),
        max_daily_loss_usd=Decimal("500"),
        max_drawdown_pct=Decimal("0.05"),
        min_profit_threshold_bps=Decimal("3"),
        max_slippage_bps=Decimal("5"),
        order_timeout_ms=500,
        position_close_timeout_ms=2000,
    )
    import bot.risk as risk_module
    from dataclasses import replace
    live_cfg = replace(risk_module.CONFIG, dry_run=False)

    risk = RiskEngine(cfg)
    feed = _make_feed(bid=50100, ask=49900)
    sim = PaperTradingSimulator(feed, risk)

    import bot.risk as risk_module
    original = risk_module.CONFIG
    risk_module.CONFIG = replace(original, dry_run=False)
    try:
        fill = await sim.execute(_intent())
    finally:
        risk_module.CONFIG = original

    assert fill is not None
    assert sim.trade_count == 1
    # Net profit = (bid - ask) * qty - fees
    assert fill.buy_price == Decimal("49900")
    assert fill.sell_price == Decimal("50100")
    # net = gross (fees already netted by strategy signal filter)
    assert fill.net_profit > 0
    assert fill.fees == Decimal("0")


@pytest.mark.asyncio
async def test_no_quote_skips_fill():
    from bot.config import RiskConfig
    cfg = RiskConfig(
        max_position_usd=Decimal("10000"),
        max_daily_loss_usd=Decimal("500"),
        max_drawdown_pct=Decimal("0.05"),
        min_profit_threshold_bps=Decimal("3"),
        max_slippage_bps=Decimal("5"),
        order_timeout_ms=500,
        position_close_timeout_ms=2000,
    )
    risk = RiskEngine(cfg)
    feed = MagicMock()
    feed.get_quote.return_value = None
    sim = PaperTradingSimulator(feed, risk)

    import bot.risk as risk_module
    from dataclasses import replace
    original = risk_module.CONFIG
    risk_module.CONFIG = replace(original, dry_run=False)
    try:
        fill = await sim.execute(_intent())
    finally:
        risk_module.CONFIG = original

    assert fill is None
    assert sim.trade_count == 0


@pytest.mark.asyncio
async def test_pnl_accumulates():
    from bot.config import RiskConfig
    from dataclasses import replace
    import bot.risk as risk_module

    cfg = RiskConfig(
        max_position_usd=Decimal("10000"),
        max_daily_loss_usd=Decimal("500"),
        max_drawdown_pct=Decimal("0.05"),
        min_profit_threshold_bps=Decimal("3"),
        max_slippage_bps=Decimal("5"),
        order_timeout_ms=500,
        position_close_timeout_ms=2000,
    )
    risk = RiskEngine(cfg)
    feed = _make_feed(bid=50100, ask=49900)
    sim = PaperTradingSimulator(feed, risk)

    original = risk_module.CONFIG
    risk_module.CONFIG = replace(original, dry_run=False)
    try:
        await sim.execute(_intent())
        await sim.execute(_intent())
    finally:
        risk_module.CONFIG = original

    assert sim.trade_count == 2
    assert sim.total_pnl > 0
