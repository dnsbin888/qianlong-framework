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
                    # 解析XML提取股票代码供QMT使用
                    parse_tpool_stocks()
            except: pass
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    print(f"[TDXWatcher] Monitoring {TPOOL_DIR} every 30s")


TDX_STOCKS_FILE = r"d:\quant_framework\tdx_pool_stocks.json"
TDX_POOL_TXT = r"D:\quant_web\data\custom_pools"


def parse_tpool_stocks():
    """解析tpool XML + custom_pools txt → JSON供QMT读 (自动化)"""
    import re, struct

    # 加载已知股票代码库 (过滤假阳)
    valid_codes = set()
    try:
        csv_path = r"D:\quant_web\stock_names_full.csv"
        if os.path.exists(csv_path):
            with open(csv_path, encoding="utf-8") as f:
                for line in f:
                    code = line.split(",")[0].strip()
                    if code.isdigit() and len(code) == 6:
                        valid_codes.add(code)
    except Exception:
        pass

    result = {}

    # 方式1: tpool XML (TDX自动选股, 二进制嵌入)
    if os.path.isdir(TPOOL_DIR):
        for fname in sorted(os.listdir(TPOOL_DIR)):
            if not fname.endswith('.xml'):
                continue
            fpath = os.path.join(TPOOL_DIR, fname)
            try:
                with open(fpath, "rb") as f:
                    raw = f.read()
                # 解码二进制: 搜索6位数字序列
                codes = set()
                i = 0
                while i < len(raw) - 1:
                    if 0x30 <= raw[i] <= 0x39:  # 数字开始
                        chunk = raw[i:i+6]
                        if len(chunk) == 6 and all(0x30 <= b <= 0x39 for b in chunk):
                            code = chunk.decode('ascii')
                            codes.add(code)
                            i += 6
                            continue
                    i += 1
                stocks = []
                for c in codes:
                    if valid_codes and c not in valid_codes:
                        continue
                    prefix = "sh" if c.startswith(("6", "5", "9")) else "sz"
                    stocks.append(prefix + c)
                if stocks:
                    result[fname.replace('.xml', '')] = {
                        "count": len(stocks),
                        "stocks": sorted(stocks),
                        "updated": datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%H:%M:%S"),
                        "source": "auto",
                    }
            except Exception:
                pass

    # 方式2: custom_pools txt (手动导出, 补充)
    if os.path.isdir(TDX_POOL_TXT):
        for fname in sorted(os.listdir(TDX_POOL_TXT)):
            if not fname.endswith('.txt'):
                continue
            fpath = os.path.join(TDX_POOL_TXT, fname)
            try:
                with open(fpath, "r", encoding="gbk", errors="ignore") as f:
                    raw = f.read()
                codes = set(re.findall(r'\b(\d{6})\b', raw))
                stocks = []
                for c in codes:
                    if valid_codes and c not in valid_codes:
                        continue
                    prefix = "sh" if c.startswith(("6", "5", "9")) else "sz"
                    stocks.append(prefix + c)
                if stocks:
                    name = fname.replace('.txt', '')
                    if name not in result:
                        result[name] = {
                            "count": len(stocks),
                            "stocks": sorted(stocks),
                            "updated": datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%H:%M:%S"),
                            "source": "manual",
                        }
            except Exception:
                pass

    with open(TDX_STOCKS_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False)
    total = sum(v['count'] for v in result.values())
    auto_n = sum(1 for v in result.values() if v.get('source') == 'auto')
    print(f"[TDXParser] {len(result)}池 {total}只 (自动{auto_n}池 + 手动{len(result)-auto_n}池)")
    return result
    """解析tpool XML → 正则提取有效A股代码 → JSON供QMT读"""
    import re

    if not os.path.isdir(TPOOL_DIR):
        return {}

    # 加载已知股票代码库 (过滤假阳)
    valid_codes = set()
    try:
        csv_path = r"D:\quant_web\stock_names_full.csv"
        if os.path.exists(csv_path):
            with open(csv_path, encoding="utf-8") as f:
                for line in f:
                    code = line.split(",")[0].strip()
                    if code.isdigit() and len(code) == 6:
                        valid_codes.add(code)
    except Exception:
        pass

    if not valid_codes:
        # 兜底: A股前缀过滤
        valid_prefix = tuple(f"{c}{d}" for c in "0368" for d in "0123456789")
    else:
        valid_prefix = None

    result = {}
    for fname in sorted(os.listdir(TPOOL_DIR)):
        if not fname.endswith('.xml'):
            continue
        fpath = os.path.join(TPOOL_DIR, fname)
        try:
            with open(fpath, "r", encoding="gbk", errors="ignore") as f:
                raw = f.read()
            # 匹配6位数字，过滤到真实A股代码
            all_codes = set(re.findall(r'\b(\d{6})\b', raw))
            stocks = []
            for c in all_codes:
                if valid_codes and c in valid_codes:
                    prefix = "sh" if c.startswith(("6", "5", "9")) else "sz"
                    stocks.append(prefix + c)
                elif not valid_codes and c.startswith(valid_prefix):
                    prefix = "sh" if c.startswith(("6", "5", "9")) else "sz"
                    stocks.append(prefix + c)
            stocks = list(set(stocks))  # 去重
            if stocks:
                result[fname.replace('.xml', '')] = {
                    "count": len(stocks),
                    "stocks": sorted(stocks),
                    "updated": datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%H:%M:%S"),
                }
        except Exception:
            pass

    with open(TDX_STOCKS_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"[TDXParser] {len(result)}个池解析完成, 写入 {TDX_STOCKS_FILE}")
    return result


def factor_tdx_signal(df) -> float | None:
    """TDX公式信号因子 (注册到factor_registry)

    读取最新TDX tpool选股结果, 返回信号覆盖度评分(0-100)。
    df参数不使用(全局信号源, 对标factor_westock模式)。
    信号池越活跃=市场短线机会越多=评分越高。

    用法: 注册到factor_registry.json → LGBM/XGBoost自动学习
    """
    try:
        stocks = {}
        if os.path.exists(TDX_STOCKS_FILE):
            with open(TDX_STOCKS_FILE, 'r', encoding='utf-8') as f:
                stocks = json.load(f)
        if not stocks:
            return None
        total = sum(v.get('count', 0) for v in stocks.values())
        n_pools = len(stocks)
        # 信号覆盖度: 池数×10 + 股票数×0.5, 封顶100
        score = min(100, n_pools * 10 + total * 0.5)
        return round(score, 1)
    except Exception:
        return None


def get_latest_signals():
    """读取最新缓存的信号状态"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return scan_tpool()
