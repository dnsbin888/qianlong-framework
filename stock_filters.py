"""个股过滤器框架 v1.0 — 可扩展过滤器链 (对标 Zipline Pipeline)
用法:
  from stock_filters import apply_all
  passed = apply_all(sd)  # sd={sym: DataFrame}, 返回通过过滤的 sym 列表

加新过滤:
  写一个函数 fn(sd) → {sym: True/False}, 加到 FILTERS 列表即可
"""
import numpy as np
import os, json
from collections import defaultdict

# ═══════════════════════════════════════════
# 过滤器注册表 — 加新过滤在这里加一行
# ═══════════════════════════════════════════

FILTERS = []  # 按顺序执行: [(name, fn), ...]


def register(name):
    """装饰器: @register("名称") → 自动加入过滤器链"""
    def decorator(fn):
        FILTERS.append((name, fn))
        return fn
    return decorator


# ═══════ 已有过滤 (从 tradability_mask 迁移, 零改动) ═══════

@register("ST排除")
def st_filter(sd):
    """排除 ST/*ST"""
    return {sym: 'ST' not in sym.upper() and '*ST' not in sym.upper() for sym in sd}


@register("停牌检测")
def suspended_filter(sd):
    """排除近5日无成交的停牌股"""
    result = {}
    for sym, df in sd.items():
        try:
            vol = df['volume'].values
            if len(vol) < 5:
                result[sym] = True; continue
            recent = vol[-5:]
            valid = recent[~np.isnan(recent)]
            if len(valid) == 0:
                result[sym] = True  # 数据缺失不排除
            else:
                result[sym] = not np.all(valid == 0)
        except:
            result[sym] = True
    return result


@register("净资产为负")
def negative_equity_filter(sd):
    """排除净资产为负 (代理: 价格<1元 或 *ST)"""
    result = {}
    for sym, df in sd.items():
        try:
            close = float(df['close'].values[-1])
            result[sym] = close >= 1.0 and '*ST' not in sym.upper()
        except:
            result[sym] = True
    return result


@register("流动性+缓冲区")
def liquidity_with_buffer(sd, pct_cut=15.0):
    """流动性过滤 + 缓冲区联动 (宽进严出)

    缓冲逻辑:
      首次运行(新日期) → 当天流动性合格→纳入
      后续同一天 → 必须连续20天不合格才剔除, 连续30天合格才纳入
    """
    buf_file = r"D:\quant_framework\liquidity_buffer.json"
    today = str(np.datetime64('today'))[:10]

    # 1. 计算当日流动性
    amounts = {}
    for sym, df in sd.items():
        try:
            vol = df['volume'].values[-20:]
            close = df['close'].values[-20:]
            amt = np.nanmean(vol * close)
            if amt > 0: amounts[sym] = amt
        except: amounts[sym] = 1e9  # 计算失败 → 不排除(安全优先)
    if len(amounts) < 30:
        return {sym: True for sym in sd}
    threshold = np.percentile(list(amounts.values()), pct_cut)
    today_ok = {sym: amounts.get(sym, 1e9) >= threshold for sym in sd}

    # 2. 加载缓冲区
    buf = {}
    if os.path.exists(buf_file):
        try:
            with open(buf_file, encoding='utf-8') as f:
                buf = json.load(f)
        except: pass

    # 3. 按日期处理
    if buf.get('_date') != today:
        # 新的一天: 直接采用当日判断
        for sym, ok in today_ok.items():
            buf[sym] = {'streak_ok': 1 if ok else 0, 'streak_bad': 0 if ok else 1, 'in_pool': ok}
        buf['_date'] = today
    else:
        # 同一天: 宽进严出
        for sym, ok in today_ok.items():
            entry = buf.get(sym, {'streak_ok': 0, 'streak_bad': 0, 'in_pool': False})
            if ok:
                entry['streak_ok'] = entry.get('streak_ok', 0) + 1
                entry['streak_bad'] = 0
            else:
                entry['streak_bad'] = entry.get('streak_bad', 0) + 1
                entry['streak_ok'] = 0
            if entry.get('in_pool', False):
                if entry['streak_bad'] >= 20: entry['in_pool'] = False
            else:
                if entry['streak_ok'] >= 30: entry['in_pool'] = True
            buf[sym] = entry

    # 4. 原子写入 (修复numpy.bool_→Python bool JSON序列化问题)
    tmp = buf_file + '.tmp'
    # 递归转换numpy类型
    def _to_py(v):
        if isinstance(v, (np.bool_,)): return bool(v)
        if isinstance(v, (np.integer,)): return int(v)
        if isinstance(v, (np.floating,)): return float(v)
        if isinstance(v, dict): return {kk: _to_py(vv) for kk, vv in v.items()}
        if isinstance(v, list): return [_to_py(x) for x in v]
        return v
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(_to_py(buf), f)
    os.replace(tmp, buf_file)

    return {sym: bool(buf.get(sym, {}).get('in_pool', True)) for sym in today_ok}


# ═══════ S3: 绝对金额过滤 (对标私募, >1000万) ═══════

@register("日均成交额>1000万")
def min_daily_amount(sd):
    """排除日均成交额不足1000万的僵尸股 (对标私募流动性标准)"""
    result = {}
    for sym, df in sd.items():
        try:
            amt = df['volume'].values[-20:] * df['close'].values[-20:]
            avg_amt = float(np.nanmean(amt)) if len(amt) > 0 else 0
            result[sym] = avg_amt >= 10_000_000  # 1000万门槛
        except Exception:
            result[sym] = True  # 数据缺失不排除
    return result


@register("LHB龙虎榜")
def lhb_filter(sd):
    """龙虎榜过滤: 标记今日龙虎榜净买入票 (游资核心信号)
    返回: {sym: True/False}, True=在龙虎榜净买入名单中
    """
    import os as _os
    result = {sym: True for sym in sd}  # 默认全通过(不在名单中=不排除)
    try:
        _lhb_file = r"D:\quant_web\data\lhb_daily.json"
        if _os.path.exists(_lhb_file):
            _lhb = json.load(open(_lhb_file, encoding='utf-8'))
            _buy_list = set()
            for _r in (_lhb if isinstance(_lhb, list) else _lhb.get('data',[])):
                _sym = _r.get('symbol','') or _r.get('code','')
                _net = float(_r.get('net_amount',0) or _r.get('net_buy',0) or 0)
                if _sym and _net > 0:  # 净买入>0
                    _buy_list.add(_sym)
            # 龙虎榜票不排除, 但在 generate_signal_table 中可获得仓位加成
            # 此处全部通过, 标记信息用于后续评分增强
            print(f"[LHB] 今日龙虎榜: {len(_buy_list)}只净买入票")
    except Exception:
        pass
    return result


# ═══════ 诊断工具 (不参与过滤) ═══════

def lhb_mark():
    """龙虎榜个股标记"""
    lhb_file = r"D:\quant_web\data\lhb_daily.json"
    result = {}
    if os.path.exists(lhb_file):
        try:
            with open(lhb_file, encoding='utf-8') as f:
                data = json.load(f)
            for rec in data.get("records", []):
                sym = rec.get("symbol", "")
                if sym: result[sym] = True
        except: pass
    return result


def lhb_sector_heat(sd=None):
    """LHB板块热度 v2.0 — 三重过滤: 净买入>0 + 行业≥3只 + 今天还在涨
    Returns: {行业名: 净买入票数}
    """
    lhb_file = r"D:\quant_web\data\lhb_daily.json"
    if not os.path.exists(lhb_file):
        return {}

    try:
        with open(lhb_file, encoding='utf-8') as f:
            data = json.load(f)
    except: return {}

    # 加载行业映射
    ind_map = {}
    try:
        sys.path.insert(0, r"D:\quant_web")
        from generate_signal_table import get_industry as _gi
    except:
        try:
            from stock_names import get_industry as _gi
        except:
            return {}

    from collections import Counter
    buy_records = [r for r in data.get("records", [])
                   if r.get("net_amount", 0) > 0]
    sector_count = Counter()
    for r in buy_records:
        sym = r.get("symbol", "")
        try:
            ind = _gi(sym)
        except:
            ind = ""
        if ind: sector_count[ind] += 1

    hot = {}
    for ind, cnt in sector_count.items():
        if cnt < 3: continue
        if sd:
            stocks_in_sector = [s for s in sd if _gi(s) == ind]
            if stocks_in_sector:
                chgs = []
                for s in stocks_in_sector[:50]:
                    try:
                        c = sd[s]['close'].values
                        if len(c) >= 2:
                            chgs.append((c[-1]-c[-2])/max(c[-2],0.01))
                    except: pass
                if chgs and np.mean(chgs) > 0:
                    hot[ind] = cnt
        else:
            hot[ind] = cnt
    return hot


def connors_rsi(sd):
    """Connors RSI(2) — 标记超卖股 (RSI<10)
    经典短期反转指标, 1980s提出, A股量化圈广泛使用
    """
    result = {}
    for sym, df in sd.items():
        try:
            c = df['close'].values
            if len(c) < 3: result[sym] = None; continue
            diff = [c[-1] - c[-2], c[-2] - c[-3]]
            gain = sum(d for d in diff if d > 0) / 2
            loss = -sum(d for d in diff if d < 0) / 2
            rs = gain / max(loss, 1e-9)
            rsi = 100 - 100 / (1 + rs)
            result[sym] = rsi < 10
        except: result[sym] = None
    return result

def apply_all(sd, enabled=None):
    """遍历所有注册的过滤器, 返回通过过滤的股票列表

    Args:
        sd: {symbol: DataFrame} 全市场数据
        enabled: 要启用的过滤器名称列表, None=全部启用

    Returns:
        (passed_list, stats_dict)
    """
    stats = {}
    current = dict(sd)
    for name, fn in FILTERS:
        if enabled is not None and name not in enabled:
            continue
        try:
            mask = fn(current)
            before = len(current)
            current = {k: v for k, v in current.items() if mask.get(k, True)}
            stats[name] = before - len(current)
        except Exception as e:
            print(f"[Filter] {name} 跳过: {e}")
            stats[name] = 0
    return list(current.keys()), stats


def hurst_histogram(sd):
    """计算全市场 Hurst 分布 (供校准阈值使用)"""
    hs = []
    for sym, df in sd.items():
        try:
            ts = np.diff(np.log(df['close'].values[-60:]))
            ts = ts[~np.isnan(ts)]
            if len(ts) < 20: continue
            lags = range(2, min(20, len(ts)//2))
            tau = [np.std(np.subtract(ts[lag:], ts[:-lag])) for lag in lags]
            if len(tau) < 3: continue
            reg = np.polyfit(np.log(list(lags)), np.log(tau), 1)
            hs.append(reg[0])
        except: pass
    if not hs: return {}
    hs = np.array(hs)
    return {
        'count': len(hs), 'mean': float(np.mean(hs)),
        'p10': float(np.percentile(hs, 10)), 'p25': float(np.percentile(hs, 25)),
        'p50': float(np.percentile(hs, 50)), 'p75': float(np.percentile(hs, 75)),
        'p90': float(np.percentile(hs, 90)),
        'reverting_pct': float(np.mean(hs < 0.45) * 100),  # 反转区占比
        'trending_pct': float(np.mean(hs > 0.55) * 100),   # 趋势区占比
    }


if __name__ == "__main__":
    import sys; sys.path.insert(0, r"D:\quant_web")
    from data_loader import load_stock_data_cache
    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
    print(f"数据: {len(sd)} 只")
    pool, stats = apply_all(sd)
    print(f"过滤后: {len(pool)} 只")
    for name, n in stats.items():
        if n: print(f"  {name}: 排除{n}只")
    h = hurst_histogram(sd)
    if h:
        print(f"Hurst: 均值{h['mean']:.3f} 中位{h['p50']:.3f} 反转区{h['reverting_pct']:.1f}% 趋势区{h['trending_pct']:.1f}%")
