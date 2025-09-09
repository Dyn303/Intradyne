from pathlib import Path
import os

from src.backtester.engine import run_backtest


def test_backtester_produces_ledger(tmp_path):
    # ensure ledger path points to temp file
    ledger_path = tmp_path / "ledger.jsonl"
    os.environ["EXPLAIN_LEDGER_PATH"] = str(ledger_path)
    n = run_backtest(days=1, symbols=["BTC/USDT"], ledger_path=str(ledger_path)) 
    assert n >= 1
    assert ledger_path.exists()
    data = ledger_path.read_text().strip().splitlines()
    assert any("backtest_order" in line for line in data)



