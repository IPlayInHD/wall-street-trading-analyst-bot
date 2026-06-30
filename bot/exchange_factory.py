"""
Exchange initialisation factory.

No API keys required.  All exchanges connect via public WebSocket feeds
for real live market data.  Order execution is handled by the paper
trading simulator (bot/simulator.py).
"""

from __future__ import annotations

from typing import Dict

import ccxt.pro as ccxtpro

from bot.logger import log

# All exchanges supported via unauthenticated public order-book feeds
PUBLIC_EXCHANGES = {
    "binance": lambda: ccxtpro.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}}),
    "bybit":   lambda: ccxtpro.bybit({"enableRateLimit": True, "options": {"defaultType": "spot"}}),
    "kraken":  lambda: ccxtpro.kraken({"enableRateLimit": True}),
    "okx":     lambda: ccxtpro.okx({"enableRateLimit": True}),
}


def build_exchanges() -> Dict[str, ccxtpro.Exchange]:
    """
    Connect to all exchanges using public feeds only.
    No API keys are required — market data is free and unauthenticated.
    """
    exchanges: Dict[str, ccxtpro.Exchange] = {}
    for name, factory in PUBLIC_EXCHANGES.items():
        try:
            ex = factory()
            exchanges[name] = ex
            log.info("exchange_factory.connected", exchange=name, mode="public_feed")
        except Exception as exc:
            log.warning("exchange_factory.skip", exchange=name, error=str(exc))

    log.info(
        "exchange_factory.ready",
        count=len(exchanges),
        exchanges=list(exchanges.keys()),
        note="Paper trading mode — no API keys needed",
    )
    return exchanges
