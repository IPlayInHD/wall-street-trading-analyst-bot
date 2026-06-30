"""Tests for the risk management engine."""

from decimal import Decimal

import pytest

from bot.config import RiskConfig
from bot.risk import RejectionReason, RiskEngine, TradeIntent


def _make_intent(**overrides) -> TradeIntent:
    base = dict(
        strategy="test",
        exchange_buy="binance",
        exchange_sell="bybit",
        symbol="BTC/USDT",
        quantity=Decimal("0.1"),
        expected_buy_price=Decimal("50000"),
        expected_sell_price=Decimal("50100"),
        estimated_profit_usd=Decimal("10"),
        estimated_slippage_bps=Decimal("1"),
        quote_age_ms=10.0,
    )
    base.update(overrides)
    return TradeIntent(**base)


@pytest.fixture()
def engine():
    cfg = RiskConfig(
        max_position_usd=Decimal("10000"),
        max_daily_loss_usd=Decimal("500"),
        max_drawdown_pct=Decimal("0.05"),
        min_profit_threshold_bps=Decimal("3"),
        max_slippage_bps=Decimal("5"),
        order_timeout_ms=500,
        position_close_timeout_ms=2000,
    )
    return RiskEngine(cfg)


def _live_engine():
    """Engine with dry_run=False for testing individual risk checks."""
    from bot.config import Config, RiskConfig
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
    eng = RiskEngine(cfg)
    return eng


def test_dry_run_rejection(monkeypatch):
    import bot.risk as risk_module
    # CONFIG is frozen; patch the module attribute that risk.check reads
    live_cfg = object.__new__(type(risk_module.CONFIG))
    object.__setattr__(live_cfg, "dry_run", True)
    monkeypatch.setattr(risk_module, "CONFIG", risk_module.CONFIG)
    engine = _live_engine()
    # dry_run defaults to True from env — should reject
    result = engine.check(_make_intent())
    assert result == RejectionReason.DRY_RUN


def test_stale_quote_rejection(monkeypatch):
    import bot.risk as risk_module
    from dataclasses import replace
    live_cfg = replace(risk_module.CONFIG, dry_run=False)
    monkeypatch.setattr(risk_module, "CONFIG", live_cfg)
    engine = _live_engine()
    result = engine.check(_make_intent(quote_age_ms=500.0))
    assert result == RejectionReason.STALE_QUOTE


def test_excess_slippage_rejection(monkeypatch):
    import bot.risk as risk_module
    from dataclasses import replace
    live_cfg = replace(risk_module.CONFIG, dry_run=False)
    monkeypatch.setattr(risk_module, "CONFIG", live_cfg)
    engine = _live_engine()
    result = engine.check(_make_intent(estimated_slippage_bps=Decimal("10")))
    assert result == RejectionReason.EXCESS_SLIPPAGE


def test_notional_limit(monkeypatch):
    import bot.risk as risk_module
    from dataclasses import replace
    live_cfg = replace(risk_module.CONFIG, dry_run=False)
    monkeypatch.setattr(risk_module, "CONFIG", live_cfg)
    engine = _live_engine()
    result = engine.check(_make_intent(quantity=Decimal("10")))
    assert result == RejectionReason.NOTIONAL_LIMIT


def test_daily_loss_limit(monkeypatch):
    import bot.risk as risk_module
    from dataclasses import replace
    live_cfg = replace(risk_module.CONFIG, dry_run=False)
    monkeypatch.setattr(risk_module, "CONFIG", live_cfg)
    engine = _live_engine()
    engine.state.realized_pnl = Decimal("-600")
    result = engine.check(_make_intent())
    assert result == RejectionReason.DAILY_LOSS_LIMIT


def test_ok_passes(monkeypatch):
    import bot.risk as risk_module
    from dataclasses import replace
    live_cfg = replace(risk_module.CONFIG, dry_run=False)
    monkeypatch.setattr(risk_module, "CONFIG", live_cfg)
    engine = _live_engine()
    result = engine.check(_make_intent())
    assert result == RejectionReason.OK


def test_fill_recording(engine):
    engine.record_fill("BTC/USDT", Decimal("5000"), Decimal("10"))
    assert engine.state.realized_pnl == Decimal("10")
    assert engine.state.trade_count == 1
