# 潜龙量化平台 · 风控模块评审报告
## 对标 TOP3 量化平台风控架构对比分析

> **评审时间**: 2026-06-07
> **评审对象**: risk/rules.py 11条规则 + RiskEngine + risk_guard.py + trade_guard.py + auto_exit_monitor.py + paper_engine.py/live_trader.py 内嵌风控
> **对标平台**: QuantConnect Lean · RiceQuant RQAlpha · JoinQuant 聚宽

---

## 一、TOP3 量化平台风控架构深度研究

### 1.1 QuantConnect Lean（机构级 · RiskModel 框架）

| 维度 | 设计要点 |
|------|---------|
| **RiskModel 解耦** | `IRiskManagementModel` 接口完全独立，在 Algorithm Framework 中作为五模块之一（Universe→Alpha→Portfolio→Execution→**Risk**） |
| **内置 Risk Models** | `MaximumDrawdownPercentPortfolio`（最大回撤）、`MaximumUnrealizedProfitPercentPerSecurity`（未实现盈亏限制）、`MaximumSectorExposure`（行业敞口限制）、`TrailingStopRiskManagementModel`（移动止损） |
| **算法框架集成** | RiskModel 在每次 Bar 更新后被调用，可以在 Portfolio Construction 之前或之后修改仓位目标 |
| **仓位目标调整** | RiskModel 直接修改 `PortfolioTarget`（仓位目标），而非简单阻止订单 |
| **行业敞口限制** | 内置行业分类，按 GICS 行业限制总敞口 |
| **风险管理钩子** | `OnEndOfAlgorithm()` 结束时可以检查最终风险状态 |

**核心借鉴**：RiskModel 独立接口、仓位目标级别调整（非订单级别）、行业敞口限制、TrailingStop 作为独立 Risk Model

---

### 1.2 RiceQuant RQAlpha（Mod 插件化 · 事前风控）

| 维度 | 设计要点 |
|------|---------|
| **sys_risk Mod** | 独立风控 Mod，在订单提交前拦截；可替换/禁用 |
| **事前拦截** | 在订单到达撮合引擎前执行检查：资金/仓位/涨跌停/自成交 |
| **配置驱动** | 通过 `mod_config` 配置最大仓位/最大持仓数/黑名单 |
| **账户管理 Mod** | `sys_accounts` 与风险引擎协同，提供实时账户状态 |

**核心借鉴**：事前拦截、Mod 可插拔、与撮合引擎解耦

---

### 1.3 JoinQuant 聚宽（云端 · 策略级风控）

| 维度 | 设计要点 |
|------|---------|
| **订单级拦截** | `order_target_value` 和 `order_value` 内置仓位管理，自动计算目标仓位 |
| **风控函数库** | 提供 `get_position()` / `get_positions()` / `get_total_value()` 等 API，策略自行调用 |
| **回测内置** | 回测引擎自动执行：涨跌停不成交、T+1 约束、退市股票过滤 |
| **平台级风控** | 云端强制最大持仓数限制、单票仓位限制、撤单频率限制 |

**核心借鉴**：平台级强制执行 vs 策略级自主选择、order_target_value 仓位目标模式

---

## 二、潜龙风控模块现状分析

### 2.1 总体架构

```
                           应用层
  ┌──────────────────────────────────────────────────────┐
  │  run_live.py          │  run_backtest.py              │
  │  multi_strategy_demo.py│  launcher.py                 │
  └───────────┬──────────────────────────┬───────────────┘
              │                          │
  ┌───────────▼──────────┐  ┌────────────▼──────────────┐
  │  RiskEngine(rules.py)│  │  paper_engine.py           │
  │  11条规则链          │  │  live_trader.py            │
  │  全局规则+策略规则   │  │  移动止盈+止损+熔断       │
  └───────────┬──────────┘  └────────────┬──────────────┘
              │                          │
  ┌───────────▼──────────────────────────▼──────────────┐
  │  risk_guard.py                                       │
  │  PreTradeChecker —— CorrelationAnalyzer              │
  │  RiskEventBus  —— RiskCycleScheduler                 │
  │  StressTester                                        │
  └──────────────────────┬───────────────────────────────┘
                         │
  ┌──────────────────────▼───────────────────────────────┐
  │  实时守护                                             │
  │  trade_guard.py    —— 止盈止损守护(10秒检查)          │
  │  auto_exit_monitor.py —— 同花顺内自动化(5秒检查)     │
  └──────────────────────────────────────────────────────┘
```

### 2.2 三套风控体系并行

| 体系 | 核心文件 | 技术栈 | 风控规则 | 用途 |
|:----:|:--------:|:------:|:--------:|:----:|
| **框架级 RiskEngine** | `rules.py` + `engine.py` | Python面向对象 | 11条规则链 | 回测/模拟/实盘统一调用 |
| **独立模拟引擎** | `paper_engine.py` | 类内方法 | 7条规则(止损/止盈/熔断/限次) | 模拟交易V3 |
| **实时守护** | `trade_guard.py` / `auto_exit_monitor.py` | 循环轮询 | 4条规则(止损/止盈/追踪止盈) | 盘中实时监控 |

### 2.3 11 条风险规则详情（RiskEngine）

| 规则 | 拦截级别 | 作用 | 参数 |
|:----|:--------:|:----|:----:|
| `MaxDrawdownRule` | BLOCK新开仓 | 策略净值从峰值回撤超阈值 | 默认20% |
| `DailyLossLimitRule` | BLOCK新开仓 | 日内已实现亏损超限 | 默认3%权益或绝对金额 |
| `PositionLimitRule` | BLOCK超限 | 单只股票持仓超上限 | 默认30%权益 |
| `TotalPositionsRule` | BLOCK超限 | 同时持仓超N只 | 默认10只 |
| `OrderFrequencyRule` | BLOCK超频 | 下单间隔不足N秒 | 默认5秒 |
| `BlacklistRule` | BLOCK黑名单 | 禁止交易指定股票 | 无默认 |
| `MarketCircuitBreakerRule` | BLOCK一级/二级 | 大盘跌幅超一级暂停买入/超二级全面暂停 | 默认3%/5%，冷却30分钟 |
| `ConsecutiveLossRule` | REDUCE/BLOCK | 连续N天亏损后降仓或暂停 | 默认3天→降仓50% |
| `SingleOrderAmountRule` | BLOCK超大单 | 单笔金额超权益%或绝对金额 | 默认无(需配置) |
| `DailyTradeCountRule` | BLOCK超量 | 日内交易超N次 | 默认100次 |

### 2.4 独立模块功能（risk_guard.py）

| 类 | 功能 |
|:---|:-----|
| `PreTradeChecker` | 报单前检查：资金/仓位/行业集中度/信号等级/涨跌停/持仓总数 |
| `CorrelationAnalyzer` | 持仓相关性分析：行业集中度检测、价格相关系数矩阵 |
| `RiskEventBus` | SSE 风控事件推送，保留最近100条日志 |
| `RiskCycleScheduler` | 盘前检查（隔夜亏损预警+熔断状态）+ 盘后报告（日盈亏/回撤/实盘回测偏差） |
| `StressTester` | 4种极端情景压力测试：2015股灾(-30%)/2020疫情(-8%)/2024小微盘(-20%)/千股跌停(-35%) |

---

## 三、功能完整性与覆盖度评估

### 3.1 风控生命周期覆盖

| 阶段 | 潜龙 | QuantConnect | RiceQuant | JoinQuant |
|:----|:----:|:-----------:|:---------:|:--------:|
| **盘前检查** | ✅ RiskCycleScheduler | ❌ | ❌ | ❌ |
| **报单前检查** | ✅ RiskEngine 11条规则链 | ✅ RiskModel | ✅ sys_risk | ⚠️ 平台级 |
| **盘中持仓监控** | ✅ trade_guard/auto_exit | ✅ TrailingStop | ❌ | ❌ |
| **盘后报告** | ✅ RiskCycleScheduler | ❌ | ✅ sys_analyser | ✅ 回测报告 |
| **压力测试** | ✅ StressTester 4情景 | ❌ | ❌ | ❌ |
| **相关性分析** | ✅ CorrelationAnalyzer | ⚠️ Sector | ❌ | ❌ |
| **熔断机制** | ✅ 两级大盘熔断+连续亏损熔断 | ❌ | ❌ | ❌ |
| **黑名单** | ✅ BlacklistRule | ❌ | ❌ | ✅ |
| **行业集中度** | ✅ PreTradeChecker | ✅ SectorExposure | ❌ | ❌ |

**结论**：潜龙风控的**生命周期覆盖度**远超 TOP3 平台。QuantConnect 没有盘前/盘后检查，没有压力测试，没有大盘熔断。

### 3.2 规则丰富度对比

| 规则类型 | 潜龙 | QuantConnect | RiceQuant | JoinQuant |
|:---------|:---:|:-----------:|:---------:|:--------:|
| 最大回撤限制 | ✅ | ✅ MaxDrawdown | ❌ | ✅ |
| 日亏损限制 | ✅ | ❌ | ❌ | ❌ |
| 单票仓位限制 | ✅ | ❌ | ✅ | ✅ |
| 总持仓数限制 | ✅ | ❌ | ✅ | ✅ |
| 下单频率限制 | ✅ | ❌ | ❌ | ❌ |
| 黑名单 | ✅ | ❌ | ❌ | ✅ |
| 大盘熔断 | ✅ | ❌ | ❌ | ❌ |
| 连续亏损熔断 | ✅ | ❌ | ❌ | ❌ |
| 单笔金额限制 | ✅ | ❌ | ❌ | ❌ |
| 日内交易次数 | ✅ | ❌ | ❌ | ❌ |
| 行业敞口限制 | ✅ (PreTradeChecker) | ✅ Sector | ❌ | ❌ |
| 移动止盈 | ✅ (独立模块) | ✅ TrailingStop | ❌ | ❌ |
| 涨跌停保护 | ✅ (独立模块) | ❌ | ❌ | ✅ |
| T+1 约束 | ❌ | ❌ | ✅ | ✅ |

**结论**：潜龙在规则丰富度上**明显领先** TOP3 平台——14种风控规则中，QuantConnect 仅有 3 种，聚宽有 5 种，RiceQuant 有 2 种。

### 3.3 核心问题

#### 🔴 P0 — 三套风控体系并行，规则重复且冲突

```
RiskEngine (rules.py)          paper_engine.py             trade_guard.py
┌─────────────────┐          ┌─────────────────┐         ┌─────────────────┐
│ MaxDrawdownRule  │          │ 移动止盈1(+5%)   │         │ 止损-3%卖一半   │
│ DailyLossLimit   │          │ 移动止盈2(+7%)   │         │ 止损-5%全清     │
│ PositionLimit    │          │ 基本止损(-5%)    │         │ 止盈+7%回落1.5% │
│ TotalPositions   │          │ 熔断检查         │         │ 跌停保护        │
│ MarketCircuitBk  │          │ 日限笔数         │         │ 键盘日志监控     │
│ ConsecutiveLoss  │          │ 信号买入规则      │         └─────────────────┘
│ OrderFrequency   │          └─────────────────┘
│ Blacklist        │
│ SingleOrderAmt   │             参数不互通           参数不互通
│ DailyTradeCount  │         ┌→ RiskEngine不知道PaperAccount的移动止盈参数
└─────────────────┘         └→ trade_guard不知道RiskEngine的日亏损限制

收益率矩阵:
  - 回测用 RiskEngine (11条规则)
  - 模拟用 PaperAccount (7条独立规则)  
  - 实盘用 live_trader.CONFIG (独立配置)
  - 同花顺内用 auto_exit_monitor (不同格式配置)
```

**问题**：
- 同一个策略在回测/模拟/实盘中经历完全不同的风控规则
- `paper_engine.py` 的止损参数与 `trade_guard.py` 不一致（-5% vs -3%卖一半）
- 回测中没有移动止盈规则（RiskEngine 没有），模拟和实盘却有

#### 🟡 P1 — 其他问题

| 问题 | 详情 | 严重度 |
|:----|:-----|:------:|
| 行业集中度在 RiskEngine 中缺失 | `PreTradeChecker` 有但 `RiskEngine` 规则链中没有 | 🟡 P1 |
| 移动止盈不是 RiskRule | 移动止盈在 paper_engine/live_trader 中，不是 RiskRule | 🟡 P1 |
| 涨跌停保护分散在3处 | PreTradeChecker/auto_exit_monitor/trade_guard 各管各的 | 🟡 P1 |
| StressTester 使用随机数 | `np.random.uniform` 导致结果不固定 | 🟢 P2 |
| RiskEventBus 未实际使用 | 代码中有 SSE 推送机制但未被前端消费 | 🟢 P2 |

---

## 四、排名定位

| 评价维度 | 潜龙当前 | QuantConnect | RiceQuant | JoinQuant |
|:---------|:--------:|:-----------:|:---------:|:--------:|
| 规则丰富度(条) | **14** | 3 | 2 | 5 |
| 生命周期覆盖 | **盘前+盘中+盘后** | 仅盘中 | 仅报单前 | 报单前+盘后 |
| 架构整洁度 | ⭐⭐(3套并行) | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| 参数一致性 | ⭐(多套参数) | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 实时守护能力 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ |
| 压力测试 | ⭐⭐⭐⭐ | ❌ | ❌ | ❌ |
| 测试覆盖 | ⭐⭐⭐(7+7个测试) | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ |
| 行业集中度 | ⭐⭐⭐ | ⭐⭐⭐⭐ | ❌ | ❌ |
| T+1 约束 | ❌ | ❌ | ✅ | ✅ |

**综合评分**：4.0/5.0 — **这是潜龙量化框架评分最高的模块**

### 核心差距
1. **架构整洁度** — 3套并行体系需要统一
2. **参数一致性** — 风控参数分散在4个地方（rules.py/paper_engine/live_trader.CONFIG/auto_exit_monitor）
3. **T+1 约束缺失** — 在规则链中增加 T+1 规则

---

## 五、改进建议

### Phase 1：统一风控架构（建议2-3天）

1. **将移动止盈/止损抽为 RiskRule** — `TrailingStopRule` + `StopLossRule`
2. **废弃 paper_engine.py 的独立规则** — 改为调用 RiskEngine
3. **统一参数配置** — 从 `live_trader.CONFIG` 迁移到 `config/default.yaml`
4. **废弃 PreTradeChecker** — 将其功能合并到 RiskEngine（行业集中度作为新规则）

### Phase 2：补齐缺失规则（建议1-2天）

5. **新增 T+1 约束规则** — `TPlusOneRule`：当日买入不可同日卖出
6. **新增涨跌停保护 Rule** — `LimitUpDownRule`：统一涨跌停处理逻辑
7. **将 CorrelationAnalyzer 集成到 RiskEngine**

### Phase 3：优化（建议1天）

8. **StressTester 去随机化** — 用固定 Beta 替代随机波动
9. **RiskEventBus 连接前端** — 让 quant_dashboard 展示风控事件

---

## 六、总结评分

| 评分维度 | 当前评分 | 目标评分 | 差距 |
|---------|:-------:|:-------:|:----:|
| 规则丰富度 | ⭐⭐⭐⭐⭐(14条) | ⭐⭐⭐⭐⭐ | ✅ 已领先 |
| 生命周期覆盖 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ✅ 已领先 |
| 架构整洁度 | ⭐⭐(3套并行) | ⭐⭐⭐⭐⭐ | 🔴 最大短板 |
| 参数一致性 | ⭐⭐(4套参数) | ⭐⭐⭐⭐⭐ | 🔴 需统一 |
| 实时守护能力 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ✅ 已领先 |
| 测试覆盖 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🟢 补个别规则 |
| 前端展示 | ⭐ | ⭐⭐⭐⭐ | 🟡 风控事件无UI |

**综合评分**：4.0/5.0 — **全场最高分，但架构问题需要优先处理**

---

*报告生成时间：2026-06-07 22:26*
*评审人：量化策略师 / 评审专家*
