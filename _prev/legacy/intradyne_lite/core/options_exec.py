

def ibkr_option_oca_exits(conn, symbol: str, opt_type: str, side: str, contracts: int, strike: float, expiry: str, tp_price: float | None, sl_price: float | None):
    """Create OCA group exits for an option position.
    opt_type: 'CALL' or 'PUT'
    side: 'long' or 'short' (your existing position direction)
    """
    if not hasattr(conn, "ib") or not hasattr(conn, "Option"):
        raise RuntimeError("IBKR connection required for options exits")
    ib = conn.ib
    Option = conn.Option; Order = conn.Order
    action_close = "SELL" if side.lower()=="long" else "BUY"
    exp = expiry.replace("-","")
    opt = Option(symbol, exp, float(strike), "C" if opt_type.upper().startswith("C") else "P", "SMART")
    oca = f"OCA_OPT_{int(dt.datetime.now().timestamp())}"
    # TP limit
    if tp_price is not None:
        tp = Order()
        tp.orderType = "LMT"
        tp.action = action_close
        tp.totalQuantity = int(contracts)
        tp.lmtPrice = float(tp_price)
        tp.ocaGroup = oca; tp.ocaType = 1
        ib.placeOrder(opt, tp); ib.sleep(0.2)
    # SL stop
    if sl_price is not None:
        sl = Order()
        sl.orderType = "STP"
        sl.action = action_close
        sl.totalQuantity = int(contracts)
        sl.auxPrice = float(sl_price)
        sl.ocaGroup = oca; sl.ocaType = 1
        ib.placeOrder(opt, sl); ib.sleep(0.2)
    return {"ok": True, "ocaGroup": oca, "contracts": int(contracts), "tp": tp_price, "sl": sl_price}
