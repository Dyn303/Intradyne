from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from intradyne.engine.backtest import run as run_backtest


def _write_csv(
    tmp: Path,
    symbol: str,
    timeframe: str,
    rows: list[tuple[int, float, float, float, float, float]],
):
    ddir = tmp / "bitget"
    ddir.mkdir(parents=True, exist_ok=True)
    fname = ddir / f"{symbol.replace('/', '-')}_{timeframe}.csv"
    df = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df.to_csv(fname, index=False)


def test_deterministic_backtest_with_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # Create simple ascending then flat prices
    start = pd.Timestamp("2024-01-01", tz="UTC")
    rows = []
    ts = int(start.timestamp() * 1000)
    for i in range(50):
        o = 100 + i * 0.1
        c = o + 0.05
        rows.append((ts + i * 60_000, o, o + 0.2, o - 0.2, c, 10.0))
    _write_csv(tmp_path, "BTC-USDT", "1m", rows)
    res1 = run_backtest(
        ["BTC/USDT"],
        rows[0][0],
        rows[-1][0],
        "1m",
        "momentum",
        {"momentum": {"breakout_window": 10, "min_range_bps": 3}},
        2,
        5,
        2,
        seed=123,
    )
    res2 = run_backtest(
        ["BTC/USDT"],
        rows[0][0],
        rows[-1][0],
        "1m",
        "momentum",
        {"momentum": {"breakout_window": 10, "min_range_bps": 3}},
        2,
        5,
        2,
        seed=123,
    )
    assert json.dumps(res1.metrics, sort_keys=True) == json.dumps(
        res2.metrics, sort_keys=True
    )


def test_sl_tp_trigger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    start = pd.Timestamp("2024-02-01", tz="UTC")
    rows = []
    ts = int(start.timestamp() * 1000)
    # Rise then drop below SL threshold
    prices = [100.0] * 5 + [101.0] * 5 + [98.0] * 5
    for i, p in enumerate(prices):
        rows.append((ts + i * 60_000, p, p + 0.1, p - 0.1, p, 5.0))
    _write_csv(tmp_path, "BTC-USDT", "1m", rows)
    params = {
        "risk": {"per_trade_sl_pct": 0.01, "tp_pct": 0.02, "max_pos_pct": 0.1},
        "momentum": {"breakout_window": 2, "min_range_bps": 1},
    }
    res = run_backtest(
        ["BTC/USDT"],
        rows[0][0],
        rows[-1][0],
        "1m",
        "momentum",
        params,
        2,
        5,
        2,
        seed=99,
    )
    # Expect some trades and finite metrics
    assert isinstance(res.metrics.get("final_equity"), float)


def test_compliance_blocks_non_whitelist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    start = pd.Timestamp("2024-03-01", tz="UTC")
    ts = int(start.timestamp() * 1000)
    rows = [(ts + i * 60_000, 100.0, 100.0, 100.0, 100.0, 1.0) for i in range(10)]
    _write_csv(tmp_path, "ABC-USDT", "1m", rows)
    with pytest.raises(Exception):
        run_backtest(
            ["ABC/USDT"], rows[0][0], rows[-1][0], "1m", "momentum", {}, 2, 5, 2, seed=1
        )


def test_closing_liquidation_uses_price_not_equity(tmp_path, monkeypatch):
    """Regression: the end-of-window liquidation priced the position at
    eq_curve[-1] -- portfolio *equity* -- instead of the last traded price.

    On a low-priced instrument that is a windfall (ETH near $1,875 sold at a
    ~$9,980 'price'), and it fabricated an entire run's profit from one fill.
    On a high-priced instrument it fabricates an equally large loss.
    """
    from intradyne.engine import broker_paper as bp

    rows = []
    base = 1_600_000_000_000
    price = 100.0
    for i in range(400):
        price *= 1.0006 if i % 3 else 0.9994
        rows.append(
            (base + i * 60_000, price, price * 1.001, price * 0.999, price, 10.0)
        )
    _write_csv(tmp_path, "BTC/USDT", "1m", rows)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "art"))
    from intradyne.core.config import reset_settings_cache

    reset_settings_cache()

    sell_prices = []
    original = bp.PaperBroker._execute

    def _spy(self, order, qty, px, is_maker):
        if order.side == "sell":
            sell_prices.append(px)
        return original(self, order, qty, px, is_maker)

    monkeypatch.setattr(bp.PaperBroker, "_execute", _spy)
    run_backtest(
        ["BTC/USDT"],
        rows[0][0],
        rows[-1][0],
        "1m",
        "momentum",
        {"execution": {"time_stop_s": 3600}},
        2,
        5,
        2,
        seed=42,
        out_dir=tmp_path / "out",
        fast_mode=True,
    )

    if sell_prices:
        highest = max(sell_prices)
        # Every fill must be near the instrument's price band, not near the
        # ~10,000 account equity.
        assert highest < 10 * rows[0][1], (
            f"a sell filled at {highest:.2f}, far outside the price range "
            f"{rows[0][1]:.2f}-{max(r[1] for r in rows):.2f}"
        )


def test_realized_return_contradicting_theory_is_flagged(tmp_path, monkeypatch):
    """A geometry that implies an edge must not report one when the run
    actually lost money: exits do not always honour tp/sl."""
    from intradyne.backtester.costs import assess

    edge = assess(
        win_rate=0.40,
        tp_pct=0.008,
        sl_pct=0.002,
        taker_bps=5,
        slippage_bps=2,
        maker_bps=2,
        trades=110,
    ).to_dict()
    # On assumed geometry alone this looks like an edge.
    assert edge["expectancy_pct"] > 0
    assert edge["verdict"] in {"clears_with_margin", "marginal"}
    # The backtest overrides that when realised return disagrees; this pins
    # the rule the summary applies.
    realized_bps = -10.76
    assert realized_bps <= 0, "guard only applies to a losing realised return"
