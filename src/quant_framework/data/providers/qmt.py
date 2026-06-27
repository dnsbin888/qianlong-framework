"""
QMT Data Provider Adapter — 毫秒级行情源
=========================================

完全兼容 ``DataProvider`` 抽象接口，内置 ``USE_QMT`` 降级开关。

- **QMT 模式** (USE_QMT=True): xtdata 毫秒级行情，盘口五档，实时推送
- **Sina 保底** (USE_QMT=False): 新浪财经批量 API，零依赖，自动兜底

双模式输出统一封装为 ``MarketSnapshot`` 数据类，通过 ``queue.Queue`` 异步推送。

安全设计:
    USE_QMT=False 时完全不依赖 xtquant 模块，QMT 掉线自动回退 Sina。
    老系统的 app.py / 下单逻辑 / 新浪缓存 完全不受影响。

Usage::

    from quant_framework.data.providers.qmt import QMTDataProvider, MarketSnapshot

    provider = QMTDataProvider(
        USE_QMT=False,                    # ← 降级开关，True=QMT
        qmt_path=r'D:\国金证券QMT交易端',   # QMT安装目录
    )
    provider.connect()

    # 订阅实时行情 → 异步消费
    provider.subscribe_quote(['600000', '000001'])
    while True:
        snap = provider.snapshot_queue.get(timeout=5)
        print(f"{snap.symbol}: ¥{snap.price}  {snap.change_pct:+.2f}%")

    # 或兼容框架的拉取模式
    quotes = provider.get_quote(['600000'])
    bars = provider.get_kline(['600000'], period='1d', count=100)
"""

from __future__ import annotations

import json
import os
import queue
import re
import ssl
import struct
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from quant_framework.core.types import Symbol
from quant_framework.data.models import Bar, Quote
from quant_framework.data.provider import DataProvider


# ═══════════════════════════════════════════════════════════════
# MarketSnapshot — 统一行情快照数据类
# ═══════════════════════════════════════════════════════════════

@dataclass
class MarketSnapshot:
    """统一行情快照 — 无论来源 (QMT/Sina/通达信) 均封装为此格式。

    放入 ``queue.Queue`` 供异步消费者 (策略引擎/风控/UI) 统一消费。
    """

    symbol: str
    timestamp: datetime = field(default_factory=datetime.now)

    # ── OHLCV ──
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    price: float = 0.0
    pre_close: float = 0.0
    volume: float = 0.0       # 累计成交量 (股)
    amount: float = 0.0        # 累计成交额 (元)

    # ── 五档盘口 ──
    bid_prices: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0, 0.0])
    bid_volumes: list[int] = field(default_factory=lambda: [0, 0, 0, 0, 0])
    ask_prices: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0, 0.0])
    ask_volumes: list[int] = field(default_factory=lambda: [0, 0, 0, 0, 0])

    # ── 涨跌停价 ──
    limit_up: float = 0.0
    limit_down: float = 0.0

    # ── 衍生字段 ──
    change_pct: float = 0.0     # 涨跌幅 (%)
    name: str = ""              # 股票名称
    turnover: float = 0.0       # 换手率 (%)
    vol_ratio: float = 1.0      # 量比

    # ── 元信息 ──
    data_source: str = "unknown"  # 'qmt' | 'sina' | 'tdx' | 'cache'

    # ── 便捷属性 ──

    @property
    def is_up(self) -> bool:
        """是否上涨"""
        return self.change_pct > 0

    @property
    def is_limit_up(self) -> bool:
        """是否涨停"""
        return self.limit_up > 0 and abs(self.price - self.limit_up) < 0.005

    @property
    def is_limit_down(self) -> bool:
        """是否跌停"""
        return self.limit_down > 0 and abs(self.price - self.limit_down) < 0.005

    # ── 转换方法 ──

    def to_quote(self) -> Quote:
        """转换为框架标准 ``Quote`` Pydantic 模型"""
        return Quote(
            symbol=self.symbol,
            timestamp=self.timestamp,
            open=self.open,
            high=self.high,
            low=self.low,
            price=self.price,
            pre_close=self.pre_close,
            volume=self.volume,
            amount=self.amount,
            bid_prices=list(self.bid_prices),
            bid_volumes=list(self.bid_volumes),
            ask_prices=list(self.ask_prices),
            ask_volumes=list(self.ask_volumes),
            limit_up=self.limit_up,
            limit_down=self.limit_down,
            change_pct=self.change_pct,
        )

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (兼容旧系统 dict 格式)"""
        return {
            "symbol": self.symbol,
            "name": self.name,
            "close": self.price,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "pre_close": self.pre_close,
            "volume": self.volume,
            "amount": self.amount,
            "change_pct": self.change_pct,
            "turnover": self.turnover,
            "vol_ratio": self.vol_ratio,
            "bid_prices": self.bid_prices,
            "ask_prices": self.ask_prices,
            "limit_up": self.limit_up,
            "limit_down": self.limit_down,
            "data_source": self.data_source,
            "timestamp": self.timestamp.isoformat(),
        }

    def __repr__(self) -> str:
        arrow = "↑" if self.is_up else "↓" if self.change_pct < 0 else "→"
        return (
            f"Snapshot({self.symbol} {self.name} ¥{self.price:.2f} "
            f"{arrow}{abs(self.change_pct):.2f}% src={self.data_source})"
        )


# ═══════════════════════════════════════════════════════════════
# QMTDataProvider — 核心适配器
# ═══════════════════════════════════════════════════════════════

class QMTDataProvider(DataProvider):
    """QMT 行情数据适配器 — 实现 ``DataProvider`` 全部抽象方法。

    **双模式架构**::

        USE_QMT=True   →  xtdata.get_market_data_ex() / subscribe_quote() / get_full_tick()
        USE_QMT=False  →  新浪财经 hq.sinajs.cn 批量API  (现有保底方案)

    实时行情通过后台线程推送到 ``snapshot_queue`` (queue.Queue)，
    同时兼容框架的拉取模式 ``get_quote()``。

    Parameters
    ----------
    USE_QMT:
        ``True`` = 使用 QMT xtdata 毫秒级行情；``False`` = 新浪保底。
        默认 ``False``，确认 QMT 环境稳定后再改为 ``True``。
    qmt_path:
        QMT 交易端安装目录 (如 ``D:\\国金证券QMT交易端``)。
        只在 ``USE_QMT=True`` 时需要，用于 ``sys.path`` 注入。
    account_id:
        资金账号 (QMT 实盘模式需要，MiniQMT/行情模式可留空)。
    snapshot_queue:
        外部传入 ``queue.Queue``，不传则内部创建 (maxsize=10000)。
    sina_batch_size:
        新浪单次批量请求最大股票数，默认80。
    bg_interval:
        后台行情推送间隔 (秒)，QMT 模式建议 0.5，Sina 模式建议 3。
    """

    # 新浪批量请求常量
    _SINA_BATCH_SIZE = 80
    _SINA_URL = "https://hq.sinajs.cn/list="

    # QMT period 映射
    _QMT_PERIOD_MAP: dict[str, str] = {
        "tick": "tick",
        "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
        "60m": "60m", "1h": "60m",
        "1d": "1d", "1w": "1w", "1M": "1mon",
    }

    # QMT 历史数据字段列表
    _QMT_FIELDS = ["open", "high", "low", "close", "volume", "amount"]

    def __init__(
        self,
        USE_QMT: bool = False,
        qmt_path: str = "",
        account_id: str = "",
        snapshot_queue: queue.Queue | None = None,
        sina_batch_size: int = 80,
        bg_interval: float | None = None,
    ) -> None:
        # ══════════ 核心开关 ══════════
        self.USE_QMT = USE_QMT

        # ══════════ QMT 配置 ══════════
        self._qmt_path = qmt_path
        self._account_id = account_id
        self._xtdata: Any = None  # xtquant.xtdata 模块引用

        # ══════════ 行情队列 ══════════
        self.snapshot_queue: queue.Queue = snapshot_queue or queue.Queue(maxsize=10000)

        # ══════════ 配置 ══════════
        self._sina_batch_size = sina_batch_size
        self._bg_interval = bg_interval if bg_interval is not None else (0.5 if USE_QMT else 3.0)

        # ══════════ 内部状态 ══════════
        self._connected: bool = False
        self._subscriptions: set[Symbol] = set()
        self._quote_cache: dict[Symbol, Quote] = {}

        # ── 新浪缓存 (兜底模式) ──
        self._sina_cache: dict[str, dict[str, Any]] = {}
        self._sina_last_fetch: float = 0.0

        # ── 后台线程 ──
        self._bg_thread: threading.Thread | None = None
        self._bg_stop = threading.Event()

        # ── 统计 ──
        self._stats: dict[str, int] = {
            "qmt_fetches": 0,
            "sina_fetches": 0,
            "fallbacks": 0,
            "snapshots_pushed": 0,
            "errors": 0,
        }

        # ── 价格缓存 (kline 兜底) ──
        self._price_cache: dict[str, float] = {}
        self._load_price_cache()

    # ═══════════════════════════════════════════════════════
    #  Lifecycle
    # ═══════════════════════════════════════════════════════

    def connect(self) -> None:
        """建立数据源连接，启动后台行情推送。"""
        if self.USE_QMT:
            self._connect_qmt()
        else:
            self._connect_sina()

        self._connected = True
        self._bg_stop.clear()
        self._bg_thread = threading.Thread(
            target=self._bg_pump_loop,
            name="QMTProvider-BGPump",
            daemon=True,
        )
        self._bg_thread.start()
        mode = "QMT xtdata" if self.USE_QMT else "Sina (保底)"
        print(f"[QMTProvider] ✅ 已连接 → {mode} | 后台推送间隔 {self._bg_interval}s")

    def disconnect(self) -> None:
        """释放连接，停止后台线程。"""
        self._bg_stop.set()
        if self._bg_thread and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=5.0)
        self._xtdata = None
        self._connected = False
        print("[QMTProvider] ⏏ 已断开")

    def is_connected(self) -> bool:
        return self._connected

    @property
    def name(self) -> str:
        return "qmt" if self.USE_QMT else "qmt_sina_fallback"

    # ═══════════════════════════════════════════════════════
    #  Quote — 实时行情接口
    # ═══════════════════════════════════════════════════════

    def subscribe_quote(self, symbols: list[Symbol]) -> None:
        """订阅实时行情推送。

        QMT 模式: 调用 ``xtdata.subscribe_quote()`` 注册 tick 回调。
        Sina 模式: 仅加入订阅列表，后台轮询时自动拉取。
        """
        self._subscriptions.update(symbols)

        if self.USE_QMT and self._xtdata:
            for sym in symbols:
                qmt_code = self._to_qmt_code(sym)
                try:
                    self._xtdata.subscribe_quote(
                        qmt_code,
                        period="tick",
                        callback=self._on_qmt_tick,
                    )
                except Exception as e:
                    print(f"[QMTProvider] subscribe_quote({qmt_code}) 失败: {e}")
                    self._stats["fallbacks"] += 1
                    self._stats["errors"] += 1

    def unsubscribe_quote(self, symbols: list[Symbol]) -> None:
        """取消订阅。"""
        self._subscriptions.difference_update(symbols)

        if self.USE_QMT and self._xtdata:
            for sym in symbols:
                try:
                    self._xtdata.unsubscribe_quote(self._to_qmt_code(sym))
                except Exception:
                    pass

    def get_quote(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        """拉取最新行情快照 (兼容框架拉取模式)。

        QMT: ``get_full_tick()`` 批量获取 → Quote 模型
        Sina: 新浪批量HTTP → 解析 → Quote 模型
        """
        if not symbols:
            return {}

        if self.USE_QMT and self._xtdata:
            return self._get_quote_qmt(symbols)
        else:
            return self._get_quote_sina(symbols)

    # ═══════════════════════════════════════════════════════
    #  K-line — 历史K线接口
    # ═══════════════════════════════════════════════════════

    def get_kline(
        self,
        symbols: list[Symbol],
        period: str = "1d",
        count: int = 100,
    ) -> dict[Symbol, list[Bar]]:
        """获取历史K线数据。

        QMT: ``get_market_data_ex()`` → Bar 列表
        Sina: 本地 TDX .day 文件 → Bar 列表 (零网络开销)
        """
        if not symbols:
            return {}

        if self.USE_QMT and self._xtdata:
            try:
                return self._get_kline_qmt(symbols, period, count)
            except Exception as e:
                print(f"[QMTProvider] get_kline QMT失败: {e} → 回退本地数据")
                self._stats["fallbacks"] += 1
                return self._get_kline_local(symbols, period, count)
        else:
            return self._get_kline_local(symbols, period, count)

    # ═══════════════════════════════════════════════════════
    #  Polling — 轮询引擎接口
    # ═══════════════════════════════════════════════════════

    def wait_update(self, timeout: float | None = None) -> list[Symbol]:
        """阻塞等待数据更新 (兼容 ``PollingEngine``)。

        从 ``snapshot_queue`` 中消费最新的 MarketSnapshot，
        转换为 Quote 写入内部缓存。
        """
        updated: list[Symbol] = []
        deadline = time.time() + (timeout if timeout is not None else 10.0)

        while time.time() < deadline:
            try:
                snap: MarketSnapshot = self.snapshot_queue.get(timeout=1.0)
                if snap:
                    self._quote_cache[snap.symbol] = snap.to_quote()
                    if snap.symbol not in updated:
                        updated.append(snap.symbol)
                    if updated:
                        break  # 拿到更新即返回
            except queue.Empty:
                if updated:
                    break
        return updated

    @property
    def subscribed_symbols(self) -> set[Symbol]:
        return self._subscriptions.copy()

    # ═══════════════════════════════════════════════════════
    #  统计 & 诊断
    # ═══════════════════════════════════════════════════════

    def stats(self) -> dict[str, Any]:
        """获取运行统计信息。"""
        return {
            "mode": "QMT" if self.USE_QMT else "Sina (保底)",
            "connected": self._connected,
            "subscriptions": len(self._subscriptions),
            "queue_size": self.snapshot_queue.qsize(),
            **self._stats,
        }

    def health_check(self) -> dict[str, Any]:
        """快速健康检查 — 尝试获取一只股票行情。"""
        result = {"ok": False, "latency_ms": 0, "source": "", "error": ""}
        t0 = time.perf_counter()

        try:
            # 用 600000 (浦发银行) 做探活
            quotes = self.get_quote(["600000"])
            elapsed = (time.perf_counter() - t0) * 1000
            result["latency_ms"] = round(elapsed, 1)

            if quotes and "600000" in quotes:
                q = quotes["600000"]
                result["ok"] = True
                result["source"] = "qmt" if self.USE_QMT else "sina"
                result["price"] = q.price
            else:
                result["error"] = "无数据返回"
        except Exception as e:
            result["error"] = str(e)
            result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        return result

    # ═══════════════════════════════════════════════════════
    #  QMT 回调 — 实时 tick → MarketSnapshot → Queue
    # ═══════════════════════════════════════════════════════

    def _on_qmt_tick(self, data: Any) -> None:
        """QMT tick 回调 — 将 xtdata 推送到统一队列。

        此函数由 xtdata 的订阅线程调用，因此只是把数据入队，
        不做耗时操作。
        """
        try:
            if not data:
                return
            # data 格式: {stock_code: {field: value, ...}}
            for qmt_code, tick in (data.items() if isinstance(data, dict) else []):
                snap = self._parse_qmt_tick_to_snapshot(qmt_code, tick)
                if snap and snap.price > 0:
                    try:
                        self.snapshot_queue.put_nowait(snap)
                        self._stats["snapshots_pushed"] += 1
                    except queue.Full:
                        # 队列满则丢弃最旧的一条
                        try:
                            self.snapshot_queue.get_nowait()
                            self.snapshot_queue.put_nowait(snap)
                        except queue.Empty:
                            pass
        except Exception:
            pass  # 回调中不抛异常

    # ═══════════════════════════════════════════════════════
    #  Internal: QMT Backend
    # ═══════════════════════════════════════════════════════

    def _connect_qmt(self) -> None:
        """加载 xtquant 模块。

        MiniQMT (极简版): ``pip install xtquant`` 即可，无需完整 QMT 客户端。
        完整 QMT: 需指定安装路径以注入 ``sys.path``。
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

    def _connect_sina(self) -> None:
        """Sina 保底模式 — 预加载价格缓存。"""
        self._load_price_cache()
        print("[QMTProvider] Sina 保底模式就绪 (USE_QMT=False)")

    def _to_qmt_code(self, symbol: str) -> str:
        """内部代码 → QMT 格式.

        Examples::

            '600000' → '600000.SH'
            '000001' → '000001.SZ'
            'sh600000' → '600000.SH'
        """
        s = symbol.strip().lower().replace("sh", "").replace("sz", "").replace("bj", "")
        if len(s) == 6 and s.isdigit():
            if s.startswith("6"):
                return f"{s}.SH"
            elif s.startswith(("0", "3")):
                return f"{s}.SZ"
            elif s.startswith(("4", "8")):
                return f"{s}.BJ"
        return symbol

    def _from_qmt_code(self, qmt_code: str) -> str:
        """QMT 代码 → 内部6位代码."""
        return qmt_code.replace(".SH", "").replace(".SZ", "").replace(".BJ", "").lower()

    def _parse_qmt_tick_to_snapshot(self, qmt_code: str, tick: dict) -> MarketSnapshot | None:
        """将 QMT tick dict 解析为 MarketSnapshot。"""
        try:
            pre_close = float(tick.get("lastClose", 0))
            price = float(tick.get("lastPrice", 0))
            if price <= 0:
                return None

            change_pct = (
                round((price - pre_close) / pre_close * 100, 2) if pre_close > 0 else 0.0
            )

            bid_px = tick.get("bidPrice", [])
            bid_vol = tick.get("bidVol", [])
            ask_px = tick.get("askPrice", [])
            ask_vol = tick.get("askVol", [])

            return MarketSnapshot(
                symbol=self._from_qmt_code(qmt_code),
                timestamp=datetime.now(),
                open=float(tick.get("open", 0)),
                high=float(tick.get("high", 0)),
                low=float(tick.get("low", 0)),
                price=price,
                pre_close=pre_close,
                volume=float(tick.get("volume", 0)),
                amount=float(tick.get("amount", 0)),
                bid_prices=_safe_list_float(bid_px, 5),
                bid_volumes=_safe_list_int(bid_vol, 5),
                ask_prices=_safe_list_float(ask_px, 5),
                ask_volumes=_safe_list_int(ask_vol, 5),
                limit_up=float(tick.get("limitUp", 0)),
                limit_down=float(tick.get("limitDown", 0)),
                change_pct=change_pct,
                name=str(tick.get("stockName", "")),
                data_source="qmt",
            )
        except Exception:
            return None

    def _get_quote_qmt(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        """QMT 批量获取行情快照 → Quote 模型。"""
        result: dict[Symbol, Quote] = {}
        qmt_codes = [self._to_qmt_code(s) for s in symbols]

        try:
            ticks = self._xtdata.get_full_tick(qmt_codes)
            self._stats["qmt_fetches"] += 1

            for i, tick in enumerate(ticks):
                if not tick:
                    continue
                sym = symbols[i] if i < len(symbols) else self._from_qmt_code(qmt_codes[i])
                snap = self._parse_qmt_tick_to_snapshot(qmt_codes[i], tick)
                if snap:
                    result[sym] = snap.to_quote()
        except Exception as e:
            print(f"[QMTProvider] get_full_tick 失败: {e} → 回退Sina")
            self._stats["fallbacks"] += 1
            return self._get_quote_sina(symbols)

        return result

    def _get_kline_qmt(
        self, symbols: list[Symbol], period: str, count: int
    ) -> dict[Symbol, list[Bar]]:
        """QMT 获取历史K线 → Bar 列表。"""
        import pandas as pd

        result: dict[Symbol, list[Bar]] = {}
        qmt_codes = [self._to_qmt_code(s) for s in symbols]
        qmt_period = self._QMT_PERIOD_MAP.get(period, "1d")

        try:
            data: dict[str, pd.DataFrame] = self._xtdata.get_market_data_ex(
                field_list=self._QMT_FIELDS,
                stock_list=qmt_codes,
                period=qmt_period,
                count=count,
                dividend_type="front",  # 前复权
            )
        except Exception:
            # 某些 QMT 版本不支持 dividend_type 参数
            data = self._xtdata.get_market_data_ex(
                field_list=self._QMT_FIELDS,
                stock_list=qmt_codes,
                period=qmt_period,
                count=count,
            )

        if not data or "close" not in data:
            return result

        close_df: pd.DataFrame = data["close"]

        for qmt_code in qmt_codes:
            sym = self._from_qmt_code(qmt_code)
            bars: list[Bar] = []

            # QMT DataFrame 格式: columns=stock_codes, index=datetime
            # 或: MultiIndex (datetime, stock_code)
            try:
                if qmt_code in close_df.columns:
                    # 格式1: 每只股票一列
                    col = close_df[qmt_code]
                    for ts, val in col.dropna().items():
                        ts_dt = ts if isinstance(ts, datetime) else pd.Timestamp(ts).to_pydatetime()
                        bars.append(Bar(
                            symbol=sym, dt=ts_dt,
                            open=float(data["open"][qmt_code].get(ts, 0.0)),
                            high=float(data["high"][qmt_code].get(ts, 0.0)),
                            low=float(data["low"][qmt_code].get(ts, 0.0)),
                            close=float(val),
                            volume=float(data["volume"][qmt_code].get(ts, 0.0)),
                            amount=float(data["amount"][qmt_code].get(ts, 0.0)),
                            period=period,
                        ))
                elif isinstance(close_df.index, pd.MultiIndex):
                    # 格式2: MultiIndex (time, code)
                    for (ts, code), val in close_df.items():
                        if code != qmt_code:
                            continue
                        ts_dt = ts if isinstance(ts, datetime) else pd.Timestamp(ts).to_pydatetime()
                        bars.append(Bar(
                            symbol=sym, dt=ts_dt,
                            open=float(data["open"].get((ts, code), 0.0)),
                            high=float(data["high"].get((ts, code), 0.0)),
                            low=float(data["low"].get((ts, code), 0.0)),
                            close=float(val),
                            volume=float(data["volume"].get((ts, code), 0.0)),
                            amount=float(data["amount"].get((ts, code), 0.0)),
                            period=period,
                        ))
            except Exception as e:
                print(f"[QMTProvider] 解析K线失败 {qmt_code}: {e}")
                continue

            if bars:
                bars.sort(key=lambda b: b.dt)
                result[sym] = bars

        return result

    # ═══════════════════════════════════════════════════════
    #  Internal: Sina 保底后端
    # ═══════════════════════════════════════════════════════

    def _get_sina_ctx(self) -> ssl.SSLContext:
        """新浪API SSL上下文 (忽略证书验证)."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _get_quote_sina(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        """新浪批量获取行情 → Quote 模型。"""
        result: dict[Symbol, Quote] = {}

        # 去前缀，统一为6位代码
        code_map: dict[str, str] = {}
        sina_list: list[str] = []
        for sym in symbols:
            clean = str(sym).strip().lower()
            clean = clean.replace("sh", "").replace("sz", "").replace("bj", "")
            if len(clean) != 6 or not clean.isdigit():
                continue
            if clean.startswith(("11", "12", "13", "15", "16", "18", "19", "20", "5")):
                continue  # 跳过可转债/基金
            prefix = "sh" if clean[0] == "6" else "sz"
            sc = f"{prefix}{clean}"
            sina_list.append(sc)
            code_map[sc] = sym

        if not sina_list:
            return result

        # 分批请求
        batches = [
            sina_list[i : i + self._SINA_BATCH_SIZE]
            for i in range(0, len(sina_list), self._SINA_BATCH_SIZE)
        ]

        for batch in batches:
            url = f"{self._SINA_URL}{','.join(batch)}"
            headers = {
                "Referer": "https://finance.sina.com.cn",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }

            text = None
            for attempt in range(3):
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(
                        req, timeout=8, context=self._get_sina_ctx()
                    ) as resp:
                        text = resp.read().decode("gbk")
                    break
                except Exception:
                    if attempt < 2:
                        time.sleep(1)

            if not text:
                continue

            try:
                for line in text.strip().split("\n"):
                    if '="' not in line:
                        continue
                    sc = line.split('hq_str_')[1].split('="')[0] if "hq_str_" in line else ""
                    data_str = line.split('="')[1].rstrip('";\n')
                    parts = data_str.split(",")

                    if len(parts) < 32:
                        continue

                    name = parts[0]
                    open_p = float(parts[1]) if parts[1] else 0.0
                    pre_close = float(parts[2]) if parts[2] else 0.0
                    price = float(parts[3]) if parts[3] else 0.0
                    high = float(parts[4]) if parts[4] else 0.0
                    low = float(parts[5]) if parts[5] else 0.0
                    volume = float(parts[8]) if parts[8] else 0.0
                    amount = float(parts[9]) if parts[9] else 0.0

                    if price <= 0:
                        continue

                    change_pct = (
                        round((price - pre_close) / pre_close * 100, 2)
                        if pre_close > 0
                        else 0.0
                    )

                    orig_sym = code_map.get(sc, sc.replace("sh", "").replace("sz", ""))
                    quote = Quote(
                        symbol=orig_sym,
                        timestamp=datetime.now(),
                        open=open_p,
                        high=high,
                        low=low,
                        price=price,
                        pre_close=pre_close,
                        volume=volume,
                        amount=amount,
                        change_pct=change_pct,
                    )
                    result[orig_sym] = quote
            except Exception:
                continue

        self._stats["sina_fetches"] += 1
        return result

    def _get_kline_local(
        self, symbols: list[Symbol], period: str, count: int
    ) -> dict[Symbol, list[Bar]]:
        """本地数据获取K线 (Sina保底模式)。

        优先级:
            1. stock_data.pkl 缓存 (最快，已有全量日线)
            2. TDX .day 本地文件 (零网络)
            3. 返回空
        """
        if period not in ("1d", "1w", "1M"):
            # 分钟线暂不支持本地读取，返回空
            return {}

        result: dict[Symbol, list[Bar]] = {}

        # 尝试从 stock_data.pkl 读取
        try:
            bars = self._read_kline_from_pickle(symbols, period, count)
            if bars:
                return bars
        except Exception:
            pass

        # 尝试从 TDX .day 文件读取
        try:
            bars = self._read_kline_from_tdx(symbols, period, count)
            if bars:
                return bars
        except Exception:
            pass

        return result

    def _read_kline_from_pickle(
        self, symbols: list[Symbol], period: str, count: int
    ) -> dict[Symbol, list[Bar]]:
        """从 stock_data 缓存读取K线 (P2: DataManager统一入口, parquet/gzip/pickle)."""
        import sys as _qmt_sys
        _qmt_sys.path.insert(0, r"D:\quant_web")
        from data_loader import load_stock_data_from_cache

        result: dict[Symbol, list[Bar]] = {}
        df = load_stock_data_from_cache()
        if df is None:
            # 最终兜底: 旧路径直接读
            import pickle as _pickle
            for cp in [r"D:\quant_web\stock_data.parquet", r"D:\quant_web\stock_data.pkl.gz", r"D:\quant_web\stock_data.pkl"]:
                if os.path.exists(cp):
                    try:
                        if cp.endswith(".parquet"):
                            from data_loader import load_stock_data_cache
                            df = load_stock_data_cache(cp)
                        elif cp.endswith(".gz"):
                            import gzip
                            with gzip.open(cp, "rb") as f:
                                df = _pickle.load(f)
                        else:
                            with open(cp, "rb") as f:
                                df = _pickle.load(f)
                        break
                    except Exception:
                        continue

        if df is None:
            return result

        for sym in symbols:
            clean = str(sym).strip().lower()
            clean = clean.replace("sh", "").replace("sz", "").replace("bj", "")
            # 尝试多种 key 格式
            for key in [clean, f"sh{clean}", f"sz{clean}"]:
                if key not in df:
                    continue
                stock_df = df[key]
                if hasattr(stock_df, "tail"):
                    stock_df = stock_df.tail(count)
                elif hasattr(stock_df, "__len__") and len(stock_df) > count:
                    stock_df = stock_df[-count:]

                bars: list[Bar] = []
                for idx, row in stock_df.iterrows() if hasattr(stock_df, "iterrows") else []:
                    ts = idx if isinstance(idx, datetime) else datetime.now()
                    bars.append(Bar(
                        symbol=sym, dt=ts,
                        open=float(getattr(row, "open", row[0] if hasattr(row, "__getitem__") else 0)),
                        high=float(getattr(row, "high", row[1] if hasattr(row, "__getitem__") else 0)),
                        low=float(getattr(row, "low", row[2] if hasattr(row, "__getitem__") else 0)),
                        close=float(getattr(row, "close", row[3] if hasattr(row, "__getitem__") else 0)),
                        volume=float(getattr(row, "volume", row[4] if hasattr(row, "__getitem__") else 0)),
                        period=period,
                    ))
                if bars:
                    result[sym] = bars
                break
        return result

    def _read_kline_from_tdx(
        self, symbols: list[Symbol], period: str, count: int
    ) -> dict[Symbol, list[Bar]]:
        """从 TDX .day 本地文件读取K线 (优先级2)。"""
        result: dict[Symbol, list[Bar]] = {}

        # 常见通达信数据目录
        tdx_roots = [
            r"D:\通达信",
            r"D:\new_tdx",
            r"D:\zd_zsT",
            r"D:\zd_zxT",
            r"C:\zd_zsT",
        ]
        subdirs = ["vipdoc", "T0002"]

        for sym in symbols:
            clean = str(sym).strip().lower()
            clean = clean.replace("sh", "").replace("sz", "").replace("bj", "")
            if len(clean) != 6 or not clean.isdigit():
                continue

            market = "sh" if clean[0] == "6" else "sz" if clean[0] in ("0", "3") else None
            if market is None:
                continue

            day_file = f"{market}{clean}.day"
            day_dir = os.path.join(market, "lday") if market == "sh" else os.path.join(market, "lday")

            for root in tdx_roots:
                if not os.path.isdir(root):
                    continue
                for sub in subdirs:
                    fpath = os.path.join(root, sub, day_dir, day_file)
                    if not os.path.exists(fpath):
                        # 尝试另一种目录结构
                        fpath = os.path.join(root, sub, "lday", day_file)

                    if os.path.exists(fpath):
                        try:
                            bars = self._parse_tdx_day_file(fpath, sym, period, count)
                            if bars:
                                result[sym] = bars
                                break
                        except Exception:
                            continue
                if sym in result:
                    break
        return result

    def _parse_tdx_day_file(
        self, filepath: str, symbol: str, period: str, count: int
    ) -> list[Bar]:
        """解析通达信 .day 二进制文件。"""
        bars: list[Bar] = []

        with open(filepath, "rb") as f:
            data = f.read()

        rec_size = 32  # 通达信标准格式
        n_records = len(data) // rec_size
        start = max(0, n_records - count)

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

                open_p = open_raw / 100.0 if open_raw < 1_000_000 else open_raw / 10000.0
                high_p = high_raw / 100.0 if high_raw < 1_000_000 else high_raw / 10000.0
                low_p = low_raw / 100.0 if low_raw < 1_000_000 else low_raw / 10000.0
                close_p = close_raw / 100.0 if close_raw < 1_000_000 else close_raw / 10000.0

                bars.append(Bar(
                    symbol=symbol, dt=dt,
                    open=open_p, high=high_p, low=low_p, close=close_p,
                    volume=float(vol_raw),
                    amount=float(amount_val),
                    period=period,
                ))
            except struct.error:
                continue

        return bars

    # ═══════════════════════════════════════════════════════
    #  Internal: 后台行情推送线程
    # ═══════════════════════════════════════════════════════

    def _bg_pump_loop(self) -> None:
        """后台行情推送主循环。

        QMT 模式: 主要通过 ``_on_qmt_tick()`` 回调推数据，
                  此线程做定期订阅检查和保活。
        Sina 模式: 轮询拉取新浪数据，解析为 MarketSnapshot 入队。
        """
        print(f"[QMTProvider] 后台推送线程启动 (间隔 {self._bg_interval}s)")

        while not self._bg_stop.is_set():
            try:
                if not self.USE_QMT:
                    # Sina 模式: 主动轮询
                    self._bg_poll_sina()

                # QMT 模式: 检查订阅列表是否都注册了回调
                elif self._xtdata and self._subscriptions:
                    for sym in list(self._subscriptions):
                        try:
                            self._xtdata.subscribe_quote(
                                self._to_qmt_code(sym), period="tick",
                                callback=self._on_qmt_tick,
                            )
                        except Exception:
                            pass

            except Exception as e:
                self._stats["errors"] += 1
                if self._stats["errors"] <= 3:
                    print(f"[QMTProvider] 后台线程异常: {e}")

            self._bg_stop.wait(self._bg_interval)

    def _bg_poll_sina(self) -> None:
        """Sina 模式: 拉取所有订阅股票 → 推入队列。"""
        if not self._subscriptions:
            return

        syms = list(self._subscriptions)
        quotes = self._get_quote_sina(syms)

        for sym, quote in quotes.items():
            snap = MarketSnapshot(
                symbol=sym,
                timestamp=quote.timestamp,
                open=quote.open,
                high=quote.high,
                low=quote.low,
                price=quote.price,
                pre_close=quote.pre_close,
                volume=quote.volume,
                amount=quote.amount,
                bid_prices=list(quote.bid_prices),
                bid_volumes=list(quote.bid_volumes),
                ask_prices=list(quote.ask_prices),
                ask_volumes=list(quote.ask_volumes),
                limit_up=quote.limit_up,
                limit_down=quote.limit_down,
                change_pct=quote.change_pct,
                data_source="sina",
            )
            try:
                self.snapshot_queue.put_nowait(snap)
                self._stats["snapshots_pushed"] += 1
            except queue.Full:
                try:
                    self.snapshot_queue.get_nowait()
                    self.snapshot_queue.put_nowait(snap)
                except queue.Empty:
                    pass

    # ═══════════════════════════════════════════════════════
    #  Internal: 工具方法
    # ═══════════════════════════════════════════════════════

    def _load_price_cache(self) -> None:
        """加载价格缓存 (K线兜底用)。"""
        cache_paths = [
            r"D:\quant_framework\price_cache.json",
            r"D:\quant_web\data\price_cache.json",
        ]
        for cp in cache_paths:
            if os.path.exists(cp):
                try:
                    with open(cp, "r") as f:
                        self._price_cache = json.load(f)
                    print(f"[QMTProvider] 价格缓存已加载: {len(self._price_cache)} 条")
                    return
                except Exception:
                    pass


# ═══════════════════════════════════════════════════════════════
#  Helper functions
# ═══════════════════════════════════════════════════════════════

def _safe_list_float(data: Any, n: int) -> list[float]:
    """安全提取 float 列表。"""
    result: list[float] = []
    if isinstance(data, (list, tuple)):
        for i in range(min(len(data), n)):
            try:
                result.append(float(data[i]))
            except (ValueError, TypeError):
                result.append(0.0)
    while len(result) < n:
        result.append(0.0)
    return result


def _safe_list_int(data: Any, n: int) -> list[int]:
    """安全提取 int 列表。"""
    result: list[int] = []
    if isinstance(data, (list, tuple)):
        for i in range(min(len(data), n)):
            try:
                result.append(int(data[i]))
            except (ValueError, TypeError):
                result.append(0)
    while len(result) < n:
        result.append(0)
    return result
