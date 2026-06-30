"""Central configuration — loaded once at startup from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _bool(key: str, default: bool = False) -> bool:
    return _env(key, str(default)).lower() in ("1", "true", "yes")


def _decimal(key: str, default: str = "0") -> Decimal:
    return Decimal(_env(key, default))


@dataclass(frozen=True)
class ExchangeCreds:
    api_key: str
    secret: str
    passphrase: str = ""


@dataclass(frozen=True)
class RiskConfig:
    max_position_usd: Decimal = field(default_factory=lambda: _decimal("MAX_POSITION_USD", "10000"))
    max_daily_loss_usd: Decimal = field(default_factory=lambda: _decimal("MAX_DAILY_LOSS_USD", "500"))
    max_drawdown_pct: Decimal = field(default_factory=lambda: _decimal("MAX_DRAWDOWN_PCT", "0.05"))
    min_profit_threshold_bps: Decimal = field(default_factory=lambda: _decimal("MIN_PROFIT_THRESHOLD_BPS", "15"))
    max_slippage_bps: Decimal = field(default_factory=lambda: _decimal("MAX_SLIPPAGE_BPS", "8"))
    order_timeout_ms: int = int(_env("ORDER_TIMEOUT_MS", "500"))
    position_close_timeout_ms: int = int(_env("POSITION_CLOSE_TIMEOUT_MS", "2000"))


@dataclass(frozen=True)
class Config:
    # Exchanges
    binance: ExchangeCreds = field(default_factory=lambda: ExchangeCreds(
        api_key=_env("BINANCE_API_KEY"),
        secret=_env("BINANCE_SECRET"),
    ))
    coinbase: ExchangeCreds = field(default_factory=lambda: ExchangeCreds(
        api_key=_env("COINBASE_API_KEY"),
        secret=_env("COINBASE_SECRET"),
    ))
    kraken: ExchangeCreds = field(default_factory=lambda: ExchangeCreds(
        api_key=_env("KRAKEN_API_KEY"),
        secret=_env("KRAKEN_SECRET"),
    ))
    bybit: ExchangeCreds = field(default_factory=lambda: ExchangeCreds(
        api_key=_env("BYBIT_API_KEY"),
        secret=_env("BYBIT_SECRET"),
    ))
    okx: ExchangeCreds = field(default_factory=lambda: ExchangeCreds(
        api_key=_env("OKX_API_KEY"),
        secret=_env("OKX_SECRET"),
        passphrase=_env("OKX_PASSPHRASE"),
    ))

    # Infrastructure
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379/0"))

    # Risk
    risk: RiskConfig = field(default_factory=RiskConfig)

    # Strategy toggles
    enable_cross_exchange_arb: bool = field(default_factory=lambda: _bool("ENABLE_CROSS_EXCHANGE_ARB", True))
    enable_triangular_arb: bool = field(default_factory=lambda: _bool("ENABLE_TRIANGULAR_ARB", True))
    enable_statistical_arb: bool = field(default_factory=lambda: _bool("ENABLE_STATISTICAL_ARB", True))
    enable_latency_arb: bool = field(default_factory=lambda: _bool("ENABLE_LATENCY_ARB", True))

    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", True))


CONFIG = Config()
