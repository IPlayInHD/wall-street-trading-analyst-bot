"""
Live web dashboard — served by the bot on http://localhost:8080

No external tools required. Opens in any browser.
Auto-refreshes every second via WebSocket push.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Set

from aiohttp import WSMsgType, web

if TYPE_CHECKING:
    from bot.simulator import PaperTradingSimulator
    from bot.risk import RiskEngine

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>HFT Arbitrage Bot — Live Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0a0e1a;
    color: #e0e6f0;
    font-family: 'Courier New', monospace;
    padding: 20px;
  }
  h1 {
    font-size: 1.3rem;
    color: #00e5ff;
    letter-spacing: 2px;
    margin-bottom: 20px;
    border-bottom: 1px solid #1e2d4a;
    padding-bottom: 10px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 14px;
    margin-bottom: 24px;
  }
  .card {
    background: #0f1829;
    border: 1px solid #1e2d4a;
    border-radius: 8px;
    padding: 16px;
  }
  .card .label {
    font-size: 0.68rem;
    color: #5a7090;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 6px;
  }
  .card .value {
    font-size: 1.6rem;
    font-weight: bold;
  }
  .green  { color: #00e676; }
  .red    { color: #ff5252; }
  .cyan   { color: #00e5ff; }
  .yellow { color: #ffd740; }
  .white  { color: #e0e6f0; }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
  }
  th {
    color: #5a7090;
    text-align: left;
    padding: 8px 10px;
    border-bottom: 1px solid #1e2d4a;
    font-size: 0.68rem;
    letter-spacing: 1px;
    text-transform: uppercase;
  }
  td {
    padding: 7px 10px;
    border-bottom: 1px solid #111c2e;
  }
  tr:hover td { background: #111c2e; }
  .section-title {
    font-size: 0.72rem;
    color: #5a7090;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 10px;
    margin-top: 4px;
  }
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.72rem;
  }
  .badge-green  { background: #003320; color: #00e676; }
  .badge-red    { background: #330000; color: #ff5252; }
  .badge-blue   { background: #001433; color: #00e5ff; }
  .badge-yellow { background: #332200; color: #ffd740; }
  #status {
    position: fixed; bottom: 14px; right: 20px;
    font-size: 0.7rem; color: #5a7090;
  }
  .pulse { animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  @media(max-width:700px){ .two-col{ grid-template-columns:1fr; } }
</style>
</head>
<body>
<h1>&#9650; WALL STREET HFT ARBITRAGE BOT &nbsp;|&nbsp; LIVE DASHBOARD</h1>

<div class="grid">
  <div class="card">
    <div class="label">Net P&amp;L (Session)</div>
    <div class="value" id="pnl">$0.00</div>
  </div>
  <div class="card">
    <div class="label">Total Trades</div>
    <div class="value cyan" id="trades">0</div>
  </div>
  <div class="card">
    <div class="label">Win Rate</div>
    <div class="value" id="winrate">—</div>
  </div>
  <div class="card">
    <div class="label">Profit Factor</div>
    <div class="value yellow" id="pf">—</div>
  </div>
  <div class="card">
    <div class="label">Signals Fired</div>
    <div class="value white" id="signals">0</div>
  </div>
  <div class="card">
    <div class="label">Rejected</div>
    <div class="value" id="rejected">0</div>
  </div>
  <div class="card">
    <div class="label">Drawdown</div>
    <div class="value" id="drawdown">0.00%</div>
  </div>
  <div class="card">
    <div class="label">Daily Loss Used</div>
    <div class="value" id="dloss">$0.00</div>
  </div>
</div>

<div class="two-col">
  <div>
    <div class="section-title">Strategy Breakdown</div>
    <table>
      <thead><tr><th>Strategy</th><th>Trades</th><th>P&amp;L</th></tr></thead>
      <tbody id="strat-body"></tbody>
    </table>
  </div>
  <div>
    <div class="section-title">Recent Fills</div>
    <table>
      <thead><tr><th>Symbol</th><th>Strategy</th><th>Net USD</th><th>Latency</th></tr></thead>
      <tbody id="fills-body"></tbody>
    </table>
  </div>
</div>

<div id="status"><span class="pulse">●</span> CONNECTING…</div>

<script>
const $ = id => document.getElementById(id);
const fmt = (n, d=2) => (n >= 0 ? '+' : '') + n.toFixed(d);
const fmtUSD = n => (n >= 0 ? '+$' : '-$') + Math.abs(n).toFixed(4);

function paint(d) {
  const pnl = d.total_pnl || 0;
  $('pnl').textContent = fmtUSD(pnl);
  $('pnl').className = 'value ' + (pnl >= 0 ? 'green' : 'red');

  $('trades').textContent = d.trade_count || 0;
  $('signals').textContent = d.signal_count || 0;
  $('rejected').textContent = d.rejected_count || 0;

  const wr = d.win_rate;
  $('winrate').textContent = wr != null ? wr.toFixed(1) + '%' : '—';
  $('winrate').className = 'value ' + (wr >= 60 ? 'green' : wr >= 40 ? 'yellow' : 'red');

  const pf = d.profit_factor;
  $('pf').textContent = pf != null ? (pf === 9999 ? '∞' : pf.toFixed(2)) : '—';

  const dd = (d.drawdown || 0) * 100;
  $('drawdown').textContent = dd.toFixed(2) + '%';
  $('drawdown').className = 'value ' + (dd < 2 ? 'green' : dd < 4 ? 'yellow' : 'red');

  const dl = Math.abs(d.realized_pnl_negative || 0);
  $('dloss').textContent = '$' + dl.toFixed(2);

  // Strategy breakdown
  const sb = $('strat-body');
  sb.innerHTML = '';
  for (const [strat, info] of Object.entries(d.by_strategy || {})) {
    const cls = info.pnl >= 0 ? 'green' : 'red';
    sb.innerHTML += `<tr>
      <td><span class="badge badge-blue">${strat.replace('_arb','').replace('_',' ')}</span></td>
      <td>${info.count}</td>
      <td class="${cls}">${fmtUSD(info.pnl)}</td>
    </tr>`;
  }
  if (!Object.keys(d.by_strategy || {}).length) {
    sb.innerHTML = '<tr><td colspan="3" style="color:#5a7090;padding:12px">Waiting for first trade…</td></tr>';
  }

  // Recent fills
  const fb = $('fills-body');
  fb.innerHTML = '';
  for (const f of (d.recent_fills || []).slice().reverse()) {
    const cls = f.net > 0 ? 'green' : 'red';
    const badge = f.net > 0 ? 'badge-green' : 'badge-red';
    fb.innerHTML += `<tr>
      <td>${f.symbol}</td>
      <td><span class="badge badge-blue">${f.strategy.replace('_arb','').replace('_',' ')}</span></td>
      <td class="${cls}">${fmtUSD(f.net)}</td>
      <td style="color:#5a7090">${f.latency.toFixed(0)}ms</td>
    </tr>`;
  }
  if (!(d.recent_fills || []).length) {
    fb.innerHTML = '<tr><td colspan="4" style="color:#5a7090;padding:12px">Waiting for fills…</td></tr>';
  }

  $('status').innerHTML = '<span style="color:#00e676">●</span> LIVE &nbsp;' + new Date().toLocaleTimeString();
}

function connect() {
  const ws = new WebSocket('ws://' + location.host + '/ws');
  ws.onmessage = e => paint(JSON.parse(e.data));
  ws.onclose = () => {
    $('status').innerHTML = '<span class="pulse" style="color:#ff5252">●</span> RECONNECTING…';
    setTimeout(connect, 2000);
  };
}
connect();
</script>
</body>
</html>
"""

RECENT_FILLS_MAX = 50


class Dashboard:
    def __init__(self, simulator: "PaperTradingSimulator", risk: "RiskEngine") -> None:
        self._sim = simulator
        self._risk = risk
        self._clients: Set[web.WebSocketResponse] = set()
        self._signal_count = 0
        self._app = web.Application()
        self._app.router.add_get("/", self._index)
        self._app.router.add_get("/ws", self._ws_handler)

    def increment_signal(self) -> None:
        self._signal_count += 1

    async def start(self, port: int = 8080) -> None:
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        asyncio.create_task(self._broadcast_loop())

    async def _index(self, request: web.Request) -> web.Response:
        return web.Response(text=HTML, content_type="text/html")

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._clients.add(ws)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            self._clients.discard(ws)
        return ws

    async def _broadcast_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            if not self._clients:
                continue
            payload = self._build_payload()
            data = json.dumps(payload)
            dead = set()
            for ws in self._clients:
                try:
                    await ws.send_str(data)
                except Exception:
                    dead.add(ws)
            self._clients -= dead

    def _build_payload(self) -> dict:
        sim = self._sim
        fills = sim.fills

        winners = [f for f in fills if f.net_profit > 0]
        losers  = [f for f in fills if f.net_profit <= 0]
        win_rate = len(winners) / len(fills) * 100 if fills else None
        gross_profit = sum(float(f.net_profit) for f in winners)
        gross_loss   = sum(float(f.net_profit) for f in losers)
        pf = abs(gross_profit / gross_loss) if gross_loss else (9999 if gross_profit > 0 else None)

        by_strategy: dict = {}
        for f in fills:
            s = f.strategy
            by_strategy.setdefault(s, {"count": 0, "pnl": 0.0})
            by_strategy[s]["count"] += 1
            by_strategy[s]["pnl"] += float(f.net_profit)

        recent = [
            {
                "symbol": f.symbol,
                "strategy": f.strategy,
                "net": float(f.net_profit),
                "latency": f.latency_ms,
            }
            for f in fills[-RECENT_FILLS_MAX:]
        ]

        risk_state = self._risk.state
        return {
            "total_pnl": float(sim.total_pnl),
            "trade_count": sim.trade_count,
            "rejected_count": sim.rejected_count,
            "signal_count": self._signal_count,
            "win_rate": win_rate,
            "profit_factor": pf,
            "drawdown": float(risk_state.drawdown),
            "realized_pnl_negative": float(min(risk_state.realized_pnl, 0)),
            "by_strategy": by_strategy,
            "recent_fills": recent,
        }
