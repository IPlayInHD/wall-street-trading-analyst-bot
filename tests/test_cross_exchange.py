"""Tests for the cross-exchange arbitrage strategy."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from bot.market_data import Quote
from bot.strategies.cross_exchange import CrossExchangeArb


def _mock_feed(quotes_by_exchange):
    feed = MagicMock()
    feed.all_quotes_for_symbol.return_value = list(quotes_by_exchange.values())
    return feed


def _quote(exchange, symbol, bid, ask, bid_size=1, ask_size=1, ts=0.0):
    return Quote(
        exchange=exchange,
        symbol=symbol,
        bid=Decimal(str(bid)),
        ask=Decimal(str(ask)),
        bid_size=Decimal(str(bid_size)),
        ask_size=Decimal(str(ask_size)),
        ts=ts,
    )


def test_detects_arb_opportunity():
    # Binance ask=49900, Bybit bid=50050 → clear arb
    binance_q = _quote("binance", "BTC/USDT", bid=49850, ask=49900)
    bybit_q = _quote("bybit", "BTC/USDT", bid=50050, ask=50100)
    feed = _mock_feed({"binance": binance_q, "bybit": bybit_q})

    arb = CrossExchangeArb(feed, ["BTC/USDT"])
    intent = arb.on_quote(binance_q)
    assert intent is not None
    assert intent.exchange_buy == "binance"
    assert intent.exchange_sell == "bybit"
    assert intent.estimated_profit_usd > 0


def test_no_arb_when_spread_too_small():
    # Almost no spread — should not fire
    binance_q = _quote("binance", "BTC/USDT", bid=49990, ask=49995)
    bybit_q = _quote("bybit", "BTC/USDT", bid=49997, ask=50000)
    feed = _mock_feed({"binance": binance_q, "bybit": bybit_q})

    arb = CrossExchangeArb(feed, ["BTC/USDT"])
    intent = arb.on_quote(binance_q)
    assert intent is None


def test_no_arb_single_exchange():
    q = _quote("binance", "BTC/USDT", bid=50000, ask=50100)
    feed = MagicMock()
    feed.all_quotes_for_symbol.return_value = [q]
    arb = CrossExchangeArb(feed, ["BTC/USDT"])
    assert arb.on_quote(q) is None


def test_cooldown_prevents_duplicate_signals():
    binance_q = _quote("binance", "BTC/USDT", bid=49850, ask=49900)
    bybit_q = _quote("bybit", "BTC/USDT", bid=50050, ask=50100)
    feed = _mock_feed({"binance": binance_q, "bybit": bybit_q})

    arb = CrossExchangeArb(feed, ["BTC/USDT"])
    first = arb.on_quote(binance_q)
    second = arb.on_quote(binance_q)
    assert first is not None
    assert second is None  # cooldown suppressed it
