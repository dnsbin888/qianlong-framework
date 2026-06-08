# 潜龙量化平台 · 回测模块评审报告
## 对标 TOP3 量化平台架构对比分析

> **评审时间**: 2026-06-07  
> **评审人**: 量化策略师 / 评审专家  
> **对标平台**: QuantConnect Lean · RiceQuant RQAlpha · JoinQuant  
> **评审对象**: `backtest_engine.py` + `run_backtest_fast.py` + 关联风控模块

---

## 一、TOP3 量化平台回测架构深度研究

### 1.1 QuantConnect Lean（机构级开源引擎，C#/Python）

| 维度 | 设计要点 |
|------|---------|
| **架构范式** | 严格事件驱动，事件总线（`EventSystem`）统一调度所有市场数据、订单、成交、定时事件 |
| **数据管理** | 数据无关设计（`Data Agnostic`），支持任何格式；`SubscriptionSystem` 管理数据流；细粒度数据封装（`BaseData` 派生类）|
| **投资组合建模** | `SecurityPortfolio` 完整建模：持仓、资金、手续费、保证金、杠杆；支持多资产（股票/期货/期权/Crypto） |
| **订单执行模型** | `FillModel` 抽象撮合模型，可自定义滑点/延迟/部分成交；`FeeModel` 封装手续费；`SlippageModel` 封装滑点 |
| **风控体系** | `RiskManagementModel` 前置风控 + 实时组合风控；支持最大回撤停止、最大仓位限制、止损模型 |
| **性能指标** | 内置 `Statistics` 模块：Sharpe、Sortino、Calmar、最大回撤、`Capacity` 容量分析（估算策略可承载资金规模）|
| **实盘对齐** | 回测→纸交易→实盘 同一套算法代码，零修改迁移；`BrokerageModel` 抽象不同券商差异 |
| **扩展机制** | `AlgorithmFramework`：Universe 选股 + Alpha 信号 + Portfolio 建仓 + Execution 执行 + Risk 风控，五模块解耦 |

**核心借鉴**：事件总线设计、数据抽象层、投资组合完整建模、Capacity 容量分析

---

### 1.2 RiceQuant RQAlpha（国内主流通达信替代，Python 开源）

| 维度 | 设计要点 |
|------|---------|
| **架构范式** | 事件驱动 + Mod 插件化架构；核心引擎负责事件分发，各 Mod 订阅感兴趣的事件 |
| **Mod 体系** | `sys_simulation`（撮合引擎）、`sys_accounts`（账户/持仓）、`sys_analyser`（分析/指标）、`sys_transaction_cost`（税费）、`sys_risk`（事前风控）、`sys_scheduler`（定时任务）|
| **数据管理** | 与 RQData 深度集成；支持 Point-in-Time 财务数据（避免未来函数）；自动处理除权除息、停牌、ST |
| **回测真实性** | 日线级 Bar 内撮合（支持 next_bar / current_bar）；分钟级完整回放；避免前视偏差（Look-ahead Bias） |
| **API 设计** | 策略只需关注 `init()` + `handle_bar(context, bar)`，引擎负责调度；与聚宽 API 高度兼容 |
| **风控** | 事前风控 Mod 可拦截违规订单；支持最大仓位、最大亏损、涨跌停限制 |
| **分析输出** | `sys_analyser` 自动生成回测报告、收益曲线、风险指标、行业分布；支持 Plot 可视化 |

**核心借鉴**：Mod 插件化设计、Point-in-Time 数据、API 简洁性

---

### 1.3 JoinQuant 聚宽（国内最大量化社区平台）

| 维度 | 设计要点 |
|------|---------|
| **架构范式** | 向量化 + 事件驱动双模式；小策略用向量化提速，复杂事件逻辑用事件驱动 |
| **数据管理** | 云端数据服务；自动处理分红配股、停牌、涨跌停；财务数据有严格的披露日期对齐 |
| **避免回测陷阱** | 内置 `before_trading_start`（盘前）、`handle_data`（盘中）、`after_trading_end`（盘后）三阶段，严格防止跨期数据泄露 |
| **绩效分析** | 丰富的风险指标：Alpha、Beta、信息比率、收益归因；支持基准对比（Benchmark） |
| **实盘对接** | 回测通过后可一键实盘，支持模拟盘和真实券商接入 |

**核心借鉴**：双模式回测（向量化+事件驱动）、盘前/盘中/盘后三阶段隔离、基准对比

---

## 二、潜龙回测模块现状分析

### 2.1 文件结构梳理

| 文件 | 职责 | 评价 |
|------|------|------|
| `backtest_engine.py` | 核心回测引擎，事件驱动，T+1 执行 | ⭐ 主体框架完整，但细节有待完善 |
| `run_backtest_fast.py` | 快速回测，信号预计算+逐日模拟 | ⚠️ 与主力引擎逻辑不一致，形成"双引擎"分裂 |
| `compare_strategies.py` | 多策略对比，网格搜索最优参数 | ✅ 设计合理 |
| `risk_guard.py` | 事前风控+相关性分析+压力测试 | ✅ 功能较完整 |
| `trade_guard.py` | 实盘守护进程，监控持仓盈亏 | ✅ 实盘保护机制 |

### 2.2 现有优势（值得保留）

1. **事件驱动框架已建立**：`BacktestEngine.run()` 逐日推进，检查持仓退出→扫描买入信号→记录权益，流程清晰
2. **交易成本真实建模**：佣金+印花税+动态滑点（与成交额挂钩），比多数开源框架更细致
3. **移动止盈（Trailing Stop）**：一级/二级两档追踪止盈，设计合理
4. **行业集中度分析**：`_compute_metrics` 中已加入行业集中度统计
5. **VaR / CVaR 风险指标**：已有计算，符合机构级风控要求
6. **风控模块独立**：`risk_guard.py` 有完整的事前检查、相关性分析、压力测试

### 2.3 核心问题（按严重程度排序）

#### 🔴 P0 — 严重影响回测真实性的问题

**问题 1：止损/止盈价格人为设定，不反映真实市场**

```python
# backtest_engine.py 第130-133行
if exit_type == "stop_loss":
    sell_price = pos["buy_price"] * (1 + stop_loss)  # 人为设定价格！
elif exit_type == "take_profit":
    sell_price = pos["buy_price"] * (1 + take_profit)
```

**问题**：止损/止盈触发时，不应该用"成本价+百分比"人为设定成交价，而应该用**当日实际市场价格**（open/close/high/low）。人为设定会导致回测收益偏离真实情况。

**修复方向**：触发止损时，用当日 `open`（若 next_bar 执行）或 `low`（若盘中止损）作为成交价，并加入滑点。

---

**问题 2：`run_backtest_fast.py` 与 `backtest_engine.py` 逻辑不一致**

`run_backtest_fast.py` 是另一套回测逻辑：
- 卖出价 = `open * 0.999`（固定滑点）
- 买入价 = `close * 1.001`（固定滑点）
- 无移动止盈
- 无交易成本（佣金/印花税）

两套引擎会导致同一策略得出不同结果，无法信任回测结论。

**修复方向**：废弃 `run_backtest_fast.py`，或将其改为调用 `BacktestEngine` 的轻量封装。

---

**问题 3：未来函数风险 — `factor_cache` 可能在回测当日已包含未来数据**

```python
# backtest_engine.py 第232-235行
for fc in (self.factor_cache or []):
    if getattr(fc, 'symbol', '') == sym:
        sig_val = getattr(fc, signal_field, 0) or 0
```

若 `factor_cache` 是用全量数据一次性计算得到的，那么在回测第 `i` 天时，因子值可能已包含第 `i+N` 天的信息（未来函数）。

**修复方向**：因子计算必须在回测循环内按时间切片进行，或确保 `factor_cache` 的每个条目都标注了计算所用的最后一根 Bar 的日期。

---

#### 🟡 P1 — 影响策略研发效率的问题

**问题 4：缺少基准对比（Benchmark）**

当前 `_compute_metrics` 计算了 Sharpe、Sortino、最大回撤等，但**没有与基准指数（如沪深300、中证500）对比**。无法判断策略是否跑赢大盘。

**修复方向**：在 `equity_curve` 计算同时，计算基准指数的同期收益，增加 Alpha、Beta、信息比率等指标。

---

**问题 5：缺少参数稳定性检验（Walk-Forward Analysis）**

当前回测是固定区间一次性回测，容易过拟合。没有滚动窗口检验、样本外测试。

**修复方向**：增加 Walk-Forward 分析：将回测区间分成多段，前段优化参数，后段验证，滚动进行。

---

**问题 6：数据管理原始，缺少数据质量检查**

```python
self.stock_data = stock_data  # 直接传入 {symbol: DataFrame}
```

没有：
- 数据完整性检查（缺交易日、缺字段）
- 异常值处理（涨停板一字板、退市股票）
- 前视偏差防护（确保回测第 i 天只能看到第 i 天及之前的数据）

**修复方向**：建立 `DataPortal` 抽象层，统一管理数据访问，内置防未来函数机制。

---

#### 🟢 P2 — 改进建议

**问题 7：缺少容量分析（Capacity Analysis）**

TOP3 平台均有容量分析（Lean 的 `Capacity`、RQAlpha 的换手率分析）。潜龙回测没有评估"策略能承载多少资金"的功能。

**问题 8：策略 API 不够简洁**

对比 RQAlpha 的 `init() + handle_bar()` 简洁模式，潜龙的策略逻辑散落在 `backtest_engine.py` 的 `run()` 方法内（第180-246行，信号计算逻辑与引擎耦合在一起）。

**修复方向**：将策略逻辑解耦，引擎只负责调度，策略以独立函数/类形式注入。

---

## 三、改进计划（Roadmap）

### Phase 1：修复 P0 问题（紧急，1-2周）

| 任务 | 具体内容 | 预计工作量 |
|------|---------|-----------|
| **P0-1** 修复止损/止盈价格 | 改用当日实际市场价格；止损用 open（next_bar）或 low（盘中）；加入滑点模型 | 1天 |
| **P0-2** 统一回测引擎 | 废弃 `run_backtest_fast.py`，所有回测入口统一到 `BacktestEngine` | 1天 |
| **P0-3** 防未来函数 | 建立 `DataPortal` 抽象层，所有数据访问通过 `data_portal.get_price(sym, dt)` 进行，内置日期屏障 | 3天 |

### Phase 2：补齐 P1 功能（重要，2-4周）

| 任务 | 具体内容 | 预计工作量 |
|------|---------|-----------|
| **P1-1** 基准对比 | 接入指数数据（沪深300/中证500），计算 Alpha、Beta、信息比率、相对收益曲线 | 2天 |
| **P1-2** Walk-Forward 分析 | 实现滚动窗口参数检验，输出参数稳定性报告 | 3天 |
| **P1-3** 数据质量框架 | 建立数据校验：缺失值、异常值、停牌处理、除权除息调整 | 3天 |

### Phase 3：架构升级（提升，1-2月）

| 任务 | 具体内容 | 借鉴来源 |
|------|---------|---------|
| **P2-1** 策略解耦 | 参考 RQAlpha Mod 设计，将策略逻辑独立为可插拔模块；引擎提供 `before_trading`、`handle_bar`、`after_trading` 三个钩子 | RQAlpha |
| **P2-2** 事件总线 | 建立轻量事件总线，支持自定义事件（如财报发布、宏观数据发布） | QuantConnect Lean |
| **P2-3** 容量分析 | 根据换手率和股票流动性，估算策略最大容量 | QuantConnect Lean |
| **P2-4** 多频率支持** | 当前仅支持日线，需扩展至分钟级/小时级回测 | 三大平台均有 |

---

## 四、具体代码改进示例

### 4.1 修复 P0-1：止损价格改用真实市场价格

**当前代码（错误）**：
```python
# backtest_engine.py 第128-133行
if exit_type:
    sell_price = current_price  # ← 这行是对的
    if exit_type == "stop_loss":
        sell_price = pos["buy_price"] * (1 + stop_loss)  # ← 错误！人为设定
    elif exit_type == "take_profit":
        sell_price = pos["buy_price"] * (1 + take_profit)  # ← 错误！
```

**修复后**：
```python
if exit_type:
    # 止损：用当日最低价（保守估计）或次日开盘价
    if exit_type == "stop_loss":
        # T+1 执行：用次日 open 价，加滑点
        sell_price = current_price * 0.998  # 保守滑点
    elif exit_type == "take_profit":
        sell_price = current_price  # 用当前收盘价
    else:
        sell_price = current_price  # 正常退出用当前价
```

---

### 4.2 修复 P0-3：建立 DataPortal 防未来函数

**新建 `data_portal.py`**：
```python
class DataPortal:
    """数据门户 — 统一管理数据访问，防止未来函数"""
    
    def __init__(self, stock_data: dict):
        self._raw_data = stock_data
        self._current_date = None  # 引擎每次推进时设置
    
    def set_current_date(self, dt):
        """引擎调用，设置当前回测日期"""
        self._current_date = dt
    
    def get_price(self, symbol: str, field='close') -> float:
        """获取当前可看到的价格（只能看到 _current_date 及之前的数据）"""
        df = self._raw_data.get(symbol)
        if df is None or self._current_date not in df.index:
            return np.nan
        return float(df.loc[self._current_date, field])
    
    def get_history(self, symbol: str, end_dt=None, lookback=20) -> pd.DataFrame:
        """获取历史数据（自动截断到当前日期之前）"""
        df = self._raw_data.get(symbol)
        if df is None:
            return pd.DataFrame()
        end = end_dt or self._current_date
        return df[df.index <= end].tail(lookback)
```

---

### 4.3 补齐 P1-1：增加基准对比

**在 `BacktestEngine.run()` 中增加基准指数处理**：

```python
def run(self, ..., benchmark_sym='sh000300'):  # 沪深300
    # ... 现有逻辑 ...
    
    # 在逐日循环中，同步记录基准收益
    benchmark_df = self.stock_data.get(benchmark_sym)
    for i, today in enumerate(trading_days):
        # ... 现有持仓检查/买入逻辑 ...
        
        # 基准权益
        if benchmark_df is not None and today in benchmark_df.index:
            if i == 0:
                benchmark_start = float(benchmark_df.loc[today, 'close'])
                benchmark_equity = [1.0]  # 归一化
            else:
                benchmark_equity.append(
                    float(benchmark_df.loc[today, 'close']) / benchmark_start
                )
    
    # 在 _compute_metrics 中增加 benchmark 对比
    metrics['alpha'], metrics['beta'] = self._calc_alpha_beta(
        equity_curve, benchmark_equity, risk_free_rate=0.03
    )
```

---

## 五、总结评分

| 评分维度 | 潜龙当前 | QuantConnect | RiceQuant | JoinQuant | 改进优先级 |
|---------|---------|-------------|----------|----------|----------|
| **回测真实性** | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🔴 P0 |
| **事件驱动架构** | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🟡 P1 |
| **数据管理** | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🟡 P1 |
| **风控体系** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | 🟢 P2 |
| **策略解耦** | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🟢 P2 |
| **性能指标完整性** | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🟡 P1 |
| **实盘对齐** | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🟢 P2 |

**综合结论**：潜龙回测模块在**回测真实性**和**数据管理**两个核心维度上存在较大差距，需要优先修复 P0 问题。风控体系已有较好基础，可在此基础上继续完善。架构上建议向 RQAlpha 的 Mod 插件化方向演进，降低策略与引擎的耦合。

---

*报告生成时间：2026-06-07*  
*评审人：量化策略师 / 评审专家*
