"""潜龙 QMT 策略库
===================
所有 QMT 执行端策略统一存放于此目录。

用法 (在 QMT 策略编辑器中):
    import sys
    sys.path.insert(0, r"D:\quant_framework")
    from qmt_strategies.qmt_engine import on_bar, load_pool, reset_daily, get_status

策略文件:
    qmt_engine.py        — 策略引擎核心 (双道并行: 快速通道+审核通道)
    qmt_quick_trade.py   — 轻量快速通道模块
    qmt_full_strategy.py — 完整策略 (可直接粘贴到 QMT 编辑器)
"""
