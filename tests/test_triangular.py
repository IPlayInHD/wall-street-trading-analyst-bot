"""Tests for the triangular arbitrage strategy."""

from decimal import Decimal

import pytest

from bot.market_data import Quote
from bot.strategies.triangular import TriangularArb


def _q(exchange, symbol, bid, ask, ts=0.0):
    return Quote(
        exchange=exchange,
        symbol=symbol,
        bid=Decimal(str(bid)),
        ask=Decimal(str(ask)),
        bid_size=Decimal("10"),
        ask_size=Decimal("10"),
        ts=ts,
    )


def test_no_signal_with_incomplete_triangle():
    arb = TriangularArb()
    # Only feed one quote — no triangle possible
    q = _q("binance", "BTC/USDT", 50000, 50010)
    result = arb.on_quote(q)
    assert result is None


def test_no_signal_when_no_profit(monkeypatch):
    monkeypatch.setattr("bot.strategies.triangular.TAKER_FEE", Decimal("0.001"))
    arb = TriangularArb()
    # Feed a full triangle with tight, fair prices
    arb.on_quote(_q("binance", "BTC/USDT", 50000, 50010))
    arb.on_quote(_q("binance", "ETH/BTC", Decimal("0.06"), Decimal("0.0601")))
    result = arb.on_quote(_q("binance", "ETH/USDT", 3000, 3001))
    # With fair prices, no triangle profit
    assert result is None


def test_signal_on_mispriced_triangle(monkeypatch):
    monkeypatch.setattr("bot.strategies.triangular.TAKER_FEE", Decimal("0.0001"))
    arb = TriangularArb()
    # Manufacture a clear opportunity: ETH/USDT bid is too high
    arb.on_quote(_q("binance", "BTC/USDT", 50000, 50001))
    arb.on_quote(_q("binance", "ETH/BTC", Decimal("0.06"), Decimal("0.0601")))
    # ETH/USDT should be ~3000 but bid is 3200 — arb opportunity
    result = arb.on_quote(_q("binance", "ETH/USDT", 3200, 3201))
    assert result is not None
    assert result.exchange == "binance"
    assert result.profit_pct > 0
