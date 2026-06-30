"""
Exchange initialisation factory.

Builds ccxt.pro async exchange instances with institutional-grade settings:
  • rate limiting respect
  • WebSocket connection pooling
  • unified error handling
"""

from __future__ import annotations

from typing import Dict

import ccxt.pro as ccxtpro

from bot.config import CONFIG
from bot.logger import log


def build_exchanges() -> Dict[str, ccxtpro.Exchange]:
    """
    Instantiate all configured exchanges.  Exchanges with missing API keys
    are skipped (safe to run with partial credentials for testing).
    """
    candidates = {
        "binance": lambda: ccxtpro.binance({
            "apiKey": CONFIG.binance.api_key,
            "secret": CONFIG.binance.secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot", "adjustForTimeDifference": True},
        }),
        "coinbase": lambda: ccxtpro.coinbase({
            "apiKey": CONFIG.coinbase.api_key,
            "secret": CONFIG.coinbase.secret,
            "enableRateLimit": True,
        }),
        "kraken": lambda: ccxtpro.kraken({
            "apiKey": CONFIG.kraken.api_key,
            "secret": CONFIG.kraken.secret,
            "enableRateLimit": True,
        }),
        "bybit": lambda: ccxtpro.bybit({
            "apiKey": CONFIG.bybit.api_key,
            "secret": CONFIG.bybit.secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }),
        "okx": lambda: ccxtpro.okx({
            "apiKey": CONFIG.okx.api_key,
            "secret": CONFIG.okx.secret,
            "password": CONFIG.okx.passphrase,
            "enableRateLimit": True,
        }),
    }

    exchanges: Dict[str, ccxtpro.Exchange] = {}
    for name, factory in candidates.items():
        creds = getattr(CONFIG, name)
        if not creds.api_key:
            log.info("exchange_factory.skipped", exchange=name, reason="no_api_key")
            continue
        try:
            ex = factory()
            exchanges[name] = ex
            log.info("exchange_factory.connected", exchange=name)
        except Exception as exc:
            log.error("exchange_factory.error", exchange=name, error=str(exc))

    if not exchanges:
        # Fall back to public-feed-only mode (no trading, useful for testing)
        log.warning("exchange_factory.no_credentials", msg="Running in public-feed mode")
        for name in ("binance", "bybit"):
            ex = getattr(ccxtpro, name)({"enableRateLimit": True})
            exchanges[name] = ex

    return exchanges
