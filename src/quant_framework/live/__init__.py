"""Live trading module — 实时策略运行与信号生成。

本模块负责:
    - 从配置文件加载自动调参结果
    - 在盘中根据实时行情计算策略信号
    - 信号记录到 SQLite，供盘后复盘

零实盘原则: 当前阶段仅记录信号，绝对禁止向 QMT 发送真实下单指令。
"""

from quant_framework.live.live_strategy_runner import LiveStrategyRunner

__all__ = ["LiveStrategyRunner"]
