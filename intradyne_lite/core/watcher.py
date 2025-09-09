
from __future__ import annotations
from typing import Optional, Dict, Any
import threading, time, sqlite3, os

class BracketWatcher:
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()

    def start(self, cfg: dict, get_conn):
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(cfg, get_conn), daemon=True)
        self._thread.start()
        return True

    def stop(self):
        if self._thread:
            self._stop.set()
            return True
        return False

    def _loop(self, cfg: dict, get_conn):
        db = ((cfg.get("storage") or {}).get("sqlite_path")) or "/app/data/trades.sqlite"
        os.makedirs(os.path.dirname(db), exist_ok=True)
        while not self._stop.is_set():
            try:
                con = sqlite3.connect(db)
                cur = con.cursor()
                cur.execute("CREATE TABLE IF NOT EXISTS virtual_brackets(id INTEGER PRIMARY KEY, account TEXT, symbol TEXT, side TEXT, qty REAL, tp REAL, sl REAL, active INTEGER DEFAULT 1)")
                cur.execute("SELECT id, account, symbol, side, qty, tp, sl FROM virtual_brackets WHERE active=1")
                rows = cur.fetchall()
                con.close()
                for (bid, account, symbol, side, qty, tp, sl) in rows:
                    # poll price
                    cfg_local = cfg
                    conn, _acct = get_conn(cfg_local, account)
                    price = float(conn.get_price(symbol))
                    if side.lower()=="buy":
                        hit_tp = (tp is not None and price >= float(tp))
                        hit_sl = (sl is not None and price <= float(sl))
                        close_side = "sell"
                    else:
                        hit_tp = (tp is not None and price <= float(tp))
                        hit_sl = (sl is not None and price >= float(sl))
                        close_side = "buy"
                    if hit_tp or hit_sl:
                        try:
                            conn.place_order(symbol, close_side, float(qty), None, None, None)  # market close
                        except Exception:
                            pass
                        con2 = sqlite3.connect(db); c2 = con2.cursor()
                        c2.execute("UPDATE virtual_brackets SET active=0 WHERE id=?", (bid,))
                        con2.commit(); con2.close()
                time.sleep(2.0)
            except Exception:
                time.sleep(3.0)

WATCHER = BracketWatcher()

def register_bracket(cfg: dict, account: str, symbol: str, side: str, qty: float, tp: float | None, sl: float | None) -> int:
    db = ((cfg.get("storage") or {}).get("sqlite_path")) or "/app/data/trades.sqlite"
    os.makedirs(os.path.dirname(db), exist_ok=True)
    con = sqlite3.connect(db); cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS virtual_brackets(id INTEGER PRIMARY KEY, account TEXT, symbol TEXT, side TEXT, qty REAL, tp REAL, sl REAL, active INTEGER DEFAULT 1)")
    cur.execute("INSERT INTO virtual_brackets(account, symbol, side, qty, tp, sl, active) VALUES (?,?,?,?,?,?,1)", (account, symbol, side, qty, tp, sl))
    bid = cur.lastrowid
    con.commit(); con.close()
    return int(bid)
