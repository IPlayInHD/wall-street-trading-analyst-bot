"""
Statistical Arbitrage (Pairs Trading) Strategy
────────────────────────────────────────────────
Popularised by Morgan Stanley's quant desk in the 1980s (Nunzio Tartaglia's
team), stat-arb exploits mean-reversion between historically co-integrated
assets.  Unlike pure arbitrage, it carries model risk but offers much
higher capacity.

Methodology:
  1. Maintain a rolling z-score of the log-price spread between a pair.
  2. When z-score > ENTRY_Z (spread has widened abnormally), go long the
     cheap leg and short the expensive one — betting on convergence.
  3. Exit when z-score reverts toward 0 (or stop-loss if it widens further).

Pairs are pre-screened for co-integration (ADF test, Engle-Granger).
During live operation only the z-score is tracked in real-time using an
exponential moving average of the spread to avoid look-ahead bias.

This implementation uses crypto-native pairs with very high historical
correlation (e.g. BTC/ETH, SOL/AVAX, BNB/TRX).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from bot.logger import log
from bot.market_data import Quote
from bot.risk import TradeIntent

# (symbol_A, symbol_B, hedge_ratio)
# hedge_ratio: how many units of B per unit of A to achieve dollar-neutral
CO_INTEGRATED_PAIRS: List[Tuple[str, str, float]] = [
    ("BTC/USDT", "ETH/USDT", 15.5),     # ~1 BTC ≈ 15.5 ETH historically
    ("SOL/USDT", "AVAX/USDT", 0.33),
    ("BNB/USDT", "TRX/USDT", 500.0),
    ("XRP/USDT", "XLM/USDT", 3.0),
    ("MATIC/USDT", "DOT/USDT", 5.0),
]

ENTRY_Z = 2.0       # z-score threshold to open position
EXIT_Z = 0.3        # z-score threshold to close position
STOP_Z = 3.5        # z-score threshold for stop-loss (spread widened too much)
LOOKBACK = 200      # rolling window length for spread stats
POSITION_USD = Decimal("3000")


@dataclass
class SpreadState:
    spread_history: Deque[float]
    in_position: bool = False
    position_direction: int = 0   # +1 = long A / short B, -1 = reverse
    entry_z: float = 0.0

    def z_score(self) -> Optional[float]:
        if len(self.spread_history) < 30:
            return None
        arr = np.array(list(self.spread_history))
        mean = arr.mean()
        std = arr.std()
        if std < 1e-10:
            return None
        return float((arr[-1] - mean) / std)


class StatisticalArb:
    """
    Mean-reversion pairs trader using rolling z-score of log-spread.

    Each pair maintains its own SpreadState.  Signals are emitted as
    TradeIntents with the buying exchange and selling exchange both set
    to the same venue (since stat-arb trades within one exchange,
    buying one leg and selling the other).
    """

    def __init__(self, exchange: str) -> None:
        self._exchange = exchange
        self._states: Dict[Tuple[str, str], SpreadState] = {
            (a, b): SpreadState(spread_history=deque(maxlen=LOOKBACK))
            for a, b, _ in CO_INTEGRATED_PAIRS
        }
        self._quotes: Dict[str, Quote] = {}
        self._pair_map: Dict[str, List[Tuple[str, str, float]]] = {}
        for a, b, ratio in CO_INTEGRATED_PAIRS:
            self._pair_map.setdefault(a, []).append((a, b, ratio))
            self._pair_map.setdefault(b, []).append((a, b, ratio))

    def on_quote(self, quote: Quote) -> List[TradeIntent]:
        if quote.exchange != self._exchange:
            return []
        self._quotes[quote.symbol] = quote
        intents = []
        for pair in self._pair_map.get(quote.symbol, []):
            intent = self._evaluate_pair(*pair)
            if intent:
                intents.append(intent)
        return intents

    def _evaluate_pair(
        self, sym_a: str, sym_b: str, hedge_ratio: float
    ) -> Optional[TradeIntent]:
        qa = self._quotes.get(sym_a)
        qb = self._quotes.get(sym_b)
        if not qa or not qb:
            return None

        mid_a = float(qa.mid)
        mid_b = float(qb.mid)
        if mid_a <= 0 or mid_b <= 0:
            return None

        # Log-spread: log(price_A) - hedge_ratio * log(price_B)
        spread = math.log(mid_a) - hedge_ratio * math.log(mid_b)
        state = self._states[(sym_a, sym_b)]
        state.spread_history.append(spread)
        z = state.z_score()
        if z is None:
            return None

        if not state.in_position:
            return self._check_entry(sym_a, sym_b, hedge_ratio, z, state, qa, qb)
        else:
            return self._check_exit(sym_a, sym_b, hedge_ratio, z, state, qa, qb)

    def _check_entry(
        self,
        sym_a: str, sym_b: str, hedge_ratio: float,
        z: float, state: SpreadState,
        qa: Quote, qb: Quote,
    ) -> Optional[TradeIntent]:
        if abs(z) < ENTRY_Z:
            return None

        direction = -1 if z > 0 else 1  # z>0 → A expensive → short A / long B
        qty_a = POSITION_USD / qa.mid
        qty_b = qty_a * Decimal(str(hedge_ratio))

        if direction == 1:  # long A / short B
            buy_sym, sell_sym = sym_a, sym_b
            buy_px, sell_px = qa.ask, qb.bid
            quantity = qty_a
        else:               # short A / long B
            buy_sym, sell_sym = sym_b, sym_a
            buy_px, sell_px = qb.ask, qa.bid
            quantity = qty_b

        state.in_position = True
        state.position_direction = direction
        state.entry_z = z

        profit_estimate = POSITION_USD * Decimal(str(abs(z) - EXIT_Z)) / Decimal("100")

        log.info(
            "stat_arb.entry",
            pair=(sym_a, sym_b),
            z_score=round(z, 3),
            direction=direction,
            buy=buy_sym,
            sell=sell_sym,
        )

        return TradeIntent(
            strategy="statistical_arb",
            exchange_buy=self._exchange,
            exchange_sell=self._exchange,
            symbol=buy_sym,
            quantity=quantity,
            expected_buy_price=buy_px,
            expected_sell_price=sell_px,
            estimated_profit_usd=profit_estimate,
            estimated_slippage_bps=Decimal("2"),
            quote_age_ms=0.0,
        )

    def _check_exit(
        self,
        sym_a: str, sym_b: str, hedge_ratio: float,
        z: float, state: SpreadState,
        qa: Quote, qb: Quote,
    ) -> Optional[TradeIntent]:
        should_exit = (
            abs(z) < EXIT_Z
            or abs(z) > STOP_Z
            or (state.position_direction > 0 and z < 0)
            or (state.position_direction < 0 and z > 0)
        )
        if not should_exit:
            return None

        # Reverse the original entry to close
        direction = state.position_direction
        qty_a = POSITION_USD / qa.mid

        if direction == 1:  # was long A / short B → sell A / buy B
            buy_sym, sell_sym = sym_b, sym_a
            buy_px, sell_px = qb.ask, qa.bid
            quantity = qty_a * Decimal(str(hedge_ratio))
        else:
            buy_sym, sell_sym = sym_a, sym_b
            buy_px, sell_px = qa.ask, qb.bid
            quantity = qty_a

        state.in_position = False
        state.position_direction = 0

        log.info(
            "stat_arb.exit",
            pair=(sym_a, sym_b),
            z_score=round(z, 3),
            entry_z=round(state.entry_z, 3),
        )

        return TradeIntent(
            strategy="statistical_arb_exit",
            exchange_buy=self._exchange,
            exchange_sell=self._exchange,
            symbol=buy_sym,
            quantity=quantity,
            expected_buy_price=buy_px,
            expected_sell_price=sell_px,
            estimated_profit_usd=Decimal("0"),  # exit trade, profit realised at fill
            estimated_slippage_bps=Decimal("2"),
            quote_age_ms=0.0,
        )
