"""通达信信号实时监控 — tpool目录变化自动同步到平台"""
import os, time, threading, json
from datetime import datetime

TPOOL_DIR = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\T0002\tpool"
STATE_FILE = r"d:\quant_framework\tdx_signals.json"

def scan_tpool():
    """扫描tpool目录，返回最新公式选股结果"""
    if not os.path.isdir(TPOOL_DIR):
        return {"status": "error", "message": "tpool目录不存在"}

    pools = {}
    for fname in os.listdir(TPOOL_DIR):
        if not fname.endswith('.xml'): continue
        fpath = os.path.join(TPOOL_DIR, fname)
        name = fname.replace('.xml', '')
        mtime = os.path.getmtime(fpath)
        size = os.path.getsize(fpath)
        pools[name] = {"file": fname, "size": size, "updated": datetime.fromtimestamp(mtime).strftime("%H:%M:%S")}

    return {"status": "ok", "tpool": TPOOL_DIR, "pools": pools, "count": len(pools), "scan_time": datetime.now().strftime("%H:%M:%S")}


def watch_tpool(callback=None, interval=30):
    """后台监控tpool变化，有新结果时回调"""
    last_state = {}

    def _loop():
        nonlocal last_state
        while True:
            try:
                result = scan_tpool()
                if result["status"] == "ok":
                    current = result["pools"]
                    # 检测新增或修改
                    changed = []
                    for name, info in current.items():
                        if name not in last_state or last_state[name]["size"] != info["size"]:
                            changed.append(name)
                    if changed and callback:
                        callback(changed, result)
                    last_state = current
                    # 写入状态文件供API使用
                    with open(STATE_FILE, 'w', encoding='utf-8') as f:
                        json.dump(result, f, ensure_ascii=False)
            except: pass
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    print(f"[TDXWatcher] Monitoring {TPOOL_DIR} every 30s")


def get_latest_signals():
    """读取最新缓存的信号状态"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return scan_tpool()
