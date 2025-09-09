
from __future__ import annotations
from fastapi import FastAPI, Header, HTTPException, Request
from typing import Optional
from pydantic import BaseModel
import os, time

from intradyne_lite.core.config import load_config
from intradyne_lite.core.shariah import check_symbol
from intradyne_lite.core.technicals import atr, trend_up, trend_down
from intradyne_lite.core.sentiment import set_score as sent_set, get_score as sent_get, bias_allow_long
from intradyne_lite.core.watcher import WATCHER, register_bracket
from intradyne_lite.core.profiles import load_profiles
from intradyne_lite.core.analytics import trades_recent, daily_pnl_series, summary, pnl_group, log_trade, latency_stats, log_latency, daily_pnl_series_filtered, summary_filtered
from intradyne_lite.core.options import covered_call, protective_put
from intradyne_lite.core.connectors import ibkr_place_covered_call, ibkr_place_protective_put
from intradyne_lite.core.notifier import notify



from starlette.middleware.base import BaseHTTPMiddleware
import time, threading, os

class RateLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.window = int(os.getenv("RATE_LIMIT_WINDOW","60"))
        self.max_reqs = int(os.getenv("RATE_LIMIT_REQS","120"))
        self.lock = threading.Lock()
        self.counters = {}  # key -> (window_start_ts, count)

    def _key(self, request: Request):
        api_key = request.headers.get("X-API-Key") or ""
        ip = request.client.host if request.client else "unknown"
        return f"{api_key}:{ip}"

    async def dispatch(self, request: Request, call_next):
        key = self._key(request)
        now = int(time.time())
        w = now // self.window
        with self.lock:
            ts, cnt = self.counters.get(key, (w,0))
            if ts != w:
                ts, cnt = w, 0
            cnt += 1
            self.counters[key] = (ts, cnt)
            if cnt > self.max_reqs:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail":"rate limit exceeded"}, status_code=429)
        return await call_next(request)

# attach after app init
app = FastAPI(title="IntraDyne Lite v1.7")

# Globals (runtime tunables)
ENABLED_TREND_FILTER = True
ENABLED_SENTIMENT_GATE = False
MIN_SENTIMENT_SCORE = -0.2
ATR_MULTIPLIER = 2.0
RISK_PER_TRADE_PCT = 0.01
DAILY_MAX_LOSS_PCT = 0.03
TIMEFRAME_DEFAULT = "1h"
MA_N = 50

def require_auth(x_api_key: Optional[str], authorization: Optional[str]):
    if not (x_api_key or authorization):
        raise HTTPException(status_code=401, detail="Auth required")

def _infer_capital(cfg: dict, account: str | None):
    cap = (cfg.get("risk") or {}).get("capital")
    try:
        return float(cap) if cap is not None else 10000.0
    except Exception:
        return 10000.0


def _daily_pnl(cfg: dict, account: str | None) -> float:
    import sqlite3, datetime as _dt, os
    db = ((cfg.get("storage") or {}).get("sqlite_path")) or "/app/data/trades.sqlite"
    os.makedirs(os.path.dirname(db), exist_ok=True)
    con = sqlite3.connect(db)
    cur = con.cursor()
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS trades (ts TEXT, account TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, pnl REAL)")
        day = _dt.datetime.utcnow().date().isoformat()
        if account:
            cur.execute("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE substr(ts,1,10)=? AND account=?", (day, account))
        else:
            cur.execute("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE substr(ts,1,10)=?", (day,))
        val = cur.fetchone()
        return float((val or [0])[0] or 0.0)
    finally:
        con.close()



def _choose_conn(cfg: dict, account: Optional[str]):
    from intradyne_lite.core.connectors import build_conn
    return build_conn(cfg, account)


class ToggleReq(BaseModel):
    trend_filter: bool | None = None
    sentiment_gate: bool | None = None
    min_sentiment: float | None = None
    atr_mult: float | None = None
    risk_per_trade_pct: float | None = None
    daily_max_loss_pct: float | None = None
    timeframe: str | None = None
    ma_n: int | None = None

@app.post("/strategy/toggle")
def strategy_toggle(req: ToggleReq, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    global ENABLED_TREND_FILTER, ENABLED_SENTIMENT_GATE, MIN_SENTIMENT_SCORE, ATR_MULTIPLIER, RISK_PER_TRADE_PCT, DAILY_MAX_LOSS_PCT, TIMEFRAME_DEFAULT, MA_N
    if req.trend_filter is not None: ENABLED_TREND_FILTER = bool(req.trend_filter)
    if req.sentiment_gate is not None: ENABLED_SENTIMENT_GATE = bool(req.sentiment_gate)
    if req.min_sentiment is not None: MIN_SENTIMENT_SCORE = float(req.min_sentiment)
    if req.atr_mult is not None: ATR_MULTIPLIER = float(req.atr_mult)
    if req.risk_per_trade_pct is not None: RISK_PER_TRADE_PCT = float(req.risk_per_trade_pct)
    if req.daily_max_loss_pct is not None: DAILY_MAX_LOSS_PCT = float(req.daily_max_loss_pct)
    if req.timeframe is not None: TIMEFRAME_DEFAULT = str(req.timeframe)
    if req.ma_n is not None: MA_N = int(req.ma_n)
    return {"ok": True, "settings": {
        "trend_filter": ENABLED_TREND_FILTER, "sentiment_gate": ENABLED_SENTIMENT_GATE,
        "min_sentiment": MIN_SENTIMENT_SCORE, "atr_mult": ATR_MULTIPLIER,
        "risk_per_trade_pct": RISK_PER_TRADE_PCT, "daily_max_loss_pct": DAILY_MAX_LOSS_PCT,
        "timeframe": TIMEFRAME_DEFAULT, "ma_n": MA_N}}

@app.post("/sentiment/set")
def sentiment_set(symbol: str, score: float, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    sent_set(symbol, score)
    return {"symbol": symbol, "score": score}

@app.get("/sentiment/get")
def sentiment_get(symbol: str, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    return {"symbol": symbol, "score": sent_get(symbol)}

@app.post("/shariah/check")
def shariah_check(symbol: str, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","config.yaml"))
    ok, reason = check_symbol(cfg, symbol, None)
    return {"symbol": symbol, "ok": ok, "reason": reason}

@app.get("/strategy/suggest_qty")
def suggest_qty(symbol: str, account: Optional[str] = None, risk_pct: Optional[float] = None, timeframe: Optional[str] = None, ma_n: Optional[int] = None, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","config.yaml"))
    conn, _acct = _choose_conn(cfg, account)
    tf = timeframe or TIMEFRAME_DEFAULT
    candles = conn.fetch_ohlcv(symbol, tf, 200)
    a = atr(candles, 14) or None
    cap = _infer_capital(cfg, account)
    rp = (risk_pct if risk_pct is not None else RISK_PER_TRADE_PCT)
    price = candles[-1][4] if candles else conn.get_price(symbol)
    stop_dist = (a*ATR_MULTIPLIER) if a is not None else max(price*0.01, 1e-9)
    qty = (cap*rp)/max(stop_dist,1e-9)/max(price,1e-9)
    return {"symbol": symbol, "atr": a, "stop_dist": stop_dist, "price": price, "risk_capital": cap*rp, "suggest_qty": qty}

@app.get("/signals/preview")
def signals_preview(symbol: str, account: Optional[str] = None, timeframe: Optional[str] = None, ma_n: Optional[int] = None, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","config.yaml"))
    conn, _acct = _choose_conn(cfg, account)
    tf = timeframe or TIMEFRAME_DEFAULT
    m = ma_n or MA_N
    candles = conn.fetch_ohlcv(symbol, tf, 200)
    tu = trend_up(candles, m)
    td = trend_down(candles, m)
    s = sent_get(symbol)
    return {"symbol": symbol, "trend_up": tu, "trend_down": td, "sentiment": s, "candles": len(candles)}


@app.get("/ops/ping")
def ops_ping(x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    res = notify("IntraDyne heartbeat OK")
    return {"ok": True, "notified": res}

@app.get("/ops/test_connectors")
def ops_test_connectors(symbol: str = "BTC/USDT", x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    conn, acct = _choose_conn(cfg, None)
    try:
        candles = conn.fetch_ohlcv(symbol, "1h", 10)
        price = conn.get_price(symbol)
        ok = candles is not None and price is not None
        return {"ok": bool(ok), "symbol": symbol, "last_price": price, "candles": len(candles or [])}
    except Exception as e:
        notify(f"Connector test failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class TestTradeReq(BaseModel):
    symbol: str
    side: str = "buy"
    qty: float = 0.0001
    dry_run: bool = True

@app.post("/ops/test_trade")
def ops_test_trade(req: TestTradeReq, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    conn, acct = _choose_conn(cfg, None)
    # guards
    ok_, reason_ = check_symbol(cfg, req.symbol, None)
    if not ok_:
        notify(f"Trade blocked (Shariah): {req.symbol} {reason_}")
        raise HTTPException(status_code=400, detail=f"Shariah screen failed: {reason_}")
    if ENABLED_SENTIMENT_GATE and req.side.lower()=="buy" and not bias_allow_long(req.symbol, MIN_SENTIMENT_SCORE):
        notify(f"Trade blocked (Sentiment): {req.symbol}")
        raise HTTPException(status_code=400, detail="Sentiment gate blocks long")
    # risk sizing (simulate)
    price = conn.get_price(req.symbol)
    pnl_preview = 0.0
    if req.dry_run:
        return {"simulated": True, "symbol": req.symbol, "side": req.side, "qty": req.qty, "price": price}
    # real path would: conn.place_order(...); here we simulate and log pnl=0
    return {"placed": True, "symbol": req.symbol, "side": req.side, "qty": req.qty, "price": price}


@app.get("/orders/open")
def orders_open(account: Optional[str] = None, symbol: Optional[str] = None, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    conn, acct = _choose_conn(cfg, account)
    try:
        return {"ok": True, "orders": conn.list_open_orders(symbol)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/orders/cancel")
def orders_cancel(order_id: str, account: Optional[str] = None, symbol: Optional[str] = None, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    conn, acct = _choose_conn(cfg, account)
    try:
        r = conn.cancel_order(order_id, symbol)
        return {"ok": True, "resp": r}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/watcher/start")
def watcher_start(x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    started = WATCHER.start(cfg, _choose_conn)
    return {"ok": bool(started)}

@app.post("/watcher/stop")
def watcher_stop(x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    stopped = WATCHER.stop()
    return {"ok": bool(stopped)}

class BracketReq(BaseModel):
    account: Optional[str] = None
    symbol: str
    side: str
    qty: float
    tp: float | None = None
    sl: float | None = None

@app.post("/watcher/register")
def watcher_register(req: BracketReq, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    bid = register_bracket(cfg, req.account or "default", req.symbol, req.side, float(req.qty), req.tp, req.sl)
    return {"ok": True, "id": bid}


@app.post("/profiles/apply")
def profiles_apply(name: str, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    global ENABLED_TREND_FILTER, ENABLED_SENTIMENT_GATE, MIN_SENTIMENT_SCORE, ATR_MULTIPLIER, RISK_PER_TRADE_PCT, DAILY_MAX_LOSS_PCT, TIMEFRAME_DEFAULT, MA_N
    profs = load_profiles(os.getenv("PROFILES","/app/profiles.yaml"))
    if name not in profs:
        raise HTTPException(status_code=404, detail="profile not found")
    p = profs[name]
    ENABLED_TREND_FILTER = bool(p.get("trend_filter", True))
    ENABLED_SENTIMENT_GATE = bool(p.get("sentiment_gate", False))
    MIN_SENTIMENT_SCORE = float(p.get("min_sentiment", -0.2))
    ATR_MULTIPLIER = float(p.get("atr_mult", 2.0))
    RISK_PER_TRADE_PCT = float(p.get("risk_per_trade_pct", 0.01))
    DAILY_MAX_LOSS_PCT = float(p.get("daily_max_loss_pct", 0.03))
    TIMEFRAME_DEFAULT = str(p.get("timeframe", "1h"))
    MA_N = int(p.get("ma_n", 50))
    return {"ok": True, "applied": name, "settings": {
        "trend_filter": ENABLED_TREND_FILTER, "sentiment_gate": ENABLED_SENTIMENT_GATE,
        "min_sentiment": MIN_SENTIMENT_SCORE, "atr_mult": ATR_MULTIPLIER,
        "risk_per_trade_pct": RISK_PER_TRADE_PCT, "daily_max_loss_pct": DAILY_MAX_LOSS_PCT,
        "timeframe": TIMEFRAME_DEFAULT, "ma_n": MA_N}}


from fastapi.responses import JSONResponse, Response
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import io

@app.get("/analytics/summary")
def analytics_summary(days: int = 90, start_capital: float | None = None, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    return summary(cfg, start_capital, days)

@app.get("/analytics/trades")
def analytics_trades(limit: int = 100, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    return {"trades": trades_recent(cfg, limit)}

@app.get("/analytics/equity.png")
def analytics_equity_png(days: int = 90, start_capital: float | None = None, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    dps = daily_pnl_series(cfg, days)
    cap = start_capital or float((cfg.get("risk") or {}).get("capital") or 10000.0)
    eq = cap
    xs, ys = [], []
    for d, pnl in dps:
        eq += pnl; xs.append(d); ys.append(eq)
    fig = plt.figure(figsize=(7,3.2))
    plt.plot(xs, ys)
    plt.xticks(rotation=45); plt.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png"); plt.close(fig); buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")


@app.get("/options/covered_call")
def options_covered_call(symbol: str, qty: float, strike: float, expiry: str, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    return covered_call(symbol, qty, strike, expiry)

@app.get("/options/protective_put")
def options_protective_put(symbol: str, qty: float, strike: float, expiry: str, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    return protective_put(symbol, qty, strike, expiry)


@app.get("/mobile/summary")
def mobile_summary(x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    s = summary(cfg, None, 30)
    return {"settings": {
                "trend_filter": ENABLED_TREND_FILTER, "sentiment_gate": ENABLED_SENTIMENT_GATE,
                "min_sentiment": MIN_SENTIMENT_SCORE, "atr_mult": ATR_MULTIPLIER,
                "risk_per_trade_pct": RISK_PER_TRADE_PCT, "daily_max_loss_pct": DAILY_MAX_LOSS_PCT,
                "timeframe": TIMEFRAME_DEFAULT, "ma_n": MA_N
            },
            "pnl_30d": s, "version": "v1.7.7"}

@app.get("/mobile/trades/recent")
def mobile_trades_recent(limit: int = 100, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    return {"trades": trades_recent(cfg, limit)}


from fastapi.responses import StreamingResponse
import csv

@app.get("/analytics/trades.csv")
def analytics_trades_csv(limit: int = 1000, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    rows = trades_recent(cfg, limit)
    def gen():
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=["ts","account","symbol","side","qty","price","pnl"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
        yield out.getvalue()
    return StreamingResponse(gen(), media_type="text/csv")

@app.get("/analytics/equity.csv")
def analytics_equity_csv(days: int = 90, start_capital: float | None = None, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    dps = daily_pnl_series(cfg, days)
    cap = start_capital or float((cfg.get("risk") or {}).get("capital") or 10000.0)
    eq = cap
    def gen():
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["date","equity"])
        for d, pnl in dps:
            eq_nonlocal = None
        # rebuild eq incrementally
        out = io.StringIO()
        w = csv.writer(out); w.writerow(["date","equity"])
        eq2 = cap
        for d,p in dps:
            eq2 += p; w.writerow([d, f"{eq2:.2f}"])
        yield out.getvalue()
    return StreamingResponse(gen(), media_type="text/csv")


class OptPlaceReq(BaseModel):
    account: Optional[str] = None
    symbol: str
    qty: int
    strike: float
    expiry: str  # 'YYYYMMDD'

@app.post("/options/place/covered_call")
def options_place_covered_call(req: OptPlaceReq, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    conn, acct = _choose_conn(cfg, req.account)
    kind = (acct or {}).get("kind","").lower()
    if kind != "ibkr":
        raise HTTPException(status_code=400, detail="Options placement currently supported for IBKR accounts only")
    resp = ibkr_place_covered_call(conn, req.symbol, int(req.qty), float(req.strike), str(req.expiry))
    return {"ok": True, "resp": resp}

@app.post("/options/place/protective_put")
def options_place_protective_put(req: OptPlaceReq, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    conn, acct = _choose_conn(cfg, req.account)
    kind = (acct or {}).get("kind","").lower()
    if kind != "ibkr":
        raise HTTPException(status_code=400, detail="Options placement currently supported for IBKR accounts only")
    resp = ibkr_place_protective_put(conn, req.symbol, int(req.qty), float(req.strike), str(req.expiry))
    return {"ok": True, "resp": resp}


class TradeLogReq(BaseModel):
    ts: str
    account: str | None = None
    symbol: str
    side: str
    qty: float
    price: float
    pnl: float = 0.0
    strategy: str | None = None
    profile: str | None = None
    venue: str | None = None

@app.post("/trades/log")
def trades_log(req: TradeLogReq, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    log_trade(cfg, req.dict())
    return {"ok": True}


@app.get("/analytics/pnl_by")
def analytics_pnl_by(group: str = "account", days: int = 90, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    return {"group": group, "rows": pnl_group(cfg, group, days)}


@app.get("/analytics/latency")
def analytics_latency(group: str = "action", days: int = 7, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    return {"group": group, "rows": latency_stats(cfg, group, days)}


import time as _t, hmac, hashlib, base64

def _sign(payload: str, key: str) -> str:
    digest = hmac.new(key.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")

def _verify(sig: str, payload: str, key: str) -> bool:
    try:
        expected = _sign(payload, key)
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False

@app.get("/mobile/signed_urls")
def mobile_signed_urls(ttl: int = 300, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    key = os.getenv("MOBILE_SIGNING_KEY", "")
    if not key:
        raise HTTPException(status_code=400, detail="MOBILE_SIGNING_KEY not set")
    exp = int(_t.time()) + int(ttl)
    base = f"{exp}"
    sig1 = _sign(f"/signed/equity.png|{base}", key)
    sig2 = _sign(f"/signed/trades.csv|{base}", key)
    return {
        "equity_png": f"/signed/equity.png?exp={exp}&sig={sig1}",
        "trades_csv": f"/signed/trades.csv?exp={exp}&sig={sig2}",
        "expires": exp
    }

@app.get("/signed/equity.png")
def signed_equity_png(exp: int, sig: str):
    key = os.getenv("MOBILE_SIGNING_KEY", "")
    if not key or int(exp) < int(_t.time()):
        raise HTTPException(status_code=401, detail="expired or key missing")
    if not _verify(sig, f"/signed/equity.png|{exp}", key):
        raise HTTPException(status_code=401, detail="bad signature")
    # proxy to analytics/equity.png (no auth)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    dps = daily_pnl_series(cfg, 90)
    cap = float((cfg.get("risk") or {}).get("capital") or 10000.0)
    eq = cap; xs=[]; ys=[]
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    for d,p in dps:
        eq += p; xs.append(d); ys.append(eq)
    import io
    fig = plt.figure(figsize=(7,3.2)); plt.plot(xs, ys); plt.xticks(rotation=45); plt.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png"); plt.close(fig); buf.seek(0)
    from fastapi import Response as _Resp
    return _Resp(content=buf.read(), media_type="image/png")

@app.get("/signed/trades.csv")
def signed_trades_csv(exp: int, sig: str):
    key = os.getenv("MOBILE_SIGNING_KEY", "")
    if not key or int(exp) < int(_t.time()):
        raise HTTPException(status_code=401, detail="expired or key missing")
    if not _verify(sig, f"/signed/trades.csv|{exp}", key):
        raise HTTPException(status_code=401, detail="bad signature")
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    rows = trades_recent(cfg, 500)
    import csv
    out = io.StringIO(); w = csv.DictWriter(out, fieldnames=["ts","account","symbol","side","qty","price","pnl","strategy","profile","venue"])
    w.writeheader()
    for r in rows: w.writerow(r)
    from fastapi.responses import PlainTextResponse as _Plain
    return _Plain(out.getvalue(), media_type="text/csv")


class OptExitReq(BaseModel):
    account: Optional[str] = None
    symbol: str
    opt_type: str   # CALL or PUT
    side: str       # long or short (your current position)
    contracts: int
    strike: float
    expiry: str     # YYYYMMDD or YYYY-MM-DD
    tp_price: float | None = None
    sl_price: float | None = None

@app.post("/options/exits/oca")
def options_exits_oca(req: OptExitReq, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    conn, acct = _choose_conn(cfg, req.account)
    kind = (acct or {}).get("kind","").lower()
    if kind != "ibkr":
        raise HTTPException(status_code=400, detail="OCA exits supported for IBKR accounts only")
    resp = ibkr_option_oca_exits(conn, req.symbol, req.opt_type, req.side, int(req.contracts), float(req.strike), str(req.expiry), req.tp_price, req.sl_price)
    return {"ok": True, "resp": resp}


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": "v1.8.0"}


@app.get("/analytics/profile/summary")
def analytics_profile_summary(name: str, days: int = 90, start_capital: float | None = None, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    return summary_filtered(cfg, days, None, name, start_capital)

@app.get("/analytics/account/summary")
def analytics_account_summary(account: str, days: int = 90, start_capital: float | None = None, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    return summary_filtered(cfg, days, account, None, start_capital)


@app.get("/analytics/profile/equity.png")
def analytics_profile_equity_png(name: str, days: int = 90, start_capital: float | None = None, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    dps = daily_pnl_series_filtered(cfg, days, None, name)
    cap = start_capital or float((cfg.get("risk") or {}).get("capital") or 10000.0)
    eq = cap; xs=[]; ys=[]
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    for d,p in dps: eq += p; xs.append(d); ys.append(eq)
    import io
    fig = plt.figure(figsize=(7,3.2)); plt.plot(xs, ys); plt.xticks(rotation=45); plt.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png"); plt.close(fig); buf.seek(0)
    from fastapi import Response as _Resp
    return _Resp(content=buf.read(), media_type="image/png")

@app.get("/analytics/account/equity.png")
def analytics_account_equity_png(account: str, days: int = 90, start_capital: float | None = None, x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    require_auth(x_api_key, authorization)
    cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
    dps = daily_pnl_series_filtered(cfg, days, account, None)
    cap = start_capital or float((cfg.get("risk") or {}).get("capital") or 10000.0)
    eq = cap; xs=[]; ys=[]
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    for d,p in dps: eq += p; xs.append(d); ys.append(eq)
    import io
    fig = plt.figure(figsize=(7,3.2)); plt.plot(xs, ys); plt.xticks(rotation=45); plt.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png"); plt.close(fig); buf.seek(0)
    from fastapi import Response as _Resp
    return _Resp(content=buf.read(), media_type="image/png")


from fastapi import BackgroundTasks

@app.on_event("startup")
def startup_event():
    try:
        cfg = load_config(os.getenv("CONFIG","/app/config.yaml"))
        if os.getenv("WATCHER_AUTOSTART","").lower() in ("1","true","yes"):
            try:
                WATCHER.start(cfg, _choose_conn)
            except Exception:
                pass
        prof = os.getenv("PROFILE_DEFAULT")
        if prof:
            try:
                from intradyne_lite.core.profiles import load_profiles
                p = load_profiles(os.getenv("PROFILES","/app/profiles.yaml")).get(prof)
                if p:
                    global ENABLED_TREND_FILTER, ENABLED_SENTIMENT_GATE, MIN_SENTIMENT_SCORE, ATR_MULTIPLIER, RISK_PER_TRADE_PCT, DAILY_MAX_LOSS_PCT, TIMEFRAME_DEFAULT, MA_N
                    ENABLED_TREND_FILTER = bool(p.get("trend_filter", True))
                    ENABLED_SENTIMENT_GATE = bool(p.get("sentiment_gate", False))
                    MIN_SENTIMENT_SCORE = float(p.get("min_sentiment", -0.2))
                    ATR_MULTIPLIER = float(p.get("atr_mult", 2.0))
                    RISK_PER_TRADE_PCT = float(p.get("risk_per_trade_pct", 0.01))
                    DAILY_MAX_LOSS_PCT = float(p.get("daily_max_loss_pct", 0.03))
                    TIMEFRAME_DEFAULT = str(p.get("timeframe", "1h"))
                    MA_N = int(p.get("ma_n", 50))
            except Exception:
                pass
    except Exception:
        pass
