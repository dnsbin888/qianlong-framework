def _bd_to_df(bh):
    import pandas as pd
    return pd.DataFrame({
        "close": bh["c"], "high": bh["h"], "low": bh["l"],
        "volume": bh["v"], "open": bh["o"],
    })


def _tdx_notice(ql_sym, qmt_code, price, sig_id):
    """TDX公式命中 → 推审核通道(前端可见), 绝不下单"""
    def _post():
        try:
            import urllib.request
            data = json.dumps({"symbol": ql_sym, "signal_type": "TDX公式",
                "price": round(price, 2), "channel": "review", "enabled": False,
                "qmt_code": qmt_code, "signal_id": sig_id}).encode()
            urllib.request.urlopen(urllib.request.Request(FLASK_URL, data=data,
                headers={"Content-Type": "application/json"}), timeout=3)
        except Exception as _e:
            print(f"[QMT] TDX推送Flask失败: {_e}")
    import threading
    threading.Thread(target=_post, daemon=True).start()


def _check_tdx(df):
    global _tdx_formulas
    try:
        if _tdx_formulas is None:
            _tdx_formulas = json.load(open(r"d:\quant_framework\signal_config.json", encoding="utf-8")).get("tdx_formulas", {})
        from qmt_strategies.tdx_formulas import check_tdx_formula
        for _fn, _on in _tdx_formulas.items():
            if _on and check_tdx_formula(_fn, df):
                return True
    except Exception:
        pass
    return False


def on_stock_trade(ContextInfo, trade):
    pass


def on_account_status(ContextInfo, account):
    pass
