"""The archive loader silently decides two things that would corrupt every
downstream number if wrong: what unit the timestamps are in, and what happens
to intervals with no trades."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

pd = pytest.importorskip("pandas")

from fetch_klines_archive import months, to_grid  # noqa: E402

COLS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trades",
    "taker_buy_base",
]


def _frame(rows):
    return pd.DataFrame(rows, columns=COLS)


def test_month_range_crosses_a_year_boundary():
    assert months("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_a_single_month_is_its_own_range():
    assert months("2026-03", "2026-03") == ["2026-03"]


@pytest.mark.parametrize("scale,label", [(1e3, "milliseconds"), (1e6, "microseconds")])
def test_both_timestamp_units_land_on_the_same_grid(scale, label):
    """The archive switched units partway through; a run mixing the two would
    put months centuries apart."""
    base = 1_800_000_000
    rows = [
        [int((base + i * 60) * scale), 10.0, 10.0, 10.0, 10.0, 1.0, 1, 0.5]
        for i in range(3)
    ]
    g = to_grid(_frame(rows), "1m")
    assert list(g["ts"]) == [base, base + 60, base + 120], label


def test_quiet_intervals_are_filled_at_the_last_price_with_no_volume():
    """A gap must not become a missing bar: without this a '480 bar' horizon
    quietly means more than 480 minutes."""
    base = 1_800_000_000
    rows = [
        [int(base * 1e3), 10.0, 10.0, 10.0, 10.0, 5.0, 3, 2.0],
        # 60s..120s absent entirely
        [int((base + 180) * 1e3), 11.0, 11.0, 11.0, 11.0, 7.0, 4, 3.0],
    ]
    g = to_grid(_frame(rows), "1m")
    assert len(g["close"]) == 4
    assert list(g["close"]) == [10.0, 10.0, 10.0, 11.0]
    # A filled bar has no range, so it can never trigger a barrier...
    assert g["high"][1] == g["low"][1] == 10.0
    # ...and no volume, so it cannot look like activity to a flow signal.
    assert g["volume"][1] == 0.0
    assert g["trades"][1] == 0.0


def test_the_aggressor_split_reconstructs_sell_volume():
    """Order-flow signals depend on this being the taker split and not
    something else; it is the only reason bars can stand in for ticks."""
    base = 1_800_000_000
    g = to_grid(_frame([[int(base * 1e3), 10.0, 10.0, 10.0, 10.0, 8.0, 5, 3.0]]), "1m")
    assert g["buy_volume"][0] == 3.0
    assert g["sell_volume"][0] == 5.0


def test_sell_volume_never_goes_negative_on_a_dirty_row():
    base = 1_800_000_000
    g = to_grid(_frame([[int(base * 1e3), 10.0, 10.0, 10.0, 10.0, 1.0, 1, 4.0]]), "1m")
    assert g["sell_volume"][0] == 0.0
