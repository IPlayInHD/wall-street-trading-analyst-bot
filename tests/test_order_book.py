"""Tests for the local order-book simulation."""

from decimal import Decimal

import pytest

from bot.order_book import LocalOrderBook, Side


def test_side_best_ask():
    s = Side(ascending=True)
    s.update(Decimal("100"), Decimal("1"))
    s.update(Decimal("99"), Decimal("2"))
    assert s.best() == (Decimal("99"), Decimal("2"))


def test_side_best_bid():
    s = Side(ascending=False)
    s.update(Decimal("100"), Decimal("1"))
    s.update(Decimal("101"), Decimal("2"))
    assert s.best() == (Decimal("101"), Decimal("2"))


def test_side_simulate_fill():
    s = Side(ascending=True)
    s.update(Decimal("100"), Decimal("1"))
    s.update(Decimal("101"), Decimal("1"))
    avg, filled = s.simulate_fill(Decimal("1.5"))
    assert filled == Decimal("1.5")
    # avg = (1*100 + 0.5*101) / 1.5 = 150.5/1.5 ≈ 100.333...
    assert abs(avg - Decimal("100.3333333333")) < Decimal("0.0001")


def test_side_remove():
    s = Side(ascending=True)
    s.update(Decimal("100"), Decimal("1"))
    s.update(Decimal("101"), Decimal("1"))
    s.remove(Decimal("100"))
    assert len(s) == 1
    assert s.best()[0] == Decimal("101")


def test_local_order_book_spread():
    book = LocalOrderBook("binance", "BTC/USDT")
    book.apply_snapshot(
        bids=[(49900, 0.5), (49800, 1.0)],
        asks=[(50000, 0.5), (50100, 1.0)],
    )
    assert book.spread() == Decimal("100")
    assert book.mid() == Decimal("49950")


def test_slippage_estimation():
    book = LocalOrderBook("binance", "BTC/USDT")
    book.apply_snapshot(
        bids=[(49900, 0.1), (49800, 1.0)],
        asks=[(50000, 0.1), (50100, 1.0)],
    )
    slip = book.slippage_bps("buy", Decimal("0.5"))
    assert slip is not None
    assert slip > 0
