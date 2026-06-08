"""通用 .day 文件数据提供器 — 读取同花顺/通达信本地K线数据。

双格式支持 (auto-detect by record size):
  通达信 (32 bytes/record):
    date    uint32  YYYYMMDD
    open    uint32  开盘价 * 100
    high    uint32  最高价 * 100
    low     uint32  最低价 * 100
    close   uint32  收盘价 * 100
    amount  float32 成交额
    volume  uint32  成交量
    rsv     uint32  保留

  同花顺 (40 bytes/record):
    通达信8字段 + 2个额外保留字段

与框架 DataProvider 接口对接，零网络开销，同花顺/通达信已下载的数据直接可用。
"""

from __future__ import annotations

import os
import struct
from datetime import datetime, timedelta
from typing import Any

from quant_framework.core.types import Symbol
from quant_framework.data.models import Bar, Quote
from quant_framework.data.provider import DataProvider

# 通达信格式: 8 fields * 4 bytes = 32 bytes
_TDX_DAY_SIZE = 32
_TDX_DAY_FORMAT = "<I I I I I f I I"

# 同花顺传统格式: 40 bytes
_THS_DAY_SIZE = 40
_THS_DAY_FORMAT = "<I I I I I f I I I I"

# 同花顺 hd1.0 新格式: 192 byte header + 28 byte records
_THS_HD_HEADER_SIZE = 192
_THS_HD_REC_SIZE = 28
_THS_HD_REC_FORMAT = "<I I I I I I I"  # date + OHLC + amount + volume (all uint32)
_THS_HD_MAGIC = b"hd1.0"

# 价格缩放: hd1.0 格式价格存储为 int*10^8 (需要除以100000000)
_THS_HD_PRICE_SCALE = 100_000_000.0

# 分钟线格式 (44 bytes, 同花顺)
_MIN_SIZE = 44

# 市场路径映射 — 支持多种目录结构
_MARKET_PATHS = {
    "sh": ["shase", "sh"],     # 同花顺=shase, 通达信=sh
    "sz": ["sznse", "sz"],     # 同花顺=sznse, 通达信=sz
    "bj": ["bse", "bj"],       # 北交所
    "hk": ["hk"],              # 港股
}

# 6位代码 → (市场目录, 文件名) 映射
def _resolve_symbol(root_dir: str, symbol: str) -> tuple[str, str]:
    """Convert symbol like '600000' to (market_subdir, filename).

    Tries multiple formats:
      - 同花顺: shase/day/600000.day
      - 通达信: sh/lday/sh600000.day
    """
    code = symbol.strip()
    if len(code) == 6 and code.isdigit():
        first = code[0]
        if first in ("6", "5"):
            market_candidates = _MARKET_PATHS["sh"]
        elif first in ("0", "3", "2"):
            market_candidates = _MARKET_PATHS["sz"]
        elif first in ("4", "8"):
            market_candidates = _MARKET_PATHS["bj"]
        else:
            market_candidates = _MARKET_PATHS["sh"]

        # Try each market directory and filename format
        for mkt in market_candidates:
            # Check if using TDX prefix (sh600000.day)
            is_tdx = mkt in ("sh", "sz", "bj")
            for day_subdir in ["lday", "day"]:
                fname = f"{mkt}{code}.day" if is_tdx else f"{code}.day"
                path = os.path.join(root_dir, mkt, day_subdir, fname)
                if os.path.isfile(path):
                    return (os.path.join(mkt, day_subdir), fname)

        # Fallback: return the first reasonable path
        mkt = market_candidates[0]
        return (os.path.join(mkt, "lday"), f"{mkt}{code}.day")
    else:
        return ("shase/day", f"{symbol}.day")


def _detect_format(filepath: str) -> str:
    """Auto-detect .day file format.
    Returns: 'tdx' (32B), 'ths' (40B), 'ths_hd' (hd1.0 with 192B header+28B rec).
    """
    with open(filepath, "rb") as f:
        header = f.read(8)
    if header[:5] == _THS_HD_MAGIC:
        return "ths_hd"

    size = os.path.getsize(filepath)
    if size % _TDX_DAY_SIZE == 0 and size // _TDX_DAY_SIZE > 0:
        return "tdx"
    if size % _THS_DAY_SIZE == 0 and size // _THS_DAY_SIZE > 0:
        return "ths"
    return "ths_hd"  # fallback


def _parse_day_record(
    data: bytes, offset: int, fmt: str
) -> tuple[int, float, float, float, float, float, float]:
    """Parse a single day record. Supports TDX(32B), THS(40B), THS-hd1.0(28B)."""
    if fmt == "ths_hd":
        date_val, open_raw, high_raw, low_raw, close_raw, amount_raw, volume_raw = struct.unpack_from(
            _THS_HD_REC_FORMAT, data, offset
        )
        # hd1.0: all values are uint32, prices stored * 10^8
        scale = _THS_HD_PRICE_SCALE
        if open_raw > 100_000_000:  # Has valid data
            return (
                date_val,
                open_raw / scale,
                high_raw / scale,
                low_raw / scale,
                close_raw / scale,
                float(amount_raw) / 100.0,  # amount in cents?
                float(volume_raw),
            )
        return (date_val, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    elif fmt == "tdx":
        date_val, open_raw, high_raw, low_raw, close_raw, amount, volume, rsv = struct.unpack_from(
            _TDX_DAY_FORMAT, data, offset
        )
        divisor = 100.0 if open_raw < 10_000_000 else 1000.0
        return (date_val, open_raw/divisor, high_raw/divisor, low_raw/divisor, close_raw/divisor, amount, float(volume))
    else:  # ths (40B)
        date_val, open_raw, high_raw, low_raw, close_raw, amount, volume, rsv1, rsv2, *_ = struct.unpack_from(
            _THS_DAY_FORMAT, data, offset
        )
        divisor = 100.0 if open_raw < 10_000_000 else 1000.0
        return (date_val, open_raw/divisor, high_raw/divisor, low_raw/divisor, close_raw/divisor, amount, float(volume))


def _date_to_datetime(date_int: int) -> datetime | None:
    """Convert YYYYMMDD int to datetime. Returns None for invalid dates."""
    s = str(date_int)
    if len(s) != 8:
        return None
    try:
        return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (ValueError, IndexError):
        return None


class THSDayDataProvider(DataProvider):
    """直接读取同花顺本地 .day 文件的数据提供器。

    共享同花顺已下载的K线数据，无需额外网络请求。
    支持所有A股日线数据和分钟线数据。

    Usage:
        provider = THSDayDataProvider(root_dir="d:/同花顺软件/同花顺/history")
        provider.connect()
        bars = provider.get_kline(["600000", "000001"], "1d", 100)
        # bars["600000"] = [Bar(...), ...] -- 最近100根日线
    """

    def __init__(self, root_dir: str | None = None) -> None:
        """
        Args:
            root_dir: 同花顺 history 目录路径。
                     默认自动查找：d:/同花顺软件/同花顺/history
        """
        if root_dir is None:
            # Use centralized path resolver (honours TDX_DATA_ROOT env var)
            from quant_framework.config.paths import get_tdx_data_root

            resolved = get_tdx_data_root()
            root_dir = str(resolved) if resolved else ""

        self._root = root_dir or ""
        self._connected = False
        self._quote_cache: dict[Symbol, Quote] = {}
        self._subscriptions: set[Symbol] = set()
        self._symbol_names: dict[str, str] = {}  # code → name

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        if not self._root or not os.path.isdir(self._root):
            raise FileNotFoundError(f"同花顺 history 目录不存在: {self._root}")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    @property
    def name(self) -> str:
        return "ths_day"

    @property
    def root_dir(self) -> str:
        return self._root

    # ------------------------------------------------------------------
    # 符号扫描
    # ------------------------------------------------------------------

    def scan_symbols(self) -> list[str]:
        """扫描 history/vipdoc 目录下所有可用的股票代码。

        Returns:
            排序后的股票代码列表。
        """
        symbols: set[str] = set()
        # Scan both THS (day/) and TDX (lday/) directory structures
        for market_list in _MARKET_PATHS.values():
            for market_dir in market_list:
                for day_subdir in ["lday", "day"]:
                    day_path = os.path.join(self._root, market_dir, day_subdir)
                    if os.path.isdir(day_path):
                        for fname in os.listdir(day_path):
                            if not fname.endswith(".day"):
                                continue
                            # Extract code: sh600000.day → 600000, 600000.day → 600000
                            code = fname.rsplit(".", 1)[0]
                            # Strip TDX prefix
                            for prefix in ["sh", "sz", "bj"]:
                                if code.startswith(prefix) and code[len(prefix):].isdigit():
                                    code = code[len(prefix):]
                                    break
                            if code.isdigit() and len(code) == 6:
                                symbols.add(code)
        return sorted(symbols)

    def get_data_range(self, symbol: str) -> tuple[datetime, datetime] | None:
        """获取某只股票数据的起止日期。

        Returns:
            (最早日期, 最晚日期) 或 None。
        """
        data = self._read_day_file(symbol)
        if not data or len(data) < 2:
            return None
        dates = sorted(data.keys())
        start = _date_to_datetime(dates[0])
        end = _date_to_datetime(dates[-1])
        if start is None or end is None:
            return None
        return (start, end)

    def get_file_size_kb(self, symbol: str) -> int:
        """获取某只股票的数据文件大小 (KB)。"""
        market_dir, fname = _resolve_symbol(self._root, symbol)
        path = os.path.join(self._root, market_dir, fname)
        if os.path.isfile(path):
            return int(os.path.getsize(path) // 1024)
        return 0

    # ------------------------------------------------------------------
    # Quote (不支持实时行情)
    # ------------------------------------------------------------------

    def subscribe_quote(self, symbols: list[Symbol]) -> None:
        self._subscriptions.update(symbols)

    def unsubscribe_quote(self, symbols: list[Symbol]) -> None:
        for s in symbols:
            self._subscriptions.discard(s)

    def get_quote(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        return {s: self._quote_cache.get(s, Quote(symbol=s)) for s in symbols}

    # ------------------------------------------------------------------
    # K-line — 核心方法
    # ------------------------------------------------------------------

    def get_kline(
        self, symbols: list[Symbol], period: str, count: int
    ) -> dict[Symbol, list[Bar]]:
        """读取同花顺 .day 文件中的历史K线。

        Args:
            symbols: 股票代码列表。
            period: '1d', '1w', '1M' 等（目前仅支持日线）。
            count: 返回最近 N 条。

        Returns:
            symbol → list[Bar] 映射。
        """
        result: dict[Symbol, list[Bar]] = {}

        for sym in symbols:
            if period == "1d":
                bars = self._read_daily_bars(sym, count)
            elif period in ("1w", "1M"):
                # 周线/月线从日线合成
                daily = self._read_daily_bars(sym, 0)  # 全部
                bars = self._resample_bars(daily, period, count)
            else:
                # 分钟线 — 同花顺 minute 目录（同格式但44字节每条）
                bars = self._read_minute_bars(sym, period, count)

            result[sym] = bars

        return result

    def _read_daily_bars(self, symbol: str, count: int) -> list[Bar]:
        """读取日线数据。

        Args:
            symbol: 股票代码。
            count: 返回最近 N 条 (0 = 全部)。

        Returns:
            Bar 列表（时间升序）。
        """
        data = self._read_day_file(symbol)
        if not data:
            return []

        # 按日期排序
        sorted_dates = sorted(data.keys())
        if count > 0:
            sorted_dates = sorted_dates[-count:]

        bars: list[Bar] = []
        for date_int in sorted_dates:
            dt = _date_to_datetime(date_int)
            if dt is None:
                continue
            open_p, high_p, low_p, close_p, amount, volume = data[date_int]
            bars.append(Bar(
                symbol=symbol,
                dt=dt,
                open=open_p,
                high=high_p,
                low=low_p,
                close=close_p,
                volume=volume,
                amount=amount,
                period="1d",
            ))
        return bars

    def _read_day_file(self, symbol: str) -> dict[int, tuple[float, float, float, float, float, float]]:
        """读取整个 .day 文件，返回 {date_int: (open, high, low, close, amount, volume)}."""
        market_dir, fname = _resolve_symbol(self._root, symbol)
        path = os.path.join(self._root, market_dir, fname)

        if not os.path.isfile(path):
            return {}

        try:
            with open(path, "rb") as f:
                raw = f.read()
        except (PermissionError, OSError):
            return {}

        # Auto-detect format
        fmt = _detect_format(path)

        if fmt == "ths_hd":
            data_start = _THS_HD_HEADER_SIZE
            rec_size = _THS_HD_REC_SIZE
        elif fmt == "tdx":
            data_start = 0
            rec_size = _TDX_DAY_SIZE
        else:
            data_start = 0
            rec_size = _THS_DAY_SIZE

        data = raw[data_start:]
        if len(data) < rec_size:
            return {}

        num_records = len(data) // rec_size
        result: dict[int, tuple[float, float, float, float, float, float]] = {}

        for i in range(num_records):
            offset = i * rec_size
            try:
                date_int, open_p, high_p, low_p, close_p, amount, volume = _parse_day_record(data, offset, fmt)
                # Skip invalid/padding records
                if date_int < 19900101 or date_int > 21000101:
                    continue
                if open_p <= 0 and close_p <= 0:
                    continue
                result[date_int] = (open_p, high_p, low_p, close_p, amount, volume)
            except struct.error:
                continue

        return result

    def _read_minute_bars(self, symbol: str, period: str, count: int) -> list[Bar]:
        """读取分钟线数据（同花顺 minute 目录，44字节格式）。

        注意：需要同花顺下载分钟线数据，且文件在对应市场/minute/ 目录下。
        分钟线文件名格式：600000.min 或 minute/600000.dat
        """
        market_dir, fname = _resolve_symbol(self._root, symbol)
        # minute 文件可能在 minute/ 或 day/ 同级目录
        min_name = fname.replace(".day", ".min")
        path = os.path.join(self._root, market_dir, min_name)

        if not os.path.isfile(path):
            return []

        try:
            with open(path, "rb") as f:
                raw = f.read()
        except (PermissionError, OSError):
            return []

        if len(raw) < _MIN_SIZE:
            return []

        num_records = len(raw) // _MIN_SIZE
        bars: list[Bar] = []

        for i in range(num_records):
            offset = i * _MIN_SIZE
            try:
                time_raw, date_raw, open_raw, high_raw, low_raw, close_raw, amount, volume, rsv = (
                    struct.unpack_from("<H H I I I I f I f", raw, offset)
                )
                hour = (time_raw >> 8) & 0xFF
                minute = time_raw & 0xFF
                base_dt = _date_to_datetime(date_raw)
                if base_dt is None:
                    continue
                dt = base_dt + timedelta(hours=hour, minutes=minute)

                bars.append(Bar(
                    symbol=symbol,
                    dt=dt,
                    open=open_raw / 100.0 if open_raw > 0 else 0,
                    high=high_raw / 100.0 if high_raw > 0 else 0,
                    low=low_raw / 100.0 if low_raw > 0 else 0,
                    close=close_raw / 100.0 if close_raw > 0 else 0,
                    volume=float(volume),
                    amount=amount,
                    period=period,
                ))
            except struct.error:
                continue

        # 按时间排序，取最近 count 条
        bars.sort(key=lambda b: b.dt)
        return bars[-count:] if count > 0 else bars

    @staticmethod
    def _resample_bars(daily_bars: list[Bar], period: str, count: int) -> list[Bar]:
        """从日线合成周线/月线。

        Args:
            daily_bars: 日线 Bar 列表（升序）。
            period: '1w' | '1M'
            count: 返回最近 N 条。

        Returns:
            合成后的周期 Bar 列表。
        """
        import pandas as pd

        if not daily_bars:
            return []

        records = [
            {"datetime": b.dt, "open": b.open, "high": b.high,
             "low": b.low, "close": b.close, "volume": b.volume, "amount": b.amount}
            for b in daily_bars
        ]
        df = pd.DataFrame(records)
        df.set_index("datetime", inplace=True)

        rule = "W" if period == "1w" else "ME"
        resampled = df.resample(rule).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "amount": "sum",
        }).dropna()

        bars: list[Bar] = []
        for dt, row in resampled.iterrows():
            bars.append(Bar(
                symbol=daily_bars[0].symbol if daily_bars else "",
                dt=dt.to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                amount=float(row["amount"]),
                period=period,
            ))

        return bars[-count:] if count > 0 else bars

    # ------------------------------------------------------------------
    # Polling (不支持 — 仅回测用)
    # ------------------------------------------------------------------

    def wait_update(self, timeout: float | None = None) -> list[Symbol]:
        return []

    # ------------------------------------------------------------------
    # 市场扫描 — 统计信息
    # ------------------------------------------------------------------

    def scan_summary(self) -> dict[str, Any]:
        """扫描数据目录，返回统计摘要。"""
        summary: dict[str, Any] = {
            "root": self._root,
            "markets": {},
            "total_symbols": 0,
            "total_size_mb": 0,
        }
        for market_name, market_dir in _MARKET_PATHS.items():
            day_dir = os.path.join(self._root, market_dir, "day")
            if not os.path.isdir(day_dir):
                continue
            files = [f for f in os.listdir(day_dir) if f.endswith(".day")]
            size_mb = sum(os.path.getsize(os.path.join(day_dir, f)) for f in files) / 1048576
            summary["markets"][market_name] = {
                "dir": market_dir,
                "symbols": len(files),
                "size_mb": round(size_mb, 2),
            }
            summary["total_symbols"] += len(files)
            summary["total_size_mb"] += size_mb

        summary["total_size_mb"] = round(summary["total_size_mb"], 2)
        return summary
