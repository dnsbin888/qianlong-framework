# 潜龙配置文件清单 v1.0 (2026-07-07)

## 活跃文件 (在用)

| 文件 | 路径 | 作用 | 谁读 |
|------|------|------|------|
| **trade_config_master.json** | `D:\quant_framework\` | ⭐ 唯一真相源: 所有交易参数 | config_loader.py → 全系统 |
| **config_loader.py** | `D:\quant_framework\` | 统一参数读取入口 | live_trader, paper_engine, generate_signal_table |
| **auto_trade_plan.json** | `D:\quant_web\data\` | QMT快速通道执行计划 | QMT策略 (潜龙快速通道.py) |
| **qmt_trade_config.json** | `D:\quant_web\data\` | ML评分+仓位+止损盈 | QMT策略 |
| **signal_table.json** | `D:\quant_web\data\` | 89条综合信号表 | 前端 / API |
| **live_trader_config.json** | `D:\quant_framework\` | QMT通道配置覆盖 (仅channel/account, 非TP/SL) | live_trader.py |
| **user_strategies.json** | `D:\quant_framework\user_customizations\` | 用户策略参数 | live_trader.py |
| **user_config.json** | `D:\quant_data\config\` | UI设置 (自动交易开关等) | app.py |
| **factor_registry.json** | `D:\quant_framework\` | 因子注册表 (唯一真相源) | factor_health, ML训练 |
| **paper_account.json** | `D:\quant_framework\` | 模拟盘状态 | paper_engine |

## 已废弃 (不再使用)

| 文件 | 位置 | 废弃原因 | 处理 |
|------|------|------|------|
| **factor_registry.json** | `D:\quant_web\data\` | 旧版静态权重, 已被Framework版替代 | → .deprecated |
| **factor_registry.json** | `D:\quant_web\static\` | 同上 | → .deprecated |
| **qmt_intraday_signals.json** | `D:\quant_web\data\` | 已被日分桶 qmt_signals_YYYYMMDD.json 替代 | 已删除 |

## 废弃代码段

| 位置 | 行 | 内容 | 替代 |
|------|------|------|------|
| `live_trader.py` | 53-92 | CONFIG 硬编码字典 (35参数) | config_loader.get_param() |
| `live_trader.py` | 184-188 | STRATEGY_PARAMS 硬编码默认值 | config_loader |
| `paper_engine.py` | 226-250 | PositionSizingRule 硬编码 sizing_map | 方案A百分位 |
| `hrp_sizer.py` | 54-68 | 伪HRP (距离排序) | riskfolio.HCPortfolio |

## 参数读取优先级

```
config_loader.get_param("key")
  → trade_config_master.json (权威)
  → 通道差异 (sim/real)
  → 兜底默认值
```

## 新增参数流程

```
1. trade_config_master.json 加字段
2. config_loader.py 加一行映射
3. 全系统 get_param("新参数") → 即时生效
```
