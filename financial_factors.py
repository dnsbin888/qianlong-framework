"""财务因子模块 — PE/PB/ROE/利润增速/营收增速等基本面因子。

P0-因子-02: 集成到因子引擎，带财报披露日期对齐。

核心类:
  FinancialDataLoader — 财务数据加载器，按披露日期对齐，确保回测不看到未来数据。

披露日期规则 (A股):
  Q1 (1/1–3/31):   披露截止 4月30日
  Q2 (4/1–6/30):   披露截止 8月31日 (中报)
  Q3 (7/1–9/30):   披露截止 10月31日 (三季报)
  Q4 (10/1–12/31): 披露截止 次年4月30日 (年报)

Usage:
  loader = FinancialDataLoader()
  loader.load_from_dataframe(fin_df)  # fin_df 含 symbol/report_period/disclosure_date/...
  pe = loader.get_financial_factor("000001", 20220515, "pe_ttm")
  # → 返回 2022-05-15 时实际可用的最新 PE_TTM 值 (来自2021Q4或2022Q1年报)
"""

from __future__ import annotations

import os
import pickle
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd


# ======================================================================
# 披露日期计算
# ======================================================================

def _ymd_to_date(value) -> datetime:
    """将 int(YYYYMMDD) 或 str/datetime 转为 datetime.date 类对象。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, (int, np.integer)):
        s = str(int(value))
        return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]))
    if isinstance(value, str):
        value = value.replace("-", "")
        return datetime(int(value[:4]), int(value[4:6]), int(value[6:8]))
    return value


def get_disclosure_deadline(report_end_date) -> datetime:
    """计算财报的法定披露截止日。

    规则:
      Q1 (end 3/31)    → 4/30 当年
      Q2 (end 6/30)    → 8/31 当年
      Q3 (end 9/30)    → 10/31 当年
      Q4 (end 12/31)   → 4/30 次年

    Args:
        report_end_date: 报告期截止日 (datetime / int YYYYMMDD / str)

    Returns:
        datetime — 该期财报必须在此时之前披露。
    """
    dt = _ymd_to_date(report_end_date)
    month = dt.month
    year = dt.year

    if month == 3:
        return datetime(year, 4, 30)       # Q1
    elif month == 6:
        return datetime(year, 8, 31)       # Q2 (中报)
    elif month == 9:
        return datetime(year, 10, 31)      # Q3
    elif month == 12:
        return datetime(year + 1, 4, 30)   # Q4 (年报)
    else:
        # 未知月份：保守地设为报告期+4个月
        m = month + 4
        if m > 12:
            return datetime(year + 1, m - 12, 28)
        return datetime(year, m, 28)


# ======================================================================
# FinancialDataLoader
# ======================================================================

# 支持的财务因子名称及默认值
_FINANCIAL_FACTOR_DEFAULTS = {
    "pe_ttm": 0.0,
    "pb": 0.0,
    "roe": 0.0,
    "profit_growth": 0.0,
    "revenue_growth": 0.0,
    "debt_ratio": 0.0,
    "net_profit_margin": 0.0,
    "eps": 0.0,
}


class FinancialDataLoader:
    """财务数据加载器 — 按披露日期对齐，确保回测安全。

    存储结构:
      每条记录为 (symbol, report_period, disclosure_date, {factor_name: value})
      内部索引: {symbol: [(report_period_end, disclosure_date, {factor_dict}), ...]}
    """

    def __init__(self, data_path: str | None = None):
        """
        Args:
            data_path: 可选，财务数据 pickle 文件路径。
                       文件内容应为 DataFrame，包含以下列:
                         symbol, report_period, disclosure_date,
                         pe_ttm, pb, roe, profit_growth, ...
        """
        self._records: dict[str, list[tuple[datetime, datetime, dict[str, float]]]] = {}
        self._symbols: set[str] = set()
        self._factor_names: set[str] = set()

        if data_path and os.path.isfile(data_path):
            self.load(data_path)

    # ── 数据加载 ──

    def load(self, path: str) -> int:
        """从 pickle 文件加载财务数据。

        文件应为 pickled DataFrame，包含列:
          symbol, report_period, disclosure_date, pe_ttm, pb, roe, ...

        Returns:
            int — 加载的记录数。
        """
        with open(path, "rb") as f:
            df = pickle.load(f)
        return self.load_from_dataframe(df)

    def load_from_dataframe(self, df: pd.DataFrame) -> int:
        """从 DataFrame 加载财务数据。

        必需列: symbol, report_period
        可选列: disclosure_date (缺失时使用法定截止日)
        因子列: pe_ttm, pb, roe, profit_growth, revenue_growth, debt_ratio, eps, ...

        Returns:
            int — 加载的记录数。
        """
        count = 0
        for _, row in df.iterrows():
            symbol = str(row.get("symbol", "")).strip()
            if not symbol:
                continue

            report_period = row.get("report_period")
            if report_period is None or pd.isna(report_period):
                continue
            rp_dt = _ymd_to_date(report_period)

            # 披露日期: 优先用实际值，否则用法定截止日
            disc = row.get("disclosure_date")
            if disc is None or pd.isna(disc):
                disc_dt = get_disclosure_deadline(rp_dt)
            else:
                disc_dt = _ymd_to_date(disc)

            # 提取因子值
            factors = {}
            for fname in _FINANCIAL_FACTOR_DEFAULTS:
                if fname in df.columns:
                    val = row.get(fname)
                    if val is not None and not (isinstance(val, float) and np.isnan(val)):
                        factors[fname] = float(val)

            if not factors:
                continue

            self._add_record(symbol, rp_dt, disc_dt, factors)
            count += 1

        return count

    def add_manual_record(
        self,
        symbol: str,
        report_period: datetime,
        factors: dict[str, float],
        disclosure_date: datetime | None = None,
    ) -> None:
        """手动添加一条财务记录。"""
        rp_dt = _ymd_to_date(report_period)
        disc_dt = _ymd_to_date(disclosure_date) if disclosure_date else get_disclosure_deadline(rp_dt)
        self._add_record(str(symbol).strip(), rp_dt, disc_dt, factors)

    def _add_record(
        self,
        symbol: str,
        report_period: datetime,
        disclosure_date: datetime,
        factors: dict[str, float],
    ) -> None:
        """内部: 添加一条记录并按 report_period 排序。"""
        if symbol not in self._records:
            self._records[symbol] = []
        self._records[symbol].append((report_period, disclosure_date, factors))
        self._records[symbol].sort(key=lambda x: x[0])  # 按 report_period 排序
        self._symbols.add(symbol)
        self._factor_names.update(factors.keys())

    # ── 核心查询 ──

    def get_financial_factor(
        self,
        symbol: str,
        date,
        factor_name: str,
        default: float = 0.0,
    ) -> float:
        """获取指定日期实际可用的财务因子值。

        回溯逻辑:
          1. 找到所有 report_period 的披露日期 <= query_date 的记录
          2. 取其中最晚 report_period 的那条
          3. 返回该条记录的 factor_name 值

        例如: 查询 2022-05-01:
          - 2021Q4 (披露截止 2022-04-30): 可用 ✓ (已披露)
          - 2022Q1 (披露截止 2022-04-30): 可用 ✓ (已披露)
          - 2022Q2 (披露截止 2022-08-31): 不可用 ✗ (尚未披露)
          → 返回 2022Q1 的值 (如果 2022Q1 已披露且是最新的)

        Args:
            symbol: 股票代码
            date: 查询日期 (int YYYYMMDD / datetime / str)
            factor_name: 因子名称 (pe_ttm, pb, roe, ...)
            default: 无可用数据时的默认值

        Returns:
            float — 因子值
        """
        query_dt = _ymd_to_date(date)
        records = self._records.get(str(symbol).strip())
        if not records:
            return default

        # 向前查找: 取披露日期 <= query_dt 且 report_period 最新的记录
        best_record = None
        best_period = None

        for rp_dt, disc_dt, factors in records:
            if disc_dt <= query_dt:
                if best_period is None or rp_dt > best_period:
                    best_period = rp_dt
                    best_record = factors

        if best_record is None:
            return default

        val = best_record.get(factor_name)
        if val is None:
            return default
        return float(val)

    def get_latest_available_report(
        self,
        symbol: str,
        date,
    ) -> dict[str, float] | None:
        """获取指定日期最新可用的完整财务报告。

        Returns:
            {factor_name: value} 或 None
        """
        query_dt = _ymd_to_date(date)
        records = self._records.get(str(symbol).strip())
        if not records:
            return None

        best_factors = None
        best_period = None

        for rp_dt, disc_dt, factors in records:
            if disc_dt <= query_dt:
                if best_period is None or rp_dt > best_period:
                    best_period = rp_dt
                    best_factors = factors

        return best_factors

    def get_factor_series(
        self,
        symbol: str,
        dates: list,
        factor_name: str,
        default: float = 0.0,
    ) -> np.ndarray:
        """批量获取时间序列上的因子值。

        Args:
            symbol: 股票代码
            dates: 日期列表 (每个元素为 int YYYYMMDD 或 datetime)
            factor_name: 因子名称
            default: 默认值

        Returns:
            np.ndarray — 与 dates 等长的因子值数组
        """
        return np.array([self.get_financial_factor(symbol, d, factor_name, default) for d in dates])

    # ── 工具方法 ──

    @property
    def symbols(self) -> set[str]:
        """已加载的股票代码集合。"""
        return self._symbols.copy()

    @property
    def factor_names(self) -> set[str]:
        """可查询的因子名称集合。"""
        return self._factor_names.copy()

    def has_symbol(self, symbol: str) -> bool:
        """是否包含某只股票的数据。"""
        return str(symbol).strip() in self._records

    def symbol_report_count(self, symbol: str) -> int:
        """某只股票的财报记录数。"""
        records = self._records.get(str(symbol).strip())
        return len(records) if records else 0

    def get_symbol_reports(self, symbol: str) -> list[dict]:
        """获取某只股票的所有财报记录（用于调试）。"""
        records = self._records.get(str(symbol).strip(), [])
        return [
            {
                "report_period": rp.strftime("%Y-%m-%d"),
                "disclosure_date": dd.strftime("%Y-%m-%d"),
                "factors": f.copy(),
            }
            for rp, dd, f in records
        ]

    def __len__(self) -> int:
        return sum(len(v) for v in self._records.values())

    def __contains__(self, symbol: str) -> bool:
        return self.has_symbol(symbol)


# ======================================================================
# 财务因子计算函数 (供 FactorEngine 集成)
# ======================================================================


def make_financial_factor_compute(
    loader: FinancialDataLoader,
    factor_name: str,
    default: float = 0.0,
):
    """创建一个财务因子的 compute 函数，可被 FactorEngine 调用。

    返回的函数签名为:
        compute(kline_df, symbol=None, dates=None) → pd.Series or np.array

    Args:
        loader: FinancialDataLoader 实例
        factor_name: 因子名称
        default: 默认值

    Returns:
        callable — 适配 FactorEngine 的因子计算函数

    Usage:
        loader = FinancialDataLoader()
        loader.load_from_dataframe(fin_df)

        pe_compute = make_financial_factor_compute(loader, "pe_ttm")
        # 可传给 FactorEngine
    """

    def compute(kline_df: pd.DataFrame, symbol: str = "", dates=None) -> pd.Series:
        """对 kline_df 的日期范围计算财务因子序列。

        每个日期的值 = 该日期最新可用的财报数据。
        """
        if dates is None:
            dates = kline_df.index
        elif isinstance(dates, pd.Index):
            dates = dates

        values = []
        for d in dates:
            # 转换日期为 int 格式
            if isinstance(d, (datetime, pd.Timestamp)):
                date_int = int(d.strftime("%Y%m%d"))
            elif isinstance(d, str):
                date_int = int(d.replace("-", ""))
            else:
                date_int = int(d)
            values.append(loader.get_financial_factor(symbol, date_int, factor_name, default))

        return pd.Series(values, index=kline_df.index if hasattr(dates, '__len__') and len(dates) == len(kline_df.index) else dates)

    return compute


# ======================================================================
# 实时快照 (保留旧接口兼容)
# ======================================================================

import threading
import json

_cache = {}
_cache_time = None
CACHE_FILE = r"d:\quant_framework\financial_cache.json"


def fetch_financials(symbols=None):
    """获取A股实时财务快照 (AkShare)。保留旧接口兼容。"""
    global _cache, _cache_time
    now = datetime.now()

    if _cache_time and (now - _cache_time).seconds < 1800 and _cache:
        return _cache

    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        data = {}
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            data[code] = {
                "pe": float(row.get("市盈率-动态", 0) or 0),
                "pb": float(row.get("市净率", 0) or 0),
                "market_cap": float(row.get("总市值", 0) or 0),
                "circulating_cap": float(row.get("流通市值", 0) or 0),
                "volume_ratio": float(row.get("量比", 0) or 0),
                "turnover_rate": float(row.get("换手率", 0) or 0),
                "amplitude": float(row.get("振幅", 0) or 0),
            }
        _cache = data
        _cache_time = now

        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"time": now.strftime("%Y-%m-%d %H:%M"), "data": data}, f, ensure_ascii=False)
        except Exception:
            pass

        return data
    except Exception:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                _cache = cached.get("data", {})
                _cache_time = datetime.strptime(cached.get("time", "2000-01-01"), "%Y-%m-%d %H:%M")
                return _cache
            except Exception:
                pass
        return {}


def get_stock_financial(code):
    """获取单只股票的财务快照 (旧接口)。"""
    data = fetch_financials()
    code = str(code).replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "")
    return data.get(code, {})


def compute_financial_score(fin):
    """基于财务数据计算评分 (0-30，旧接口)。"""
    score = 0
    pe = fin.get("pe", 0)
    pb = fin.get("pb", 0)

    if 0 < pe < 15:
        score += 8
    elif 15 <= pe < 40:
        score += 10
    elif 40 <= pe < 80:
        score += 5
    elif pe >= 80:
        score += 2

    if 0 < pb < 1.5:
        score += 10
    elif 1.5 <= pb < 4:
        score += 7
    elif 4 <= pb < 8:
        score += 4
    elif pb >= 8:
        score += 1

    cap = fin.get("market_cap", 0) / 1e8
    if 20 < cap < 500:
        score += 6
    elif 500 <= cap < 2000:
        score += 4
    else:
        score += 2

    return min(30, score)


def start_bg_refresh():
    """后台自动刷新 (旧接口)。"""
    def _loop():
        while True:
            try:
                fetch_financials()
            except Exception:
                pass
            import time
            time.sleep(1800)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
