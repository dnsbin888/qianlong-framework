"""QMT 行情桥接 (P0-3) — subscribe_whole_quote 替代 HTTP 轮询

将 QMT 推送的实时行情写入 realtime_quotes._quote_cache，
通过 EventBus 广播给交易循环和 Web 前端。

架构:
  QMT xtdata.subscribe_whole_quote(["SH","SZ"])
    → callback _on_qmt_tick()
    → 更新 realtime_quotes._quote_cache
    → EventBus.publish("quote", ...)
    → trading_loop.EventLoop 消费

降级:
  QMT 不可用 → 静默跳过, realtime_quotes 新浪轮询继续工作
  QMT 超时30s无数据 → 自动回退, 不中断现有通道
"""

import time
import threading
import logging
from datetime import datetime

logger = logging.getLogger("quant_framework.qmt_bridge")

# 状态
_qmt_active = False
_qmt_thread = None
_last_qmt_data_ts = 0
_qmt_stats = {"ticks": 0, "errors": 0, "started_at": None}


def is_qmt_available() -> bool:
    """检测 QMT xtdata 是否可用。"""
    try:
        import xtquant
        return True
    except ImportError:
        return False


def _qmt_to_cache_format(qmt_code: str, tick: dict) -> dict | None:
    """将 QMT tick 数据转为 realtime_quotes 缓存格式。

    QMT tick keys: lastPrice, open, high, low, volume, amount,
                   preClose, lastSettlementPrice, stockName
    """
    try:
        price = float(tick.get("lastPrice", 0))
        if price <= 0:
            return None
        pre_close = float(tick.get("preClose", 0) or tick.get("lastSettlementPrice", 0))
        change_pct = round((price - pre_close) / pre_close * 100, 2) if pre_close > 0 else 0

        # code 统一为6位数字 (无前缀)
        code = str(qmt_code).split(".")[0] if "." in str(qmt_code) else str(qmt_code)
        # 清理前缀 (sh600519.SH → 600519)
        code = code.replace("SH", "").replace("SZ", "").replace("BJ", "").upper()

        return {
            "name": str(tick.get("stockName", "")),
            "close": price,
            "change_pct": change_pct,
            "high": float(tick.get("high", 0)),
            "low": float(tick.get("low", 0)),
            "open": float(tick.get("open", 0)),
            "volume": int(float(tick.get("volume", 0))),
            "amount": float(tick.get("amount", 0)),
            "data_source": "qmt",
        }
    except Exception:
        return None


def _on_qmt_tick(datas: dict) -> None:
    """QMT subscribe_whole_quote 回调。

    由 xtdata 订阅线程调用，线程安全更新 realtime_quotes 缓存。
    """
    global _last_qmt_data_ts, _qmt_stats
    if not datas:
        return

    _last_qmt_data_ts = time.time()
    converted = 0

    try:
        import realtime_quotes as _rq
        cache = _rq._quote_cache
        old_data = cache.get("data", {})

        for qmt_code, tick in datas.items():
            entry = _qmt_to_cache_format(qmt_code, tick)
            if entry is None:
                continue
            code = str(qmt_code).split(".")[0].replace("SH", "").replace("SZ", "").replace("BJ", "").upper()
            old_data[code] = entry
            converted += 1

        # 清理: 保留最近 800 只股票防内存膨胀
        if len(old_data) > 800:
            keys = list(old_data.keys())[-700:]
            old_data = {k: old_data[k] for k in keys}

        cache["status"] = "live"
        cache["trading"] = True
        cache["count"] = len(old_data)
        cache["time"] = datetime.now().strftime("%H:%M:%S")
        cache["data"] = old_data
        _qmt_stats["ticks"] += converted

        # 发布到 EventBus (驱动交易循环)
        try:
            from quant_framework.core.event_bus import EventBus
            bus = EventBus._instance
            if bus:
                bus.publish("quote", {
                    "count": converted,
                    "total": len(old_data),
                    "ts": _last_qmt_data_ts,
                    "source": "qmt",
                })
        except Exception:
            pass

    except Exception as e:
        _qmt_stats["errors"] += 1
        # 不打印每条异常，防止刷屏


def start_qmt_bridge() -> bool:
    """启动 QMT 行情桥接 (灰度模式: 与新浪轮询并行)。

    Returns:
        True  QMT 桥接已启动
        False QMT 不可用, 不影响现有轮询
    """
    global _qmt_active, _qmt_thread, _qmt_stats

    if _qmt_active:
        return True

    if not is_qmt_available():
        print("[QMT-Bridge] xtquant 不可用，跳过 QMT 推送（新浪轮询正常工作）")
        return False

    try:
        from xtquant import xtdata

        # 启动 xtdata.run() daemon 线程 (维持回调)
        run_thread = threading.Thread(
            target=xtdata.run,
            name="QMT-xtdata-run",
            daemon=True,
        )
        run_thread.start()
        print("[QMT-Bridge] xtdata.run() daemon 已启动")

        # 订阅全市场行情
        seq = xtdata.subscribe_whole_quote(
            code_list=["SH", "SZ"],
            callback=_on_qmt_tick,
        )
        print(f"[QMT-Bridge] ✓ subscribe_whole_quote 已订阅 → seq={seq}")

        _qmt_active = True
        _qmt_stats["started_at"] = datetime.now().isoformat()
        _qmt_thread = run_thread

        # 启动健康监控线程
        monitor = threading.Thread(target=_health_monitor, daemon=True, name="QMT-Health")
        monitor.start()

        return True

    except Exception as e:
        print(f"[QMT-Bridge] 启动失败: {e} → 新浪轮询继续工作")
        return False


def _health_monitor() -> None:
    """QMT 健康监控: 超时告警 + 自动重连。"""
    global _qmt_active, _last_qmt_data_ts
    _last_qmt_data_ts = time.time()
    _retry_count = 0

    while True:
        time.sleep(15)
        if not _qmt_active:
            # 自动重试: 每60秒检测一次 QMT 是否已启动
            _retry_count += 1
            if _retry_count % 4 == 0:  # 每60秒
                if is_qmt_available():
                    print("[QMT-Bridge] 🔄 检测到 QMT 已启动，自动重连...")
                    if reconnect_qmt_bridge():
                        print("[QMT-Bridge] ✅ 自动重连成功")
                        _retry_count = 0
            continue

        gap = time.time() - _last_qmt_data_ts
        if gap > 30:
            print(f"[QMT-Bridge] ⚠️ QMT 数据超 {gap:.0f}s 无更新 (降级: 新浪轮询继续)")
            try:
                from quant_framework.core.event_bus import EventBus
                bus = EventBus._instance
                if bus:
                    bus.publish("system", {
                        "type": "qmt_stale",
                        "gap_seconds": int(gap),
                        "fallback": "sina_polling",
                    })
            except Exception:
                pass
            _last_qmt_data_ts = time.time()


def reconnect_qmt_bridge() -> bool:
    """重新连接 QMT (服务启动后手动重连)。"""
    global _qmt_active
    _qmt_active = False  # 重置状态
    return start_qmt_bridge()


def get_qmt_stats() -> dict:
    """获取 QMT 桥接统计。"""
    global _qmt_stats, _qmt_active, _last_qmt_data_ts
    return {
        "active": _qmt_active,
        "ticks": _qmt_stats["ticks"],
        "errors": _qmt_stats["errors"],
        "started_at": _qmt_stats["started_at"],
        "last_data_ago": time.time() - _last_qmt_data_ts if _last_qmt_data_ts else None,
    }
