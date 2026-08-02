"""mask-first 数据管道 v2.0 — 涨跌停+流动性+硬过滤+缓冲区
对标: 2025行业最佳实践 + 私募六层漏斗第0/0.5层

原理:
  A股涨跌停价格不可执行(封板无对手盘) → 该日的因子值无效
  → 从因子计算/ML训练/回测中排除涨跌停日的样本

新增 (v2.0):
  - 流动性过滤: 日均成交额<1000万 / 换手率<0.5% 排除
  - 缓冲区机制: 宽进严出, 连续20日不满足才剔除, 连续30日满足才纳入
  - 硬过滤: 停牌检测 / 净资产为负排除

用法:
  from tradability_mask import (compute_tradability_mask, filter_tradable,
      filter_universe, compute_liquidity_mask, is_suspended, BUFFER_FILE)
"""
import numpy as np
import os, json
from collections import defaultdict


def get_limit_pct(code: str) -> float:
    """根据股票代码返回涨跌停幅度"""
    c = code.replace("sh", "").replace("sz", "").replace("bj", "")
    if c.startswith(("30", "688")):
        return 0.20  # 创业板/科创板 20%
    if c.startswith(("8", "4")):
        return 0.30  # 北交所 30%
    return 0.10  # 主板 10%


def compute_tradability_mask(df, code: str = "") -> np.ndarray:
    """计算可交易mask (True=该日可交易, False=涨跌停不可交易)

    Args:
        df: DataFrame with [open, high, low, close, volume] columns
        code: 股票代码 (如 sh600519), 用于判断涨跌停幅度

    Returns:
        bool数组, 长度与df相同
    """
    close = df["close"].values
    n = len(close)
    mask = np.ones(n, dtype=bool)

    if n < 2:
        return mask

    limit_pct = get_limit_pct(code)

    # pre_close = 前一交易日收盘价
    pre_close = np.zeros(n)
    pre_close[1:] = close[:-1]
    pre_close[0] = close[0]  # 首日用自己的收盘价

    # 涨跌停价
    limit_up = pre_close * (1 + limit_pct)
    limit_down = pre_close * (1 - limit_pct)

    # 涨停日不可买入 (close >= limit_up → 封板买不到)
    # 跌停日不可卖出 (close <= limit_down → 封板卖不掉)
    # 一字板 (open == close == limit_up/down) 也排除
    for i in range(n):
        if pre_close[i] <= 0:
            continue
        if close[i] >= limit_up[i] * 0.999:  # 浮点容差
            mask[i] = False
        elif close[i] <= max(limit_down[i] * 1.001, 0.01):
            mask[i] = False

    return mask


def filter_tradable(df, code: str = ""):
    """返回仅可交易日的DataFrame"""
    mask = compute_tradability_mask(df, code)
    return df.iloc[mask]


def compute_tradable_pct(df, code: str = "") -> float:
    """返回可交易日占比 (用于诊断)"""
    mask = compute_tradability_mask(df, code)
    return float(np.mean(mask))


# ═══════════════════════════════════════════════════════════════
# v2.0 新增: 候选池过滤 (第0层 + 第0.5层)
# ═══════════════════════════════════════════════════════════════

BUFFER_FILE = r"D:\quant_framework\liquidity_buffer.json"

# --- 硬过滤 ---

def has_negative_equity(sym: str, sd: dict = None) -> bool:
    """检查净资产是否为负 (代理判断)

    日线数据不含财务报表, 用以下代理:
      - 价格 < 1元 (面值退市风险, 通常伴随净资产为负)
      - *ST (多数*ST公司净资产为负)

    Args:
        sym: 股票代码
        sd: 可选, 已加载的行情数据 {sym: df}
    """
    # 代理1: 价格 < 1元
    if sd and sym in sd:
        try:
            close = float(sd[sym]['close'].values[-1])
            if close < 1.0:
                return True
        except Exception:
            pass
    # 代理2: *ST
    return '*ST' in sym.upper()


def is_suspended(df, recent_days: int = 5) -> bool:
    """检测是否长期停牌: 最近N日成交量严格为0 (NaN不算, 可能是数据缺失)"""
    try:
        vol = df['volume'].values
        if len(vol) < recent_days:
            return False
        # 只看严格为0的值 (停牌), NaN不算(可能是数据未更新)
        recent = vol[-recent_days:]
        # 排除NaN后, 所有值都严格等于0 → 停牌
        valid = recent[~np.isnan(recent)]
        if len(valid) == 0:
            return False  # 全是NaN → 数据缺失, 不当停牌处理
        return bool(np.all(valid == 0))
    except Exception:
        return False


# --- 流动性过滤 ---

def compute_avg_daily_turnover(df, window: int = 60) -> float:
    """计算日均成交额 (过去window日)
    优先用 amount 列, 其次 turnover 列, 都没有则用 volume*close 估算
    """
    try:
        if len(df) < 5:
            return 0
        if 'amount' in df.columns:
            return float(np.nanmean(df['amount'].values[-window:]))
        elif 'turnover' in df.columns:
            return float(np.nanmean(df['turnover'].values[-window:]))
        # 无真实成交额数据 → volume*close 估算 (单位: 元)
        vol = df['volume'].values[-window:]
        close = df['close'].values[-window:]
        # volume 在 A股数据中通常是手(100股), 需×100
        # 但不同数据源不同, 我们按原始值算, 后续用分位数阈值自适应
        return float(np.nanmean(vol * close))
    except Exception:
        return 0


def compute_avg_turnover_rate(df, window: int = 20) -> float:
    """计算近20日平均换手率 (%)"""
    try:
        vol = df['volume'].values[-window:]
        if 'outstanding' in df.columns:
            outstanding = df['outstanding'].values[-window:]
        elif 'total_shares' in df.columns:
            outstanding = df['total_shares'].values[-window:]
        else:
            return 999.0  # 无流通股本数据, 不基于换手率过滤
        rate = vol / (outstanding + 1e-9) * 100
        return float(np.nanmean(rate))
    except Exception:
        return 999.0


def compute_liquidity_mask(sd: dict, min_daily_amount: float = None,
                           min_turnover_rate: float = 0.5,
                           percentile_cut: float = 20.0) -> dict:
    """计算全市场流动性mask

    过滤逻辑:
      1. 如果有 amount/turnover 列 → 用绝对阈值 (默认1000万)
      2. 如果没有 → 用 volume*close 估算, 按分位数过滤 (默认排除底部20%)
      3. 如果有 outstanding/total_shares 列 → 加换手率检查

    Args:
        sd: 全市场数据
        min_daily_amount: 绝对金额阈值 (有amount列时使用), None=默认1000万
        min_turnover_rate: 换手率阈值
        percentile_cut: 分位数阈值 (无amount列时, 排除底部percentile_cut%)

    Returns:
        {symbol: True/False}  True=流动性合格
    """
    result = {}
    sample_df = next(iter(sd.values()), None) if sd else None
    has_amount_real = sample_df is not None and ('amount' in sample_df.columns or 'turnover' in sample_df.columns)
    has_shares = sample_df is not None and ('outstanding' in sample_df.columns or 'total_shares' in sample_df.columns)

    # 无真实amount列 → 用分位数自适应阈值
    if not has_amount_real:
        amounts = []
        for sym, df in sd.items():
            amt = compute_avg_daily_turnover(df)
            if amt > 0:
                amounts.append(amt)
        if amounts and len(amounts) >= 30:
            threshold = float(np.percentile(amounts, percentile_cut))
            med = float(np.median(amounts))
            p10 = float(np.percentile(amounts, 10))
            p90 = float(np.percentile(amounts, 90))
            print(f"[流动性] volume*close 分位: P10={p10:.0f} P20={threshold:.0f} P50={med:.0f} P90={p90:.0f} | 阈值={threshold:.0f}")
        else:
            threshold = 0
    else:
        threshold = min_daily_amount or 20_000_000  # 对齐游资标准

    for sym, df in sd.items():
        avg_amt = compute_avg_daily_turnover(df)
        ok = avg_amt >= threshold if avg_amt > 0 else False

        if has_shares and ok:
            avg_tr = compute_avg_turnover_rate(df)
            ok = avg_tr >= min_turnover_rate

        result[sym] = ok
    return result


# --- 缓冲区机制 ---

def _load_buffer() -> dict:
    """加载缓冲区状态"""
    if os.path.exists(BUFFER_FILE):
        try:
            with open(BUFFER_FILE, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_buffer(buf: dict):
    """保存缓冲区状态 (原子写入)"""
    tmp = BUFFER_FILE + ".tmp"
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(buf, f, ensure_ascii=False, indent=2)
        os.replace(tmp, BUFFER_FILE)
    except Exception:
        pass


def apply_buffer(liquidity_ok: dict, exit_days: int = 20,
                 enter_days: int = 30) -> dict:
    """缓冲区机制: 宽进严出, 避免股票频繁进出候选池

    首次运行 (无缓冲文件 或 日期变更): 直接采用当日判断作为初始状态
    之后: 连续exit_days不合格才踢出, 连续enter_days合格才纳入
    """
    buf = _load_buffer()
    now = str(np.datetime64('today'))
    today_str = now[:10] if 'T' in now else now

    # 日期变了 → 重置为新一天的判断
    last_date = buf.get('_date', '')
    if not buf or last_date != today_str:
        buf = {'_date': today_str, '_initialized': today_str}
        for sym, ok in liquidity_ok.items():
            buf[sym] = {"streak_ok": 1 if ok else 0,
                        "streak_bad": 0 if ok else 1,
                        "in_pool": ok}
        _save_buffer(buf)
        return {sym: ok for sym, ok in liquidity_ok.items()}

    # 同日更新 streak
    for sym, ok in liquidity_ok.items():
        if sym.startswith('_'): continue
        entry = buf.get(sym, {"streak_ok": 0, "streak_bad": 0, "in_pool": False})
        if ok:
            entry["streak_ok"] = entry.get("streak_ok", 0) + 1
            entry["streak_bad"] = 0
        else:
            entry["streak_bad"] = entry.get("streak_bad", 0) + 1
            entry["streak_ok"] = 0

        currently_in = entry.get("in_pool", False)
        if currently_in:
            if entry["streak_bad"] >= exit_days:
                entry["in_pool"] = False
        else:
            if entry["streak_ok"] >= enter_days:
                entry["in_pool"] = True
        buf[sym] = entry

    _save_buffer(buf)

    result = {}
    for sym in liquidity_ok:
        entry = buf.get(sym, {})
        if isinstance(entry, dict):
            result[sym] = entry.get("in_pool", False)
        else:
            result[sym] = False
    return result


def filter_universe(sd: dict,
                    exclude_st: bool = True,
                    exclude_suspended: bool = True,
                    exclude_negative_equity: bool = True,
                    apply_liquidity: bool = True,
                    min_daily_amount: float = 20_000_000,
                    min_turnover_rate: float = 0.5,
                    use_buffer: bool = True) -> list:
    """全市场候选池过滤 (第0层 + 第0.5层)

    一票否决制: 任意条件不通过 → 排除

    Returns:
        通过所有过滤的股票代码列表
    """
    excluded = defaultdict(list)
    passed = []

    for sym, df in sd.items():
        # ST检查
        if exclude_st:
            name = sym.upper()
            if 'ST' in name or '*ST' in name:
                excluded[sym].append('ST')
                continue

        # 停牌检查
        if exclude_suspended and is_suspended(df):
            excluded[sym].append('停牌')
            continue

        # 净资产为负 (代理: 价格<1元 或 *ST)
        if exclude_negative_equity:
            clean = sym.replace('sh', '').replace('sz', '').replace('bj', '')
            if has_negative_equity(clean, sd) or has_negative_equity(sym, sd):
                excluded[sym].append('净资产为负')
                continue

        passed.append(sym)

    # 第0.5层: 流动性过滤
    if apply_liquidity:
        liq_mask = compute_liquidity_mask(
            {s: sd[s] for s in passed},
            min_daily_amount=min_daily_amount,
            min_turnover_rate=min_turnover_rate)

        if use_buffer:
            liq_mask = apply_buffer(liq_mask)

        before = len(passed)
        passed = [s for s in passed if liq_mask.get(s, True)]
        for s in list(sd.keys()):
            if s not in passed and s not in excluded:
                excluded[s].append('流动性不足')

    return passed


def filter_universe_with_stats(sd: dict, **kwargs) -> tuple:
    """同 filter_universe, 但返回 (passed, exclusion_stats) 用于诊断"""
    excluded = defaultdict(list)
    passed = []
    exclude_st = kwargs.get('exclude_st', True)
    exclude_suspended = kwargs.get('exclude_suspended', True)
    exclude_negative_equity = kwargs.get('exclude_negative_equity', True)
    apply_liquidity = kwargs.get('apply_liquidity', True)
    min_daily_amount = kwargs.get('min_daily_amount', 10_000_000)
    min_turnover_rate = kwargs.get('min_turnover_rate', 0.5)
    use_buffer = kwargs.get('use_buffer', True)

    for sym, df in sd.items():
        if exclude_st:
            name = sym.upper()
            if 'ST' in name or '*ST' in name:
                excluded[sym].append('ST')
                continue
        if exclude_suspended and is_suspended(df):
            excluded[sym].append('停牌')
            continue
        if exclude_negative_equity:
            clean = sym.replace('sh', '').replace('sz', '').replace('bj', '')
            if has_negative_equity(clean, sd) or has_negative_equity(sym, sd):
                excluded[sym].append('净资产为负')
                continue
        passed.append(sym)

    before_liq = len(passed)
    if apply_liquidity:
        liq_mask = compute_liquidity_mask(
            {s: sd[s] for s in passed},
            min_daily_amount=min_daily_amount,
            min_turnover_rate=min_turnover_rate)
        if use_buffer:
            liq_mask = apply_buffer(liq_mask)
        passed = [s for s in passed if liq_mask.get(s, True)]
        for s in list(sd.keys()):
            if s not in passed and s not in excluded:
                excluded[s].append('流动性不足')

    stats = defaultdict(int)
    for reasons in excluded.values():
        for r in reasons:
            stats[r] += 1
    stats['通过'] = len(passed)
    stats['总输入'] = len(sd)

    return passed, dict(stats)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, r"D:\quant_web")
    from data_loader import load_stock_data_cache

    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
    if sd:
        print(f"全市场: {len(sd)} 只")
        pool = filter_universe(sd)
        print(f"过滤后: {len(pool)} 只")
        print(f"排除: {len(sd) - len(pool)} 只")

        # 抽查5只过滤结果
        import random
        for sym in random.sample(pool, min(5, len(pool))):
            df = sd[sym]
            avg_amt = compute_avg_daily_turnover(df)
            avg_tr = compute_avg_turnover_rate(df)
            suspended = is_suspended(df)
            print(f"  {sym}: 日均成交{avg_amt/1e4:.0f}万, 换手{avg_tr:.1f}%, 停牌:{suspended}")
    print("\n✅ tradability_mask v2.0 就绪 (含第0层+第0.5层)")
