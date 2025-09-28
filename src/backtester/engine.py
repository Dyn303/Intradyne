from __future__ import annotations

from typing import Dict, Iterable, List


def compute_daily_returns(equity: List[float]) -> List[float]:
    if not equity:
        return []
    rets: List[float] = [0.0]
    for i in range(1, len(equity)):
        prev = equity[i - 1]
        cur = equity[i]
        if prev == 0:
            rets.append(0.0)
        else:
            rets.append((cur / prev - 1.0) * 100.0)
    return rets


def compute_max_drawdown(equity: List[float]) -> float:
    max_peak = 0.0
    max_dd_pct = 0.0
    for v in equity:
        if v > max_peak:
            max_peak = v
        if max_peak > 0:
            dd = (max_peak - v) / max_peak * 100.0
            if dd > max_dd_pct:
                max_dd_pct = dd
    return max_dd_pct


# Minimal backtest runner used by the CLI shim in src/intradyne/backtest.py
# It simulates a naive moving-average strategy over synthetic prices to avoid
# external data dependencies. Returns the number of generated orders.
def run_backtest(
    *,
    days: int,
    symbols: Iterable[str],
    ma_window: int = 20,
    trend_ema: int | None = None,
    atr_window: int = 14,
    sl_atr_k: float = 0.0,
    tp_atr_k: float = 0.0,
    risk_per_trade: float = 0.0,
    # New knobs to reduce over-trading / improve expectancy
    confirm_bars: int = 1,
    atr_entry_min: float = 0.0,  # min ATR%% to avoid chop (e.g., 0.001 = 10 bps)
    atr_entry_max: float = 0.0,  # max ATR%% to avoid too-volatile entries (0 disables)
    regime: bool = False,  # basic regime classification (trend vs. chop)
    # Sentiment simulation (optional)
    use_sentiment: bool = False,
    sentiment_min: float = 0.0,
    size_min: float = 0.8,
    size_max: float = 1.2,
    report: bool = False,
) -> int | Dict[str, object]:
    import math

    orders = 0
    all_returns: List[float] = []
    symbol_results: Dict[str, Dict[str, float]] = {}
    for _sym in symbols:
        prices: List[float] = []
        above_ma = False
        entry_px: float | None = None
        pnl: float = 0.0
        wins = 0
        trades = 0
        ema: float | None = None
        # ATR proxy: rolling mean absolute return (% per bar)
        abs_rets: List[float] = []
        # Bar-confirmation counter
        confirm_up: int = 0
        # generate a deterministic synthetic price path per symbol
        for t in range(days * 24):  # hourly samples
            # smooth oscillation + gentle drift
            price = 100.0 + 5.0 * math.sin(t / 12.0) + 0.05 * t
            prices.append(price)
            # update EMA trend if enabled
            if trend_ema and trend_ema > 1:
                if ema is None:
                    ema = price
                else:
                    k = 2.0 / (trend_ema + 1.0)
                    ema = price * k + ema * (1.0 - k)
            # update ATR% proxy
            if len(prices) >= 2:
                r = abs(prices[-1] / max(1e-9, prices[-2]) - 1.0)
                abs_rets.append(r)
                if len(abs_rets) > max(2, atr_window):
                    abs_rets.pop(0)
            if len(prices) < ma_window:
                continue
            ma = sum(prices[-ma_window:]) / ma_window
            # Trend filter: require price > EMA for longs when enabled
            in_trend = (
                True
                if not trend_ema or trend_ema <= 1
                else (ema is not None and price > (ema or price))
            )
            # Update confirmation counter
            if price > ma:
                confirm_up += 1
            else:
                confirm_up = 0
            # Basic regime classification (trend vs. chop)
            regime_ok = True
            if regime:
                # Trend if EMA slope positive and |price-ma|/ma > 10 bps; else chop
                ema_slope_ok = False
                if trend_ema and ema is not None:
                    # approximate slope via last two prices
                    prev = prices[-2]
                    ema_prev = (
                        prev
                        if ema is None
                        else (
                            ema * (1.0 - 2.0 / (trend_ema + 1.0))
                            + prev * (2.0 / (trend_ema + 1.0))
                        )
                    )
                    ema_slope_ok = (ema or price) > ema_prev
                dist_ok = abs(price - ma) / max(1e-9, ma) > 0.001  # >10bps from MA
                is_trend = ema_slope_ok and dist_ok
                if not is_trend:
                    # In chop, require stricter confirmation (one extra bar)
                    regime_ok = confirm_up >= max(1, confirm_bars + 1)
            # ATR entry window gate
            atr_pct = (sum(abs_rets) / len(abs_rets)) if abs_rets else 0.0
            atr_min_ok = (atr_entry_min <= 0) or (atr_pct >= atr_entry_min)
            atr_max_ok = (atr_entry_max <= 0) or (atr_pct <= atr_entry_max)
            # Synthetic sentiment (slow oscillation)
            sent = 0.0
            if use_sentiment:
                sent = math.sin(2.0 * math.pi * (t / float(max(1, 24 * 7))))
            sent_ok = (not use_sentiment) or (sent >= sentiment_min)
            if (
                not above_ma
                and price > ma
                and in_trend
                and confirm_up >= max(1, confirm_bars)
                and atr_min_ok
                and atr_max_ok
                and regime_ok
                and sent_ok
            ):
                # cross up -> buy
                orders += 1
                above_ma = True
                entry_px = price
            elif above_ma:
                # Optional ATR-based early exit (SL/TP)
                stop_hit = False
                take_hit = False
                if entry_px is not None and atr_pct > 0:
                    if sl_atr_k > 0:
                        stop_px = entry_px * (1.0 - sl_atr_k * atr_pct)
                        if price <= stop_px:
                            stop_hit = True
                    if tp_atr_k > 0:
                        tp_px = entry_px * (1.0 + tp_atr_k * atr_pct)
                        if price >= tp_px:
                            take_hit = True
                if price < ma or stop_hit or take_hit:
                    # cross down or SL/TP -> sell (close)
                    orders += 1
                    above_ma = False
                    if entry_px is not None:
                        r = (price / entry_px) - 1.0
                        # Vol targeting via ATR if requested
                        weight = 1.0
                        if risk_per_trade > 0 and atr_pct > 0:
                            weight = min(1.0, risk_per_trade / max(1e-9, atr_pct))
                        # Sentiment-based sizing throttle
                        factor = 1.0
                        if use_sentiment:
                            factor = (
                                size_min + (size_max - size_min) * (sent + 1.0) / 2.0
                            )
                        eff_r = r * weight * max(0.0, factor)
                        all_returns.append(eff_r)
                        pnl += eff_r
                        wins += 1 if eff_r > 0 else 0
                        trades += 1
                        entry_px = None
        # Close any open at end
        if above_ma and entry_px is not None:
            r = (prices[-1] / entry_px) - 1.0
            # approximate ATR at end
            atr_pct = (sum(abs_rets) / len(abs_rets)) if abs_rets else 0.0
            weight = 1.0
            if risk_per_trade > 0 and atr_pct > 0:
                weight = min(1.0, risk_per_trade / max(1e-9, atr_pct))
            # Apply sentiment factor for closing mark as well
            factor = 1.0
            if use_sentiment:
                # approximate sentiment at end
                sent = math.sin(2.0 * math.pi * (len(prices) / float(max(1, 24 * 7))))
                factor = size_min + (size_max - size_min) * (sent + 1.0) / 2.0
            eff_r = r * weight * max(0.0, factor)
            all_returns.append(eff_r)
            pnl += eff_r
            wins += 1 if eff_r > 0 else 0
            trades += 1
        symbol_results[_sym] = {
            "pnl": pnl,
            "trades": float(trades),
            "winrate": (wins / trades) if trades else 0.0,
        }
    if not report:
        return orders
    # Aggregate simple metrics
    total_trades = int(sum(r["trades"] for r in symbol_results.values()))
    winrate = (
        sum((r["winrate"] * r["trades"]) for r in symbol_results.values())
        / total_trades
        if total_trades
        else 0.0
    )
    avg_ret = sum(all_returns) / len(all_returns) if all_returns else 0.0
    return {
        "orders": orders,
        "trades": total_trades,
        "winrate": winrate,
        "avg_return": avg_ret,
        "by_symbol": symbol_results,
    }
