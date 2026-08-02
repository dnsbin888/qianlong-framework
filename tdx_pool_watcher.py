"""
TDX原生公式信号桥接 v2.0 — 策略E·自动执行
============================================
支持两种模式:
  type=blk  → 自动选股→板块(.blk)  每种格式: 市场码1位+代码6位=7位
  type=pool → 股票池→日志(.log)     XML格式: market属性+coded属性

用法: python tdx_pool_watcher.py [--once]
      后台持续运行, 每5秒扫描源文件mtime
"""
import os, json, sys, time, xml.etree.ElementTree as ET
sys.path.insert(0, r"D:\quant_web")  # stock_names 模块路径

# ══════════ 配置 ══════════
TDX_T0002 = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\T0002"
BLOCK_DIR = os.path.join(TDX_T0002, "blocknew")
POOL_DIR = os.path.join(TDX_T0002, "tpool")
OUTPUT_PATH = r"D:\quant_web\data\tdx_live_signals.json"
SCAN_INTERVAL = 2  # 秒 (对齐QMT快速通道)
MARKET_BLK = {"0": "sz", "1": "sh"}
MARKET_LOG = {"0": "sz", "1": "sh"}


def load_pool_config():
    # 优先读新配置, 降级读旧 signal_config
    new_cfg = r"D:\quant_web\data\tdx_pools_config.json"
    old_cfg = r"D:\quant_framework\signal_config.json"
    cfg_path = new_cfg if os.path.exists(new_cfg) else old_cfg
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        pools = cfg.get("pools", cfg.get("tdx_pools", {}))
        return {k: v for k, v in pools.items() if isinstance(v, dict) and v.get("enabled")}
    except Exception:
        return {}


def _get_stock_name(symbol):
    """从 stock_names 获取名称 (全量CSV)"""
    try:
        from stock_names import get_stock_name, init_names
        init_names()
        n = get_stock_name(symbol)
        if n and n != symbol and not str(n).isdigit():
            return n
    except Exception:
        pass
    return ""


def parse_blk(filepath):
    """解析.blk文件: 每行"市场码+6位代码" → [{symbol, name, date, detected_at}]"""
    signals = []
    today = time.strftime("%Y%m%d")
    detected = time.strftime("%Y-%m-%d %H:%M:%S")
    # 使用文件修改时间作为选股日期
    try:
        file_mtime = time.strftime("%Y%m%d", time.localtime(os.path.getmtime(filepath)))
    except Exception:
        file_mtime = today
    try:
        with open(filepath, "r", encoding="gbk", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or len(line) < 7:
                    continue
                mkt = line[0]
                code = line[1:7]
                prefix = MARKET_BLK.get(mkt, "")
                if not prefix:
                    continue
                sym = f"{prefix}{code}"
                name = _get_stock_name(sym)
                signals.append({
                    "symbol": sym,
                    "name": name,
                    "date": file_mtime,
                    "detected_at": detected,
                })
    except Exception as e:
        print(f"[TDX] 解析 {filepath} 失败: {e}")
    return signals


def parse_log(filepath):
    """解析股票池.log: XML → 全12字段 (代码/名称/时间/进入价/现价/涨幅/收益/总手/最高收益/最高周期/最高日期/最高价)"""
    signals = []
    try:
        tree = ET.parse(filepath)
        for stk in tree.getroot().iter("stk"):
            mkt = stk.get("market", "")
            code = stk.get("code", "")
            prefix = MARKET_LOG.get(mkt, "")
            if not prefix or not code:
                continue
            sym = f"{prefix}{code.zfill(6)}"
            name = stk.get("name", "") or _get_stock_name(sym)
            in_price = float(stk.get("inprice", 0) or 0)
            cur_price = float(stk.get("price", 0) or 0)  # 现价
            chg = float(stk.get("chgratio", 0) or 0)     # 涨幅% (可能不含%号)
            profit = float(stk.get("profit", 0) or 0)    # 收益
            volume = float(stk.get("totalvol", 0) or 0)  # 总手
            max_profit = float(stk.get("maxprofit", 0) or 0)
            signals.append({
                "symbol": sym,
                "name": name,
                "price": cur_price if cur_price > 0 else in_price,
                "entry_price": in_price,
                "change_pct": round(chg, 2),
                "profit": round(profit, 2),
                "volume": int(volume),
                "max_profit": round(max_profit, 2),
                "max_period": stk.get("maxperiod", ""),
                "max_date": stk.get("maxdate", ""),
                "max_price": float(stk.get("maxprice", 0) or 0),
                "time": stk.get("intime", ""),
                "date": stk.get("indate", ""),
                "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
    except Exception as e:
        print(f"[TDX] 解析 {filepath} 失败: {e}")
    return signals
    return signals


def load_existing():
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_signals(data):
    tmp = OUTPUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUTPUT_PATH)


def dedupe_signals(pool_name, new_signals, existing):
    pool_data = existing.get(pool_name, {})
    seen = set()
    if "_signals" in pool_data:
        for s in pool_data["_signals"]:
            seen.add((s.get("date", ""), s.get("symbol", "")))
    fresh = []
    today = time.strftime("%Y%m%d")
    for s in new_signals:
        key = (s.get("date", today), s["symbol"])
        if key not in seen:
            seen.add(key)
            fresh.append(s)
    return fresh


def watch():
    print(f"[TDX] 桥接监控启动 v2.0, 扫描间隔={SCAN_INTERVAL}s")
    last_mtimes = {}
    existing = load_existing()

    while True:
        try:
            pools = load_pool_config()
            updated = False
            for pool_name, cfg in pools.items():
                ptype = cfg.get("type", "pool")
                label = cfg.get("label", pool_name)

                # 确定源文件
                if ptype == "blk":
                    src_path = os.path.join(BLOCK_DIR, f"{pool_name}.blk")
                else:
                    pool_num = cfg.get("num", 1)
                    today = time.strftime("%Y%m%d")
                    src_path = os.path.join(POOL_DIR, pool_name, str(pool_num), f"{today}.log")

                if not os.path.exists(src_path):
                    continue

                try:
                    mtime = os.path.getmtime(src_path)
                except Exception:
                    continue

                if mtime <= last_mtimes.get(pool_name, 0):
                    continue
                last_mtimes[pool_name] = mtime

                # 解析
                if ptype == "blk":
                    new_signals = parse_blk(src_path)
                else:
                    new_signals = parse_log(src_path)

                if not new_signals:
                    continue

                fresh = dedupe_signals(pool_name, new_signals, existing)
                if fresh:
                    if pool_name not in existing:
                        existing[pool_name] = {"label": label, "type": ptype, "_signals": []}
                    existing[pool_name]["_signals"].extend(fresh)
                    updated = True
                    print(f"[TDX] {label}: +{len(fresh)}只")
                    for s in fresh[:5]:
                        extra = f" @{s.get('price',0):.2f}" if s.get("price") else ""
                        print(f"  {s['symbol']}{extra}")

            if updated:
                save_signals(existing)
        except Exception as e:
            print(f"[TDX] 异常: {e}")
        time.sleep(SCAN_INTERVAL)


def run_once():
    pools = load_pool_config()
    if not pools:
        print("[TDX] 无启用池 (检查 signal_config.json → tdx_pools)")
        return
    existing = load_existing()
    updated = False
    for pool_name, cfg in pools.items():
        ptype = cfg.get("type", "pool")
        label = cfg.get("label", pool_name)
        if ptype == "blk":
            src_path = os.path.join(BLOCK_DIR, f"{pool_name}.blk")
        else:
            pool_num = cfg.get("num", 1)
            today = time.strftime("%Y%m%d")
            src_path = os.path.join(POOL_DIR, pool_name, str(pool_num), f"{today}.log")

        if not os.path.exists(src_path):
            print(f"[TDX] {label}: 文件不存在 ({src_path})")
            continue

        if ptype == "blk":
            new_signals = parse_blk(src_path)
        else:
            new_signals = parse_log(src_path)

        fresh = dedupe_signals(pool_name, new_signals, existing)
        if fresh:
            if pool_name not in existing:
                existing[pool_name] = {"label": label, "type": ptype, "_signals": []}
            existing[pool_name]["_signals"].extend(fresh)
            updated = True
            print(f"[TDX] {label}: +{len(fresh)}只")
            for s in fresh:
                extra = f" @{s.get('price',0):.2f}" if s.get("price") else ""
                print(f"  {s['symbol']}{extra}")
        else:
            print(f"[TDX] {label}: {len(new_signals)}条, 无新增")
    if updated:
        save_signals(existing)
        print(f"[TDX] → {OUTPUT_PATH}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_once()
    else:
        watch()
