"""QMT 行情桥接子进程 (Python 3.11)
=================================
独立进程: 订阅 QMT 全市场实时行情 → 写入 quote_cache.json → Flask 读取

启动方式:
    C:\Python311\python.exe D:\quant_framework\qmt_bridge_worker.py

依赖:
    - QMT XtMiniQmt.exe 已登录
    - xtquant 在 D:\国金证券QMT交易端\bin.x64\Lib\site-packages
"""
import sys
import os
import json
import time
import threading

# ── 路径 ──
QMT_SITE = r"D:\国金证券QMT交易端\bin.x64\Lib\site-packages"
CACHE_FILE = r"D:\quant_framework\quote_cache.json"
if QMT_SITE not in sys.path:
    sys.path.insert(0, QMT_SITE)

# ── 全局缓存 ──
_quote_cache = {}       # {code: {price, change_pct, volume, time}}
_last_flush = 0
_stats = {"started": "", "ticks": 0, "last_tick": "", "symbols": 0, "errors": 0}
_lock = threading.Lock()

def _on_tick(datas: dict):
    """QMT 回调: 收到实时行情"""
    global _quote_cache, _stats
    if not datas:
        return
    try:
        with _lock:
            for code, tick in datas.items():
                if not tick or not isinstance(tick, dict):
                    continue
                price = tick.get("lastPrice", 0)
                if price <= 0:
                    continue
                _quote_cache[code] = {
                    "price": float(price),
                    "change_pct": round(float(tick.get("pctChg", 0) or 0), 2),
                    "volume": int(tick.get("volume", 0) or 0),
                    "amount": float(tick.get("amount", 0) or 0),
                    "time": tick.get("time", ""),
                }
            _stats["ticks"] += 1
            _stats["symbols"] = len(_quote_cache)
    except Exception:
        _stats["errors"] += 1


def _flush_loop():
    """每秒将缓存写入 JSON 文件"""
    global _last_flush
    while True:
        time.sleep(1)
        try:
            with _lock:
                # 只保留有价格的股票, 限制 5000 只防文件过大
                slim = dict(list(_quote_cache.items())[:5000])
            tmp = CACHE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "count": len(slim),
                    "data": slim,
                }, f, ensure_ascii=False)
            os.replace(tmp, CACHE_FILE)
            _last_flush = time.time()
        except Exception:
            pass


def main():
    print("[QMT-Bridge] 启动 Python 3.11 行情桥接...")

    # 导入 xtquant
    try:
        from xtquant import xtdata
    except ImportError as e:
        print(f"[QMT-Bridge] FATAL: xtquant 导入失败: {e}")
        print(f"  sys.path: {sys.path[:5]}")
        sys.exit(1)

    # 连接 QMT
    try:
        xtdata.reconnect()
        print("[QMT-Bridge] xtdata.reconnect() OK")
    except Exception as e:
        print(f"[QMT-Bridge] FATAL: QMT 连接失败: {e}")
        print("  请确认 XtMiniQmt.exe 已登录")
        sys.exit(1)

    # 启动 flush 线程
    flush_thread = threading.Thread(target=_flush_loop, daemon=True)
    flush_thread.start()

    # 启动 xtdata.run() daemon
    run_thread = threading.Thread(target=xtdata.run, daemon=True)
    run_thread.start()
    print("[QMT-Bridge] xtdata.run() daemon 已启动")

    # 订阅全市场行情
    try:
        seq = xtdata.subscribe_whole_quote(["SH", "SZ"], callback=_on_tick)
        print(f"[QMT-Bridge] subscribe_whole_quote OK, seq={seq}")
    except Exception as e:
        print(f"[QMT-Bridge] 订阅失败: {e}")
        sys.exit(1)

    _stats["started"] = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[QMT-Bridge] 全市场行情桥接已启动 → {CACHE_FILE}")

    # 保持运行, 监控状态
    while True:
        time.sleep(10)
        print(f"[QMT-Bridge] 心跳: {_stats['symbols']}只, "
              f"{_stats['ticks']}次推送, 错误{_stats['errors']}")


if __name__ == "__main__":
    main()
