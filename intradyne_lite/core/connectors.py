
from __future__ import annotations
from typing import Any, Dict, Optional, List
import time

class BaseConn:
    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200): raise NotImplementedError
    def get_price(self, symbol: str) -> float: raise NotImplementedError
    def place_order(self, symbol: str, side: str, qty: float, price: Optional[float]=None, sl: Optional[float]=None, tp: Optional[float]=None) -> Dict[str,Any]:
        raise NotImplementedError
    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Dict[str,Any]: raise NotImplementedError
    def list_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str,Any]]: raise NotImplementedError

class CCXTConn(BaseConn):
    def __init__(self, exchange_id: str, apiKey: Optional[str]=None, secret: Optional[str]=None, password: Optional[str]=None, sandbox: bool=False, params: Dict[str,Any]|None=None):
        try:
            import ccxt
        except Exception as e:
            raise RuntimeError("ccxt not installed") from e
        if not hasattr(ccxt, exchange_id):
            raise RuntimeError(f"ccxt exchange '{exchange_id}' not found")
        ex_cls = getattr(ccxt, exchange_id)
        self.client = ex_cls({ "apiKey": apiKey, "secret": secret, "password": password, **(params or {}) })
        if sandbox and hasattr(self.client, "set_sandbox_mode"):
            self.client.set_sandbox_mode(True)
    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200):
        return self.client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    def get_price(self, symbol: str) -> float:
        t = self.client.fetch_ticker(symbol)
        return float(t.get("last") or t.get("close") or 0.0)
    def place_order(self, symbol: str, side: str, qty: float, price: Optional[float]=None, sl: Optional[float]=None, tp: Optional[float]=None):
        side = side.lower()
        typ = "market" if price is None else "limit"
        params = {}
        o = self.client.create_order(symbol, typ, side, qty, price, params)
        resp = {"primary": o}
        # Best-effort bracket: create TP limit & SL stop if supported
        try:
            if tp:
                resp["tp"] = self.client.create_order(symbol, "limit", "sell" if side=="buy" else "buy", qty, tp, {})
            if sl:
                # Stop orders vary per exchange; try stop params
                params_sl = {}
                if "binance" in self.client.id:
                    params_sl = {"stopPrice": sl, "type":"STOP_MARKET"}
                    resp["sl"] = self.client.create_order(symbol, "STOP_MARKET", "sell" if side=="buy" else "buy", qty, None, params_sl)
                else:
                    # generic fallback: place market stop via params if available
                    resp["sl"] = {"note":"Manual stop required if exchange lacks stop support"}
        except Exception as e:
            resp["bracket_error"] = str(e)
        return resp
    def cancel_order(self, order_id: str, symbol: Optional[str] = None):
        return self.client.cancel_order(order_id, symbol) if symbol else self.client.cancel_order(order_id)
    def list_open_orders(self, symbol: Optional[str] = None):
        return self.client.fetch_open_orders(symbol)

class AlpacaConn(BaseConn):
    def __init__(self, key: str, secret: str, base_url: str):
        try:
            from alpaca_trade_api import REST
        except Exception as e:
            raise RuntimeError("alpaca-trade-api not installed") from e
        self.client = REST(key, secret, base_url)
    def fetch_ohlcv(self, symbol: str, timeframe: str = "1Hour", limit: int = 200):
        # Alpaca crypto & equities bars unify under get_bars v2 in newer API; here minimal stub not to hard-depend versions
        return []
    def get_price(self, symbol: str) -> float:
        try:
            q = self.client.get_latest_trade(symbol)
            return float(q.price)
        except Exception:
            # try crypto quotes
            try:
                q = self.client.get_latest_crypto_trade(symbol)
                return float(q.price)
            except Exception:
                return 0.0
    def place_order(self, symbol: str, side: str, qty: float, price: Optional[float]=None, sl: Optional[float]=None, tp: Optional[float]=None):
        side = side.lower()
        typ = "market" if price is None else "limit"
        order_class = None
        take_profit = stop_loss = None
        if sl or tp:
            order_class = "bracket"
            if tp:
                take_profit = {"limit_price": float(tp)}
            if sl:
                stop_loss = {"stop_price": float(sl)}
        o = self.client.submit_order(symbol=symbol, qty=qty, side=side, type=typ, time_in_force="day",
                                     order_class=order_class, take_profit=take_profit, stop_loss=stop_loss,
                                     limit_price=float(price) if price else None)
        return {"id": getattr(o, "id", None) or getattr(o, "order_id", None), "raw": str(o)}
    def cancel_order(self, order_id: str, symbol: Optional[str] = None):
        self.client.cancel_order(order_id); return {"canceled": order_id}
    def list_open_orders(self, symbol: Optional[str] = None):
        return [o._raw for o in self.client.list_orders(status="open")]

class IBKRConn(BaseConn):
    def __init__(self, host: str="127.0.0.1", port: int=7497, clientId: int=1):
        try:
            from ib_insync import IB, util, MarketOrder, LimitOrder, Stock, Crypto
        except Exception as e:
            raise RuntimeError("ib-insync not installed") from e
        self.IB = IB; self.util = util; self.MarketOrder = MarketOrder; self.LimitOrder = LimitOrder; self.Stock = Stock; self.Crypto = Crypto
        self.ib = IB()
        self.ib.connect(host, port, clientId=clientId)
    def fetch_ohlcv(self, symbol: str, timeframe: str = "1 hour", limit: int = 200):
        return []
    def get_price(self, symbol: str) -> float:
        # naive mapping: STOCK by default
        c = self.Stock(symbol, "SMART", "USD")
        t = self.ib.reqMktData(c, "", False, False)
        self.ib.sleep(1)
        return float(t.last) if t.last else 0.0
    def place_order(self, symbol: str, side: str, qty: float, price: Optional[float]=None, sl: Optional[float]=None, tp: Optional[float]=None):
        contract = self.Stock(symbol, "SMART", "USD")
        order = self.MarketOrder(side.capitalize(), qty) if price is None else self.LimitOrder(side.capitalize(), qty, price)
        trade = self.ib.placeOrder(contract, order)
        # bracket handling omitted for brevity; users can manage via TWS
        self.ib.sleep(1)
        return {"orderId": trade.order.orderId, "status": trade.orderStatus.status}
    def cancel_order(self, order_id: str, symbol: Optional[str] = None):
        self.ib.cancelOrderByPermId(int(order_id)); return {"canceled": order_id}
    def list_open_orders(self, symbol: Optional[str] = None):
        return [t.order.__dict__ for t in self.ib.trades() if t.orderStatus.status in ("Submitted","PreSubmitted")]

def build_conn(cfg: Dict[str,Any], account_id: Optional[str] = None) -> (BaseConn, Dict[str,Any]):
    accounts = (cfg.get("accounts") or [])
    acc = None
    if account_id:
        for a in accounts:
            if a.get("id")==account_id: acc=a; break
    if not acc and accounts:
        acc = accounts[0]
    if not acc:
        # fallback dummy
        class Dummy(BaseConn):
            def fetch_ohlcv(self, symbol, timeframe="1h", limit=200):
                now = int(time.time()*1000)
                return [[now- i*3600_000, 100, 101, 99, 100+(i%5-2)*0.1, 1] for i in range(limit)][::-1]
            def get_price(self, symbol): return 100.0
            def place_order(self, *a, **k): return {"note":"dummy"}
            def cancel_order(self, *a, **k): return {"note":"dummy"}
            def list_open_orders(self, *a, **k): return []
        return Dummy(), {"id":"default", "kind":"dummy"}
    kind = (acc.get("kind") or "").lower()
    if kind=="ccxt":
        p = acc.get("params") or {}
        return CCXTConn(acc["exchange_id"], acc.get("apiKey"), acc.get("secret"), acc.get("password"), bool(acc.get("sandbox")), p), acc
    if kind=="alpaca":
        return AlpacaConn(acc["key"], acc["secret"], acc.get("base_url","https://paper-api.alpaca.markets")), acc
    if kind=="ibkr":
        return IBKRConn(acc.get("host","127.0.0.1"), int(acc.get("port",7497)), int(acc.get("clientId",1))), acc
    raise RuntimeError(f"Unknown account kind: {kind}")


# --- IBKR Options helpers ---
def ibkr_place_covered_call(conn_obj, symbol: str, qty: int, strike: float, expiry: str):
    """Place covered call: buy shares + sell call option. expiry 'YYYYMMDD'. qty in shares."""
    try:
        from ib_insync import Option, MarketOrder, LimitOrder
    except Exception as e:
        raise RuntimeError("ib-insync not installed for IBKR options") from e
    ib = conn_obj.ib
    stock = conn_obj.Stock(symbol, "SMART", "USD")
    # 1. Buy shares (market)
    tr_stock = ib.placeOrder(stock, MarketOrder("BUY", qty)); ib.sleep(0.5)
    # 2. Sell calls
    contracts = max(int(qty // 100), 1)
    opt = Option(symbol, expiry, float(strike), "C", "SMART", "USD")
    tr_call = ib.placeOrder(opt, MarketOrder("SELL", contracts)); ib.sleep(0.5)
    return {"stockOrderId": tr_stock.order.orderId, "callOrderId": tr_call.order.orderId, "contracts": contracts}

def ibkr_place_protective_put(conn_obj, symbol: str, qty: int, strike: float, expiry: str):
    """Protective put: buy shares + buy put option. expiry 'YYYYMMDD'. qty in shares."""
    try:
        from ib_insync import Option, MarketOrder
    except Exception as e:
        raise RuntimeError("ib-insync not installed for IBKR options") from e
    ib = conn_obj.ib
    stock = conn_obj.Stock(symbol, "SMART", "USD")
    tr_stock = ib.placeOrder(stock, MarketOrder("BUY", qty)); ib.sleep(0.5)
    contracts = max(int(qty // 100), 1)
    opt = Option(symbol, expiry, float(strike), "P", "SMART", "USD")
    tr_put = ib.placeOrder(opt, MarketOrder("BUY", contracts)); ib.sleep(0.5)
    return {"stockOrderId": tr_stock.order.orderId, "putOrderId": tr_put.order.orderId, "contracts": contracts}
