"""Cross-sectional decile testing, with the null the crypto work had to learn.

This is the machinery `docs/SLOT_1_PREREGISTRATION.md` needs. It is written
against a `Panel` rather than against equities, so the same code that is
validated on free crypto bars runs unchanged on the SPUS universe once prices
for it exist. Swapping markets is a matter of building a different Panel.

## The null is the part that matters

A decile rule earns something mechanically. Ranking by trailing return and
buying the bottom names selects on an extremum, and an extremum is a biased
sample whenever prices carry noise -- so a control that enters at random names
does not share the selection and cannot separate "this predicts" from "the
bottom decile is not a typical name".

The crypto programme learned this expensively. Under a random-entry control,
four configurations passed at +37 to +92 bps with t between +10.8 and +13.1,
and every one was drift plus selection arithmetic. The null here shuffles
returns **across names within each date**, which destroys the cross-sectional
signal while preserving each date's market move and the dispersion between
names. Whatever the decile rule earns mechanically, it earns on the shuffle
too.

## Clustering is by date, not by position

Positions opened on one date share that date's market move. Treating them as
independent draws is what turns noise into a significant t.
"""

from __future__ import annotations

import math
import statistics as st
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class Panel:
    """A price panel with point-in-time membership.

    `membership[i, j]` is whether symbol j was in the investable universe on
    date i. It is a separate array from the prices on purpose: a name can have
    a price and not be investable -- delisted from the index, screened out on
    compliance, below a liquidity floor. Conflating the two is how a universe
    silently becomes survivorship-biased.
    """

    dates: np.ndarray  # (T,) epoch seconds, ascending
    symbols: Sequence[str]  # (N,)
    close: np.ndarray  # (T, N) float, NaN where unpriced
    membership: np.ndarray  # (T, N) bool

    def __post_init__(self) -> None:
        t, n = len(self.dates), len(self.symbols)
        if self.close.shape != (t, n) or self.membership.shape != (t, n):
            raise ValueError(
                f"shape mismatch: dates={t} symbols={n} "
                f"close={self.close.shape} membership={self.membership.shape}"
            )


@dataclass(frozen=True)
class Result:
    label: str
    n_positions: int
    n_dates: int
    edge_bps: float
    null_mean_bps: float
    null_lo: float
    null_hi: float
    p_value: float


def _forward_returns(close: np.ndarray, hold: int) -> np.ndarray:
    """(T, N) forward returns in bps over `hold` rows; NaN where unavailable."""
    t = close.shape[0]
    out = np.full_like(close, np.nan, dtype=float)
    if hold < t:
        out[: t - hold] = (close[hold:] / close[: t - hold] - 1.0) * 10_000.0
    return out


def _trailing_returns(close: np.ndarray, lookback: int) -> np.ndarray:
    t = close.shape[0]
    out = np.full_like(close, np.nan, dtype=float)
    if lookback < t:
        out[lookback:] = (close[lookback:] / close[:-lookback] - 1.0) * 10_000.0
    return out


def _decile_by_date(
    panel: Panel, lookback: int, hold: int, weakest: bool, decile: float
) -> Dict[int, List[float]]:
    """Forward returns of the selected decile, grouped by rebalance date.

    Rebalances are spaced by `hold` so holding periods never overlap: two
    positions whose windows overlap share price moves and are not independent.
    """
    trail = _trailing_returns(panel.close, lookback)
    fwd = _forward_returns(panel.close, hold)
    by_date: Dict[int, List[float]] = {}

    for i in range(lookback, panel.close.shape[0] - hold, hold):
        live = panel.membership[i] & np.isfinite(trail[i]) & np.isfinite(fwd[i])
        idx = np.flatnonzero(live)
        if idx.size < 4:
            # Fewer than four names is not a cross-section; ranking two things
            # and calling the lower one a decile measures nothing.
            continue
        order = idx[np.argsort(trail[i][idx])]
        k = max(1, int(round(decile * idx.size)))
        chosen = order[:k] if weakest else order[-k:]
        # Excess over the investable universe on the same date, not the raw
        # forward return.
        #
        # A long-only decile carries the market. Measuring its absolute return
        # measures market direction: the same rule looks profitable in a rising
        # market and ruinous in a falling one, for reasons having nothing to do
        # with the ranking. Validation on crypto bars made this visible -- every
        # null sat around -80 bps rather than zero, which is simply what the
        # market did over that window.
        #
        # Subtracting the equal-weighted universe return isolates what the
        # *ranking* contributed, which is the only thing under test, and it
        # centres the null at zero so a broken null is visible as one.
        bench = float(np.mean(fwd[i][idx]))
        by_date[int(panel.dates[i])] = [float(fwd[i][j]) - bench for j in chosen]
    return by_date


def _shuffle_within_date(close: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Permute each date's cross-section of returns across names.

    Destroys which name earned what, keeps what the market did that day and
    how widely names dispersed around it. Prices are rebuilt from the
    permuted returns so every downstream calculation is identical.
    """
    rets = close[1:] / close[:-1] - 1.0
    out = rets.copy()
    for i in range(out.shape[0]):
        row = out[i]
        ok = np.flatnonzero(np.isfinite(row))
        if ok.size > 1:
            row[ok] = rng.permutation(row[ok])
    rebuilt = np.empty_like(close)
    rebuilt[0] = close[0]
    for i in range(out.shape[0]):
        rebuilt[i + 1] = rebuilt[i] * (1.0 + out[i])
    return rebuilt


def _date_clustered_mean(by_date: Dict[int, List[float]]) -> Tuple[float, int, int]:
    """Mean of per-date means, plus position and date counts."""
    if not by_date:
        return 0.0, 0, 0
    per_date = [st.mean(v) for v in by_date.values() if v]
    n_pos = sum(len(v) for v in by_date.values())
    return (st.mean(per_date) if per_date else 0.0), n_pos, len(per_date)


def run_test(
    panel: Panel,
    lookback: int,
    hold: int,
    weakest: bool,
    decile: float = 0.1,
    n_boot: int = 200,
    seed: int = 0,
    label: str = "",
) -> Optional[Result]:
    """One configuration against its resampling null."""
    real = _date_clustered_mean(_decile_by_date(panel, lookback, hold, weakest, decile))
    edge, n_pos, n_dates = real
    if n_dates < 3:
        return None

    rng = np.random.default_rng(seed)
    null: List[float] = []
    for _ in range(n_boot):
        shuffled = Panel(
            dates=panel.dates,
            symbols=panel.symbols,
            close=_shuffle_within_date(panel.close, rng),
            membership=panel.membership,
        )
        m, _, d = _date_clustered_mean(
            _decile_by_date(shuffled, lookback, hold, weakest, decile)
        )
        if d >= 3:
            null.append(m)
    if len(null) < 20:
        return None

    null.sort()
    lo = null[int(0.025 * len(null))]
    hi = null[min(len(null) - 1, int(0.975 * len(null)))]
    # +1 so a finite number of draws never claims p = 0.
    more = sum(1 for x in null if abs(x) >= abs(edge))
    p = (more + 1) / (len(null) + 1)

    return Result(
        label=label or f"{'weak' if weakest else 'strong'} lb={lookback} hold={hold}",
        n_positions=n_pos,
        n_dates=n_dates,
        edge_bps=edge,
        null_mean_bps=st.mean(null),
        null_lo=lo,
        null_hi=hi,
        p_value=p,
    )


def bonferroni_alpha(n_tests: int, alpha: float = 0.05) -> float:
    """The bar for `n_tests` comparisons.

    Called with the *planned* number of tests, not the number that looked
    interesting afterwards -- which is the entire point of correcting.
    """
    if n_tests < 1:
        raise ValueError("n_tests must be at least 1")
    return alpha / n_tests


def format_results(rows: Sequence[Result], alpha: float, cost_bps: float) -> str:
    out = [
        f"{'configuration':26} {'n':>7} {'dates':>6} {'edge':>9} "
        f"{'null 95%':>19} {'p':>8} {'verdict':>9}",
    ]
    for r in rows:
        ok = r.p_value < alpha and r.edge_bps > cost_bps
        verdict = "PASS" if ok else ("p only" if r.p_value < alpha else "no")
        out.append(
            f"{r.label:26} {r.n_positions:7,} {r.n_dates:6} {r.edge_bps:+9.2f} "
            f"[{r.null_lo:+8.2f},{r.null_hi:+8.2f}] {r.p_value:8.4f} {verdict:>9}"
        )
    return "\n".join(out)


def sanity_check(rows: Sequence[Result]) -> List[str]:
    """Harness faults that present as findings.

    Every one of these has occurred in this project. An empty table printed as
    "0 of 8 passed" from a units bug; a turnover of zero came from comparing
    two empty sets; a control whose own mean was four sigma from zero was read
    as a baseline. Each looked like a result.
    """
    problems: List[str] = []
    if not rows:
        problems.append(
            "no configuration was evaluated -- an empty table is not a result"
        )
        return problems
    if all(r.n_positions == 0 for r in rows):
        problems.append("no positions were taken in any configuration")
    if all(abs(r.edge_bps) < 1e-9 for r in rows):
        problems.append(
            "every edge is exactly zero -- suspect a parsing or alignment fault"
        )
    for r in rows:
        if not math.isfinite(r.edge_bps):
            problems.append(f"{r.label}: edge is not finite")
        if r.n_dates < 10:
            problems.append(
                f"{r.label}: only {r.n_dates} rebalance dates -- too few to cluster on"
            )
    return problems
