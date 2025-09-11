
from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional
import sqlite3, datetime as dt, os, math

def _connect(cfg: dict):
    db = ((cfg.get("storage") or {}).get("sqlite_path")) or "/app/data/trades.sqlite"
    os.makedirs(os.path.dirname(db), exist_ok=True)
    con = sqlite3.connect(db)
    return con

def trades_recent(cfg: dict, limit: int = 100) -> List[Dict[str, Any]]:
    con = _connect(cfg); cur = con.cursor()
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS trades (ts TEXT, account TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, pnl REAL)")
        cur.execute("SELECT ts, account, symbol, side, qty, price, pnl FROM trades ORDER BY ts DESC LIMIT ?", (int(limit),))
        rows = cur.fetchall()
        return [ {"ts":r[0],"account":r[1],"symbol":r[2],"side":r[3],"qty":float(r[4]),"price":float(r[5]),"pnl":float(r[6])} for r in rows ]
    finally:
        con.close()

def daily_pnl_series(cfg: dict, days: int = 90) -> List[Tuple[str,float]]:
    con = _connect(cfg); cur = con.cursor()
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS trades (ts TEXT, account TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, pnl REAL)")
        since = (dt.datetime.utcnow().date() - dt.timedelta(days=int(days))).isoformat()
        cur.execute("SELECT substr(ts,1,10) d, COALESCE(SUM(pnl),0) FROM trades WHERE substr(ts,1,10) >= ? GROUP BY d ORDER BY d ASC", (since,))
        return [(r[0], float(r[1])) for r in cur.fetchall()]
    finally:
        con.close()

def summary(cfg: dict, start_capital: Optional[float] = None, days: int = 90) -> Dict[str, Any]:
    dps = daily_pnl_series(cfg, days)
    cap = start_capital or float((cfg.get("risk") or {}).get("capital") or 10000.0)
    eq = cap
    eq_curve = []
    wins = losses = 0
    total_pnl = 0.0
    for d, pnl in dps:
        eq += pnl
        eq_curve.append((d, eq))
        total_pnl += pnl
        if pnl > 0: wins += 1
        elif pnl < 0: losses += 1
    # metrics
    ret = (eq - cap)/cap if cap else 0.0
    winrate = wins / max((wins+losses),1)
    # drawdown
    peak = -1e18
    max_dd = 0.0
    for _, v in eq_curve:
        if v > peak: peak = v
        if peak>0:
            dd = (peak - v)/peak
            if dd > max_dd: max_dd = dd
    return {"start_capital": cap, "equity": eq, "return_pct": ret, "wins": wins, "losses": losses, "winrate": winrate, "max_drawdown_pct": max_dd, "period_days": days, "total_pnl": total_pnl, "points": len(eq_curve)}


def _conn(cfg: dict):
    import sqlite3, os
    db = ((cfg.get("storage") or {}).get("sqlite_path")) or "/app/data/trades.sqlite"
    os.makedirs(os.path.dirname(db), exist_ok=True)
    return sqlite3.connect(db)

def ensure_trade_schema(cfg: dict):
    con = _conn(cfg); cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS trades (ts TEXT, account TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, pnl REAL, strategy TEXT, profile TEXT, venue TEXT)")
    con.commit(); con.close()

def log_trade(cfg: dict, row: dict):
    ensure_trade_schema(cfg)
    con = _conn(cfg); cur = con.cursor()
    cur.execute("INSERT INTO trades(ts,account,symbol,side,qty,price,pnl,strategy,profile,venue) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (row.get("ts"), row.get("account"), row.get("symbol"), row.get("side"), float(row.get("qty",0)), float(row.get("price",0)), float(row.get("pnl",0)), row.get("strategy"), row.get("profile"), row.get("venue")))
    con.commit(); con.close()

def pnl_group(cfg: dict, group: str = "account", days: int = 90):
    con = _conn(cfg); cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS trades (ts TEXT, account TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, pnl REAL, strategy TEXT, profile TEXT, venue TEXT)")
    since = (dt.datetime.utcnow().date() - dt.timedelta(days=int(days))).isoformat()
    col = "account" if group not in ("strategy","profile","venue") else group
    cur.execute(f"SELECT COALESCE({col},'') g, COUNT(*), COALESCE(SUM(pnl),0), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), SUM(CASE WHEN pnl<0 THEN 1 ELSE 0 END) FROM trades WHERE substr(ts,1,10)>=? GROUP BY {col}", (since,))
    rows = cur.fetchall()
    con.close()
    out = []
    for g,cnt,sp,win,loss in rows:
        total = (win or 0)+(loss or 0)
        out.append({"group": g, "trades": int(cnt or 0), "pnl": float(sp or 0.0), "winrate": (float(win)/total) if total else 0.0})
    return out

def log_latency(cfg: dict, account: str, action: str, ms: float):
    con = _conn(cfg); cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS latency (ts TEXT, account TEXT, action TEXT, ms REAL)")
    cur.execute("INSERT INTO latency(ts,account,action,ms) VALUES (datetime('now'),?,?,?)", (account, action, float(ms)))
    con.commit(); con.close()

def latency_stats(cfg: dict, group: str = "action", days: int = 7):
    con = _conn(cfg); cur = con.cursor()
    since = (dt.datetime.utcnow() - dt.timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
    col = "action" if group not in ("account","action") else group
    cur.execute(f"SELECT {col}, COUNT(*), AVG(ms), MAX(ms) FROM latency WHERE ts>=? GROUP BY {col}", (since,))
    rows = cur.fetchall(); con.close()
    return [{"group": r[0], "count": int(r[1] or 0), "avg_ms": float(r[2] or 0.0), "max_ms": float(r[3] or 0.0)} for r in rows]


def daily_pnl_series_filtered(cfg: dict, days: int = 90, account: str | None = None, profile: str | None = None):
    con = _connect(cfg); cur = con.cursor()
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS trades (ts TEXT, account TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, pnl REAL, strategy TEXT, profile TEXT, venue TEXT)")
        since = (dt.datetime.utcnow().date() - dt.timedelta(days=int(days))).isoformat()
        q = "SELECT substr(ts,1,10) d, COALESCE(SUM(pnl),0) FROM trades WHERE substr(ts,1,10) >= ?"
        params = [since]
        if account:
            q += " AND account=?"; params.append(account)
        if profile:
            q += " AND profile=?"; params.append(profile)
        q += " GROUP BY d ORDER BY d ASC"
        cur.execute(q, tuple(params))
        return [(r[0], float(r[1])) for r in cur.fetchall()]
    finally:
        con.close()

def summary_filtered(cfg: dict, days: int = 90, account: str | None = None, profile: str | None = None, start_capital: float | None = None):
    dps = daily_pnl_series_filtered(cfg, days, account, profile)
    cap = start_capital or float((cfg.get("risk") or {}).get("capital") or 10000.0)
    eq = cap
    eq_curve = []
    wins = losses = 0
    total_pnl = 0.0
    for d, pnl in dps:
        eq += pnl
        eq_curve.append((d, eq))
        total_pnl += pnl
        if pnl > 0: wins += 1
        elif pnl < 0: losses += 1
    ret = (eq - cap)/cap if cap else 0.0
    winrate = wins / max((wins+losses),1)
    peak = -1e18; max_dd = 0.0
    for _, v in eq_curve:
        if v > peak: peak = v
        if peak>0:
            dd = (peak - v)/peak
            if dd > max_dd: max_dd = dd
    return {"start_capital": cap, "equity": eq, "return_pct": ret, "wins": wins, "losses": losses, "winrate": winrate, "max_drawdown_pct": max_dd, "period_days": days, "total_pnl": total_pnl, "points": len(eq_curve)}
