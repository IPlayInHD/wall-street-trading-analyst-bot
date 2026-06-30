#!/usr/bin/env python3
"""
Wall Street HFT Arbitrage Bot — Entry Point

Usage:
    python main.py                    # live mode (reads .env)
    DRY_RUN=true python main.py       # paper-trading (default)

Press Ctrl-C to stop gracefully.
"""

import asyncio
import signal
import sys

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass  # uvloop optional but recommended for production

from bot.engine import TradingEngine
from bot.logger import log


async def main() -> None:
    engine = TradingEngine()
    loop = asyncio.get_running_loop()

    def _shutdown(sig: signal.Signals) -> None:
        log.info("main.shutdown_signal", signal=sig.name)
        loop.create_task(engine.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown, sig)

    try:
        await engine.start()
    except KeyboardInterrupt:
        pass
    finally:
        await engine.stop()
        log.info("main.stopped")


if __name__ == "__main__":
    asyncio.run(main())
