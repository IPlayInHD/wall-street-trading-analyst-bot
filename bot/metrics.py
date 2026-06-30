"""
Prometheus metrics for the HFT bot.

Exposes a /metrics endpoint on port 8000 for Grafana scraping.
Tracks trade P&L, latency histograms, signal rates, and rejection counts
— the same KPIs a Wall Street risk desk monitors in real-time.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

TRADE_COUNT = Counter("hft_trades_total", "Total executed trades", ["strategy", "symbol"])
TRADE_PNL = Counter("hft_pnl_usd_total", "Cumulative realised P&L in USD", ["strategy"])
SIGNAL_COUNT = Counter("hft_signals_total", "Strategy signals generated", ["strategy"])
REJECTION_COUNT = Counter("hft_rejections_total", "Risk rejections", ["reason"])

ORDER_LATENCY = Histogram(
    "hft_order_latency_ms",
    "Order round-trip latency in milliseconds",
    ["exchange"],
    buckets=[5, 10, 25, 50, 100, 200, 500, 1000],
)

SPREAD_BPS = Gauge("hft_spread_bps", "Current bid-ask spread in bps", ["exchange", "symbol"])
POSITION_USD = Gauge("hft_position_usd", "Current open position in USD", ["symbol"])
DAILY_PNL = Gauge("hft_daily_pnl_usd", "Intraday realised P&L")
DRAWDOWN_PCT = Gauge("hft_drawdown_pct", "Current drawdown from peak equity")


def start_metrics_server(port: int = 8000) -> None:
    start_http_server(port)
