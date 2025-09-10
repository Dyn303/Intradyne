from __future__ import annotations

from pathlib import Path

from src.engine import main as engine_main


def test_backtest_generates_summary_and_snapshot(tmp_path: Path, monkeypatch):
    # Run a 3-day synthetic backtest and verify files/columns
    monkeypatch.chdir(tmp_path)
    rc = engine_main(["--strategy", "aggressive", "--mode", "backtest", "--capital", "200", "--days", "3"])
    assert rc == 0
    csv = Path("trading_summary.csv")
    snap = Path("portfolio_snapshot.json")
    assert csv.exists() and snap.exists()
    # Check CSV has header and rows
    lines = csv.read_text().strip().splitlines()
    assert lines[0].startswith("date,equity,daily_pnl,daily_return_pct,trades,max_drawdown_pct,sharpe")
    assert len(lines) >= 2

