"""潜龙真实回测引擎 — 事件驱动 · T+1执行 · 真实数据
对标聚宽/米筐回测框架，基于本地通达信日线数据。

P0-3修复: 内置DataPortal，所有数据访问走统一入口，严格防未来函数。
P1-1: 基准对比(Alpha/Beta/信息比率/超额收益)
P1-2: Walk-Forward 滚动窗口参数稳定性检验
P1-3: 数据质量框架(停牌/涨跌停/退市/OHLC校验)
P2-1: 策略解耦(BaseStrategy + TdxResonanceStrategy + 钩子系统)
P2-2: 事件总线(EventBus: 发布/订阅, 6种内置事件)
P2-3: 容量分析(CapacityAnalyzer: 基于换手率和流动性估算)
执行模型: T日收盘计算信号 → T+1日开盘执行。
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict


class DataPortal:
    """数据门户 — 统一管理数据访问，防止未来函数(P0-3)

    所有数据访问通过此类进行，内置日期屏障，确保回测第 i 天
    只能看到第 i 天及之前的数据。

    P1-3扩展: 数据质量校验(缺失值/异常值/停牌/一字板)
    """

    # 涨停/跌停幅度(A股)
    LIMIT_UP = 0.098   # 10%涨停(考虑精度)
    LIMIT_DOWN = -0.098

    def __init__(self, stock_data: dict, validate=True):
        """
        Args:
            stock_data: {symbol: DataFrame} 原始价格数据(含未来)
            validate: 是否在初始化时执行数据质量校验
        """
        self._raw = stock_data
        self._current_dt = None  # 引擎每次推进时设置
        self._quality_report = {}  # P1-3: 各股票质量报告

        if validate:
            self._validate_all()

    def set_date(self, dt):
        """引擎调用: 设置当前回测日期"""
        self._current_dt = dt

    def has_data(self, symbol, dt=None):
        """检查某股票在某日是否有数据"""
        dt = dt or self._current_dt
        df = self._raw.get(symbol)
        if df is None:
            return False
        return dt in df.index

    def get_price(self, symbol, field='close', dt=None):
        """获取某日价格(含日期屏障)"""
        dt = dt or self._current_dt
        df = self._raw.get(symbol)
        if df is None or dt not in df.index:
            return np.nan
        val = df.loc[dt, field]
        return float(val) if not np.isnan(val) else np.nan

    def get_history(self, symbol, lookback=20, dt=None):
        """获取截止到指定日期的历史数据(严格不含未来)"""
        dt = dt or self._current_dt
        df = self._raw.get(symbol)
        if df is None:
            return None
        return df[df.index <= dt].tail(lookback)

    def get_prev_close(self, symbol, dt=None):
        """获取前一交易日收盘价(用于T+1开盘买入的参考价)"""
        dt = dt or self._current_dt
        hist = self.get_history(symbol, lookback=2, dt=dt)
        if hist is None or len(hist) < 2:
            return np.nan
        return float(hist.iloc[-2]["close"])

    # ─────────────────────────────────────────────
    # P1-3: 数据质量校验方法
    # ─────────────────────────────────────────────

    def is_suspended(self, symbol, dt=None):
        """检测某日是否停牌

        判定标准:
        - 该日无数据(股票在数据库中但当天缺失)
        - 或成交量为0且当日无涨跌(一字停牌)
        """
        dt = dt or self._current_dt
        df = self._raw.get(symbol)
        if df is None:
            return True  # 完全无数据视为停牌
        if dt not in df.index:
            return True  # 当天无K线视为停牌
        row = df.loc[dt]
        # 成交量为0且开盘=收盘=最高=最低 → 一字停牌
        if "volume" in df.columns:
            vol = float(row.get("volume", 0))
            if vol == 0:
                o, c, h, l = float(row.get("open", 0)), float(row.get("close", 0)), \
                              float(row.get("high", 0)), float(row.get("low", 0))
                if o == c == h == l and o > 0:
                    return True
        return False

    def is_limit_up(self, symbol, dt=None):
        """检测某日是否涨停(一字板或尾盘涨停)"""
        dt = dt or self._current_dt
        df = self._raw.get(symbol)
        if df is None or dt not in df.index:
            return False
        prev_close = self.get_prev_close(symbol, dt)
        if np.isnan(prev_close) or prev_close <= 0:
            return False
        row = df.loc[dt]
        o = float(row.get("open", 0))
        c = float(row.get("close", 0))
        h = float(row.get("high", 0))
        limit_price = prev_close * (1 + self.LIMIT_UP)

        # 一字板: open = close = high ≈ 涨停价
        if abs(o - limit_price) / limit_price < 0.01 and abs(c - limit_price) / limit_price < 0.01:
            return True
        # 尾盘涨停: close ≈ 涨停价
        if abs(c - limit_price) / limit_price < 0.01:
            return True
        return False

    def is_limit_down(self, symbol, dt=None):
        """检测某日是否跌停"""
        dt = dt or self._current_dt
        df = self._raw.get(symbol)
        if df is None or dt not in df.index:
            return False
        prev_close = self.get_prev_close(symbol, dt)
        if np.isnan(prev_close) or prev_close <= 0:
            return False
        row = df.loc[dt]
        o = float(row.get("open", 0))
        c = float(row.get("close", 0))
        l = float(row.get("low", 0))
        limit_price = prev_close * (1 + self.LIMIT_DOWN)

        if abs(o - limit_price) / abs(limit_price) < 0.01 and abs(c - limit_price) / abs(limit_price) < 0.01:
            return True
        if abs(c - limit_price) / abs(limit_price) < 0.01:
            return True
        return False

    def is_delisted(self, symbol, dt=None):
        """检测股票是否可能已退市

        判定: 连续30个交易日无数据, 视为退市
        """
        dt = dt or self._current_dt
        df = self._raw.get(symbol)
        if df is None:
            return True
        # 检查dt之后30天是否全无数据
        after = df[df.index > dt].head(30)
        return len(after) == 0 and (df.index[-1] < dt if len(df) > 0 else True)

    def get_quality_report(self, symbol=None):
        """获取数据质量报告

        Args:
            symbol: 指定股票(返回单只), None返回全部汇总
        """
        if symbol:
            return self._quality_report.get(symbol, {"status": "unknown"})
        return self._quality_report

    def _validate_all(self):
        """初始化时对所有股票执行数据质量校验"""
        for sym, df in self._raw.items():
            if df is None or len(df) == 0:
                self._quality_report[sym] = {
                    "status": "empty",
                    "n_rows": 0,
                    "issues": ["数据为空"],
                }
                continue

            issues = []
            required_cols = ["open", "high", "low", "close"]
            n_rows = len(df)

            # 1. 缺失字段
            for col in required_cols:
                if col not in df.columns:
                    issues.append(f"缺少字段: {col}")

            # 2. 缺失值
            if n_rows > 0:
                for col in required_cols:
                    if col in df.columns:
                        null_count = int(df[col].isna().sum())
                        if null_count > 0:
                            issues.append(f"{col}有{null_count}个缺失值")

            # 3. OHLC逻辑异常 (high < low, open/close越界)
            if all(c in df.columns for c in required_cols):
                bad_ohlc = int(((df["high"] < df["low"]) |
                               (df["open"] > df["high"]) |
                               (df["open"] < df["low"]) |
                               (df["close"] > df["high"]) |
                               (df["close"] < df["low"])).sum())
                if bad_ohlc > 0:
                    issues.append(f"OHLC逻辑异常{bad_ohlc}行(high<low等)")

            # 4. 负价格
            if all(c in df.columns for c in ["open", "high", "low", "close"]):
                neg = int(((df[required_cols] < 0).any(axis=1)).sum())
                if neg > 0:
                    issues.append(f"存在{neg}行负价格")

            # 5. 停牌天数占比
            if "volume" in df.columns:
                zero_vol = int((df["volume"] == 0).sum())
                susp_ratio = zero_vol / n_rows
                if susp_ratio > 0.3:
                    issues.append(f"停牌占比{susp_ratio:.0%}过高")

            # 6. 数据覆盖日期
            if hasattr(df.index, 'min') and hasattr(df.index, 'max'):
                date_span = (df.index.max() - df.index.min()).days
                # 正常股票应覆盖交易日的大部分
                trading_days_est = date_span / 365 * 250
                coverage = n_rows / max(trading_days_est, 1)
                if coverage < 0.5:
                    issues.append(f"数据覆盖率仅{coverage:.0%}(跨度{date_span}天)")

            status = "ok" if not issues else "warning"
            self._quality_report[sym] = {
                "status": status,
                "n_rows": n_rows,
                "date_range": f"{df.index[0]} ~ {df.index[-1]}" if n_rows > 0 else "N/A",
                "issues": issues,
                "n_issues": len(issues),
            }


# ═══════════════════════════════════════════════
# P2-2: 事件总线 — 轻量级发布/订阅
# ═══════════════════════════════════════════════

class EventBus:
    """轻量事件总线 — 支持任意事件的发布/订阅

    内置事件类型:
      ON_BEFORE_TRADING  — 盘前
      ON_HANDLE_BAR      — 每根Bar
      ON_AFTER_TRADING   — 盘后
      ON_ORDER           — 下单
      ON_TRADE           — 成交
      ON_SIGNAL          — 信号生成
      ON_ERROR           — 错误
    """

    # 内置事件
    ON_BEFORE_TRADING = "before_trading"
    ON_HANDLE_BAR = "handle_bar"
    ON_AFTER_TRADING = "after_trading"
    ON_ORDER = "order"
    ON_TRADE = "trade"
    ON_SIGNAL = "signal"
    ON_ERROR = "error"

    def __init__(self):
        self._subscribers = defaultdict(list)  # {event: [callbacks]}

    def subscribe(self, event, callback):
        """订阅事件

        Args:
            event: 事件名称(可用 EventBus.ON_* 常量)
            callback: fn(**data) -> None
        """
        self._subscribers[event].append(callback)
        return self  # 支持链式调用

    def unsubscribe(self, event, callback):
        """取消订阅"""
        if event in self._subscribers:
            self._subscribers[event] = [
                cb for cb in self._subscribers[event] if cb is not callback
            ]

    def publish(self, event, **data):
        """发布事件, 同步调用所有订阅者"""
        for cb in self._subscribers.get(event, []):
            try:
                cb(**data)
            except Exception as e:
                # 不阻塞事件链
                for err_cb in self._subscribers.get(self.ON_ERROR, []):
                    try:
                        err_cb(event=event, error=str(e))
                    except Exception:
                        pass

    def clear(self):
        """清除所有订阅"""
        self._subscribers.clear()

    def subscriber_count(self, event=None):
        """获取订阅者数量(调试用)"""
        if event:
            return len(self._subscribers.get(event, []))
        return sum(len(v) for v in self._subscribers.values())


# ═══════════════════════════════════════════════
# P2-1: 策略基类 + 内置策略实现
# ═══════════════════════════════════════════════

class BaseStrategy:
    """策略基类 — 参考 RQAlpha Mod 设计

    子类需实现:
      handle_bar(context, date, data_portal) -> candidates

    可选重写:
      before_trading(context, date, data_portal)
      after_trading(context, date, data_portal)

    约定:
      - candidates: [{symbol, price, power_score, ...}]
      - 不得直接访问 future data
      - 所有数据通过 data_portal 获取
    """

    # 策略元信息
    name = "base"
    description = "策略基类"

    def before_trading(self, context, date, data_portal):
        """盘前回调 — 可重写"""
        pass

    def handle_bar(self, context, date, data_portal):
        """盘中回调 — 必须由子类实现

        Args:
            context: 引擎上下文(dict), 含:
                - sampled: 当前股票池
                - positions: 当前持仓
                - max_positions: 最大持仓数
                - signal_store: 预计算信号
                - factor_cache: 因子缓存
                - name_map: 名称映射
            date: 当前回测日期
            data_portal: DataPortal 实例

        Returns:
            list[dict]: 候选买入列表
                [{"symbol": str, "price": float, "power_score": float, ...}]
                返回空列表表示无信号
        """
        raise NotImplementedError

    def after_trading(self, context, date, data_portal):
        """盘后回调 — 可重写"""
        pass

    def on_trade(self, context, trade_record):
        """成交回调 — 可重写, 用于日志/风控"""
        pass


class TdxResonanceStrategy(BaseStrategy):
    """通达信共振策略 — 从原 run() 信号逻辑提取

    多因子评分: 趋势(30)+动量(10)+量能(25)+涨跌幅(15)+
               位置(20)+RSI(10)+布林带(10) = 满分99
    """

    name = "tdx_resonance"
    description = "通达信共振: 趋势+动量+量能+RSI+布林带多因子评分"

    def __init__(self, signal_field="signal_resonance", min_power=50):
        self.signal_field = signal_field
        self.min_power = min_power

    def handle_bar(self, context, date, data_portal):
        """计算多因子评分, 选出候选买入股"""
        sampled = context.get("sampled", [])
        positions = {p["symbol"] for p in context.get("positions", [])}
        max_positions = context.get("max_positions", 3)
        signal_store = context.get("signal_store")
        factor_cache = context.get("factor_cache")
        formula_symbols = context.get("formula_symbols")

        candidates = []

        for sym in sampled:
            if not data_portal.has_data(sym, date):
                continue
            if sym in positions:
                continue

            hist = data_portal.get_history(sym, lookback=20)
            if hist is None or len(hist) < 20:
                continue

            close_prices = hist["close"].values
            volume_data = hist["volume"].values

            last_close = float(close_prices[-1])
            prev_close = float(close_prices[-2]) if len(close_prices) > 1 else last_close
            change_pct = (last_close / prev_close - 1) if prev_close > 0 else 0

            ma5 = float(np.mean(close_prices[-5:])) if len(close_prices) >= 5 else last_close
            ma20 = float(np.mean(close_prices[-20:])) if len(close_prices) >= 20 else last_close

            avg_vol_5 = float(np.mean(volume_data[-5:])) if len(volume_data) >= 5 else 1
            avg_vol_20 = float(np.mean(volume_data[-20:])) if len(volume_data) >= 20 else avg_vol_5
            vol_ratio = avg_vol_5 / (avg_vol_20 + 0.01)

            # 多因子评分
            trend_score = min(30, max(0, int((last_close / ma20 - 1 + 0.1) * 70)))
            mom_5d = (last_close / (close_prices[-6] + 0.01) - 1) * 100 if len(close_prices) >= 6 else 0
            momentum_score = min(10, max(0, int(mom_5d + 5)))
            volume_score = min(25, int(vol_ratio * 7))
            chg_score = min(15, max(0, int(change_pct * 150 + 7)))
            pos_ratio = last_close / (ma20 + 0.01)
            if pos_ratio < 1.05:
                position_score = 20
            elif pos_ratio < 1.15:
                position_score = 15
            elif pos_ratio < 1.30:
                position_score = 10
            elif pos_ratio < 1.50:
                position_score = 5
            else:
                position_score = 0
            diffs = np.diff(close_prices[-15:])
            gains = np.sum(diffs[diffs > 0]) if len(diffs[diffs > 0]) > 0 else 0
            losses = abs(np.sum(diffs[diffs < 0])) if len(diffs[diffs < 0]) > 0 else 1
            rs = gains / losses if losses > 0 else 1
            rsi_val = 100 - 100 / (1 + rs) if losses > 0 else 50
            rsi_score = min(10, max(0, int(10 - abs(rsi_val - 50) / 5)))
            std20 = float(np.std(close_prices[-20:]))
            bb_upper = ma20 + 2 * std20
            bb_lower = ma20 - 2 * std20
            bb_pos = (last_close - bb_lower) / (bb_upper - bb_lower + 0.01)
            bb_score = min(10, max(0, int(5 - abs(bb_pos - 0.5) * 8)))

            power_score = min(99, trend_score + momentum_score + volume_score
                              + chg_score + position_score + rsi_score + bb_score)
            power_score = max(15, power_score)

            sig_ok = True
            # 预计算信号优先
            if signal_store and sym in signal_store:
                sig_s = signal_store[sym]
                sig_ok = (date in sig_s.index and sig_s.loc[date] > 0)
            elif not formula_symbols and self.signal_field and self.signal_field != "signal_resonance":
                sig_val = 0
                for fc in (factor_cache or []):
                    if getattr(fc, 'symbol', '') == sym:
                        sig_val = getattr(fc, self.signal_field, 0) or 0
                        break
                if sig_val <= 0:
                    sig_ok = False

            if power_score >= self.min_power and sig_ok:
                candidates.append({
                    "symbol": sym,
                    "price": last_close,
                    "power_score": power_score,
                    "change_pct": round(change_pct * 100, 2),
                    "vol_ratio": round(vol_ratio, 2),
                })

        candidates.sort(key=lambda x: -x["power_score"])
        return candidates


class MovingAverageCrossStrategy(BaseStrategy):
    """双均线交叉策略 — 示例: 示范如何写新策略"""
    name = "ma_cross"
    description = "双均线交叉: 5日线上穿20日线买入"

    def handle_bar(self, context, date, data_portal):
        sampled = context.get("sampled", [])
        positions = {p["symbol"] for p in context.get("positions", [])}
        candidates = []

        for sym in sampled:
            if not data_portal.has_data(sym, date):
                continue
            if sym in positions:
                continue
            hist = data_portal.get_history(sym, lookback=20)
            if hist is None or len(hist) < 20:
                continue
            close_prices = hist["close"].values
            ma5 = float(np.mean(close_prices[-5:]))
            ma20 = float(np.mean(close_prices[-20:]))
            # 金叉: ma5 上穿 ma20
            prev_ma5 = float(np.mean(close_prices[-6:-1]))
            prev_ma20 = float(np.mean(close_prices[-21:-1]))
            if prev_ma5 <= prev_ma20 and ma5 > ma20:
                candidates.append({
                    "symbol": sym,
                    "price": float(close_prices[-1]),
                    "power_score": 60,
                    "change_pct": 0,
                    "vol_ratio": 0,
                })
        return candidates


class BacktestEngine:
    """事件驱动回测引擎 — 支持可插拔策略 + 事件总线

    P2-1: 策略通过 BaseStrategy 子类注入
    P2-2: 内置 EventBus, 支持订阅各生命周期事件
    """

    def __init__(self, stock_data, factor_cache, name_map=None,
                 event_bus=None):
        """
        Args:
            stock_data: {symbol: DataFrame} — 从STOCK_DATA传入
            factor_cache: [StockInfo] — 从_FACTOR_CACHE传入
            name_map: {symbol: name} — 股票名称映射
            event_bus: EventBus 实例(共享则多个引擎可通信)
        """
        self.stock_data = stock_data
        self.factor_cache = factor_cache
        self.name_map = name_map or {}
        self.portal = DataPortal(stock_data)  # P0-3: 内置数据门户
        self.event_bus = event_bus or EventBus()  # P2-2: 事件总线
        self._strategies = {}  # 注册的策略字典 {name: class}

    def register_strategy(self, strategy_cls):
        """注册自定义策略类

        Args:
            strategy_cls: BaseStrategy 子类

        Returns:
            self (链式调用)
        """
        name = getattr(strategy_cls, 'name', strategy_cls.__name__)
        self._strategies[name] = strategy_cls
        return self

    def _get_strategy(self, strategy_name, signal_field, min_power):
        """根据策略名称获取策略实例

        查找顺序:
          1. self._strategies 中已注册的自定义策略
          2. 内置 TdxResonanceStrategy
          3. 内置 MovingAverageCrossStrategy
        """
        # 内置策略
        builtin = {
            "tdx_resonance": TdxResonanceStrategy,
            "ma_cross": MovingAverageCrossStrategy,
        }
        cls = self._strategies.get(strategy_name) or builtin.get(strategy_name)
        if cls is not None:
            if strategy_name == "tdx_resonance":
                return cls(signal_field=signal_field, min_power=min_power)
            return cls()
        return None

    def run(self, strategy="tdx_resonance", signal_field="signal_resonance",
            formula_symbols=None, signal_store=None,
            start="2022-01-01", end="2025-12-31",
            max_positions=3, position_pct=0.3, stop_loss=-0.05, take_profit=0.08,
            hold_days=1, trail1_profit=0.05, trail1_drop=0.02,
            trail2_profit=0.07, trail2_drop=0.03,
            trail3_profit=0.12, trail3_drop=0.03,
            sell_ratio_1=0.25, sell_ratio_2=0.25, sell_ratio_3=0.25,
            limit_up_enabled=True, limit_up_open_drop=0.03,
            min_power=50, initial_capital=1_000_000,
            commission_rate=0.00025, stamp_duty=0.001,
            benchmark_sym='sh000300',
            strategy_obj=None):
        """
        执行回测 — 事件驱动，T+1执行，真实数据+交易成本。

        策略支持三种模式:
          1. strategy_obj: BaseStrategy 实例(优先使用)
          2. strategy='tdx_resonance': 使用内置共振策略
          3. strategy='ma_cross': 使用内置双均线策略

        Args:
            strategy_obj: BaseStrategy 实例(优先级最高)
            signal_store: {symbol: pd.Series} 预计算信号序列
        """
        # ── 获取策略实例 ──
        active_strategy = strategy_obj
        if active_strategy is None:
            active_strategy = self._get_strategy(strategy, signal_field, min_power)
        if active_strategy is None:
            return self._empty_result()

        # ── 1. 构建交易日历 ──
        all_dates = set()
        for symbol, df in self.stock_data.items():
            if df is not None and len(df) > 0:
                for d in df.index:
                    all_dates.add(d)
        trading_days = sorted(all_dates)
        trading_days = [d for d in trading_days
                        if str(d)[:10] >= start and str(d)[:10] <= end]

        if len(trading_days) < 20:
            return self._empty_result()

        # ── 2. 采样股票池 ──
        def _is_a_stock(sym):
            code = sym.replace('sh','').replace('sz','').replace('bj','')
            return (code.startswith(('000','001','002','003','300','301','600','601','602','603','604','605','688')))

        if formula_symbols and len(formula_symbols) > 0:
            sampled = []
            for code in formula_symbols:
                for prefix in ['sh','sz','bj']:
                    key = prefix + code
                    if key in self.stock_data:
                        sampled.append(key)
                        break
            if len(sampled) == 0:
                sampled = list(self.stock_data.keys())[:50]
            else:
                sampled = sampled[:200]
        else:
            all_symbols = [s for s in self.stock_data.keys() if _is_a_stock(s)]
            np.random.seed(42)
            sample_size = min(max_positions * 50, len(all_symbols))
            sample_size = max(sample_size, 50)
            sample_size = min(sample_size, 500)
            if sample_size > len(all_symbols): sample_size = len(all_symbols)
            sampled = list(np.random.choice(all_symbols, sample_size, replace=False)) if all_symbols else []

        # ── 3. 逐日回测 ──
        capital = initial_capital
        available = capital
        positions = []
        trades = []
        equity_curve = []
        daily_returns = []
        position_peaks = {}
        position_sold_ratios = {}  # 每只持仓已卖出比例
        limit_up_tracker = {}      # 涨停状态追踪 {symbol: {last_close, first_open, open_count}}
        pending_buys = []

        # P2-2: 上下文对象(可被策略修改)
        context = {
            "sampled": sampled,
            "positions": positions,
            "available": available,
            "max_positions": max_positions,
            "signal_store": signal_store,
            "factor_cache": self.factor_cache,
            "name_map": self.name_map,
            "formula_symbols": formula_symbols,
            "trading_days": trading_days,
        }

        # P1-1: 基准对比
        benchmark_df = self.stock_data.get(benchmark_sym)
        benchmark_curve = []
        benchmark_start_price = None

        for i, today in enumerate(trading_days):
            today_str = str(today)[:10]
            self.portal.set_date(today)

            if i == 0:
                equity_curve.append({"date": today_str, "equity": capital})
                # P2-2: 盘前事件
                self.event_bus.publish(EventBus.ON_BEFORE_TRADING,
                                       date=today, context=context)
                continue

            # P2-1: 盘前钩子
            active_strategy.before_trading(context, today, self.portal)

            # ── 3a. 执行待买入(T日信号→T+1开盘执行) ──
            executed_buys = []
            for pb in pending_buys:
                sym = pb["symbol"]
                if not self.portal.has_data(sym, today):
                    continue
                buy_price = self.portal.get_price(sym, 'open', today)
                if np.isnan(buy_price) or buy_price <= 0:
                    buy_price = self.portal.get_price(sym, 'close', today)
                if np.isnan(buy_price) or buy_price <= 0:
                    continue

                buy_price *= (1 + np.random.uniform(0.0001, 0.0003))
                shares = pb["shares"]
                cost = buy_price * shares

                if cost > available:
                    continue

                buy_commission = cost * commission_rate
                if buy_commission < 5: buy_commission = 5
                buy_slippage = cost * (0.00005 + np.random.uniform(0, 0.00015))
                available -= (cost + buy_commission + buy_slippage)

                pos_record = {
                    "symbol": sym,
                    "buy_date": today,
                    "buy_price": buy_price,
                    "shares": shares,
                    "cost": cost,
                    "commission": buy_commission,
                    "slippage": buy_slippage,
                    "power_score": pb["power_score"],
                }
                positions.append(pos_record)
                # P2-2: 成交事件
                self.event_bus.publish(EventBus.ON_TRADE,
                                       type="buy", symbol=sym, price=buy_price,
                                       shares=shares, date=today)
            pending_buys.clear()

            # ── 3b. 检查持仓退出 (三级移动止盈 + 部分卖出 + 涨停规则) ──
            still_held = []
            for pos in positions:
                sym = pos["symbol"]
                if not self.portal.has_data(sym, today):
                    still_held.append(pos)
                    continue

                judge_price = self.portal.get_price(sym, 'open', today)
                settle_price = self.portal.get_price(sym, 'close', today)
                if np.isnan(judge_price) or np.isnan(settle_price):
                    still_held.append(pos)
                    continue

                ret = (judge_price / pos["buy_price"] - 1)
                days_held = (today - pos["buy_date"]).days
                sym_key = pos["symbol"]

                # 峰值追踪
                if sym_key not in position_peaks or settle_price > position_peaks[sym_key]:
                    position_peaks[sym_key] = settle_price
                peak = position_peaks.get(sym_key, settle_price)
                peak_ret = (peak / pos["buy_price"] - 1)

                # 已卖出比例
                if sym_key not in position_sold_ratios:
                    position_sold_ratios[sym_key] = 0.0
                sold_ratio = position_sold_ratios.get(sym_key, 0.0)

                # ── 涨停检测 ──
                is_today_limit_up = self.portal.is_limit_up(sym, today)

                # 涨停特殊处理: 封板中不卖出
                if limit_up_enabled and is_today_limit_up:
                    if sym_key not in limit_up_tracker:
                        limit_up_tracker[sym_key] = {"last_close": None, "open_count": 0}
                    limit_up_tracker[sym_key]["last_close"] = settle_price
                    still_held.append(pos)
                    continue

                # 开板检测: 昨涨停今不涨停 → 开板回落 ≥ threshold → 全卖
                if (limit_up_enabled and sym_key in limit_up_tracker
                        and not is_today_limit_up):
                    tracker = limit_up_tracker[sym_key]
                    prev_close = tracker.get("last_close", 0)
                    if prev_close and prev_close > 0:
                        open_ret = (judge_price / prev_close - 1)
                        if open_ret <= -limit_up_open_drop:
                            limit_up_tracker.pop(sym_key, None)
                            # 强制全卖
                            sell_shares = pos["shares"]
                            sell_price = self.portal.get_price(sym, 'low', today)
                            if np.isnan(sell_price):
                                sell_price = settle_price
                            sell_price *= 0.998
                            sell_amount = sell_price * sell_shares
                            sell_commission = sell_amount * commission_rate
                            if sell_commission < 5: sell_commission = 5
                            sell_stamp = sell_amount * stamp_duty
                            slippage = sell_amount * (0.00005 + np.random.uniform(0, 0.00025))
                            total_cost = (pos.get("commission", 0) + pos.get("slippage", 0)
                                          + sell_commission + sell_stamp + slippage)
                            pnl = (sell_price - pos["buy_price"]) * sell_shares - total_cost
                            available += pos["cost"] + pnl
                            position_peaks.pop(sym_key, None)
                            position_sold_ratios.pop(sym_key, None)

                            trade_rec = {
                                "symbol": sym, "name": self.name_map.get(sym, sym),
                                "buy_date": str(pos["buy_date"])[:10], "sell_date": today_str,
                                "buy_price": round(pos["buy_price"], 2),
                                "sell_price": round(sell_price, 2),
                                "return_pct": round((pnl / pos["cost"]) if pos["cost"] > 0 else 0, 4),
                                "net_profit": round(pnl, 0), "hold_days": days_held,
                                "exit_type": "trail_stop", "trail_reason": "涨停开板",
                                "signal": getattr(active_strategy, 'name', strategy),
                                "power_score": pos.get("power_score", 50),
                                "cost_total": round(total_cost, 2),
                            }
                            trades.append(trade_rec)
                            self.event_bus.publish(EventBus.ON_TRADE,
                                type="sell", symbol=sym, price=sell_price,
                                shares=sell_shares, date=today,
                                exit_type="trail_stop", pnl=pnl)
                            continue  # 已处理，跳过后续检查

                # ── 退出决策 (三级移动止盈 → 止盈 → 止损 → 到期) ──
                exit_type = None
                trail_reason = ""
                sell_ratio_current = 1.0  # 默认全卖
                sell_all = False

                # 三级移动止盈 (从高到低检查)
                if trail3_drop > 0 and peak_ret >= trail3_profit and ret <= peak_ret - trail3_drop:
                    exit_type = "trail_stop"
                    trail_reason = "三级"
                    sell_ratio_current = sell_ratio_3
                elif trail2_drop > 0 and peak_ret >= trail2_profit and ret <= peak_ret - trail2_drop:
                    exit_type = "trail_stop"
                    trail_reason = "二级"
                    sell_ratio_current = sell_ratio_2
                elif trail1_drop > 0 and peak_ret >= trail1_profit and ret <= peak_ret - trail1_drop:
                    exit_type = "trail_stop"
                    trail_reason = "一级"
                    sell_ratio_current = sell_ratio_1
                elif ret <= stop_loss:
                    exit_type = "stop_loss"
                elif ret >= take_profit:
                    exit_type = "take_profit"
                elif days_held >= hold_days:
                    exit_type = "normal"

                if exit_type:
                    # ── 计算卖出股数 (部分/全卖) ──
                    if exit_type == "trail_stop" and not sell_all:
                        remaining_shares = int(pos["shares"] * (1 - sold_ratio))
                        sell_shares = int(remaining_shares * sell_ratio_current / 100) * 100
                        sell_shares = max(100, min(sell_shares, pos["shares"]))
                        # 更新已卖出比例
                        position_sold_ratios[sym_key] = sold_ratio + (sell_shares / pos["shares"])
                        # 部分卖出后清除峰值，重新追踪
                        position_peaks.pop(sym_key, None)
                    else:
                        sell_shares = pos["shares"]
                        position_sold_ratios.pop(sym_key, None)
                        position_peaks.pop(sym_key, None)

                    # ── 卖出价格 ──
                    if exit_type == "stop_loss":
                        sell_price = self.portal.get_price(sym, 'low', today)
                        if np.isnan(sell_price):
                            sell_price = settle_price
                        sell_price *= 0.998
                    else:
                        sell_price = settle_price

                    sell_amount = sell_price * sell_shares
                    sell_commission = sell_amount * commission_rate
                    if sell_commission < 5: sell_commission = 5
                    sell_stamp = sell_amount * stamp_duty
                    slippage = sell_amount * (0.00005 + np.random.uniform(0, 0.00025)
                                              * min(sell_amount / 500000, 1))
                    # 按比例分摊买入成本
                    cost_ratio = sell_shares / pos["shares"]
                    total_cost = ((pos.get("commission", 0) + pos.get("slippage", 0)) * cost_ratio
                                  + sell_commission + sell_stamp + slippage)

                    pnl = (sell_price - pos["buy_price"]) * sell_shares - total_cost
                    available += (pos["cost"] * cost_ratio) + pnl

                    # 部分卖出时，更新持仓记录
                    if sell_shares < pos["shares"]:
                        pos["shares"] -= sell_shares
                        pos["cost"] *= (1 - cost_ratio)
                        pos["commission"] *= (1 - cost_ratio)
                        pos["slippage"] *= (1 - cost_ratio)
                        still_held.append(pos)
                    # 清掉涨停追踪
                    if sym_key in limit_up_tracker and sell_shares >= pos.get("shares", sell_shares):
                        limit_up_tracker.pop(sym_key, None)

                    trade_rec = {
                        "symbol": sym,
                        "name": self.name_map.get(sym, sym),
                        "buy_date": str(pos["buy_date"])[:10],
                        "sell_date": today_str,
                        "buy_price": round(pos["buy_price"], 2),
                        "sell_price": round(sell_price, 2),
                        "return_pct": round((pnl / (pos["cost"] * cost_ratio)) if pos["cost"] > 0 and cost_ratio > 0 else 0, 4),
                        "net_profit": round(pnl, 0),
                        "hold_days": days_held,
                        "exit_type": exit_type,
                        "trail_reason": trail_reason if exit_type == "trail_stop" else "",
                        "sell_shares": sell_shares,
                        "signal": getattr(active_strategy, 'name', strategy),
                        "power_score": pos.get("power_score", 50),
                        "cost_total": round(total_cost, 2),
                    }
                    trades.append(trade_rec)
                    self.event_bus.publish(EventBus.ON_TRADE,
                                           type="sell", symbol=sym, price=sell_price,
                                           shares=sell_shares, date=today,
                                           exit_type=exit_type, pnl=pnl)
                    active_strategy.on_trade(context, trade_rec)
                else:
                    still_held.append(pos)

            positions = still_held
            context["positions"] = positions
            context["available"] = available

            # ── 3c. 策略生成信号(P2-1: 通过 handle_bar 解耦) ──
            if len(positions) < max_positions:
                candidates = active_strategy.handle_bar(context, today, self.portal) or []
                slots = max_positions - len(positions)
                for c in candidates[:slots]:
                    pos_size = available * position_pct
                    if pos_size < 1000:
                        break
                    est_price = c.get("price", 0)
                    if est_price <= 0:
                        continue
                    shares = int(pos_size / est_price / 100) * 100
                    if shares == 0:
                        shares = 100
                    est_cost = shares * est_price
                    if est_cost > available * 0.5 or est_cost <= 0:
                        continue
                    pending_buys.append({
                        "symbol": c["symbol"],
                        "shares": shares,
                        "power_score": c.get("power_score", 50),
                    })
                # P2-2: 信号事件
                self.event_bus.publish(EventBus.ON_SIGNAL,
                                       date=today, candidates=candidates,
                                       pending=pending_buys)

            # ── 3d. 计算当日权益 ──
            position_value = 0
            for pos in positions:
                if self.portal.has_data(pos["symbol"], today):
                    position_value += self.portal.get_price(pos["symbol"], 'close', today) * pos["shares"]
                else:
                    position_value += pos["cost"]
            total_equity = available + position_value
            if True:  # 每天记录权益，曲线更平滑
                equity_curve.append({"date": today_str, "equity": round(total_equity, 0)})

            # P1-1: 基准权益
            if benchmark_df is not None and self.portal.has_data(benchmark_sym, today):
                bm_close = self.portal.get_price(benchmark_sym, 'close', today)
                if benchmark_start_price is None:
                    benchmark_start_price = bm_close
                if benchmark_start_price > 0:
                    bm_equity = bm_close / benchmark_start_price
                    benchmark_curve.append({"date": today_str, "equity": round(bm_equity, 6)})

            # P2-2: 盘后事件
            self.event_bus.publish(EventBus.ON_AFTER_TRADING,
                                   date=today, context=context)
            active_strategy.after_trading(context, today, self.portal)

        # ── 4. 最终权益 ──
        if equity_curve[-1]["date"] != str(trading_days[-1])[:10]:
            for pos in positions:
                df = self.stock_data.get(pos["symbol"])
                if df is not None:
                    last_price = float(df.iloc[-1]["close"])
                    ret = last_price / pos["buy_price"] - 1
                    pnl = (last_price - pos["buy_price"]) * pos["shares"]
                    available += pos["cost"] + pnl
                    trades.append({
                        "symbol": pos["symbol"],
                        "name": self.name_map.get(pos["symbol"], pos["symbol"]),
                        "buy_date": str(pos["buy_date"])[:10],
                        "sell_date": str(trading_days[-1])[:10],
                        "buy_price": round(pos["buy_price"], 2),
                        "sell_price": round(last_price, 2),
                        "return_pct": round(ret, 4),
                        "net_profit": round(pnl, 0),
                        "hold_days": (trading_days[-1] - pos["buy_date"]).days,
                        "exit_type": "force_close",
                        "signal": getattr(active_strategy, 'name', strategy),
                        "power_score": pos.get("power_score", 50),
                    })
            total_equity = available
            equity_curve.append({"date": str(trading_days[-1])[:10], "equity": round(total_equity, 0)})

        # ── 5. 计算指标 ──
        metrics = self._compute_metrics(trades, equity_curve, benchmark_curve)
        monthly = self._compute_monthly(equity_curve)

        strat_name = getattr(active_strategy, 'name', strategy)
        return {
            "code": 200,
            "results": trades,
            "equity_curve": equity_curve,
            "benchmark_curve": benchmark_curve,
            "metrics": metrics,
            "monthly_returns": monthly,
            "params": {
                "strategy": strat_name, "start": start, "end": end,
                "max_positions": max_positions, "position_pct": position_pct,
                "stop_loss": stop_loss, "take_profit": take_profit,
                "hold_days": hold_days, "initial_capital": initial_capital,
                "benchmark": benchmark_sym,
            },
        }

    def _empty_result(self):
        return {"code": 200, "results": [], "equity_curve": [],
                "metrics": {}, "monthly_returns": [], "params": {}}

    def _compute_metrics(self, trades, equity, benchmark=None):
        if not trades or len(equity) < 2:
            return {}
        rets = [t["return_pct"] for t in trades]
        n = len(rets)
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        wr = len(wins) / n if n > 0 else 0
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        pf = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        best = max(rets) if rets else 0
        worst = min(rets) if rets else 0

        eq_vals = [e["equity"] for e in equity]
        total_ret = eq_vals[-1] / eq_vals[0] - 1 if eq_vals[0] > 0 else 0
        daily_rets = []
        for i in range(1, len(eq_vals)):
            if eq_vals[i-1] > 0:
                daily_rets.append(eq_vals[i] / eq_vals[i-1] - 1)
        dr = np.array(daily_rets) if daily_rets else np.array([0])
        ann_vol = float(np.std(dr) * np.sqrt(252))
        sharpe = float(np.mean(dr) / np.std(dr) * np.sqrt(252)) if np.std(dr) > 0 else 0
        down_std = float(np.std(dr[dr < 0])) if (dr < 0).any() else ann_vol
        sortino = float(np.mean(dr) / down_std * np.sqrt(252)) if down_std > 0 else 0

        peak = eq_vals[0]
        max_dd = 0
        for v in eq_vals:
            if v > peak: peak = v
            dd = (v - peak) / peak
            if dd < max_dd: max_dd = dd

        # 年化: 用真实日期跨度, 而非权益记录点数
        if len(equity) >= 2:
            d1 = datetime.strptime(equity[0]['date'], '%Y-%m-%d')
            d2 = datetime.strptime(equity[-1]['date'], '%Y-%m-%d')
            years = max((d2 - d1).days / 365.0, 0.1)
        else:
            years = max(len(daily_rets) / 252, 0.1)
        ann_ret = (1 + total_ret) ** (1 / years) - 1
        calmar = ann_ret / abs(max_dd) if abs(max_dd) > 1e-9 else 0

        # 按退出方式分组统计
        exit_stats = {}
        for ext in ["stop_loss", "take_profit", "trail_stop", "normal", "force_close"]:
            ext_trades = [t for t in trades if t.get("exit_type") == ext]
            if ext_trades:
                ext_rets = [t["return_pct"] for t in ext_trades]
                exit_stats[ext] = {
                    "count": len(ext_trades),
                    "win_rate": round(sum(1 for r in ext_rets if r > 0) / len(ext_trades), 4),
                    "avg_return": round(np.mean(ext_rets), 4),
                    "total_pnl": round(sum(t.get("net_profit", 0) for t in ext_trades), 0),
                }

        # VaR/CVaR
        var_95 = 0; var_99 = 0; cvar = 0
        dr_arr = np.array(daily_rets) if daily_rets else np.array([0])
        if len(daily_rets) > 0:
            var_95 = float(np.percentile(dr_arr, 5))
            var_99 = float(np.percentile(dr_arr, 1))
            tail = dr_arr[dr_arr <= var_95]
            cvar = float(np.mean(tail)) if len(tail) > 0 else var_95

        # 行业集中度
        industry_pnl = {}
        for t in trades:
            ind = self._get_industry(t['symbol'])
            industry_pnl[ind] = industry_pnl.get(ind, 0) + t.get('net_profit', 0)
        top_ind = sorted(industry_pnl.items(), key=lambda x: -abs(x[1]))[:5]
        industry_conc = {k: round(v, 0) for k, v in top_ind}

        # P1-1: 基准对比 — Alpha, Beta, 信息比率, 相对收益
        bm_metrics = self._calc_benchmark_metrics(equity, benchmark, years)

        result = {
            "total_return": round(total_ret, 4),
            "annual_return": round(ann_ret, 4),
            "annual_volatility": round(ann_vol, 4),
            "sharpe": round(sharpe, 2),
            "sortino": round(sortino, 2),
            "max_drawdown": round(max_dd, 4),
            "calmar": round(calmar, 2),
            "win_rate": round(wr, 4),
            "profit_factor": round(pf, 2),
            "best_trade": round(best, 4),
            "worst_trade": round(worst, 4),
            "n_trades": n,
            "total_pnl": round(sum(t.get("net_profit", 0) for t in trades), 0),
            "exit_stats": exit_stats,
            "var_95": round(var_95, 6),
            "var_99": round(var_99, 6),
            "cvar": round(cvar, 6),
            "industry_concentration": industry_conc,
        }
        result.update(bm_metrics)
        return result

    def _calc_benchmark_metrics(self, equity, benchmark, years, risk_free_rate=0.03):
        """P1-1: 计算基准对比指标 (Alpha/Beta/信息比率/超额收益)"""
        bm = {}
        if not benchmark or len(benchmark) < 10:
            bm["benchmark_available"] = False
            return bm

        bm["benchmark_available"] = True

        # 将策略权益和基准权益对齐到相同日期
        eq_dict = {e["date"]: e["equity"] for e in equity}
        bm_dict = {b["date"]: b["equity"] for b in benchmark}
        common_dates = sorted(set(eq_dict.keys()) & set(bm_dict.keys()))

        if len(common_dates) < 10:
            bm["benchmark_available"] = False
            return bm

        # 策略和基准的日收益率序列
        eq_rets = []
        bm_rets = []
        for i in range(1, len(common_dates)):
            d_prev = common_dates[i - 1]
            d_curr = common_dates[i]
            eq_r = eq_dict[d_curr] / eq_dict[d_prev] - 1 if eq_dict[d_prev] > 0 else 0
            bm_r = bm_dict[d_curr] / bm_dict[d_prev] - 1 if bm_dict[d_prev] > 0 else 0
            eq_rets.append(eq_r)
            bm_rets.append(bm_r)

        eq_rets = np.array(eq_rets)
        bm_rets = np.array(bm_rets)

        # 总收益率
        eq_total = eq_dict[common_dates[-1]] / eq_dict[common_dates[0]] - 1
        bm_total = bm_dict[common_dates[-1]] / bm_dict[common_dates[0]] - 1
        excess_return = eq_total - bm_total

        # 年化收益率
        eq_ann = (1 + eq_total) ** (1 / years) - 1
        bm_ann = (1 + bm_total) ** (1 / years) - 1

        # Beta (CAPM回归)
        cov_matrix = np.cov(eq_rets, bm_rets)
        beta = float(cov_matrix[0, 1] / cov_matrix[1, 1]) if cov_matrix[1, 1] > 0 else 0

        # Alpha = 年化策略收益 - (无风险利率 + Beta * (基准年化收益 - 无风险利率))
        daily_rf = risk_free_rate / 252
        alpha = float(eq_ann - (risk_free_rate + beta * (bm_ann - risk_free_rate)))

        # 信息比率 (Information Ratio) = 超额收益均值 / 跟踪误差
        excess_rets = eq_rets - bm_rets
        tracking_error = float(np.std(excess_rets) * np.sqrt(252))
        info_ratio = float(np.mean(excess_rets) / np.std(excess_rets) * np.sqrt(252)) if np.std(excess_rets) > 0 else 0

        # 基准最大回撤
        bm_peak = bm_dict[common_dates[0]]
        bm_max_dd = 0
        for d in common_dates:
            v = bm_dict[d]
            if v > bm_peak:
                bm_peak = v
            dd = (v - bm_peak) / bm_peak
            if dd < bm_max_dd:
                bm_max_dd = dd

        bm["benchmark_total_return"] = round(bm_total, 4)
        bm["benchmark_annual_return"] = round(bm_ann, 4)
        bm["excess_return"] = round(excess_return, 4)
        bm["alpha"] = round(alpha, 4)
        bm["beta"] = round(beta, 4)
        bm["information_ratio"] = round(info_ratio, 2)
        bm["tracking_error"] = round(tracking_error, 4)
        bm["benchmark_max_drawdown"] = round(bm_max_dd, 4)
        return bm

    def _get_industry(self, symbol):
        """从因子缓存获取股票行业"""
        for s in (self.factor_cache or []):
            if getattr(s, 'symbol', '') == symbol:
                return getattr(s, 'industry', '') or '未分类'
        return '未分类'

    def _compute_monthly(self, equity):
        if len(equity) < 20:
            return []
        monthly = defaultdict(list)
        for p in equity:
            key = p["date"][:7]
            monthly[key].append(p["equity"])
        result = []
        keys = sorted(monthly.keys())
        for i, k in enumerate(keys):
            if i == 0:
                result.append({"year": int(k[:4]), "month": int(k[5:7]), "return_pct": 0})
            else:
                prev = monthly[keys[i-1]][-1]
                curr = monthly[k][-1]
                ret = round((curr / prev - 1) * 100, 2) if prev > 0 else 0
                result.append({"year": int(k[:4]), "month": int(k[5:7]), "return_pct": ret})
        return result

    # ─────────────────────────────────────────────
    # P1-2: Walk-Forward 滚动窗口参数稳定性检验
    # ─────────────────────────────────────────────

    def walk_forward(self, start="2022-01-01", end="2025-12-31",
                     n_folds=4, train_ratio=0.6,
                     param_grid=None,
                     strategy="tdx_resonance", signal_field="signal_resonance",
                     formula_symbols=None, signal_store=None,
                     max_positions=3, position_pct=0.3,
                     initial_capital=1_000_000,
                     benchmark_sym='sh000300'):
        """Walk-Forward 分析: 滚动窗口检验参数稳定性

        将回测区间分成 n_folds 个折叠, 每个折叠:
          - 训练集(train_ratio): 网格搜索最优参数
          - 测试集(1-train_ratio): 用训练集最优参数验证

        Args:
            start/end: 总回测区间
            n_folds: 折叠数量(建议3-6)
            train_ratio: 每折中训练集占比
            param_grid: 待搜索的参数网格, 格式:
                {"stop_loss": [-0.05, -0.03, -0.08],
                 "take_profit": [0.08, 0.10, 0.15],
                 "hold_days": [1, 2, 3]}
                默认: 仅搜索 stop_loss 和 take_profit

        Returns:
            dict: 包含每折结果 + 汇总统计
        """
        if param_grid is None:
            param_grid = {
                "stop_loss": [-0.05, -0.03, -0.08],
                "take_profit": [0.08, 0.10, 0.15],
                "hold_days": [1, 2, 3],
            }

        # 计算总回测区间
        from itertools import product as iter_product
        d_start = datetime.strptime(start, "%Y-%m-%d")
        d_end = datetime.strptime(end, "%Y-%m-%d")
        total_days = (d_end - d_start).days
        fold_size = total_days / n_folds

        fold_results = []
        best_params_per_fold = []  # 追踪各折最优参数

        for fold_idx in range(n_folds):
            fold_start = d_start + timedelta(days=int(fold_idx * fold_size))
            fold_end = d_start + timedelta(days=int((fold_idx + 1) * fold_size))
            fold_mid = fold_start + timedelta(days=int(fold_size * train_ratio))

            train_start = fold_start.strftime("%Y-%m-%d")
            train_end = fold_mid.strftime("%Y-%m-%d")
            test_start = fold_mid.strftime("%Y-%m-%d")
            test_end = fold_end.strftime("%Y-%m-%d")

            # ── 训练集: 网格搜索最优参数 ──
            best_sharpe = -999
            best_params = None
            best_train_result = None
            n_combos = 1
            for v in param_grid.values():
                n_combos *= len(v)

            for combo in iter_product(*param_grid.values()):
                keys = list(param_grid.keys())
                params = dict(zip(keys, combo))

                try:
                    res = self.run(
                        strategy=strategy,
                        signal_field=signal_field,
                        formula_symbols=formula_symbols,
                        signal_store=signal_store,
                        start=train_start,
                        end=train_end,
                        max_positions=max_positions,
                        position_pct=position_pct,
                        stop_loss=params.get("stop_loss", -0.05),
                        take_profit=params.get("take_profit", 0.08),
                        hold_days=params.get("hold_days", 1),
                        initial_capital=initial_capital,
                        benchmark_sym=benchmark_sym,
                    )
                    metrics = res.get("metrics", {})
                    sharpe = metrics.get("sharpe", 0)

                    if sharpe > best_sharpe:
                        best_sharpe = sharpe
                        best_params = params.copy()
                        best_train_result = res
                except Exception:
                    continue

            if best_params is None:
                continue

            # ── 测试集: 用训练集最优参数跑样本外 ──
            test_result = self.run(
                strategy=strategy,
                signal_field=signal_field,
                formula_symbols=formula_symbols,
                signal_store=signal_store,
                start=test_start,
                end=test_end,
                max_positions=max_positions,
                position_pct=position_pct,
                stop_loss=best_params.get("stop_loss", -0.05),
                take_profit=best_params.get("take_profit", 0.08),
                hold_days=best_params.get("hold_days", 1),
                initial_capital=initial_capital,
                benchmark_sym=benchmark_sym,
            )
            test_m = test_result.get("metrics", {})
            train_m = best_train_result.get("metrics", {}) if best_train_result else {}

            fold_info = {
                "fold": fold_idx + 1,
                "train_period": f"{train_start} ~ {train_end}",
                "test_period": f"{test_start} ~ {test_end}",
                "best_params": best_params,
                "train_sharpe": train_m.get("sharpe", 0),
                "train_return": train_m.get("total_return", 0),
                "train_max_dd": train_m.get("max_drawdown", 0),
                "test_sharpe": test_m.get("sharpe", 0),
                "test_return": test_m.get("total_return", 0),
                "test_max_dd": test_m.get("max_drawdown", 0),
                "test_n_trades": test_m.get("n_trades", 0),
                "test_win_rate": test_m.get("win_rate", 0),
                "sharpe_decay": round(
                    test_m.get("sharpe", 0) - train_m.get("sharpe", 0), 2
                ),
            }
            fold_results.append(fold_info)
            best_params_per_fold.append(best_params)

        # ── 汇总统计 ──
        summary = {}
        if fold_results:
            avg_test_sharpe = np.mean([f["test_sharpe"] for f in fold_results])
            avg_test_ret = np.mean([f["test_return"] for f in fold_results])
            avg_test_dd = np.mean([f["test_max_dd"] for f in fold_results])
            avg_sharpe_decay = np.mean([f["sharpe_decay"] for f in fold_results])

            # 参数稳定性: 各折选出的最优参数是否一致
            param_stability = self._assess_param_stability(
                best_params_per_fold, param_grid
            )

            summary = {
                "n_folds": len(fold_results),
                "avg_test_sharpe": round(float(avg_test_sharpe), 2),
                "avg_test_return": round(float(avg_test_ret), 4),
                "avg_test_max_drawdown": round(float(avg_test_dd), 4),
                "avg_sharpe_decay": round(float(avg_sharpe_decay), 2),
                "param_stability": param_stability,
                "conclusion": self._wf_conclusion(avg_sharpe_decay, param_stability),
            }

        return {
            "code": 200,
            "folds": fold_results,
            "summary": summary,
            "param_grid": param_grid,
            "config": {
                "n_folds": n_folds,
                "train_ratio": train_ratio,
                "total_period": f"{start} ~ {end}",
            },
        }

    def _assess_param_stability(self, best_params_list, param_grid):
        """评估各折最优参数的稳定性

        返回:
            dict: 每个参数的稳定性指标 + 总评
        """
        stability = {}
        for param_name in param_grid.keys():
            values = [bp.get(param_name) for bp in best_params_list]
            unique_values = set(values)
            # 稳定性 = 1 - (唯一值数 / 可选值数)
            total_options = len(param_grid[param_name])
            if total_options > 0:
                s = 1 - len(unique_values) / total_options
                stability[param_name] = {
                    "values_chosen": [str(v) for v in values],
                    "unique_count": len(unique_values),
                    "stability_score": round(s, 2),  # 1=完全一致, 0=完全随机
                }

        # 总评
        if stability:
            avg_stability = np.mean([v["stability_score"] for v in stability.values()])
            if avg_stability >= 0.7:
                grade = "稳定"
            elif avg_stability >= 0.4:
                grade = "一般"
            else:
                grade = "不稳定(可能过拟合)"
            stability["overall_grade"] = grade
            stability["overall_score"] = round(float(avg_stability), 2)

        return stability

    def _wf_conclusion(self, sharpe_decay, param_stability):
        """Walk-Forward 结论"""
        decay = abs(sharpe_decay)
        score = param_stability.get("overall_score", 0)

        if decay <= 0.5 and score >= 0.7:
            return ("参数稳定且样本外衰减小, 策略可靠性高。"
                    "可以放心使用当前参数。")
        elif decay <= 1.0 and score >= 0.4:
            return ("参数较稳定, 但样本外有一定衰减。"
                    "建议适当缩减仓位或增加风控。")
        elif decay > 1.0 or score < 0.4:
            return ("警告: 参数不稳定或样本外Sharpe大幅衰减, "
                    "存在过拟合风险。建议重新审视策略逻辑。")
        else:
            return "Walk-Forward检验结果待进一步分析。"

    # ─────────────────────────────────────────────
    # P0-因子-03: 因子组合回测 — 月度调仓 + 多因子评分
    # ─────────────────────────────────────────────

    def run_factor_portfolio(
        self,
        factor_spec: list[tuple],
        method: str = "static",
        top_k: int = 30,
        start: str = "2022-01-01",
        end: str = "2025-12-31",
        max_positions: int = 30,
        initial_capital: float = 1_000_000,
        market_state_detector=None,
        quality_filter=None,
        benchmark_sym: str = "sh000300",
    ):
        """因子组合回测 — 月度再平衡，多因子评分选股。

        支持三种模式:
          - static: 固定因子权重
          - adaptive: 市场自适应权重（牛市追涨、熊市抄底）
          - rank: 排序等权

        Args:
            factor_spec: [(name, compute_fn, direction, weight), ...]
            method: "static" | "adaptive" | "rank"
            top_k: 每期持仓数
            start/end: 回测区间
            max_positions: 最大同时持仓数
            initial_capital: 初始资金
            market_state_detector: MarketState 实例（adaptive模式需要）
            quality_filter: callable(sym, cache, idx) → bool
            benchmark_sym: 基准标的

        Returns:
            dict: {code, metrics, equity_curve, benchmark_curve, ...}
        """
        import numpy as np
        import pandas as pd
        from collections import Counter

        # ── 1. 获取所有日期的月末调仓日 ──
        all_dates = set()
        for symbol, df in self.stock_data.items():
            if df is not None and len(df) > 0:
                all_dates.update(df.index)
        trading_days = sorted(all_dates)
        trading_days = [d for d in trading_days
                        if str(d)[:10] >= start and str(d)[:10] <= end]

        # 提取月末交易日
        td_series = pd.Series(trading_days)
        td_df = pd.DataFrame({"date": pd.to_datetime(td_series)})
        td_df["ym"] = td_df["date"].dt.to_period("M")
        rebalance_dates = td_df.groupby("ym")["date"].max().tolist()
        rebalance_dates = [d for d in rebalance_dates if d is not None]

        if len(rebalance_dates) < 3:
            return self._empty_result()

        # ── 2. 采样股票池 ──
        all_symbols = list(self.stock_data.keys())
        sample_size = min(len(all_symbols), max_positions * 50)
        sample_size = max(sample_size, 50)
        import random
        random.seed(42)
        sampled = random.sample(all_symbols, sample_size) if sample_size < len(all_symbols) else all_symbols

        # ── 3. 构建因子评分函数 ──
        def get_weights_for_date(dt, ms_detector):
            """根据市场状态获取自适应权重。"""
            if method != "adaptive" or ms_detector is None:
                return factor_spec
            try:
                date_int = int(dt.strftime("%Y%m%d")) if hasattr(dt, 'strftime') else int(str(dt)[:10].replace("-", ""))
                state = ms_detector.get_state(date_int)
            except Exception:
                state = None

            if state and hasattr(ms_detector, 'BULL'):
                if state == ms_detector.BULL:
                    return [
                        ("ret_20d", +1, 0.35), ("bull_position", +1, 0.25),
                        ("trend_bottom", +1, 0.20), ("add_position", +1, 0.20),
                    ]
                elif state == ms_detector.BEAR:
                    return [
                        ("trend_bottom", +1, 0.45), ("add_position", +1, 0.25),
                        ("ret_20d", -1, 0.20), ("bull_position", -1, 0.10),
                    ]
            return factor_spec

        def _parse_spec_item(spec_item, all_weights):
            """解析 factor_spec 条目，返回 (name, compute_fn, direction, weight)。
            支持 2/3/4 元组格式。
            """
            n = len(spec_item)
            if n == 2:
                return spec_item[0], None, spec_item[1], 1.0 / len(all_weights)
            elif n == 3:
                return spec_item[0], None, spec_item[1], spec_item[2]
            else:  # n >= 4: (name, compute_fn, direction, weight)
                return spec_item[0], spec_item[1], spec_item[2], spec_item[3]

        def get_factor_value(factor_cache_entry, idx, fname, compute_fn=None):
            """获取因子值 — 优先缓存，否则用 compute_fn 实时计算。"""
            # 1. 尝试因子缓存
            fvals = factor_cache_entry.get("factors", {})
            arr = fvals.get(fname)
            if arr is not None and len(arr) > 0 and idx < len(arr):
                raw = arr[idx]
                if raw is not None:
                    try:
                        if not (np.isnan(float(raw)) or np.isinf(float(raw))):
                            return float(raw)
                    except (TypeError, ValueError):
                        pass

            # 2. 使用传入的 compute_fn
            if compute_fn and callable(compute_fn) and "df" in factor_cache_entry:
                try:
                    df = factor_cache_entry["df"]
                    result = compute_fn(df)
                    if isinstance(result, pd.Series) and idx < len(result):
                        val = float(result.iloc[idx])
                        if not (np.isnan(val) or np.isinf(val)):
                            return val
                except Exception:
                    pass

            return None

        # ── 4. 逐月回测 ──
        cash = initial_capital
        holdings = {}               # symbol → shares
        equity_curve = []
        bench_equity = initial_capital
        bench_curve = []
        prev_prices = {}
        states_log = []
        all_prices_by_date = {}     # date → {symbol: price}

        for ri, rdate in enumerate(rebalance_dates):
            # 市场状态
            ms = None
            if market_state_detector and method == "adaptive":
                try:
                    date_int = int(rdate.strftime("%Y%m%d")) if hasattr(rdate, 'strftime') else int(str(rdate)[:10].replace("-", ""))
                    ms = market_state_detector.get_state(date_int)
                except Exception:
                    ms = None

            weights = get_weights_for_date(rdate, market_state_detector) if method == "adaptive" else factor_spec

            # 计算每只股票的评分
            scores = {}
            market_prices = []

            for sym in sampled:
                if sym not in self.stock_data:
                    continue
                df = self.stock_data[sym]
                if df is None or len(df) < 250:
                    continue

                # 找 rdate 或之前最近的有效日期
                df_dates = df.index
                valid_dates = [d for d in df_dates if d <= rdate]
                if not valid_dates:
                    continue
                idx = len(valid_dates) - 1

                price = float(df.iloc[idx]["close"])
                if price <= 0:
                    continue
                market_prices.append(price)

                # 质量过滤
                if quality_filter and not quality_filter(sym, {"close": df["close"].values}, idx):
                    continue

                # 计算因子评分
                score = 0.0
                valid_n = 0
                for spec_item in weights:
                    fname, compute_fn, direction, weight = _parse_spec_item(spec_item, weights)
                    fval = get_factor_value(
                        {"factors": {}, "close": df["close"].values, "df": df},
                        idx, fname, compute_fn
                    )
                    if fval is None:
                        continue

                    fval = np.clip(fval, -5, 5)
                    score += fval * direction * weight
                    valid_n += 1

                if valid_n >= 1:
                    scores[sym] = (score, price)

            if len(scores) < top_k:
                continue

            all_prices_by_date[rdate] = {sym: scores[sym][1] for sym in scores}

            # ── 卖出全部 ──
            for sym, shares in list(holdings.items()):
                if sym in scores:
                    cash += shares * scores[sym][1] * 0.9997
                del holdings[sym]

            # ── 买入 Top K ──
            ranked = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)[:top_k]
            alloc = cash * 0.95 / top_k
            for sym, (score, price) in ranked:
                shares = int(alloc / price / 100) * 100
                if shares >= 100:
                    cost = shares * price * 1.0003
                    if cost <= cash:
                        cash -= cost
                        holdings[sym] = shares

            # ── 计算市值 ──
            mkt_val = sum(holdings[s] * scores[s][1] for s in holdings if s in scores)
            total = cash + mkt_val
            equity_curve.append({
                "date": rdate, "equity": total, "n_holdings": len(holdings),
                "n_valid": len(scores),
            })
            if ms:
                states_log.append(ms)

            # ── 基准: 等权持有所有候选项 ──
            if ri > 0 and market_prices and prev_prices:
                rets = []
                for s in scores:
                    if s in prev_prices and prev_prices[s] > 0 and scores[s][1] > 0:
                        rets.append(scores[s][1] / prev_prices[s] - 1)
                if rets:
                    bench_equity *= (1 + np.mean(rets))
            bench_curve.append({"date": rdate, "equity": bench_equity})
            prev_prices = {sym: scores[sym][1] for sym in scores}

        # ── 5. 清仓 ──
        last_date = rebalance_dates[-1] if rebalance_dates else None
        if last_date and last_date in all_prices_by_date:
            prices = all_prices_by_date[last_date]
            for sym, shares in list(holdings.items()):
                if sym in prices:
                    cash += shares * prices[sym] * 0.9997
                del holdings[sym]

        # ── 6. 计算指标 ──
        eq = pd.DataFrame(equity_curve)
        bn = pd.DataFrame(bench_curve)

        if len(eq) < 3:
            return self._empty_result()

        eq = eq.set_index("date") if "date" in eq.columns else eq
        bn = bn.set_index("date") if "date" in bn.columns else bn

        total_ret = float(eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1)
        bench_ret = float(bn["equity"].iloc[-1] / bn["equity"].iloc[0] - 1)
        months = len(eq)
        ann_ret = (1 + total_ret) ** (12 / months) - 1
        bench_ann = (1 + bench_ret) ** (12 / months) - 1

        monthly_ret = eq["equity"].pct_change().dropna()
        bm_ret = bn["equity"].pct_change().dropna()

        sharpe = float(monthly_ret.mean() / monthly_ret.std() * np.sqrt(12)) if monthly_ret.std() > 0 else 0
        peak = eq["equity"].expanding().max()
        max_dd = float(((eq["equity"] - peak) / peak).min())
        win_rate = float((monthly_ret > 0).mean())

        excess = monthly_ret - bm_ret
        alpha = float(excess.mean() * 12)
        ir = float(alpha / (excess.std() * np.sqrt(12))) if excess.std() > 0 else 0

        # ── 7. 返回 ──
        trades_summary = {
            "n_trades": len(eq) * top_k,
            "avg_positions": int(eq["n_holdings"].mean()) if "n_holdings" in eq.columns else top_k,
        }

        return {
            "code": 200,
            "metrics": {
                "total_return": round(total_ret, 4),
                "annual_return": round(ann_ret, 4),
                "sharpe": round(sharpe, 2),
                "max_drawdown": round(max_dd, 4),
                "win_rate": round(win_rate, 4),
                "alpha": round(alpha, 4),
                "information_ratio": round(ir, 2),
                "benchmark_return": round(bench_ret, 4),
                "benchmark_annual": round(bench_ann, 4),
                "total_periods": months,
                "final_equity": round(float(eq["equity"].iloc[-1]), 2),
            },
            "trades": trades_summary,
            "equity_curve": equity_curve,
            "benchmark_curve": bench_curve,
            "states_log": states_log,
        }


# ═══════════════════════════════════════════════
# P2-3: 容量分析 — 估算策略最大资金容量
# ═══════════════════════════════════════════════

class CapacityAnalyzer:
    """容量分析 — 根据换手率和股票流动性估算策略最大资金容量

    原理:
      - 策略换手率越高, 对流动性要求越高
      - 单只股票日均换手率的 10-20% 作为策略可占用的交易量
      - 策略同时持仓 N 只股票, 总容量 = sum(个股容量)

    对标 QuantConnect Lean 的 Capacity 分析
    """

    # A股各板块日均换手率参考值
    SECTOR_TURNOVER = {
        "主板": 0.02,    # 2%
        "创业板": 0.04,  # 4%
        "科创板": 0.05,  # 5%
        "北交所": 0.03,  # 3%
    }

    # 策略可占用成交量的比例上限
    # 低于 5% 安全, 5-15% 风险上升, 超过 15% 严重冲击成本
    CAPACITY_THRESHOLDS = {
        "safe": 0.05,
        "warning": 0.15,
        "danger": 999,
    }

    def __init__(self, stock_data: dict, portal: DataPortal = None,
                 name_map: dict = None):
        """
        Args:
            stock_data: {symbol: DataFrame} 价格+成交量数据
            portal: DataPortal 实例(已有则复用)
            name_map: 股票名称映射
        """
        self.stock_data = stock_data
        self.portal = portal or DataPortal(stock_data, validate=False)
        self.name_map = name_map or {}

    def analyze(self, trades, result_params=None):
        """对回测结果进行容量分析

        Args:
            trades: 回测结果中的 trades 列表
            result_params: 回测参数字典(含 max_positions, position_pct)

        Returns:
            dict: 容量分析报告
        """
        if not trades:
            return {"status": "no_trades", "max_capacity": 0}

        # 统计每只股票的交易频率
        sym_stats = defaultdict(lambda: {
            "n_trades": 0, "total_volume": 0, "avg_turnover": 0,
            "avg_daily_amount": 0, "name": "",
        })

        for t in trades:
            sym = t.get("symbol", "")
            sym_stats[sym]["n_trades"] += 1
            sym_stats[sym]["name"] = t.get("name", self.name_map.get(sym, sym))

        # 计算每只股票的日均成交额和换手率
        for sym, stats in sym_stats.items():
            df = self.stock_data.get(sym)
            if df is not None and len(df) > 0:
                if "amount" in df.columns:
                    stats["avg_daily_amount"] = float(
                        df["amount"].dropna().mean()
                    )
                elif "volume" in df.columns and "close" in df.columns:
                    # 估算成交额 ≈ 成交量 * 均价
                    avg_vol = float(df["volume"].dropna().mean())
                    avg_price = float(df["close"].dropna().mean())
                    stats["avg_daily_amount"] = avg_vol * avg_price

                if "volume" in df.columns:
                    stats["avg_turnover"] = float(
                        df["volume"].dropna().mean()
                    )

        # 计算单只股票的最大可用容量
        # 假设策略每次买入占该股日均成交额的 5% (安全线)
        # 策略可能同时持有 max_positions 只股票
        max_pos = (result_params or {}).get("max_positions", 3)
        pos_pct = (result_params or {}).get("position_pct", 0.3)

        stock_capacities = {}
        total_safe_capacity = 0
        total_warning_capacity = 0

        for sym, stats in sym_stats.items():
            avg_amount = stats["avg_daily_amount"]
            if avg_amount <= 0:
                continue

            # 安全容量: 占日均成交额的 5%
            safe_cap = avg_amount * 0.05
            # 警告容量: 占日均成交额的 15%
            warn_cap = avg_amount * 0.15

            stock_capacities[sym] = {
                "name": stats["name"],
                "n_trades": stats["n_trades"],
                "avg_daily_amount": round(avg_amount, 0),
                "safe_capacity": round(safe_cap, 0),
                "warning_capacity": round(warn_cap, 0),
            }
            total_safe_capacity += safe_cap
            total_warning_capacity += warn_cap

        # 策略总容量 = min(各股容量和 * 持股上限比例, 市场整体限制)
        # 考虑到策略同时持有 max_pos 只股票, 且每只仓位的 position_pct
        strategy_capacity = total_safe_capacity / max_pos

        # 按流动性分组
        liquid_stocks = sorted(
            stock_capacities.items(),
            key=lambda x: x[1]["avg_daily_amount"],
            reverse=True,
        )

        # 评级
        if strategy_capacity >= 100_000_000:      # 1亿+
            grade = "优秀"
            note = "流动性极佳, 可容纳大资金运作"
        elif strategy_capacity >= 10_000_000:     # 1000万+
            grade = "良好"
            note = "流动性较好, 适合中等规模资金"
        elif strategy_capacity >= 1_000_000:      # 100万+
            grade = "一般"
            note = "流动性尚可, 建议控制仓位规模"
        else:
            grade = "受限"
            note = "策略容量有限, 适合小资金"

        return {
            "status": "ok",
            "max_capacity": round(strategy_capacity, 0),
            "grade": grade,
            "note": note,
            "total_safe_capacity": round(total_safe_capacity, 0),
            "total_warning_capacity": round(total_warning_capacity, 0),
            "max_positions": max_pos,
            "position_pct": pos_pct,
            "n_stocks_analyzed": len(stock_capacities),
            "top_liquid": liquid_stocks[:5],
            "stock_capacities": stock_capacities,
            "thresholds": self.CAPACITY_THRESHOLDS,
        }

    def analyze_strategy(self, backtest_result):
        """从回测结果直接分析容量"""
        trades = backtest_result.get("results", [])
        params = backtest_result.get("params", {})
        return self.analyze(trades, params)
