# 潜龙量化平台 · 模拟执行模块评审报告
## 对标 TOP3 量化平台模拟交易架构对比分析

> **评审时间**: 2026-06-07
> **评审对象**: fill_simulator.py + SimulatedBroker + paper_engine.py + SimulatedDataProvider + backtest/engine.py 信号处理管道
> **对标平台**: QuantConnect Lean · RiceQuant RQAlpha · JoinQuant 聚宽

---

## 一、TOP3 平台模拟执行架构深度研究

### 1.1 QuantConnect Lean（机构级 · FillModel 抽象）

| 维度 | 设计要点 |
|------|---------|
| **FillModel 抽象** | `FillModel` 接口完全开放，支持自定义撮合逻辑；内置 `ImmediateFillModel`（立即成交）、`LatestPriceFillModel`（最新价成交） |
| **滑点模型** | 三种滑点模型：`ConstantSlippageModel`（固定）、`VolumeShareSlippageModel`（成交量比例）、`SpreadSlippageModel`（买卖价差） |
| **订单类型** | 13种订单类型：Market/Limit/StopMarket/StopLimit/TrailingStop/LimitIfTouched 等 |
| **FeeModel 抽象** | `FeeModel` 完全开放：`ConstantFeeModel`/`PercentFeeModel`/`InteractiveBrokersFeeModel`，支持多市场 |
| **SettlementModel** | 独立的结算模型：`ImmediateSettlementModel`（立即）/ `DelayedSettlementModel`（T+1） |
| **BrokerageModel** | 对接不同券商行为差异：`DefaultBrokerageModel`/`InteractiveBrokersBrokerageModel`/`GDAXBrokerageModel` |
| **实时行情驱动** | 实盘模式下：`SubscriptionManager` 管理数据流 → `OnData(Slice)` 驱动策略 → `PortfolioTarget` 形成仓位目标 → `ExecutionModel` 执行 |

**核心借鉴**：FillModel/FeeModel/SettlementModel 三重抽象、成交量比例滑点、13种订单类型

---

### 1.2 RiceQuant RQAlpha（Mod 插件化 · 撮合引擎）

| 维度 | 设计要点 |
|------|---------|
| **sys_simulation Mod** | 独立撮合引擎 Mod，接管订单执行逻辑；可替换/禁用 |
| **Bar内撮合** | 支持 next_bar（次日开盘）和 current_bar（当日均价）两种模式 |
| **匹配规则** | 限价单：Bar 内 low/high 价格穿越判断；市价单：立即以 opp 价成交 |
| **滑点配置** | 可选固定滑点或百分比滑点 |
| **税费模型** | `sys_transaction_cost` Mod 独立处理印花税/佣金/过户费，支持A股规则 |
| **账户管理** | `sys_accounts` Mod 管理持仓/资金/冻结资金 |
| **回测→实盘无缝** | 同一套策略代码，回测/模拟/实盘仅切换 `mod` 配置 |

**核心借鉴**：Mod 插件化拆分解耦、next_bar/current_bar 两种成交模式、与实盘共享同一套代码

---

### 1.3 JoinQuant 聚宽（云端 · 高仿真模拟）

| 维度 | 设计要点 |
|------|---------|
| **云端模拟撮合** | 在云端服务器执行真实行情驱动的高仿真模拟 |
| **价格优先级** | 市价单：用 opp 价 + 滑点；限价单：用 Bar 内穿越判断 |
| **T+1 强制约束** | 严格遵循 A 股 T+1 规则：当日买入不可同日卖出 |
| **涨跌停处理** | 涨停无法买入/跌停无法卖出，自动等待下一 Bar |
| **分红送股** | 自动处理除权除息、送股、配股 |
| **税费** | 自动计算佣金（最低5元）+ 印花税（卖出千一）+ 过户费 |

**核心借鉴**：T+1 严格约束、涨跌停处理、分红送股自动处理

---

## 二、潜龙模拟执行 V3 模块现状分析

### 2.1 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户层                                   │
│  ┌─────────────────────────────┐  ┌──────────────────────────┐  │
│  │     paper_engine.py          │  │  test_paper_rules.py    │  │
│  │     (独立模拟交易引擎V3)      │  │  (7大规则合规测试)       │  │
│  └─────────────┬───────────────┘  └──────────┬───────────────┘  │
│                │                               │                │
├────────────────┼───────────────────────────────┼────────────────┤
│          框架层 - 模拟执行                     │                │
│  ┌─────────────────────────────────────────────┐               │
│  │  execution/fill_simulator.py                │               │
│  │    ├─ SlippageModel (3种: fixed/prop/normal)│               │
│  │    ├─ FillSimulatorConfig (滑点/佣/印/税)    │               │
│  │    └─ FillSimulator (成交模拟器)             │               │
│  ├─────────────────────────────────────────────┤               │
│  │  execution/brokers/simulated.py             │               │
│  │    └─ SimulatedBroker (维护虚拟账户/持仓)    │               │
│  ├─────────────────────────────────────────────┤               │
│  │  execution/broker.py → AbstractBroker       │               │
│  ├─────────────────────────────────────────────┤               │
│  │  execution/brokers/ths.py → THSBroker(实盘)  │               │
│  └──────────────┬──────────────────────────────┘               │
│                 │                                               │
│  ┌──────────────▼──────────────────────────────┐               │
│  │  data/providers/simulated.py                 │               │
│  │    └─ SimulatedDataProvider (历史数据回放)    │               │
│  └──────────────────────────────────────────────┘               │
│                                                                 │
│  ┌──────────────────────────────────────────────┐               │
│  │  backtest/engine.py — 回测引擎信号管道        │               │
│  │    Signal → PositionSizer → RiskEngine       │               │
│  │    → Order → FillSimulator → Trade           │               │
│  └──────────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 文件结构梳理

| 文件 | 行数 | 职责 | 评价 |
|------|:----:|------|:----:|
| `fill_simulator.py` | 194 | **成交模拟器核心** — 滑点/佣金/印花税/限价单OHLC判断 | ⭐⭐⭐ 设计扎实 |
| `brokers/simulated.py` | 228 | **模拟Broker** — 虚拟账户/持仓/订单管控 | ⭐⭐⭐ 功能完整 |
| `broker.py` | 115 | **抽象Broker接口** — 10个抽象方法 | ⭐⭐⭐⭐ 接口清晰 |
| `brokers/ths.py` | 223 | **同花顺实盘Broker** — xd.cmd() 适配 | ⭐⭐⭐ 实盘对接 |
| `data/providers/simulated.py` | 180 | **模拟数据提供器** — Bar回放数据源 | ⭐⭐⭐ 设计合理 |
| `paper_engine.py` | 382 | **独立模拟交易引擎 V3** — PaperAccount | ⚠️ 与框架重复 |
| `test_paper_rules.py` | 350 | **7大规则合规测试** — 止损/止盈/熔断... | ⭐⭐⭐⭐ 测试完备 |
| `paper_account.json` | 1 | 账户状态持久化 | ⚠️ JSON明文不安全 |
| `backtest/engine.py`(信号段) | 552 | 信号处理管道：Signal→FillSimulator→Trade | ⭐⭐⭐ 流程完整 |
| `core/constants.py` | 103 | EngineMode/OrderDirection/OrderType | ⭐⭐⭐⭐ 枚举完整 |

### 2.3 现有优势（值得保留）

1. **FillSimulator 设计扎实** — decoupled 的成交模拟器，不依赖特定 Broker，可在回测和模拟 Broker 间复用
2. **A股税费规则完整** — 佣金最低5元 + 印花税（卖出千一）+ 滑点（3种模型），对比多数开源框架更细致
3. **限价单 OHLC 穿越判断** — `_determine_fill_price()` 用 Bar 内 low/high 判断限价单是否成交，逻辑正确
4. **与实盘共享 AbstractBroker 接口** — `THSBroker` 和 `SimulatedBroker` 继承同一接口，回测→实盘只需切换 Broker
5. **信号处理管道完整** — `Signal → PositionSizer → RiskEngine → Order → FillSimulator → Trade`，5步流程清晰
6. **测试覆盖率高** — `test_paper_rules.py` 覆盖7大规则，含边界条件测试
7. **SimulatedDataProvider 设计合理** — 构建统一时间轴，逐bar回放，`progress_pct` 提供进度查询

### 2.4 核心问题（按严重程度排序）

---

#### 🔴 P0 — 严重影响模拟真实性的问题

##### 问题 1：双模拟引擎并行 — PaperAccount 与 SimulatedBroker 各管各的

**现状**：
```python
# paper_engine.py — 独立的模拟交易引擎 V3（不与框架通信）
class PaperAccount:
    def __init__(self):
        self.cash = 1_000_000.0
        self.positions = {}  # 自己维护持仓
        self.trade_log = []  # 自己维护交易日志
    def place_order(self, symbol, side, ...):
        # 自己维护资金/持仓，不通过 SimulatedBroker
```
```python
# execution/brokers/simulated.py — 框架内的模拟 Broker
class SimulatedBroker(AbstractBroker):
    def __init__(self, initial_cash=1_000_000):
        self._cash = initial_cash  # 自己维护资金
        self._positions = {}  # 自己维护持仓
```

**问题**：两套独立的模拟账户系统，数据不互通。
- `paper_engine.py` 的 `PaperAccount` 不依赖框架，直接管理资金/持仓
- `SimulatedBroker` 是框架的标准 Broker，用于统一信号管道
- 同一个策略在不同模拟器中结果不同

**修复方向**：废弃 `PaperAccount`，所有模拟交易统一走 `SimulatedBroker` + `FillSimulator` 管道。

---

##### 问题 2：定价逻辑分散 — 价格获取有4种路径

**现状**（`paper_engine.py` 第68-91行）：
```python
def _get_market_price(self, symbol):
    # 1. 从 realtime_quotes 实时行情
    # 2. 从 price_cache.json 文件
    # 3. 从因子缓存
    # 4. 回退到默认值 10.0
```

**问题**：价格获取路径未经过 `DataPortal` 或统一行情源，不同行情源的价格差异会导致成交价格不一致。没有 Point-in-Time 保证。

**修复方向**：统一使用 DataProvider 获取行情。

---

##### 问题 3：模拟账户状态持久化为明文 JSON

**现状**：
```python
# paper_account.json — 明文存储账户状态
{"cash": 1001500.0, "positions": {}, "trade_log": [...], "auto_enabled": true}
```

**问题**：
- 无事务保障：写入过程中断导致 JSON 损坏
- 无版本管理：修改数据模型后旧 JSON 无法迁移
- 明文无加密

**修复方向**：使用 SQLite 替代 JSON，支持事务和版本迁移。

---

#### 🟡 P1 — 影响模拟交易可用性的问题

##### 问题 4：部分成交（Partial Fill）不支持

**现状**：
```python
# fill_simulator.py 第104-120行
# 全部成交或全部不成交，没有部分成交逻辑
if self._rng.random() > self.config.fill_probability:
    return None
return Trade(..., volume=order.requested_volume, ...)
```

**影响**：大单模拟不真实——实际市场中大单往往部分成交。

**修复方向**：增加部分成交模型，大单按流动性和成交量比例分批成交。

---

##### 问题 5：缺 T+1 强约束 — 今日买入可在同一天卖出

**现状**：
```python
# fill_simulator.py 中没有 T+1 检查
# SimulatedBroker 中也没有
```

**A股规则**：T+1 约束 — 当日买入的股票不可在同一天卖出。
**影响**：回测可能产生不真实的交易。

**修复方向**：在 SimulatedBroker 中增加 T+1 约束，买入后标记 `buy_date`，当日不可卖出。

---

##### 问题 6：涨跌停处理只做价格检查，未做数量限制

**现状**：
```python
# fill_simulator.py 第92-96行
if limit_up > 0 and order.direction == OrderDirection.BUY and fill_price >= limit_up:
    return None  # 涨停不买
if limit_down > 0 and order.direction == OrderDirection.SELL and fill_price <= limit_down:
    return None  # 跌停不卖
```

**影响**：涨停时完全不买入，但实际市场在涨停板打开时仍有成交机会。跌停同理。应该允许在涨停价位买入（如果封单松动），而非一刀切拒绝。

---

##### 问题 7：模拟延迟和人为错误模拟缺失

**现状**：
- 模拟没有网络延迟
- 没有订单被拒绝的概率（交易所拒绝）
- 没有止损/止盈触发的价格跳空处理

---

#### 🟢 P2 — 改进建议

##### 问题 8：缺少订单簿模拟

**现状**：模拟只有OHLCV，没有买卖盘口深度。无法模拟：
- 大单对盘口的冲击
- 限价单在盘口中的排队顺序
- 盘口厚度对成交概率的影响

##### 问题 9：滑点模型缺乏成交量维度

**现状**：三种滑点模型（fixed/proportional/normal）都没有考虑**订单成交量**对滑点的影响。大单滑点应显著大于小单。

**对比**：QuantConnect 的 `VolumeShareSlippageModel` 按订单量占成交量比例计算滑点。

##### 问题 10：缺少订单序列化和回放能力

**现状**：无法保存和回放订单流。如果想把模拟交易录下来回放分析，没有相应机制。

---

## 三、与 TOP3 平台对比评分

| 评价维度 | 潜龙当前 | QuantConnect | RiceQuant | JoinQuant | 优先级 |
|---------|:--------:|:-----------:|:---------:|:--------:|:------:|
| FillModel 抽象 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | 🟢 P2 |
| 滑点模型丰富度 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | 🟡 P1 |
| 佣金/税费模型 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🟢 |
| 限价单OHLC判断 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🟢 |
| 部分成交支持 | ⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | 🟡 P1 |
| T+1 强约束 | ⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🔴 P0 |
| 涨跌停处理 | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🟡 P1 |
| 接单/撤单能力 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | 🟢 |
| 实盘→回测一致性 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🟡 P1 |
| 订单回放分析 | ⭐ | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐ | 🟢 P2 |
| 容量/冲击成本 | ⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ | 🟡 P1 |
| **模拟V3独立引擎** | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ | 独有优势 |
| 规则合规测试 | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ | ✅ 已做 |

**综合评分**：3.3/5.0

---

## 四、架构存在的问题 — 双引擎分裂

### 4.1 问题核心

```
paper_engine.py               framework执行层
    PaperAccount                  SimulatedBroker
    ┌──────────┐                  ┌──────────────┐
    │ self.cash │                 │ self._cash   │
    │ self.pos  │                 │ self._pos    │
    │ self.tlog │                 │ self._trades │
    │ place_    │                 │ submit_order │
    │ order()   │                 │ update_with_ │
    └─────┬─────┘                 │ quote()      │
          │                       └──────┬───────┘
          │                              │
   auto_trade_check()              Signal → PositionSizer
   7条规则                           → RiskEngine → Order
                                   → FillSimulator → Trade
```

两个引擎的核心能力对比：

| 能力 | PaperAccount | SimulatedBroker | 
|------|:-----------:|:--------------:|
| 资金管理 | ✅ self.cash | ✅ self._cash |
| 持仓管理 | ✅ self.positions | ✅ self._positions |
| 交易日志 | ✅ self.trade_log | ✅ self._trades |
| 行情接入 | ⚠️ 多路退火 | ✅ DataProvider |
| 风险指标 | ✅ get_status() | ⚠️ 无自行计算 |
| 状态持久化 | ✅ JSON文件 | ❌ 无 |
| 自动规则 | ✅ 7条规则 | ❌ 依赖RiskEngine |
| 与回测共享 | ❌ 完全不共享 | ✅ 与BacktestEngine共享FillSimulator |
| 实盘迁移 | ❌ 手动下单 | ✅ THSBroker实现同一接口 |

### 4.2 推荐的统一架构

```
统一后：
  回测模式 → BacktestEngine → SimulatedBroker → FillSimulator
  模拟模式 → PaperEngine     → SimulatedBroker → FillSimulator  (复用规则层)
  实盘模式 → LiveEngine      → THSBroker       → 真实成交

PaperAccount 的 7 条规则 → 抽为独立 RiskRule 模块
PaperAccount 的状态持久化 → 改为 SQLite
auto_trade_check() → 改为事件驱动的规则引擎
```

---

## 五、改进计划（Roadmap）

### Phase 1：修复 P0 问题（紧急）

| 任务 | 具体内容 | 预计工作量 |
|------|---------|-----------|
| **P0-1** 统一模拟交易引擎 | 废弃 PaperAccount 自维护的资金/持仓，改为通过 SimulatedBroker 操作；PaperAccount 的 7 条规则抽取为独立 Rule 模块 | 2天 |
| **P0-2** 统一定价逻辑 | `_get_market_price()` 废弃，改为 DataProvider.get_quote() 统一获取行情 | 1天 |
| **P0-3** 状态持久化升级 | JSON → SQLite，增加事务保障和数据迁移支持 | 1天 |

### Phase 2：补齐 P1 功能（重要）

| 任务 | 具体内容 | 预计工作量 |
|------|---------|-----------|
| **P1-1** 部分成交支持 | FillSimulator 增加 partial fill 模型；大单按成交量的比例分批成交 | 2天 |
| **P1-2** T+1 强约束 | SimulatedBroker 增加 T+1 规则：买入日标记，当日不可卖出 | 1天 |
| **P1-3** 涨跌停精细处理 | 涨停板打开时允许成交；跌停封死时允许卖出（如果还有买家） | 1天 |
| **P1-4** 成交量比例滑点 | 新增 VolumeShareSlippageModel，大单滑点更大 | 1天 <br>(对标QuantConnect) |

### Phase 3：架构优化（提升）

| 任务 | 具体内容 | 来源 | 预计工作量 |
|------|---------|------|-----------|
| **P2-1** 订单簿模拟 | 基于 OHLCV 生成虚拟盘口；限价单在盘口中的排队 | — | 3天 |
| **P2-2** 订单回放 | 序列化所有订单事件，支持订单流回放分析 | — | 2天 |
| **P2-3** 模拟延迟 | 网络延迟 + 交易所处理延迟 + 概率拒单 | QuantConnect | 1天 |
| **P2-4** 红股/除权处理 | 自动处理送股、配股、分红对持仓的影响 | 聚宽 | 2天 |

---

## 六、总结评分

| 评分维度 | 潜龙当前 | QuantConnect | RiceQuant | JoinQuant | 优先级 |
|---------|:--------:|:-----------:|:---------:|:--------:|:------:|
| 成交模拟器独立性 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | 🟢 |
| 滑点模型丰富度 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | 🟡 P1 |
| A股税费规则 | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🟢 |
| 限价单OHLC判断 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🟢 |
| 部分成交支持 | ⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | 🟡 P1 |
| T+1 约束 | ⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🔴 P0 |
| 涨跌停处理 | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🟡 P1 |
| 成交量比例滑点 | ⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ | 🟡 P1 |
| 模拟规则测试 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ | 独有优势 |
| 回测→模拟一致性 | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🔴 P0 |
| 独立模拟V3引擎 | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ | 独有 |

**综合评分**：3.3/5.0

**最大优势**：FillSimulator 独立设计、A股税费规则完整、模拟规则合规测试（`test_paper_rules.py` 7大规则测试）

**最大短板**：双模拟引擎并行（P0）、T+1 约束缺失（P0）、定价逻辑分散（P0）、部分成交不支持（P1）

---

*报告生成时间：2026-06-07 22:13*
*评审人：量化策略师 / 评审专家*
