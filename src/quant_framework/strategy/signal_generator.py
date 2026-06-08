"""Signal Generator — bridge between Factor computation and Strategy signals.

The FactorEngine produces raw factor values (pure data). The SignalGenerator
converts factor values into trading Signals (with direction, price, confidence)
that strategies can consume directly.

This cleanly separates:
- Factor computation (what is happening in the market) — factors/
- Signal generation (what action to take) — strategy/signal_generator.py
- Strategy execution (when and how to act) — strategy/base.py

Data flow:
  Market Data → FactorEngine.compute() → factor values
    → SignalGenerator.generate() → Signal
      → Strategy.on_signals() → OrderRequest
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from quant_framework.core.constants import SignalDirection
from quant_framework.core.types import Symbol
from quant_framework.strategy.signals import Signal

logger = logging.getLogger("quant_framework.signal_generator")


@dataclass
class SignalRule:
    """A rule that converts factor values into trading signals.

    Each rule watches one or more factors and generates signals when
    thresholds are crossed.

    Example:
        # Buy when factor_zscore > 2.0 (top 2.5% of stocks)
        SignalRule(
            name="momentum_breakout",
            factor_name="momentum_20d",
            direction=SignalDirection.BUY,
            condition="above",
            threshold=2.0,
            confidence=0.7,
        )
    """

    name: str
    factor_name: str                        # Which factor to watch
    direction: SignalDirection
    condition: str = "above"               # "above" | "below" | "cross_above" | "cross_below"
    threshold: float = 0.0
    confidence: float = 0.7                # Signal confidence (0.0-1.0)
    reason_template: str = "{factor_name} {condition} {threshold}"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.condition not in ("above", "below", "cross_above", "cross_below"):
            raise ValueError(f"Unknown condition: {self.condition}")


class SignalGenerator:
    """Convert factor values into trading Signals.

    Usage:
        gen = SignalGenerator("my_strategy")
        gen.add_rule(SignalRule("mom_buy", "momentum_20d", SignalDirection.BUY,
                                 "above", 2.0))

        # After factor computation:
        factors_df = factor_engine.compute(universe, date)
        signals = gen.generate(factors_df)
    """

    def __init__(self, strategy_id: str) -> None:
        self.strategy_id = strategy_id
        self._rules: list[SignalRule] = []
        self._prev_values: dict[str, dict[Symbol, float]] = {}  # rule_name -> {symbol: prev_value}

    def add_rule(self, rule: SignalRule) -> None:
        """Register a signal generation rule."""
        self._rules.append(rule)
        logger.info("Signal rule added: %s (%s %s %.3f)",
                     rule.name, rule.factor_name, rule.condition, rule.threshold)

    def remove_rule(self, rule_name: str) -> bool:
        """Remove a rule by name. Returns True if found."""
        for i, r in enumerate(self._rules):
            if r.name == rule_name:
                self._rules.pop(i)
                return True
        return False

    def generate(
        self,
        factors: pd.DataFrame,
        prev_factors: pd.DataFrame | None = None,
    ) -> list[Signal]:
        """Generate trading signals from factor values.

        Args:
            factors: DataFrame with columns [symbol, factor_1, factor_2, ...].
                     Index is symbol.
            prev_factors: Previous period's factor values for cross-over detection.

        Returns:
            List of Signal objects.
        """
        signals: list[Signal] = []

        for rule in self._rules:
            if rule.factor_name not in factors.columns:
                logger.warning("Factor '%s' not found in data, skipping rule '%s'",
                               rule.factor_name, rule.name)
                continue

            factor_series = factors[rule.factor_name]
            prev_series = prev_factors[rule.factor_name] if prev_factors is not None else None

            for symbol, value in factor_series.items():
                if pd.isna(value):
                    continue

                prev_value = prev_series.get(symbol) if prev_series is not None else None

                triggered = self._check_condition(
                    value, prev_value, rule.condition, rule.threshold
                )

                if triggered:
                    reason = rule.reason_template.format(
                        factor_name=rule.factor_name,
                        condition=rule.condition,
                        threshold=rule.threshold,
                        value=round(value, 4),
                    )
                    signal = Signal(
                        strategy_id=self.strategy_id,
                        symbol=str(symbol),
                        direction=rule.direction,
                        reason=reason,
                        confidence=rule.confidence,
                        metadata={
                            "rule": rule.name,
                            "factor": rule.factor_name,
                            "value": value,
                            "threshold": rule.threshold,
                            **rule.metadata,
                        },
                    )
                    signals.append(signal)

        return signals

    def generate_ranked(
        self,
        factors: pd.DataFrame,
        top_n: int = 10,
        bottom_n: int = 0,
        factor_name: str | None = None,
    ) -> list[Signal]:
        """Generate signals based on factor ranking.

        Buys the top-N stocks by factor value, sells the bottom-N.

        Args:
            factors: DataFrame with factor columns.
            top_n: Number of top-ranked stocks to buy.
            bottom_n: Number of bottom-ranked stocks to sell.
            factor_name: Which factor to rank by. If None, uses first factor column.

        Returns:
            List of buy/sell signals for the ranked stocks.
        """
        if factor_name is None:
            factor_cols = [c for c in factors.columns if c != "symbol"]
            if not factor_cols:
                return []
            factor_name = factor_cols[0]

        if factor_name not in factors.columns:
            return []

        ranked = factors[factor_name].dropna().sort_values(ascending=False)
        signals: list[Signal] = []

        # Buy top-N
        for symbol in ranked.head(top_n).index:
            signals.append(Signal(
                strategy_id=self.strategy_id,
                symbol=str(symbol),
                direction=SignalDirection.BUY,
                reason=f"Rank top-{top_n} by {factor_name}",
                confidence=0.6,
                metadata={"rank": list(ranked.index).index(symbol) + 1, "factor": factor_name},
            ))

        # Sell bottom-N
        for symbol in ranked.tail(bottom_n).index:
            signals.append(Signal(
                strategy_id=self.strategy_id,
                symbol=str(symbol),
                direction=SignalDirection.SELL,
                reason=f"Rank bottom-{bottom_n} by {factor_name}",
                confidence=0.6,
                metadata={"rank": list(ranked.index).index(symbol) + 1, "factor": factor_name},
            ))

        return signals

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    @staticmethod
    def _check_condition(
        value: float,
        prev_value: float | None,
        condition: str,
        threshold: float,
    ) -> bool:
        """Check if a condition is triggered."""
        if condition == "above":
            return value > threshold
        elif condition == "below":
            return value < threshold
        elif condition == "cross_above":
            if prev_value is None:
                return value > threshold
            return prev_value <= threshold and value > threshold
        elif condition == "cross_below":
            if prev_value is None:
                return value < threshold
            return prev_value >= threshold and value < threshold
        return False
