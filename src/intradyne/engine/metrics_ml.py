from __future__ import annotations

try:
    from prometheus_client import Counter
except Exception:  # pragma: no cover
    # Fallback dummies if prometheus_client not installed at runtime
    class _Dummy:
        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            return None

    def Counter(*args, **kwargs):  # type: ignore
        return _Dummy()


ML_SIGNALS = Counter(
    "intradyne_ml_signals_total", "Count of ML buy signals emitted", ["symbol"]
)

ML_EXEC_BUYS = Counter(
    "intradyne_ml_exec_buys_total",
    "Count of ML buy signals that resulted in submitted orders",
    ["symbol"],
)
