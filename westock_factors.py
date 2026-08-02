"""westock-data 高级因子采集器 — 资金流向 + 筹码 + 机构评级
E52: 修复数据解析 — westock CLI 返回管线表格而非 JSON，用 _parse_westock_table() 统一解析
"""

import subprocess, json, os, time
from datetime import datetime

_NODE = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2\node.exe"
_WESTOCK_SCRIPT = r"C:\Users\Administrator\.workbuddy\plugins\marketplaces\cb_teams_marketplace\plugins\finance-data\skills\westock-data\scripts\index.js"

_CACHE = {}
_CACHE_TIME = 0
_CACHE_TTL = 3600


def _parse_westock_table(output):
    """解析 westock-data CLI 返回的管线分隔 Markdown 表格为结构化字典列表"""
    lines = [l.strip() for l in output.strip().split('\n') if l.strip()]
    if len(lines) < 3:
        return []
    # 第1行是表头，第2行是分隔线（|---|---|），第3行起是数据
    headers = [h.strip() for h in lines[0].split('|') if h.strip()]
    rows = []
    for line in lines[2:]:
        cells = [c.strip() for c in line.split('|') if c.strip()]
        if len(cells) >= len(headers):
            rows.append(dict(zip(headers, cells)))
    return rows


def _run_westock(args_str):
    """执行 westock-data CLI 命令，解析表格并返回第一行数据"""
    try:
        r = subprocess.run(
            [_NODE, _WESTOCK_SCRIPT] + args_str.split(),
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0 and r.stdout:
            rows = _parse_westock_table(r.stdout)
            if rows:
                preview = str(rows[0])[:200]
                print(f'[Westock] {args_str}: {preview}')
                return rows[0]  # 返回第一行数据
        stderr_preview = (r.stderr or '')[:100]
        print(f'[Westock] {args_str} FAIL: rc={r.returncode} stderr={stderr_preview}')
        return None
    except Exception as e:
        print(f'[Westock] {args_str} Error: {e}')
        return None


def get_fund_flow(symbols):
    """批量获取资金流向数据（asfund 命令）"""
    result = {}
    for sym in symbols[:20]:
        data = _run_westock(f"asfund {sym}")
        if data:
            result[sym] = data
    return result


def get_chip_cost(symbols):
    """批量获取筹码成本数据（chip 命令）"""
    result = {}
    for sym in symbols[:10]:
        data = _run_westock(f"chip {sym}")
        if data:
            result[sym] = data
    return result


def get_ratings(symbols):
    """批量获取机构评级（rating 命令）"""
    result = {}
    for sym in symbols[:10]:
        data = _run_westock(f"rating {sym}")
        if data:
            result[sym] = data
    return result


# E98: 新因子评分函数

def get_fund_flow_score(symbols):
    """主力净流入评分 (0-20)。asfund命令返回主力净流入数据。"""
    result = {}
    for sym in symbols[:10]:
        data = _run_westock(f"asfund {sym}")
        if data:
            try:
                # 主力净流入率: 正值=流入, 负值=流出
                main_inflow = float(data.get('main_net_inflow', 0) or data.get('net_inflow', 0) or 0)
                score = min(20, max(0, int(10 + main_inflow * 2)))  # -5%→0, 0%→10, +5%→20
                result[sym] = score
            except (ValueError, TypeError):
                result[sym] = 10  # 中性
        else:
            result[sym] = 0
    return result


def get_chip_structure_score(symbols):
    """筹码结构评分 (0-15)。chip命令返回筹码集中度+获利比例。"""
    result = {}
    for sym in symbols[:10]:
        data = _run_westock(f"chip {sym}")
        if data:
            try:
                # 获利比例 + 筹码集中度
                profit_ratio = float(data.get('profit_ratio', 50) or 50)
                concentration = float(data.get('concentration', 0) or 0)
                # 获利比例高+筹码集中→强，获利低+分散→弱
                score = min(15, max(0, int(profit_ratio / 100 * 8 + concentration * 5)))
                result[sym] = score
            except (ValueError, TypeError):
                result[sym] = 7
        else:
            result[sym] = 0
    return result


def get_rating_distribution(symbols):
    """机构评级评分 (0-15)。rating命令返回评级分布。"""
    result = {}
    for sym in symbols[:10]:
        data = _run_westock(f"rating {sym}")
        if data:
            try:
                buy_pct = float(data.get('buy_pct', 0) or data.get('buy_ratio', 0) or 0)
                target_upside = float(data.get('target_upside', 0) or data.get('upside', 0) or 0)
                score = min(15, max(0, int(buy_pct / 100 * 10 + target_upside / 10)))
                result[sym] = score
            except (ValueError, TypeError):
                result[sym] = 7
        else:
            result[sym] = 0
    return result


def get_technical_score(symbols):
    """技术指标评分 (0-20)。quote命令的MACD+RSI综合。"""
    result = {}
    for sym in symbols[:10]:
        data = _run_westock(f"quote {sym}")
        if data:
            try:
                rsi = float(data.get('rsi', 50) or 50)
                macd = float(data.get('macd', 0) or data.get('macd_signal', 0) or 0)
                # RSI 30-70 最佳区间 → 高分，MACD > 0 → 加分
                rsi_score = 10 - abs(rsi - 50) / 5  # RSI=50→10, RSI=30→6, RSI=70→6
                macd_score = 5 if macd > 0 else 0
                score = min(20, max(0, int(rsi_score + macd_score + 5)))
                result[sym] = score
            except (ValueError, TypeError):
                result[sym] = 10
        else:
            result[sym] = 0
    return result

# E56: 限速保护 — westock调用最小间隔（避免被封）
_last_westock_call = 0

# 资金流向缓存 (盘中实时刷新)
_fund_flow_cache = {}
_fund_flow_cache_time = 0


def factor_westock(df) -> float | None:
    """westock资金流向因子 (注册到factor_registry)

    实时读取westock资金流向，返回主力净流入评分(-10~10)。
    正=主力净流入，负=主力净流出。df参数不使用。

    用法: 注册到factor_registry.json → LGBM/XGBoost自动学习
    """
    global _fund_flow_cache, _fund_flow_cache_time
    import time as _t
    now = _t.time()
    # 缓存60秒，避免频繁API调用
    if now - _fund_flow_cache_time < 60 and _fund_flow_cache:
        scores = list(_fund_flow_cache.values())
        return round(float(np.mean(scores) if scores else 0), 2)

    try:
        import json, os as _os
        # 从westock实时数据读取
        p = _os.path.join(_os.path.dirname(__file__), "..", "quant_web", "data", "westock_scores.json")
        if _os.path.exists(p):
            data = json.load(open(p, encoding="utf-8"))
            if isinstance(data, dict):
                _fund_flow_cache = {k: v for k, v in data.items() if isinstance(v, (int, float))}
                _fund_flow_cache_time = now
                if _fund_flow_cache:
                    return round(float(np.mean(list(_fund_flow_cache.values()))), 2)
    except Exception:
        pass
    return None


def get_quote(symbol):
    """获取单只股票实时行情（quote 命令），返回解析后的字典或 None。
    E56: 加500ms最小间隔保护，防止频繁请求被封。"""
    # 跳过 westock CLI 不支持的品种
    code = symbol.replace('sh','').replace('sz','').replace('bj','').replace('SH','').replace('SZ','')
    if not code or not code.isdigit() or code.startswith(('204','131','11','12','13','51','159','16','18','58')):
        return None
    global _last_westock_call
    now = time.time()
    if now - _last_westock_call < 0.5:
        return None  # 限速中，回退到缓存
    _last_westock_call = now
    return _run_westock(f"quote {symbol}")


def get_realtime_quotes(symbols):
    """批量获取实时行情 (realtime_quotes.py 兜底接口)
    返回 {symbol: price} 或空dict
    """
    result = {}
    for sym in symbols[:20]:  # 限20只防超时
        try:
            q = get_quote(sym)
            if q and q.get('price'):
                result[sym] = q['price']
        except Exception:
            pass
    return result


def enrich_factors(stock_symbols):
    """对外接口：传入股票列表，返回高级因子字典

    使用 quote 命令批量获取实时行情，从中提取因子：
    - fund_score:   量比因子（0-30），基于 volume_ratio
    - chip_score:   换手率因子（0-20），基于 turnover_rate
    - rating_score: 涨跌幅因子（0-20），基于 change_percent

    Returns:
        {symbol: {fund_score, chip_score, rating_score, current_price}}
    """
    global _CACHE, _CACHE_TIME

    now = time.time()
    if now - _CACHE_TIME < _CACHE_TTL and _CACHE:
        enriched = {}
        for sym in stock_symbols:
            if sym in _CACHE:
                enriched[sym] = _CACHE[sym].copy()
            else:
                enriched[sym] = {'fund_score': 0, 'chip_score': 0, 'rating_score': 0,
                                 'current_price': None}
        return enriched

    _CACHE.clear()
    result = {}
    for sym in stock_symbols[:20]:
        # 跳过逆回购/可转债/ETF（westock CLI不支持）
        code = sym.replace('sh','').replace('sz','').replace('bj','').replace('SH','').replace('SZ','')
        if code.startswith(('204','131','11','12','13','51','159','16','18','58')):
            result[sym] = {'fund_score': 0, 'chip_score': 0, 'rating_score': 0, 'current_price': None}
            continue
        data = get_quote(sym)
        if data:
            f = {}
            # 量比因子 (0-30): 量比0.5→0分，量比2.0→30分
            try:
                vr = float(data.get('volume_ratio', 1) or 1)
            except (ValueError, TypeError):
                vr = 1.0
            f['fund_score'] = min(30, max(0, int((vr - 0.5) * 20)))

            # 换手率因子 (0-20): 换手率0%→0分，10%→20分
            try:
                tr = float(data.get('turnover_rate', 0) or 0)
            except (ValueError, TypeError):
                tr = 0.0
            f['chip_score'] = min(20, max(0, int(tr * 2)))

            # 涨跌幅因子 (0-20): -10%→0分，+10%→20分
            try:
                cp = float(data.get('change_percent', 0) or 0)
            except (ValueError, TypeError):
                cp = 0.0
            f['rating_score'] = min(20, max(0, int(cp + 10)))

            # 最新价（供价格兜底使用）
            try:
                f['current_price'] = float(data.get('current_price', 0) or data.get('close', 0) or 0)
            except (ValueError, TypeError):
                f['current_price'] = None

            result[sym] = f
        else:
            result[sym] = {'fund_score': 0, 'chip_score': 0, 'rating_score': 0,
                           'fund_flow_score': 0, 'chip_struct_score': 0,
                           'rating_dist_score': 0, 'tech_score': 0,
                           'current_price': None}

    # E98: 合并新因子（仅对已获取quote的有效A股做增强，前20只）
    _top20 = []
    for sym in stock_symbols[:50]:
        code = sym.replace('sh','').replace('sz','').replace('bj','').replace('SH','').replace('SZ','')
        if not code.startswith(('204','131','11','12','13','51','159','16','18','58')):
            _top20.append(sym)
        if len(_top20) >= 20:
            break
    flows = get_fund_flow_score(_top20)
    chips = get_chip_structure_score(_top20)
    ratings = get_rating_distribution(_top20)
    techs = get_technical_score(_top20)
    for sym in _top20:
        if sym in result:
            result[sym]['fund_flow_score'] = flows.get(sym, 0)
            result[sym]['chip_struct_score'] = chips.get(sym, 0)
            result[sym]['rating_dist_score'] = ratings.get(sym, 0)
            result[sym]['tech_score'] = techs.get(sym, 0)

    _CACHE.update(result)
    _CACHE_TIME = now

    # E30-1: 主力资金原始值 + 龙虎榜（日缓存，扩大至200只，加200ms限速）
    _daily = _load_daily_extra_cache()
    _coverage = stock_symbols[:200] if len(stock_symbols) > 30 else stock_symbols
    if not _daily or len(_daily) < min(30, len(stock_symbols)):
        _daily = _build_daily_extra_cache(_coverage)
    elif len(stock_symbols) > len(_daily):
        _new = [s for s in _coverage if s not in _daily]
        if _new:
            _extra = _build_daily_extra_cache(_new[:50])
            _daily.update(_extra)
            _save_daily_extra_cache(_daily)
    for sym in _top20:
        if sym in result:
            extra = _daily.get(sym, {})
            result[sym]['main_force_net'] = extra.get('main_force_net', 0)
            result[sym]['main_force_ratio'] = extra.get('main_force_ratio', 0)
            result[sym]['super_large_net'] = extra.get('super_large_net', 0)
            result[sym]['large_net'] = extra.get('large_net', 0)
            result[sym]['lhb_days'] = extra.get('lhb_days', 0)
            result[sym]['lhb_net_buy'] = extra.get('lhb_net_buy', 0)
            result[sym]['lhb_buy_amt'] = extra.get('lhb_buy_amt', 0)
            result[sym]['lhb_sell_amt'] = extra.get('lhb_sell_amt', 0)
            result[sym]['lhb_biz_type'] = extra.get('lhb_biz_type', '')

    return result


# ═══ E29-1: 主力资金 + 龙虎榜 日缓存 ═══
_DAILY_EXTRA_CACHE = {}
_DAILY_EXTRA_FILE = os.path.join(os.path.dirname(__file__), 'daily_extra_cache.json')


def _load_daily_extra_cache():
    global _DAILY_EXTRA_CACHE
    try:
        if os.path.exists(_DAILY_EXTRA_FILE):
            with open(_DAILY_EXTRA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('date') == datetime.now().strftime('%Y%m%d'):
                _DAILY_EXTRA_CACHE = data.get('stocks', {})
                return _DAILY_EXTRA_CACHE
    except Exception:
        pass
    return {}


def _save_daily_extra_cache(stocks_dict):
    try:
        with open(_DAILY_EXTRA_FILE, 'w', encoding='utf-8') as f:
            json.dump({'date': datetime.now().strftime('%Y%m%d'), 'stocks': stocks_dict}, f, ensure_ascii=False)
    except Exception:
        pass


def _build_daily_extra_cache(symbols):
    """批量获取主力资金+龙虎榜原始值（200ms限速，每只15s超时）"""
    stocks = {}
    for i, sym in enumerate(symbols):
        if i > 0 and i % 10 == 0:
            print(f"[Westock] 主力/龙虎榜进度: {i}/{len(symbols)}")
        if i > 0:
            time.sleep(0.2)  # E30-1: 200ms限速防被封
        code = sym.replace('sh','').replace('sz','').replace('bj','').replace('SH','').replace('SZ','')
        # 跳过非A股
        if code.startswith(('204','131','11','12','13','51','159','16','18','58')):
            continue

        extra = {'main_force_net': 0, 'main_force_ratio': 0, 'super_large_net': 0, 'large_net': 0,
                 'lhb_days': 0, 'lhb_net_buy': 0, 'lhb_buy_amt': 0, 'lhb_sell_amt': 0, 'lhb_biz_type': ''}

        # 主力资金（asfund）
        data = _run_westock(f"asfund {code}")
        if data:
            try:
                extra['main_force_net'] = _to_float(data.get('main_net_inflow', 0))
                extra['main_force_ratio'] = _to_float(data.get('main_net_ratio', 0))
                extra['super_large_net'] = _to_float(data.get('super_large_net', 0))
                extra['large_net'] = _to_float(data.get('large_net', 0))
            except Exception:
                pass

        # 龙虎榜（lhb）
        lhb_data = _run_westock(f"lhb {code}")
        if lhb_data:
            try:
                extra['lhb_days'] = int(_to_float(lhb_data.get('recent_days', 0)))
                extra['lhb_net_buy'] = _to_float(lhb_data.get('net_buy', 0))
                extra['lhb_buy_amt'] = _to_float(lhb_data.get('buy_amt', 0))
                extra['lhb_sell_amt'] = _to_float(lhb_data.get('sell_amt', 0))
                extra['lhb_biz_type'] = str(lhb_data.get('biz_type', ''))
            except Exception:
                pass

        stocks[sym] = extra

    _save_daily_extra_cache(stocks)
    return stocks


def _to_float(v):
    try:
        return float(v or 0)
    except (ValueError, TypeError):
        return 0.0
