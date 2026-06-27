"""
E220 — QMTDataProvider 适配器（行情源升级）
============================================

毫秒级 QMT xtdata 行情源，通达信 .lc5/.day 降级保底。

- **QMT 模式** (USE_QMT=True): xtdata 订阅全市场行情，callback 推送
- **降级模式** (USE_QMT=False, 默认): tdx_realtime.fetch_batch() 本地零延迟

双重保障:
    USE_QMT=True  →  QMT 掉线 / 非交易时段  →  自动回退通达信
    USE_QMT=False →  通达信本地 .lc5 + .day 文件，零网络开销

不改动 D:\\quant_web\\ 下任何现有文件。
只做行情（xtdata），不碰交易（xttrader）。

Usage::

    from qmt_data_provider import QMTDataProvider, MarketSnapshot

    provider = QMTDataProvider()
    provider.USE_QMT = False  # 默认降级，确认QMT环境后改为True

    # 订阅实时行情 → 异步消费
    import queue
    q = queue.Queue()
    provider.subscribe_realtime(['600519', '000001'], q)
    while True:
        snap = q.get(timeout=5)
        print(f"{snap.symbol}: {snap.price}")

    # 拉取历史K线
    df = provider.get_historical_data('600519', period='1d', count=240)
"""

from __future__ import annotations

import json
import os
import queue
import struct
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ═══════════════════════════════════════════════════════════════
#  格式转换
# ═══════════════════════════════════════════════════════════════

def to_qmt(code: str, market: str) -> str:
    """内部代码 → QMT 格式.

    Examples::

        to_qmt('600519', 'sh') → '600519.SH'
        to_qmt('000001', 'sz') → '000001.SZ'
    """
    return f"{code}.{'SH' if market == 'sh' else 'SZ' if market == 'sz' else 'BJ'}"


def from_qmt(qmt_code: str) -> tuple[str, str]:
    """QMT 格式 → (代码, 市场).

    Examples::

        from_qmt('600519.SH') → ('600519', 'sh')
        from_qmt('000001.SZ') → ('000001', 'sz')
    """
    return (qmt_code[:6], qmt_code[7:].lower())


def detect_market(code: str) -> str:
    """根据代码推断市场.

    Returns:
        'sh' | 'sz' | 'bj'
    """
    code = str(code).strip().lower().replace("sh", "").replace("sz", "").replace("bj", "")
    if not code.isdigit() or len(code) != 6:
        return "sh"
    if code.startswith("6"):
        return "sh"
    elif code.startswith(("0", "3")):
        return "sz"
    elif code.startswith(("4", "8")):
        return "bj"
    return "sh"


# ═══════════════════════════════════════════════════════════════
#  MarketSnapshot — 统一行情快照
# ═══════════════════════════════════════════════════════════════

@dataclass
class MarketSnapshot:
    """统一行情快照数据类。

    无论来源 (QMT/通达信) 均封装为此格式，放入 ``queue.Queue`` 供消费者使用。
    """

    symbol: str         # 股票代码（内部格式，如 '600519'）
    timestamp: int = 0  # Unix 毫秒时间戳
    price: float = 0.0  # 最新价
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: int = 0     # 累计成交量 (股)
    amount: float = 0.0 # 累计成交额 (元)
    pre_close: float = 0.0  # 昨收价
    change_pct: float = 0.0 # 涨跌幅 (%)

    # ── 五档盘口 (QMT 模式可用) ──
    bid_prices: list[float] = field(default_factory=lambda: [0.0] * 5)
    bid_volumes: list[int] = field(default_factory=lambda: [0] * 5)
    ask_prices: list[float] = field(default_factory=lambda: [0.0] * 5)
    ask_volumes: list[int] = field(default_factory=lambda: [0] * 5)

    # ── 涨跌停 ──
    limit_up: float = 0.0
    limit_down: float = 0.0

    # ── 元信息 ──
    name: str = ""
    data_source: str = "unknown"  # 'qmt' | 'tdx' | 'day'

    @property
    def is_up(self) -> bool:
        return self.change_pct > 0

    @property
    def is_limit_up(self) -> bool:
        return self.limit_up > 0 and abs(self.price - self.limit_up) < 0.005

    @property
    def is_limit_down(self) -> bool:
        return self.limit_down > 0 and abs(self.price - self.limit_down) < 0.005

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "price": self.price,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
            "amount": self.amount,
            "pre_close": self.pre_close,
            "change_pct": self.change_pct,
            "limit_up": self.limit_up,
            "limit_down": self.limit_down,
            "data_source": self.data_source,
            "timestamp": self.timestamp,
        }

    def __repr__(self) -> str:
        arrow = "↑" if self.is_up else "↓" if self.change_pct < 0 else "→"
        return (
            f"Snapshot({self.symbol} {self.name} ¥{self.price:.2f} "
            f"{arrow}{abs(self.change_pct):.2f}% src={self.data_source})"
        )


# ═══════════════════════════════════════════════════════════════
#  DataProvider — 抽象基类
# ═══════════════════════════════════════════════════════════════

class DataProvider(ABC):
    """行情数据提供者抽象基类。

    所有行情源 (QMT/通达信/新浪) 必须实现此接口。
    """

    @abstractmethod
    def get_historical_data(
        self, symbol: str, period: str = "1d", count: int = 240
    ) -> "pd.DataFrame":
        """获取历史K线数据。

        Args:
            symbol: 股票代码 (内部格式，如 '600519')
            period: K线周期 ('1d' / '1w' / '1M' / '60m' / '30m' / '15m' / '5m' / '1m')
            count: 获取最近多少根K线

        Returns:
            pandas DataFrame，包含列: open, high, low, close, volume, amount
            索引为 datetime
        """
        ...

    @abstractmethod
    def subscribe_realtime(
        self, symbols: list[str], queue_obj: queue.Queue
    ) -> None:
        """订阅实时行情，将 MarketSnapshot 推入 queue_obj。

        Args:
            symbols: 股票代码列表
            queue_obj: 消费者队列，接收 MarketSnapshot 对象
        """
        ...


# ═══════════════════════════════════════════════════════════════
#  QMTDataProvider — 核心实现
# ═══════════════════════════════════════════════════════════════

class QMTDataProvider(DataProvider):
    """QMT 行情数据适配器。

    双模式架构::

        USE_QMT=True   → xtdata 毫秒级行情 (subscribe_whole_quote + callback)
        USE_QMT=False  → tdx_realtime.fetch_batch() 本地零延迟 (默认)

    安全约束:
        - 只做行情 (xtdata)，不碰交易 (xttrader)
        - USE_QMT=False 为默认值
        - QMT 模式异常自动回退通达信
    """

    # ══════════ 核心开关 ══════════
    USE_QMT: bool = False
    """True=QMT毫秒级行情, False=通达信降级 (默认)"""

    def __init__(
        self,
        qmt_path: str = "",
        account_id: str = "",
        tdx_data_root: str = "",
    ) -> None:
        """
        Args:
            qmt_path: QMT交易端安装目录 (如 'D:\\国金证券QMT交易端')
            account_id: 资金账号 (可留空，行情不需要)
            tdx_data_root: 通达信 vipdoc 目录 (如 'D:\\通达信\\vipdoc')
        """
        # ── QMT 配置 ──
        self._qmt_path = qmt_path
        self._account_id = account_id
        self._xtdata: Any = None

        # ── 通达信配置 ──
        self._tdx_data_root = tdx_data_root or self._find_tdx_root()

        # ── 实时行情 ──
        self._subscriptions: set[str] = set()
        self._realtime_queues: list[queue.Queue] = []  # 消费者的 queue 列表
        self._bg_running = False
        self._bg_thread: threading.Thread | None = None

        # ── 统计 ──
        self._stats: dict[str, int] = {
            "qmt_fetches": 0,
            "tdx_fetches": 0,
            "snapshots_pushed": 0,
            "fallbacks": 0,
            "errors": 0,
        }

    # ═══════════════════════════════════════════════════════
    #  历史K线
    # ═══════════════════════════════════════════════════════

    def get_historical_data(
        self, symbol: str, period: str = "1d", count: int = 240
    ) -> "pd.DataFrame":
        """获取历史K线 → DataFrame。

        QMT模式: xtdata.get_market_data_ex()
        降级模式: 本地 .day 文件
        """
        import pandas as pd

        code = _clean_code(symbol)
        market = detect_market(code)

        if self.USE_QMT and self._xtdata:
            try:
                return self._get_kline_qmt(code, market, period, count)
            except Exception as e:
                print(f"[QMTProvider] get_kline QMT失败: {e} → 回退通达信")
                self._stats["fallbacks"] += 1
                return self._get_kline_tdx(code, market, period, count)
        else:
            return self._get_kline_tdx(code, market, period, count)

    def _get_kline_qmt(
        self, code: str, market: str, period: str, count: int
    ) -> "pd.DataFrame":
        """QMT 获取历史K线。"""
        import pandas as pd

        qmt_code = to_qmt(code, market)
        period_map = {
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
            "60m": "60m", "1h": "60m", "1d": "1d", "1w": "1w", "1M": "1mon",
        }
        qmt_period = period_map.get(period, "1d")
        fields = ["open", "high", "low", "close", "volume", "amount"]

        # QMT 需要先下载历史数据到本地缓存
        try:
            self._xtdata.download_history_data(
                qmt_code, period=qmt_period, incrementally=True
            )
        except Exception:
            pass  # 可能已经下载过了

        data = self._xtdata.get_market_data_ex(
            field_list=fields,
            stock_list=[qmt_code],
            period=qmt_period,
            count=count,
            dividend_type="front",  # 前复权
        )

        self._stats["qmt_fetches"] += 1

        if not data or "close" not in data:
            return pd.DataFrame(columns=fields)

        close_series = data["close"]
        records: list[dict] = []

        # QMT DataFrame 格式: columns=stock_codes, index=datetime
        if qmt_code in close_series.columns:
            col = close_series[qmt_code]
            raw_records: list[dict] = []
            for ts, val in col.dropna().items():
                ts_dt = (
                    ts if isinstance(ts, datetime)
                    else pd.Timestamp(ts).to_pydatetime()
                )
                rec = {"datetime": ts_dt, "close": float(val)}
                for f in fields:
                    try:
                        rec[f] = float(data[f][qmt_code].get(ts, 0.0))
                    except Exception:
                        rec[f] = 0.0
                raw_records.append(rec)

            raw_records.sort(key=lambda r: r["datetime"])
            records = raw_records[-count:]
        elif isinstance(close_series.index, pd.MultiIndex):
            # MultiIndex (datetime, code) 格式
            for (ts, c), val in close_series.items():
                if c != qmt_code:
                    continue
                ts_dt = (
                    ts if isinstance(ts, datetime)
                    else pd.Timestamp(ts).to_pydatetime()
                )
                rec = {"datetime": ts_dt, "close": float(val)}
                for f in fields:
                    try:
                        rec[f] = float(data[f].get((ts, c), 0.0))
                    except Exception:
                        rec[f] = 0.0
                records.append(rec)
            records.sort(key=lambda r: r["datetime"])
            records = records[-count:]

        if not records:
            return pd.DataFrame(columns=fields)

        df = pd.DataFrame(records)
        df.set_index("datetime", inplace=True)
        return df[fields]

    def _get_kline_tdx(
        self, code: str, market: str, period: str, count: int
    ) -> "pd.DataFrame":
        """通达信本地 .day 文件获取历史K线。"""
        import pandas as pd

        if period not in ("1d", "1w", "1M"):
            # 分钟线暂不支持，返回空
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "volume", "amount"]
            )

        bars = self._read_tdx_day_file(code, market, count)
        self._stats["tdx_fetches"] += 1

        if not bars:
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "volume", "amount"]
            )

        df = pd.DataFrame(bars)
        df.set_index("datetime", inplace=True)
        return df[["open", "high", "low", "close", "volume", "amount"]]

    # ═══════════════════════════════════════════════════════
    #  实时行情
    # ═══════════════════════════════════════════════════════

    def subscribe_realtime(
        self, symbols: list[str], queue_obj: queue.Queue
    ) -> None:
        """订阅实时行情。

        QMT 模式: subscribe_whole_quote(['SH','SZ'], callback=...)
        降级模式: tdx_realtime.fetch_batch() 轮询

        MarketSnapshot 推入 queue_obj，消费者从中获取。
        """
        self._subscriptions.update(_clean_code(s) for s in symbols)

        # 注册消费者的 queue
        if queue_obj not in self._realtime_queues:
            self._realtime_queues.append(queue_obj)

        # 首次调用时启动后台线程
        if not self._bg_running:
            self._bg_running = True
            self._bg_thread = threading.Thread(
                target=self._realtime_loop,
                name="QMTProvider-Realtime",
                daemon=True,
            )
            self._bg_thread.start()
            mode = "QMT" if self.USE_QMT else "通达信"
            print(f"[QMTProvider] 实时行情推送已启动 → {mode}")

    def _realtime_loop(self) -> None:
        """实时行情主循环。

        QMT 模式: 通过回调 + xtdata.run() daemon 线程推送
        降级模式: 轮询 tdx_realtime.fetch_batch()
        """
        if self.USE_QMT and self._xtdata:
            self._realtime_loop_qmt()
        else:
            self._realtime_loop_tdx()

    def _realtime_loop_qmt(self) -> None:
        """QMT 模式 — subscribe_whole_quote 全市场订阅。"""
        # xtdata.run() 必须在 daemon 线程中运行，维持回调
        run_thread = threading.Thread(
            target=self._xtdata.run,
            name="QMT-xtdata-run",
            daemon=True,
        )
        run_thread.start()
        print("[QMTProvider] xtdata.run() daemon 线程已启动")

        # 订阅沪深全市场实时行情
        try:
            seq = self._xtdata.subscribe_whole_quote(
                code_list=["SH", "SZ", "BJ"],
                callback=self._on_qmt_realtime_data,
            )
            print(f"[QMTProvider] subscribe_whole_quote 已订阅 → seq={seq}")
        except Exception as e:
            print(f"[QMTProvider] subscribe_whole_quote 失败: {e} → 回退通达信")
            self._stats["fallbacks"] += 1
            self._realtime_loop_tdx()
            return

        # 保活: 定期检查数据源健康，异常时回退
        last_data_ts = time.time()
        while self._bg_running:
            time.sleep(5)
            if time.time() - last_data_ts > 30:
                print("[QMTProvider] ⚠️ QMT 数据超30秒无更新 → 回退通达信")
                self._stats["fallbacks"] += 1
                self._realtime_loop_tdx()
                return

    def _on_qmt_realtime_data(self, datas: dict) -> None:
        """QMT 实时数据回调。

        由 xtdata 订阅线程调用，将数据封装为 MarketSnapshot 推入所有消费者队列。

        datas 格式: {stock_code: {lastPrice, open, high, low, volume, amount, ...}}
        """
        if not datas or not isinstance(datas, dict):
            return

        now_ms = int(time.time() * 1000)

        for qmt_code, tick in datas.items():
            if not tick or not isinstance(tick, dict):
                continue

            try:
                code, _ = from_qmt(qmt_code)
                price = float(tick.get("lastPrice", 0))
                if price <= 0:
                    continue

                pre_close = float(tick.get("preClose", 0))
                change_pct = (
                    round((price - pre_close) / pre_close * 100, 2)
                    if pre_close > 0
                    else 0.0
                )

                snap = MarketSnapshot(
                    symbol=code,
                    timestamp=now_ms,
                    price=price,
                    open=float(tick.get("open", 0)),
                    high=float(tick.get("high", 0)),
                    low=float(tick.get("low", 0)),
                    volume=int(float(tick.get("volume", 0))),
                    amount=float(tick.get("amount", 0)),
                    pre_close=pre_close,
                    change_pct=change_pct,
                    name=str(tick.get("stockName", "")),
                    data_source="qmt",
                )
                self._push_to_queues(snap)
                self._stats["snapshots_pushed"] += 1
            except Exception:
                pass

    def _realtime_loop_tdx(self) -> None:
        """降级模式 — 轮询 tdx_realtime.fetch_batch()。"""
        print("[QMTProvider] 通达信降级轮询已启动 (1秒间隔)")

        # 尝试导入通达信模块 (位于 D:\quant_web\)
        tdx_fetch = None
        try:
            from tdx_realtime import fetch_batch as _fetch_batch
            tdx_fetch = _fetch_batch
            print("[QMTProvider] tdx_realtime.fetch_batch 导入成功")
        except ImportError:
            print("[QMTProvider] ⚠️ tdx_realtime 不可用，使用内置日线读取")
            tdx_fetch = None

        while self._bg_running:
            if not self._subscriptions:
                time.sleep(1)
                continue

            syms = list(self._subscriptions)
            now_ms = int(time.time() * 1000)
            quotes: dict[str, dict] = {}

            if tdx_fetch:
                # 使用通达信分钟线 (.lc5) 批量读取
                tdx_symbols = []
                for s in syms:
                    market = detect_market(s)
                    tdx_symbols.append((s, market))

                try:
                    quotes = tdx_fetch(tdx_symbols, use_minline=True)
                    self._stats["tdx_fetches"] += 1
                except Exception as e:
                    self._stats["errors"] += 1
                    if self._stats["errors"] <= 3:
                        print(f"[QMTProvider] tdx_fetch 异常: {e}")
                    quotes = {}

            # 兜底: 直接用内置 .day 文件读取
            if not quotes:
                for s in syms:
                    market = detect_market(s)
                    day = self._read_tdx_day_latest(s, market)
                    if day:
                        quotes[s] = day

            # 推入队列
            for code, q in quotes.items():
                price = float(q.get("close", q.get("price", 0)))
                if price <= 0:
                    continue

                pre_close_val = float(q.get("pre_close", 0))
                open_val = float(q.get("open", 0))
                snap = MarketSnapshot(
                    symbol=code,
                    timestamp=now_ms,
                    price=price,
                    open=open_val,
                    high=float(q.get("high", 0)),
                    low=float(q.get("low", 0)),
                    volume=int(float(q.get("volume", 0))),
                    amount=float(q.get("amount", 0)),
                    pre_close=pre_close_val,
                    change_pct=(
                        round((price - pre_close_val) / pre_close_val * 100, 2)
                        if pre_close_val > 0 else 0.0
                    ),
                    data_source="tdx",
                )
                self._push_to_queues(snap)
                self._stats["snapshots_pushed"] += 1

            time.sleep(1)

    def _push_to_queues(self, snap: MarketSnapshot) -> None:
        """将快照推入所有消费者队列 (非阻塞)。"""
        for q in self._realtime_queues:
            try:
                q.put_nowait(snap)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(snap)
                except queue.Empty:
                    pass

    # ═══════════════════════════════════════════════════════
    #  生命周期
    # ═══════════════════════════════════════════════════════

    def connect(self) -> None:
        """初始化数据源连接。

        QMT 模式: 加载 xtquant.xtdata 模块
        降级模式: 验证通达信数据目录
        """
        if self.USE_QMT:
            self._connect_qmt()
        else:
            self._verify_tdx()
        print(f"[QMTProvider] ✅ 就绪 (USE_QMT={self.USE_QMT})")

    def disconnect(self) -> None:
        """释放资源。"""
        self._bg_running = False
        if self._bg_thread and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=5.0)
        self._xtdata = None
        print("[QMTProvider] ⏏ 已断开")

    def _connect_qmt(self) -> None:
        """加载 xtquant 模块。

        MiniQMT: ``pip install xtquant`` 即可。
        完整QMT: 需指定 qmt_path 以注入 sys.path。
        """
        import sys

        if self._qmt_path:
            xtdata_dir = os.path.join(self._qmt_path, "bin.x64")
            for p in [self._qmt_path, xtdata_dir]:
                if os.path.isdir(p) and p not in sys.path:
                    sys.path.insert(0, p)

        try:
            from xtquant import xtdata  # type: ignore[import-untyped]
            self._xtdata = xtdata
            print(f"[QMTProvider] xtdata 模块加载成功")
            if self._qmt_path:
                print(f"[QMTProvider]   QMT路径: {self._qmt_path}")
        except ImportError as e:
            raise ImportError(
                f"无法导入 xtquant. 请确认:\n"
                f"  1) MiniQMT: pip install xtquant\n"
                f"  2) 完整QMT: qmt_path 正确 (当前: '{self._qmt_path}')\n"
                f"  原始错误: {e}"
            )
        except Exception as e:
            raise ConnectionError(f"QMT 连接失败: {e}")

    # ═══════════════════════════════════════════════════════
    #  通达信本地数据
    # ═══════════════════════════════════════════════════════

    def _find_tdx_root(self) -> str:
        """自动寻找通达信 vipdoc 目录。"""
        candidates = [
            r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc",
            r"D:\通达信\vipdoc",
            r"D:\new_tdx\vipdoc",
            r"D:\zd_zsT\vipdoc",
            r"D:\zd_zxT\vipdoc",
            r"C:\zd_zsT\vipdoc",
        ]
        for path in candidates:
            if os.path.isdir(path):
                return path
        return candidates[0]  # 返回第一个作为默认值

    def _verify_tdx(self) -> None:
        """验证通达信数据目录。"""
        if os.path.isdir(self._tdx_data_root):
            print(f"[QMTProvider] 通达信数据目录: {self._tdx_data_root}")
            # 统计可用文件
            total = 0
            for mkt in ["sh", "sz"]:
                mp = os.path.join(self._tdx_data_root, mkt, "lday")
                if os.path.isdir(mp):
                    total += len([f for f in os.listdir(mp) if f.endswith(".day")])
            print(f"[QMTProvider]   日线文件: {total} 只")
        else:
            print(f"[QMTProvider] ⚠️ 通达信数据目录不存在: {self._tdx_data_root}")

    def _read_tdx_day_file(
        self, code: str, market: str, count: int
    ) -> list[dict]:
        """读取通达信 .day 文件，返回 count 条记录。"""
        # 支持两种目录结构: {market}/lday/ 或 lday/{market}/
        search_paths = [
            os.path.join(self._tdx_data_root, market, "lday", f"{market}{code}.day"),
            os.path.join(self._tdx_data_root, "lday", f"{market}{code}.day"),
            os.path.join(self._tdx_data_root, market, f"{market}{code}.day"),
        ]

        for fpath in search_paths:
            if not os.path.exists(fpath):
                continue
            try:
                with open(fpath, "rb") as f:
                    data = f.read()
                return self._parse_tdx_day_bytes(data, count)
            except Exception:
                continue
        return []

    def _parse_tdx_day_bytes(self, data: bytes, count: int) -> list[dict]:
        """解析通达信 .day 二进制数据。"""
        rec_size = 32
        n_records = len(data) // rec_size
        if n_records == 0:
            return []

        start = max(0, n_records - count)
        records: list[dict] = []

        for i in range(start, n_records):
            offset = i * rec_size
            try:
                (
                    date_int, open_raw, high_raw, low_raw, close_raw,
                    amount_val, vol_raw, _rsv,
                ) = struct.unpack_from("<I I I I I f I I", data, offset)

                dt_str = str(date_int)
                if len(dt_str) != 8:
                    continue
                dt = datetime(
                    int(dt_str[0:4]), int(dt_str[4:6]), int(dt_str[6:8])
                )

                # 价格缩放: 正常范围 < 1e6 时除以100，否则除以10000
                div = 100.0 if open_raw < 1_000_000 else 10000.0
                records.append({
                    "datetime": dt,
                    "open": open_raw / div,
                    "high": high_raw / div,
                    "low": low_raw / div,
                    "close": close_raw / div,
                    "volume": float(vol_raw),
                    "amount": float(amount_val),
                })
            except struct.error:
                continue

        return records

    def _read_tdx_day_latest(self, code: str, market: str) -> dict | None:
        """读取最新一条日线 (实时行情兜底)。"""
        search_paths = [
            os.path.join(self._tdx_data_root, market, "lday", f"{market}{code}.day"),
            os.path.join(self._tdx_data_root, "lday", f"{market}{code}.day"),
            os.path.join(self._tdx_data_root, market, f"{market}{code}.day"),
        ]

        for fpath in search_paths:
            if not os.path.exists(fpath):
                continue
            try:
                with open(fpath, "rb") as f:
                    f.seek(-32, os.SEEK_END)
                    raw = f.read(32)
                (
                    _date, o, h, l, c, amt, vol, _rsv,
                ) = struct.unpack_from("<I I I I I f I I", raw)
                div = 100.0 if o < 1_000_000 else 10000.0
                return {
                    "close": c / div,
                    "open": o / div,
                    "high": h / div,
                    "low": l / div,
                    "volume": float(vol),
                    "amount": float(amt),
                }
            except Exception:
                continue
        return None

    # ═══════════════════════════════════════════════════════
    #  统计
    # ═══════════════════════════════════════════════════════

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "mode": "QMT" if self.USE_QMT else "TDX (降级)",
            "use_qmt": self.USE_QMT,
            "subscriptions": len(self._subscriptions),
            "queues": len(self._realtime_queues),
            "bg_running": self._bg_running,
            "tdx_data_root": self._tdx_data_root,
            "qmt_path": self._qmt_path or "(未设置)",
            **self._stats,
        }


# ═══════════════════════════════════════════════════════════════
#  Helper
# ═══════════════════════════════════════════════════════════════

def _clean_code(symbol: str) -> str:
    """清洗股票代码 → 6位数字。"""
    s = str(symbol).strip().lower()
    s = s.replace("sh", "").replace("sz", "").replace("bj", "")
    return s


# ═══════════════════════════════════════════════════════════════
#  测试示例
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print(" E220 — QMTDataProvider 测试")
    print("=" * 60)

    # ── 1. 格式转换测试 ──
    print("\n[1] 格式转换测试...")
    assert to_qmt("600519", "sh") == "600519.SH"
    assert to_qmt("000001", "sz") == "000001.SZ"
    assert from_qmt("600519.SH") == ("600519", "sh")
    assert from_qmt("000001.SZ") == ("000001", "sz")
    print("   ✅ to_qmt / from_qmt 通过")

    # ── 2. MarketSnapshot 测试 ──
    print("\n[2] MarketSnapshot 测试...")
    snap = MarketSnapshot(
        symbol="600519",
        timestamp=1718800000000,
        price=1680.00,
        open=1670.00,
        high=1690.00,
        low=1665.00,
        volume=5000000,
        amount=8400000000.0,
        pre_close=1660.00,
        change_pct=1.20,
        data_source="tdx",
    )
    print(f"   {snap}")
    d = snap.to_dict()
    assert d["symbol"] == "600519"
    assert d["price"] == 1680.00
    assert snap.is_up is True
    print("   ✅ MarketSnapshot 通过")

    # ── 3. 降级模式 (USE_QMT=False) 测试 ──
    print("\n[3] 降级模式测试 (USE_QMT=False)...")
    provider = QMTDataProvider()
    assert provider.USE_QMT is False, "默认应为 USE_QMT=False"
    provider.connect()

    # ── 3a. 历史K线 ──
    print("\n[3a] 历史K线测试...")
    try:
        df = provider.get_historical_data("600519", period="1d", count=10)
        print(f"   行数: {len(df)}, 列: {list(df.columns)}")
        if len(df) > 0:
            print(f"   最新日期: {df.index[-1]}")
            print(f"   最新收盘: {df['close'].iloc[-1]:.2f}")
            print("   ✅ get_historical_data 通过")
        else:
            print("   ⚠️ 数据为空 (通达信目录可能不存在)")
    except Exception as e:
        print(f"   ⚠️ get_historical_data 异常: {e}")

    # ── 3b. 实时行情订阅 ──
    print("\n[3b] 实时行情订阅测试...")
    q = queue.Queue()
    provider.subscribe_realtime(["600519", "000001"], q)

    print("   等待实时数据 (最多10秒)...")
    received = 0
    start = time.time()
    while time.time() - start < 10:
        try:
            snap = q.get(timeout=2)
            print(f"   → {snap}")
            received += 1
            if received >= 3:
                break
        except queue.Empty:
            print("   (等待中...)")

    if received > 0:
        print(f"   ✅ subscribe_realtime 通过 ({received} 条快照)")
    else:
        print("   ⚠️ 未收到实时数据 (非交易时段/通达信目录不存在)")

    # ── 4. 统计 ──
    print("\n[4] 运行统计...")
    for k, v in provider.stats.items():
        print(f"   {k}: {v}")

    provider.disconnect()
    print("\n" + "=" * 60)
    print(" ✅ E220 QMTDataProvider 测试完成")
    print("=" * 60)
