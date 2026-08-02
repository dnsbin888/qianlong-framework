"""E369: QMT侧 — 读取自动交易计划 (方案B+)
   QMT策略导入此模块, 信号检测时调用 should_execute()

   用法:
       from qmt_plan_reader import PlanReader
       reader = PlanReader()
       ...
       if reader.should_execute(symbol, signal_type, ml_score):
           reader.execute_buy(symbol, price)

   特性:
       - 启动时加载文件到内存 (<1ms查询)
       - 每3秒检查文件mtime, 自动热更新
       - 线程安全
"""
import json, os, time, threading

PLAN_PATH = r"D:\quant_web\data\auto_trade_plan.json"


class PlanReader:
    def __init__(self):
        self._plan = {"stocks": {}, "global_limits": {}}
        self._mtime = 0
        self._lock = threading.Lock()
        self._load()

    # ── 加载与热更新 ──

    def _load(self):
        if not os.path.exists(PLAN_PATH):
            return
        try:
            mtime = os.path.getmtime(PLAN_PATH)
            if mtime == self._mtime:
                return
            with open(PLAN_PATH, "r", encoding="utf-8") as f:
                self._plan = json.load(f)
            self._mtime = mtime
        except Exception:
            pass

    def refresh(self):
        """每3秒调用一次, 检测文件更新"""
        with self._lock:
            self._load()

    # ── 查询 ──

    def should_execute(self, symbol: str, signal_type: str, ml_score: float) -> bool:
        """判断信号是否应该自动执行"""
        with self._lock:
            self._load()
            limits = self._plan.get("global_limits", {})
            if limits.get("circuit_breaker", False):
                return False

            stock = self._plan.get("stocks", {}).get(symbol, {})
            if not stock.get("enabled", False):
                return False

            # 信号类型检查
            allowed_types = stock.get("signal_types", [])
            if signal_type not in allowed_types:
                return False

            # ML分数门槛
            min_score = stock.get("min_ml_score", 80)
            if ml_score < min_score:
                return False

            return True

    def get_stock(self, symbol: str) -> dict:
        """获取某只票的完整参数"""
        with self._lock:
            self._load()
            return self._plan.get("stocks", {}).get(symbol, {})

    def get_limits(self) -> dict:
        with self._lock:
            self._load()
            return self._plan.get("global_limits", {})

    def is_circuit_breaker(self) -> bool:
        return self.get_limits().get("circuit_breaker", False)

    def auto_count(self) -> int:
        with self._lock:
            self._load()
            return sum(1 for s in self._plan.get("stocks", {}).values() if s.get("enabled"))


# 便捷函数 (单例)
_reader = None

def get_reader() -> PlanReader:
    global _reader
    if _reader is None:
        _reader = PlanReader()
    return _reader


def should_execute(symbol: str, signal_type: str, ml_score: float) -> bool:
    return get_reader().should_execute(symbol, signal_type, ml_score)


# ═══ 集成示例 (QMT策略中调用) ═══
"""
from qmt_plan_reader import get_reader

reader = get_reader()

# QMT 策略主循环中:
while True:
    reader.refresh()  # 每3秒

    # 检测到信号时:
    for signal in detected_signals:
        if reader.should_execute(signal.symbol, signal.type, signal.ml_score):
            # 快速通道: 直接下单
            stock = reader.get_stock(signal.symbol)
            qty = calc_shares(stock["max_position_pct"], signal.price)
            place_order(signal.symbol, qty, signal.price,
                       stop_loss=stock["stop_loss"],
                       take_profit=stock["take_profit"])
        else:
            # 审核通道: POST 推送到潜龙
            requests.post("http://127.0.0.1:5002/api/qmt/signal",
                         json={"symbol": signal.symbol, "signal_type": signal.type,
                               "price": signal.price}, timeout=2)
"""
