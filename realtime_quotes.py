"""实时行情模块 — AkShare + 同花顺数据融合"""
import threading, time
from datetime import datetime

_quote_cache = {}
_cache_time = None
_cache_ttl = 30  # 缓存30秒

def is_trading_time():
    """A股交易时间"""
    now = datetime.now()
    t = now.hour * 100 + now.minute
    w = now.weekday()
    if w >= 5: return False
    return (930 <= t <= 1130) or (1300 <= t <= 1505)

def fetch_realtime_quotes(symbols=None):
    """获取实时行情 — AkShare 东方财富接口, 非交易时段回退到价格缓存"""
    global _quote_cache, _cache_time

    now = datetime.now()
    if _cache_time and (now - _cache_time).seconds < _cache_ttl and _quote_cache.get("data"):
        return _quote_cache

    # 尝试获取实时数据
    if is_trading_time():
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            quotes = {}
            for _, row in df.iterrows():
                code = str(row.get('代码', ''))
                quotes[code] = {
                    'name': str(row.get('名称', '')),
                    'close': float(row.get('最新价', 0) or 0),
                    'change_pct': float(row.get('涨跌幅', 0) or 0),
                    'volume': float(row.get('成交量', 0) or 0),
                    'amount': float(row.get('成交额', 0) or 0),
                    'high': float(row.get('最高', 0) or 0),
                    'low': float(row.get('最低', 0) or 0),
                    'open': float(row.get('今开', 0) or 0),
                    'vol_ratio': float(row.get('量比', 0) or 0),
                    'turnover': float(row.get('换手率', 0) or 0),
                }
            _quote_cache = {"status": "ok", "count": len(quotes), "data": quotes, "time": now.strftime("%H:%M:%S")}
            _cache_time = now
            return _quote_cache
        except Exception as e:
            return {"status": "error", "message": str(e), "data": {}}

    # 非交易时段: 从价格缓存加载最新收盘价
    try:
        import json, os
        pf = r"d:\quant_framework\price_cache.json"
        if os.path.exists(pf):
            with open(pf, 'r') as f:
                pc = json.load(f)
            quotes = {}
            for code, price in list(pc.items())[:5000]:
                quotes[code] = {'name': '', 'close': float(price), 'change_pct': 0,
                               'volume': 0, 'amount': 0, 'high': float(price),
                               'low': float(price), 'open': float(price),
                               'vol_ratio': 1.0, 'turnover': 0}
            _quote_cache = {"status": "closed", "count": len(quotes), "data": quotes,
                          "time": now.strftime("%H:%M:%S"), "message": "非交易时段, 显示最近收盘价"}
            _cache_time = now
            return _quote_cache
    except Exception:
        pass

    return {"status": "closed", "message": "非交易时段, 无缓存数据", "data": {}}


# 后台自动刷新 (仅交易时段)
_refresh_thread = None

def start_bg_refresh():
    global _refresh_thread
    if _refresh_thread: return
    def _loop():
        while True:
            if is_trading_time():
                try: fetch_realtime_quotes()
                except: pass
            time.sleep(30)
    _refresh_thread = threading.Thread(target=_loop, daemon=True)
    _refresh_thread.start()
