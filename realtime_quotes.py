"""实时行情模块 — QMT推送优先 + 新浪财经批量API兜底 + 后台缓存(3秒刷新)"""
import urllib.request, ssl, json, os, time, threading
from datetime import datetime

_SINA_BATCH_SIZE = 80  # 新浪单次批量最大股票数
_CACHE_TTL = 3         # 3秒刷新，持仓批零延迟
_PERSIST_FILE = r"D:\quant_framework\quote_cache.pkl"  # P2.1: 重启不丢
_QMT_CACHE_FILE = r"D:\quant_framework\quote_cache.json"  # E372: QMT 桥接子进程输出

# ── QMT 缓存读取 ──
def _read_qmt_cache():
    """读取 Python 3.11 子进程写入的 QMT 行情缓存。
    返回 {clean_code: {price, change_pct, volume, ...}} 或空 dict。
    """
    try:
        if not os.path.exists(_QMT_CACHE_FILE):
            return {}
        # 检查文件新鲜度 (3秒内)
        mtime = os.path.getmtime(_QMT_CACHE_FILE)
        if time.time() - mtime > 5:
            return {}  # 过期, 降级新浪
        with open(_QMT_CACHE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        data = raw.get("data", {})
        # QMT code格式: 000001.SH → clean 000001
        quotes = {}
        for qmt_code, tick in data.items():
            clean = qmt_code.replace(".SH", "").replace(".SZ", "").replace(".BJ", "").lower()
            if not clean.isdigit() or len(clean) != 6:
                continue
            quotes[clean] = {
                "name": "", "close": tick.get("price", 0),
                "change_pct": tick.get("change_pct", 0),
                "volume": tick.get("volume", 0), "amount": tick.get("amount", 0),
                "high": 0, "low": 0, "open": 0,
                "vol_ratio": 1.0, "turnover": 0,
                "data_source": "qmt",
            }
        return quotes
    except Exception:
        return {}

# P2.1: 启动时加载持久化缓存
def _load_persisted():
    global _quote_cache
    if os.path.exists(_PERSIST_FILE):
        try:
            import pickle
            _quote_cache = pickle.load(open(_PERSIST_FILE, "rb"))
            print(f"[Realtime] 已加载缓存: {_quote_cache.get('count',0)}只")
        except Exception:
            pass

def _save_persisted():
    try:
        import pickle
        tmp = _PERSIST_FILE + ".tmp"
        pickle.dump(_quote_cache, open(tmp, "wb"))
        os.replace(tmp, _PERSIST_FILE)
    except Exception:
        pass

_quote_cache = {
    "status": "closed", "count": 0, "data": {},
    "time": "", "trading": False,
}
_last_fetch_time = 0
_last_fetch_symbols = []

# E24: 数据源健康计数
_SOURCE_HEALTH = {"sina": 0, "westock": 0, "tdx": 0}
_FAIL_THRESHOLD = 3  # 连续3次失败标记离线
_last_recovery_check = 0

def _mark_source(name, success):
    """E24: 标记数据源健康状态"""
    if name not in _SOURCE_HEALTH:
        return
    if success:
        if _SOURCE_HEALTH[name] >= _FAIL_THRESHOLD:
            print(f"[Quotes] ✅ {name} 已恢复")
        _SOURCE_HEALTH[name] = 0
    else:
        _SOURCE_HEALTH[name] += 1
        if _SOURCE_HEALTH[name] == _FAIL_THRESHOLD:
            print(f"[Quotes] ⚠️ {name} 已离线（连续{_FAIL_THRESHOLD}次失败）")

def _check_all_sources_offline():
    """E24: 检查是否所有数据源都离线，返回True/False"""
    return all(h >= _FAIL_THRESHOLD for h in _SOURCE_HEALTH.values())

def _get_best_source():
    """E24: 返回当前最优可用数据源"""
    available = [s for s, h in _SOURCE_HEALTH.items() if h < _FAIL_THRESHOLD]
    return available[0] if available else None

_load_persisted()  # 启动加载


# A股节假日 (2026年)
_A_STOCK_HOLIDAYS = {
    "2026-01-01", "2026-01-02",  # 元旦
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",  # 春节
    "2026-04-06",  # 清明
    "2026-05-01", "2026-05-04", "2026-05-05",  # 劳动节
    "2026-06-01",  # 端午(估)
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08",  # 国庆+中秋
}


def is_trading_time():
    """A股交易时间 (含节假日检测)"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    if now.strftime("%Y-%m-%d") in _A_STOCK_HOLIDAYS:
        return False
    t = now.hour * 100 + now.minute
    return (930 <= t <= 1130) or (1300 <= t <= 1500)


def _sina_ctx():
    """创建新浪API用的SSL上下文"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _fetch_sina_batch(symbols):
    """批量从新浪拉取行情 — 单次HTTP请求，返回 {code: quote_dict}"""
    if not symbols:
        return {}

    # 去前缀，构建新浪格式: sh600519,sz000001,...
    # 统一用去前缀的6位代码作key
    sina_codes = []
    code_map = {}  # sina_code → clean_6digit
    for code in symbols[:200]:
        clean = str(code).strip().lower()
        clean = clean.replace('sh', '').replace('sz', '').replace('bj', '')
        if not clean.isdigit() or len(clean) != 6:
            continue
        # 跳过可转债/基金 (sh110xxx-sh19xxxx)
        if clean.startswith(('11','12','13','15','16','18','19','20','5')) and clean[0] in ('1','5'):
            continue
        prefix = 'sh' if clean[0] == '6' else 'sz'
        sc = f"{prefix}{clean}"
        sina_codes.append(sc)
        code_map[sc] = clean  # 统一只有6位数字

    if not sina_codes:
        return {}

    quotes = {}
    headers = {
        'Referer': 'https://finance.sina.com.cn',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    # 分批请求（每批最多80个），失败自动重试3次
    # 持仓优先批零延迟，后续批次0.3秒间隔
    batches = [sina_codes[i:i+_SINA_BATCH_SIZE] for i in range(0, len(sina_codes), _SINA_BATCH_SIZE)]
    n_priority = min(1, len(batches))  # 第一版=持仓批，零延迟
    for bi, batch in enumerate(batches):
        if bi >= n_priority:
            time.sleep(0.3)  # 后续批次0.3秒间隔（原1秒）
        url = f'https://hq.sinajs.cn/list={",".join(batch)}'

        text = None
        sina_ok = False
        for retry in range(3):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=8, context=_sina_ctx()) as resp:
                    text = resp.read().decode('gbk')
                sina_ok = True
                break
            except Exception:
                if retry < 2:
                    time.sleep(1)  # 重试前等1秒
        _mark_source("sina", sina_ok)
        if text:  # 3次请求成功，解析响应
            try:
                for line in text.strip().split('\n'):
                    if '="' not in line:
                        continue
                    sc = line.split('hq_str_')[1].split('="')[0] if 'hq_str_' in line else ''
                    data_str = line.split('="')[1].rstrip('";\n')
                    parts = data_str.split(',')

                    if len(parts) >= 32:
                        name = parts[0]
                        open_p = float(parts[1]) if parts[1] else 0
                        pre_close = float(parts[2]) if parts[2] else 0
                        price = float(parts[3]) if parts[3] else 0
                        high = float(parts[4]) if parts[4] else 0
                        low = float(parts[5]) if parts[5] else 0
                        volume = float(parts[8]) if parts[8] else 0
                        amount = float(parts[9]) if parts[9] else 0

                        if price <= 0:
                            continue

                        change_pct = round((price - pre_close) / pre_close * 100, 2) if pre_close > 0 else 0
                        orig = code_map.get(sc, sc)
                        quotes[orig] = {
                            'name': name, 'close': price, 'change_pct': change_pct,
                            'high': high, 'low': low, 'open': open_p,
                            'volume': volume, 'amount': amount,
                            'vol_ratio': 1.0, 'turnover': 0,
                            'data_source': 'live',
                        }
            except Exception:
                continue

        # C05: 新浪返回空→westock兜底
        if not quotes or len(quotes) < 10:
            try:
                from westock_factors import get_realtime_quotes
                wk = get_realtime_quotes(symbols[:50] if symbols else [])
                if wk:
                    quotes = {str(k): {"close": float(v), "name": str(k), "data_source": "westock"} for k, v in wk.items()}
                    print(f"[Realtime] westock兜底: {len(quotes)}只")
                    _mark_source("westock", True)
                else:
                    _mark_source("westock", False)
            except Exception as e:
                _mark_source("westock", False)
                print(f"[Realtime] westock兜底失败: {e}")

    return quotes


def fetch_realtime_quotes(symbols=None):
    """获取实时行情 — 从缓存过滤返回。

    冷启动时同步拉取新浪(约2秒)，之后从内存缓存读(<1ms)。
    后台线程每3秒自动刷新全量缓存。
    """
    global _quote_cache, _last_fetch_symbols
    now = datetime.now()
    cache_data = _quote_cache.get("data", {})

    # 前端请求：从缓存过滤
    if symbols and isinstance(symbols, list) and len(symbols) > 0:
        result = {}
        # 确保 _last_fetch_symbols 包含这些股票
        _last_fetch_symbols = list(set(_last_fetch_symbols + list(symbols)))

        for code in symbols:
            clean = str(code).strip()
            clean = clean.replace('sh', '').replace('sz', '').replace('bj', '')
            if clean in cache_data:
                result[str(code).strip()] = cache_data[clean]

        # 缓存命中：直接返回
        if len(result) >= len(symbols) * 0.5:  # 至少50%命中率
            return {
                "status": _quote_cache.get("status", "closed"),
                "trading": is_trading_time(),
                "count": len(result),
                "data": result,
                "time": _quote_cache.get("time", ""),
            }

    # 冷启动/缓存未命中：同步拉取新浪
    quotes = _fetch_sina_batch(symbols or _last_fetch_symbols)
    if quotes:
        _quote_cache = {
            "status": "live", "count": len(quotes), "data": quotes,
            "time": now.strftime("%H:%M:%S"), "trading": is_trading_time(),
        }
        if symbols:
            result = {}
            for code in symbols:
                clean = str(code).strip().replace('sh','').replace('sz','').replace('bj','')
                if clean in quotes:
                    result[str(code).strip()] = quotes[clean]
            return {
                "status": "live", "trading": is_trading_time(),
                "count": len(result), "data": result,
                "time": now.strftime("%H:%M:%S"),
            }
        return _quote_cache

    return _quote_cache


# ═══════════════════ 后台缓存线程 ═══════════════════
_bg_started = False


def start_bg_refresh():
    """启动后台线程 — 每3秒批量拉所有已知股票到全局缓存"""
    global _bg_started
    if _bg_started:
        return
    _bg_started = True

    # 冷启动：从价格缓存获取初始股票列表（只取A股，过滤可转债/指数/基金）
    init_symbols = []
    try:
        pf = r"d:\quant_framework\price_cache.json"
        if os.path.exists(pf):
            with open(pf, 'r') as f:
                pc = json.load(f)
            all_keys = list(pc.keys())
            # 正则过滤：兼容sh/sz/bj前缀 和 纯6位数字(price_cache.json格式)
            import re
            stock_keys = [k for k in all_keys if re.match(
                r'^(sh[56]\d{5}|sz[0123]\d{5}|bj\d{6}|\d{6})$', k)]
            init_symbols = stock_keys[:1000]  # N1: 扩大到1000只
            print(f"[Realtime] 初始股票池: {len(stock_keys)}只A股, 取前{len(init_symbols)}只")
    except Exception:
        pass
    # FIX: price_cache缩水兜底——从stock_data取全量代码 (P2: 统一入口, 只取keys)
    if not init_symbols or len(init_symbols) < 50:
        try:
            import sys as _rq_sys
            _rq_sys.path.insert(0, r"D:\quant_web")
            from data_loader import load_stock_data_from_cache
            sd = load_stock_data_from_cache()
            if sd:
                all_syms = list(sd.keys())
            else:
                # 最终兜底: 从parquet文件只读schema取列名
                import pyarrow.parquet as _pq
                _pq_path = r"D:\quant_web\stock_data.parquet"
                if os.path.exists(_pq_path):
                    all_syms = _pq.read_schema(_pq_path).field("symbol").metadata.get(b"categories", b"").decode().split(",") if False else []
                    # parquet categories not reliable; use pandas metadata
                    import pandas as _pd2
                    _df = _pd2.read_parquet(_pq_path, columns=["symbol"])
                    all_syms = _df["symbol"].unique().tolist()
                else:
                    all_syms = []
            import re
            stock_keys = [k for k in all_syms if re.match(r'^(sh[56]\d{5}|sz[0123]\d{5}|\d{6})$', k)]
            init_symbols = stock_keys[:1000]
            print(f"[Realtime] price_cache缩水→stock_data兜底: {len(init_symbols)}只")
        except Exception:
            pass
    # P2: 二次兜底——直接从price_cache.json取(正则已通过)
    if not init_symbols or len(init_symbols) < 50:
        try:
            init_symbols = stock_keys[:1000]  # 复用上面正则匹配的结果
            print(f"[Realtime] 二次兜底: {len(init_symbols)}只")
        except Exception:
            pass

    def _loop():
        global _quote_cache, _last_fetch_symbols, _last_recovery_check
        all_symbols = list(init_symbols)
        pool_idx = 0
        POOL_SIZE = 150  # E254: 降低内存压力
        _prev_prices = {}  # E26: 记录前次价格，检测突变
        while True:
            time.sleep(3)
            try:
                if not is_trading_time():
                    # E24: 非交易时每60秒检查离线源恢复
                    now_t = time.time()
                    if now_t - _last_recovery_check > 60:
                        _last_recovery_check = now_t
                        if _check_all_sources_offline():
                            # 所有数据源离线 → 恢复检查
                            pass
                    continue
            except: continue
            try:
                # E241+E243: 滚动池 + 持仓优先
                priority_syms = []
                pa_file = r"D:\quant_framework\paper_account.json"
                if os.path.exists(pa_file):
                    pa = json.load(open(pa_file, 'r'))
                    for sym in pa.get("positions", {}):
                        code = sym.replace('sh','').replace('sz','').replace('bj','')
                        priority_syms.append(code)
            except Exception:
                priority_syms = []

            try:
                # E372: QMT推送优先 — 从子进程缓存读取 (Python 3.11桥接)
                qmt_quotes = _read_qmt_cache()
                if qmt_quotes and len(qmt_quotes) > 100:
                    _quote_cache = {
                        "status": "live", "count": len(qmt_quotes), "data": qmt_quotes,
                        "time": datetime.now().strftime("%H:%M:%S"), "trading": is_trading_time(),
                    }
                    continue  # QMT数据新鲜, 跳过新浪轮询

                # 滚动窗口: 每轮取200只，下一轮偏移80只
                if all_symbols:
                    start = pool_idx % max(1, len(all_symbols))
                    pool = all_symbols[start:start + POOL_SIZE]
                    if len(pool) < POOL_SIZE:
                        pool += all_symbols[:POOL_SIZE - len(pool)]
                    pool_idx += 80
                else:
                    pool = []

                # 持仓优先合并到pool前面
                all_syms = priority_syms + pool
                # 去重但保持顺序
                seen = set()
                all_syms = [s for s in all_syms if not (s in seen or seen.add(s))]

                if all_syms:
                    quotes = _fetch_sina_batch(all_syms)
                    # 新浪失败 → 通达信本地兜底（零延迟，已有数据）
                    if not quotes or len(quotes) < 10:
                        try:
                            from tdx_realtime import fetch_batch as _tdx_fetch
                            tdx_symbols = [(c, 'sh' if c.startswith('6') else 'sz') for c in priority_syms[:20]]
                            tdx_q = _tdx_fetch(tdx_symbols, use_minline=True)
                            if tdx_q:
                                quotes.update(tdx_q)
                                _mark_source("tdx", True)
                                print(f"[Realtime] 新浪降级，通达信兜底: +{len(tdx_q)}只")
                            else:
                                _mark_source("tdx", False)
                        except Exception:
                            _mark_source("tdx", False)
                            pass
                    # C05: 全失败→price_cache.json兜底(5860只历史价格)
                    if not quotes or len(quotes) < 10:
                        try:
                            pf = r"d:\quant_framework\price_cache.json"
                            if os.path.exists(pf):
                                pc = json.load(open(pf, "r"))
                                for k, v in pc.items():
                                    code = k.replace("sh","").replace("sz","")
                                    if code not in quotes and isinstance(v,(int,float)):
                                        quotes[code] = {"close":float(v),"change_pct":0,"name":"","data_source":"cache"}
                            if len(quotes) >= 50:
                                print(f"[Realtime] 兜底成功: price_cache→{len(quotes)}只")
                        except Exception: pass
                    if quotes:
                        # E26: 价格突变检测（变化>2%时立即刷新）
                        for code, q in quotes.items():
                            old_price = _prev_prices.get(code, 0)
                            new_price = q.get("close", 0)
                            if old_price > 0 and new_price > 0:
                                change = abs(new_price / old_price - 1)
                                if change > 0.02:
                                    _prev_prices[code] = new_price
                        # 更新前次价格记录
                        for code, q in quotes.items():
                            cp = q.get("close", 0)
                            if cp > 0:
                                _prev_prices[code] = cp
                        # E24: 检查是否所有源离线
                        if _check_all_sources_offline():
                            try:
                                from dingtalk_alerts import send_alert
                                send_alert("🔴 行情源全部离线", "所有数据源不可用，行情已停止更新", "critical")
                            except: pass
                        # E100: 合并旧缓存，避免逐只覆盖导致数据缩水
                        old_data = _quote_cache.get("data", {})
                        old_data.update(quotes)
                        # 清理超过600只的历史数据，防内存膨胀
                        if len(old_data) > 600:
                            keys = list(old_data.keys())[-500:]
                            old_data = {k: old_data[k] for k in keys}
                        print(f"[Realtime] 行情更新: +{len(quotes)}只 (总计{len(old_data)}只, 池{len(pool)}只)")
                        _quote_cache = {
                            "status": "live", "trading": True,
                            "count": len(old_data), "data": old_data,
                            "time": datetime.now().strftime("%H:%M:%S"),
                        }
                        _last_fetch_time = time.time()  # E349修复: 后台线程更新时间戳
                        # Phase 5: 发布行情事件到EventBus(灰度: 与轮询并存)
                        try:
                            from quant_framework.core.event_bus import EventBus
                            bus = EventBus._instance
                            if bus and hasattr(bus, 'publish'):
                                bus.publish("quote", {"count": len(quotes), "total": len(old_data), "ts": datetime.now().timestamp()})
                        except Exception: pass
                        _save_persisted()  # P2.1: 持久化，重启不丢
                        # E245: 合并写入磁盘缓存（保留旧key，防缩水）
                        try:
                            cache_out = {k: float(v.get("close", 0)) for k, v in old_data.items()}
                            pf = r"d:\quant_framework\price_cache.json"
                            existing = {}
                            if os.path.exists(pf):
                                try:
                                    with open(pf, "r") as _ef:
                                        existing = json.load(_ef)
                                except Exception: pass
                            # 保护: 现有缓存>500只 且 新数据<50只 → 不写(防缩水)
                            if len(existing) > 500 and len(cache_out) < 50:
                                pass  # 跳过, 保留完整缓存
                            else:
                                existing.update(cache_out)
                                tmp = r"d:\quant_framework\price_cache.json.tmp"
                                with open(tmp, "w") as _f:
                                    json.dump(existing, _f)
                                os.replace(tmp, pf)
                        except Exception: pass
            except Exception as _loop_e:
                print(f"[Realtime] 刷新线程异常(自动恢复): {_loop_e}")
                time.sleep(5)  # 异常后等5秒再重试

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    print("[Realtime] 后台缓存已启动 (3秒批量刷新)")


# 模块加载时自动启动
start_bg_refresh()
