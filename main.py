#!/usr/bin/env python3
"""
Wall Street HFT Arbitrage Bot
──────────────────────────────
Paper trading mode — real live market data, simulated order fills.
No API keys required.

Usage:
    python main.py

Press Ctrl-C to stop and print session summary.
"""

import asyncio
import signal

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

from bot.engine import TradingEngine
from bot.logger import log

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║          WALL STREET HFT ARBITRAGE BOT  v1.0                ║
║                                                              ║
║  Mode    : PAPER TRADING (no API keys needed)               ║
║  Data    : Live WebSocket feeds — Binance, Bybit,           ║
║            Kraken, OKX                                      ║
║  Strategies:                                                 ║
║    ① Cross-Exchange Arbitrage                               ║
║    ② Triangular Arbitrage                                   ║
║    ③ Latency Arbitrage                                      ║
║    ④ Statistical Arbitrage (Pairs Trading)                  ║
║                                                              ║
║  Metrics : http://localhost:8000/metrics                    ║
║  Press Ctrl-C to stop and print session summary             ║
╚══════════════════════════════════════════════════════════════╝
"""


async def main() -> None:
    print(BANNER)
    engine = TradingEngine()
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _on_signal(sig: signal.Signals) -> None:
        log.info("main.shutdown_signal", signal=sig.name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _on_signal, sig)

    engine_task = asyncio.create_task(engine.start())

    await stop_event.wait()
    engine_task.cancel()
    try:
        await engine_task
    except asyncio.CancelledError:
        pass
    finally:
        await engine.stop()
        log.info("main.stopped")


if __name__ == "__main__":
    asyncio.run(main())
