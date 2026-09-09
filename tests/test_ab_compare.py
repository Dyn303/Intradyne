"""The A/B reader, and the arithmetic that decides the comparison.

The headline metric is per *signal*, not per trade, because maker execution
takes fewer trades: an 11-hour run suppressed 187 signals and filled 20. A
per-trade average rewards that selectivity for free -- discard the worst 90%
of trades by any rule and per-trade P&L improves, having earned nothing.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "ab_compare", Path("scripts/ab_compare.py")
)
assert _spec and _spec.loader
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)


def _ledger(tmp_path, trades=(), suppressed=0, name="l.jsonl"):
    p = tmp_path / name
    lines = []
    ts = 1_000_000.0
    for move_bps in trades:
        lines.append(
            json.dumps(
                {
                    "ts": ts,
                    "event": "trade_mfe_mae",
                    "symbol": "BTC/USDT",
                    "entry": 100.0,
                    "exit": 100.0 * (1 + move_bps / 10_000.0),
                }
            )
        )
        ts += 3600.0
    for _ in range(suppressed):
        lines.append(
            json.dumps(
                {
                    "ts": ts,
                    "action": "resting_order_exists",
                    "symbol": "BTC/USDT",
                }
            )
        )
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


class TestReading:
    def test_closed_trades_become_gross_bps(self, tmp_path):
        g, s, h = ab._read(_ledger(tmp_path, trades=(10.0, -4.0)))
        assert g == pytest.approx([10.0, -4.0], abs=1e-6)
        assert s == 0

    def test_suppressed_signals_are_counted_separately(self, tmp_path):
        g, s, _ = ab._read(_ledger(tmp_path, trades=(5.0,), suppressed=3))
        assert len(g) == 1 and s == 3

    def test_a_missing_file_is_empty_not_an_error(self, tmp_path):
        assert ab._read(tmp_path / "nope.jsonl") == ([], 0, 0.0)

    @pytest.mark.parametrize(
        "bad",
        [
            "{not json",
            "[]",
            '{"event":"trade_mfe_mae"}',
            '{"event":"trade_mfe_mae","entry":0,"exit":1}',
            '{"event":"trade_mfe_mae","entry":null,"exit":1}',
            '{"event":"other"}',
        ],
    )
    def test_malformed_lines_are_skipped(self, tmp_path, bad):
        p = tmp_path / "bad.jsonl"
        p.write_text(bad, encoding="utf-8")
        g, s, _ = ab._read(p)
        assert g == [] and s == 0

    def test_runtime_spans_first_to_last_stamp(self, tmp_path):
        _, _, h = ab._read(_ledger(tmp_path, trades=(1.0, 1.0, 1.0)))
        assert h == pytest.approx(2.0, abs=1e-6)  # three stamps an hour apart


class TestPerSignalArithmetic:
    """The property that makes the headline honest."""

    def test_suppression_dilutes_the_per_signal_figure(self, capsys):
        ab._summarise("x", [10.0] * 10, suppressed=0, hours=1.0)
        clean = capsys.readouterr().out
        ab._summarise("x", [10.0] * 10, suppressed=90, hours=1.0)
        diluted = capsys.readouterr().out

        assert "+10.000 bps" in clean.split("-- headline --")[1]
        assert "+1.000 bps" in diluted.split("-- headline --")[1]

    def test_per_trade_is_unmoved_by_suppression(self, capsys):
        """Which is exactly why it cannot be the headline: an arm that
        discards nine signals in ten looks identical on this measure."""
        ab._summarise("x", [10.0] * 10, suppressed=90, hours=1.0)
        out = capsys.readouterr().out
        assert "gross mean        : +10.000 bps" in out

    def test_zero_hours_does_not_divide_by_zero(self, capsys):
        ab._summarise("x", [1.0], suppressed=0, hours=0.0)
        assert "+0.00 bps" in capsys.readouterr().out


def test_a_null_result_is_reported_as_too_early_not_as_no_difference(capsys):
    """Two different findings that a t-statistic near zero cannot separate."""
    ab._welch([0.0, 1.0, -1.0, 0.5], [0.2, 1.1, -0.9, 0.6])
    out = capsys.readouterr().out
    assert "indistinguishable" in out
    assert "insufficient data" in out
    assert "resolvable at 2 sigma" in out
