from __future__ import annotations

from src.strategies.conservative import ConservativeStrategy
from src.strategies.moderate import ModerateStrategy
from src.strategies.aggressive import AggressiveStrategy
from src.strategies.very_aggressive import VeryAggressiveStrategy
from src.core.utils import ALLOWED_UNIVERSE


def _smoke_strategy(cls) -> None:
    strat = cls()
    prices = {s: 100.0 for s in strat.universe}
    signals = strat.generate_signals(prices)
    weights = strat.allocate_portfolio(signals, portfolio=type("P", (), {})())
    assert set(weights.keys()).issubset(ALLOWED_UNIVERSE)
    assert abs(sum(weights.values()) - 1.0) < 1e-6
    assert weights.get("USDT", 0.0) >= 0.0


def test_conservative():
    _smoke_strategy(ConservativeStrategy)


def test_moderate():
    _smoke_strategy(ModerateStrategy)


def test_aggressive():
    _smoke_strategy(AggressiveStrategy)


def test_very_aggressive():
    _smoke_strategy(VeryAggressiveStrategy)
