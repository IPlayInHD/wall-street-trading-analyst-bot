# Wall Street HFT Arbitrage Bot

A production-grade high-frequency trading system modelled on techniques
used by top-tier prop desks (Virtu, Citadel Securities, Jane Street, Jump
Trading). Runs four simultaneous arbitrage strategies across 5 global
crypto exchanges with a hard real-time risk management layer.

---

## Architecture

```
MarketDataFeed (WebSocket, per-exchange)
        │
        ▼  Quote (µs latency callback)
  ┌─────────────────────────┐
  │      Strategy Bus       │
  │  ① Cross-Exchange Arb  │  buy cheap venue / sell expensive venue
  │  ② Triangular Arb      │   3-leg intra-exchange cycle
  │  ③ Latency Arb         │  exploit stale quotes on slow feeds
  │  ④ Statistical Arb     │  mean-reversion on co-integrated pairs
  └──────────┬──────────────┘
             │ TradeIntent
        RiskEngine (pre-trade)
             │ approved
        ExecutionEngine (dual-leg IOC, <500 ms)
             │ fills
        RiskEngine.record_fill (P&L accounting)
             │
        Prometheus /metrics → Grafana
```

---

## Strategies

### ① Cross-Exchange Arbitrage
Buy the same asset on the cheapest exchange, simultaneously sell it on
the most expensive.  Net profit = spread − fees − slippage.  Minimum
threshold: 3 bps after fees.  Historical analogues: Getco (equities,
2003-2010), Virtu Financial (all asset classes).

### ② Triangular Arbitrage
Exploit circular mispricing across three currency pairs on the same
exchange (e.g. BTC/USDT → ETH/BTC → ETH/USDT → back to USDT).
Three sequential IOC orders.  Popularised by FX desks in the 1990s
(Deutsche Bank, Barclays Capital).

### ③ Latency Arbitrage
When a fast exchange updates its price, slower exchanges lag by 5–50 ms.
Buy on the stale (cheaper) venue, sell on the fast (updated) venue before
the slow exchange catches up.  Signature strategy of Renaissance
Technologies and modern HFT firms.

### ④ Statistical Arbitrage (Pairs Trading)
Long the underperforming leg / short the outperforming leg of a
historically co-integrated pair, betting on mean-reversion of the
log-price spread.  Enter when z-score > 2.0, exit when < 0.3.
Invented by Morgan Stanley's quant group (Tartaglia, 1987).

---

## Risk Controls

| Control | Value | Purpose |
|---------|-------|---------|
| Max position | $10,000 / trade | Limits single-trade exposure |
| Daily loss stop | $500 | Hard kill-switch on P&L |
| Max drawdown | 5% | Circuit-breaker from peak equity |
| Min profit threshold | 3 bps | Only trade if edge > fees |
| Max slippage | 5 bps | Abort if book is thin |
| Quote staleness | 200 ms | Reject stale data |
| Order timeout | 500 ms | IOC cancels if not filled |

All controls are enforced by `RiskEngine` — strategies cannot bypass them.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure credentials
cp .env.example .env
# Edit .env with your exchange API keys

# 3. Run in paper-trading mode (default, DRY_RUN=true)
python main.py

# 4. Monitor metrics
# Prometheus scrapes http://localhost:8000/metrics
# Import grafana/dashboard.json into Grafana
```

---

## Configuration

All settings are read from environment variables (`.env` file).

| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | `true` | Paper-trade only (no real orders) |
| `MAX_POSITION_USD` | `10000` | Max per-trade notional |
| `MAX_DAILY_LOSS_USD` | `500` | Daily loss hard stop |
| `MIN_PROFIT_THRESHOLD_BPS` | `3` | Minimum edge to trade |
| `ENABLE_CROSS_EXCHANGE_ARB` | `true` | Toggle strategy |
| `ENABLE_TRIANGULAR_ARB` | `true` | Toggle strategy |
| `ENABLE_STATISTICAL_ARB` | `true` | Toggle strategy |
| `ENABLE_LATENCY_ARB` | `true` | Toggle strategy |

---

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

---

## File Structure

```
bot/
  config.py          — centralised config from env vars
  logger.py          — structured async-safe logging
  market_data.py     — WebSocket feed aggregator
  order_book.py      — in-memory LOB with slippage simulation
  risk.py            — pre/post-trade risk engine
  execution.py       — dual-leg IOC order executor
  exchange_factory.py — ccxt.pro exchange builder
  metrics.py         — Prometheus instrumentation
  engine.py          — main orchestration loop
  strategies/
    cross_exchange.py — cross-venue price arb
    triangular.py    — 3-leg intra-exchange cycle arb
    statistical.py   — pairs trading / mean reversion
    latency_arb.py   — stale-quote exploitation
main.py              — entry point
tests/               — pytest unit tests
```

---

## Legal Notice

This software is for educational and research purposes.  Trading involves
substantial risk of loss.  Ensure compliance with exchange terms of service
and applicable regulations before live deployment.  Past performance of
quantitative strategies does not guarantee future results.
