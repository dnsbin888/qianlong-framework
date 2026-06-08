# 潜龙量化平台 · 因子模块评审报告
## 对标 TOP3 量化平台因子架构对比分析

> **评审时间**: 2026-06-07
> **评审人**: 量化策略师 / 评审专家
> **对标平台**: QuantConnect Lean · RiceQuant RQAlpha · JoinQuant 聚宽
> **评审对象**: `factors/` 模块 + `financial_factors.py` + `signal_generator.py` + `factor_ic.py` 等

---

## 一、TOP3 量化平台因子模块架构深度研究

### 1.1 QuantConnect Lean（机构级 · Alpha Model 框架）

| 维度 | 设计要点 |
|------|---------|
| **架构范式** | **Algorithm Framework** 五模块解耦：`Universe → Alpha → Portfolio → Execution → Risk`。因子生成完全独立为 `AlphaModel` 组件 |
| **Alpha Model 接口** | `Update(algorithm, slice) → List[Insight]` — Insight 包含 Symbol/Direction/Period/Magnitude/Confidence，是带置信度的预测信号 |
| **InsightManager** | 中心化管理所有活跃 Insight：评分更新、自动过期（超 Period 后清除）、去重合并、事件通知 |
| **多因子组合** | `CompositeAlphaModel` 合并多个 Alpha Model 输出，每个子模型独立运行，Insight 全部汇入同一管理器 |
| **内置 Alpha 模型** | `RsiAlphaModel` / `EmaCrossAlphaModel` / `MacdAlphaModel` / `HistoricalReturnsAlphaModel` / `ConstantAlphaModel` |
| **数据时间戳** | 严格的 Point-in-Time：`Slice` 只包含当前时点的数据，`GetHistory()` 自动截止到当前 Bar |
| **因子持久化** | 通过 JSON/MongoDB 存储 Alpha 结果；Insight 有 Expiration 机制，不会持久化过期因子 |
| **评价回馈** | 每次 Bar 更新时对活跃 Insight 评分（`Insights.Step()`），实现因子表现的实时跟踪 |

**核心借鉴**: Alpha Model 隔离、Insight 置信度体系、CompositeAlphaModel 多因子组合、自动过期机制、实时评分回馈

---

### 1.2 RiceQuant RQAlpha（国内主流 · Mod 插件化因子）

| 维度 | 设计要点 |
|------|---------|
| **架构范式** | Mod 插件化架构，因子相关 Mod 独立注册：`sys_simulation`（撮合）、`sys_analyser`（分析）、`sys_transaction_cost`（成本） |
| **因子计算** | 通过 `context.extra` 传递用户自定义因子；内置 `technical_indicators` 模块提供常用技术因子 |
| **Point-in-Time 数据** | `get_price()` 自动按当前回测日期截断；财务数据有严格的 `ann_date`（披露日期）对齐，杜绝未来函数 |
| **因子预处理** | 内置 `winsorize()` / `standardize()` / `neutralize()`，与聚宽 API 兼容 |
| **因子分析** | 内置 IC 分析、分位数收益、因子相关性矩阵；通过 `sys_analyser` 自动生成分析报告 |
| **因子缓存** | 支持 `persist` / `restore` 机制，跨 session 持久化因子计算结果 |
| **多频率因子** | 支持日线/分钟因子统一管理，不同频率因子通过 `run_daily()` / `run_monthly()` 调度 |

**核心借鉴**: Point-in-Time 财务数据对齐、跨 session 因子缓存、分钟/日线统一管理

---

### 1.3 JoinQuant 聚宽（国内最大 · 云端因子平台）

| 维度 | 设计要点 |
|------|---------|
| **架构范式** | 云端因子库 + 本地策略 API；因子在云端预计算，用户通过 `get_factor()` 直接获取 |
| **因子库规模** | 1000+ 个预先计算好的因子（基础/技术/财务/情绪/另类），分类索引 |
| **因子版本管理** | 支持因子版本控制（V1/V2/V3），旧版本因子持续可用，可回溯历史版本 |
| **因子分析** | 内置因子看板：IC/ICIR 时间序列、分层回测、因子相关性、因子收益归因 |
| **因子合成** | 支持 `CompositeFactor` 自定义合成：`factor_A * 0.3 + factor_B * 0.7` |
| **数据对齐** | 严格的除权除息调整、停牌处理、财报披露日期对齐 |
| **实盘因子** | 云端因子可实时调用，与回测完全一致，无需重新计算 |
| **缓存复用** | 因子计算缓存 + 版本控制，同一因子同一参数不会重复计算 |

**核心借鉴**: 1000+ 预计算因子库、因子版本控制、CompositeFactor 合成语言、云端因子实时调用

---

### TOP3 因子架构对比总结

| 维度 | QuantConnect Lean | RiceQuant RQAlpha | JoinQuant 聚宽 |
|------|:-----------------:|:-----------------:|:--------------:|
| 因子生成隔离 | ✅ AlphaModel 组件 | ✅ Mod 插件化 | ✅ 云端独立计算 |
| Point-in-Time | ✅ Slice 严格 | ✅ get_price 自动截断 | ✅ 财务披露对齐 |
| 因子版本管理 | ❌ | ❌ | ✅ V1/V2/V3 |
| 多因子组合 | ✅ CompositeAlphaModel | ❌ | ✅ CompositeFactor |
| 置信度体系 | ✅ Insight.Confidence | ❌ | ❌ |
| 自动过期 | ✅ Expiration | ❌ | ❌ |
| 实时评分回馈 | ✅ Insights.Step() | ❌ | ❌ |
| 分钟/日线统一 | ✅ | ✅ | ✅ |
| 因子缓存持久化 | ❌ | ✅ persist/restore | ✅ 云端缓存 |
| 因子数量 | 内置5个+自定义 | 内置20+自定义 | **1000+** |

---

## 二、潜龙因子模块现状分析

### 2.1 文件结构梳理

| 文件/目录 | 职责 | 评价 |
|-----------|------|------|
| `factors/definitions.py` | 30+ 内置因子定义（FactorDef 数据类） | ⭐⭐⭐ 设计合理 |
| `factors/engine.py` | FactorEngine + FactorPreprocessor | ⭐⭐⭐ 功能完整 |
| `factors/selector.py` | FactorCompositor + StockSelector + PortfolioBacktester | ⭐⭐ 合成方法有限 |
| `factors/analysis.py` | IC/ICIR/分位数/相关性/换手率分析 | ⭐⭐⭐ 分析较完整 |
| `factors/tdx_signals.py` | 14 个通达信主图信号因子 | ⭐⭐⭐ 翻译质量高 |
| `factors/tdx_signals2.py` | 6 个通达信选股公式因子 | ⭐⭐⭐ 翻译质量高 |
| `financial_factors.py` | 财务因子 (akshare 实时) | ⚠️ 独立模块，未集成 |
| `factor_ic.py` | IC 分析独立脚本 | ⚠️ 重复 analysis.py |
| `signal_generator.py` | 因子→信号的桥梁 (SignalRule) | ⭐⭐ 设计好但未落地 |
| `run_factor_backtest.py` | 43 个因子 IC 回测 | ⚠️ 一次性脚本 |
| `run_multi_factor_v2.py` | 多因子组合回测 V2 | ⚠️ 硬编码因子，不可复用 |
| `run_smart_factor.py` | 智能多因子 V3 (自适应) | ⭐ 有市场状态概念 |
| `factor_cache.pkl` | 预计算因子缓存 (516MB) | ⚠️ 单体文件，无增量 |
| `cache_ohlcv.pkl` | 辅助因子缓存 | ⚠️ 与主缓存分离 |

### 2.2 现有优势（值得保留）

1. **因子定义体系清晰**：`FactorDef` dataclass 包含 name/label/category/direction/compute/description，元数据完整
2. **因子分类索引**：`FACTOR_MAP` + `FACTORS_BY_CATEGORY` 双索引，查找高效
3. **因子预处理完整**：`FactorPreprocessor` 支持三种去极值方法 + Z-score 标准化 + OLS 行业/市值中性化
4. **IC 分析全面**：时间序列 IC、Rank IC、ICIR、分位数收益、多空收益、t 统计量、因子相关性、换手率
5. **通达信因子翻译质量高**：14+6=20 个 TDX 公式全部 Python 化，含 CROSS/HHV/LLV/COUNT/SMA/DMA/COST/WINNER 等复杂函数
6. **信号生成器**：`SignalRule` 的 condition 机制（above/below/cross_above/cross_below）设计合理
7. **策略注册表**：`StrategyRegistry` 自动注册 12 个策略，元数据完整
8. **市场自适应**：V3 的 `MarketState` 类实现了牛/熊/震荡三种状态下的权重切换

### 2.3 核心问题（按严重程度排序）

---

#### 🔴 P0 — 严重影响因子可信度的问题

##### 问题 1：因子计算存在未来函数风险 — 预计算缓存未按时间切片

```python
# run_multi_factor_v2.py 第72-103行
# 对整个时间序列一次性计算因子值
factor_vals[fname] = func(df)  # df 是全量数据！
# 然后按 index 切片使用
idx = dates.index(rdate)       # 但因子值可能已包含未来数据
raw = fvals[idx]
```

**问题**：`factor_trend_bottom()` 的实现用到了 `HHV(H,55)` 和 `LLV(L,55)`。如果 df 是 2020-2025 的全量数据，那么在计算 2020-01-01 的因子值时，HHV 已经 "看到" 了 2020-01-01 之前 55 天的数据。**但是**，如果在 pre-compute 阶段没有正确的 Point-in-Time 切片，因子缓存中存储的就是"全知"的因子值，回测时虽然按日期索引取数，但因子计算本身已经用了未来数据。

**影响**：回测 IC 虚高，策略实际表现远不如回测。

**修复方向**：因子计算必须按时间窗口滚动进行，确保每个时间点的因子值只使用该时间点之前的数据。

---

##### 问题 2：financial_factors.py 独立于主引擎

```python
# financial_factors.py 是独立模块
# 用 akshare 获取实时数据，返回 {code: {pe, pb, market_cap, ...}}
# 但回测引擎完全不知道这个模块存在
```

**问题**：
- 财务因子无法在回测中使用（回测需要历史财务数据）
- 实时财务数据也通过独立路径进入策略，没有经过 `DataPortal` 或 `FactorEngine`
- 没有会计披露日期对齐机制，实盘中可能使用到了当日才知道的财务数据

**修复方向**：将财务因子集成到 `FactorEngine` 中，历史数据通过缓存按披露日期对齐。

---

##### 问题 3：多因子组合回测脚本硬编码因子+回测逻辑

```python
# run_multi_factor_v2.py — 硬编码因子权重
FACTOR_SPEC = [
    ("trend_bottom", factor_trend_bottom, +1, 0.40),
    ("add_position", factor_add_position, +1, 0.25),
    ("bull_position", factor_bull_position, -1, 0.25),
    ("ret_20d", FACTOR_MAP["ret_20d"].compute, -1, 0.10),
]
```

**问题**：
- 因子权重、合成方法、选股逻辑全部硬编码在脚本中
- 无法通过配置文件或 API 灵活调整
- 回测结果不可复现（未保存种子、参数、数据版本）
- `run_multi_factor_v2.py` / `run_smart_factor.py` / `run_factor_backtest.py` 三套脚本各自维护独立逻辑

**修复方向**：将多因子回测封装为可配置类，支持 YAML/JSON 配置因子权重和参数。

---

#### 🟡 P1 — 影响因子研发效率的问题

##### 问题 4：缺少 Point-in-Time 数据管理

**现状**：
- `FactorEngine._get_kline()` 获取 300 天数据，但未确保按当前回测日期截断
- `run_multi_factor_v2.py` 预计算时使用全量数据
- 没有 `ann_date`（财报披露日期）对齐机制

**影响**：财务因子和技术因子的回测都可能存在未来函数。

**修复方向**：建立 Point-in-Time 层（`PITDataPortal`），所有因子计算必须通过该层访问数据。

---

##### 问题 5：缺少因子衰减监控和自动 IC 跟踪

**现状**：
- IC 分析是一次性脚本（`run_factor_backtest.py`），不是持续监控
- 无法自动检测因子 IC 衰减或失效
- 回测中使用了大量因子，但不知道哪些因子在实时表现中已经失效

**影响**：策略可能在已失效的因子上持续做决策，实际表现差。

**修复方向**：建立 IC 监控定时任务，自动计算每日/周/月 IC，IC 衰减到阈值自动告警。

---

##### 问题 6：因子定义格式不一致

**现状**：
- 内置因子用 `FactorDef` dataclass
- tdx 因子用 `dict`，name 作为 key 而不是 FactorDef 的 name 属性
- `FactorEngine._resolve_factors()` 中做了 `f.__dict__` 转换，丢失类型信息
- tdx 因子的 category 是普通字符串不是 `FactorCategory` 枚举

**影响**：同一套代码处理不同格式，容易出错；无法在 tdx 因子上使用 category 过滤。

**修复方向**：统一为 `FactorDef` 格式，tdx 因子也使用 FactorDef 数据类。

---

##### 问题 7：因子缓存为单体文件，无增量更新

**现状**：
- `factor_cache.pkl` 516MB，加载慢
- 无版本控制，缓存一旦损坏全丢
- 新增因子必须全量重建
- 不同数据源的因子分散在 `factor_cache.pkl` 和 `cache_ohlcv.pkl` 两个文件中

**修复方向**：改为按因子分组缓存，每个因子独立文件，支持增量计算。

---

#### 🟢 P2 — 改进建议

##### 问题 8：缺少合成因子工厂

**现状**：不支持类似聚宽 `CompositeFactor` 的声明式因子合成：
```python
# 理想能力
composite = (factor("ret_20d") * 0.3 + factor("vol_20d") * (-0.2)) / factor("turnover_5d")
```

**影响**：构建新因子需要写完整的计算函数，无法通过组合已有因子快速生成新因子。

---

##### 问题 9：缺失因子注册可视化

**现状**：
- 没有 API 可以获取所有因子的最近 IC 表现
- 没有因子相关性热力图（`factor_correlation()` 有但没被 UI 使用）
- 没有因子选择建议（哪些因子当前有效）

---

##### 问题 10：因子计算 O(n*m) 效率低

**现状**：
- 遍历股票（n）× 遍历因子（m），单线程
- 3000 只股票 × 43 个因子需要数小时
- 没有并行计算或向量化支持

---

##### 问题 11：SignalGenerator 未集成到回测流程

**现状**：
- `SignalGenerator` 设计良好但未被回测引擎调用
- 当前回测引擎（`backtest_engine.py`）和框架回测引擎（`backtest/engine.py`）各有不同的信号处理管道
- 没有统一的 `Factor → Signal → Order` 标准流程

---

## 三、与 TOP3 平台对比评分

| 评价维度 | 潜龙当前 | QuantConnect | RiceQuant | JoinQuant | 优先级 |
|---------|:--------:|:-----------:|:---------:|:--------:|:------:|
| 因子定义体系 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🟢 |
| Point-in-Time 数据 | ⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🔴 P0 |
| 因子预处理 | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🟢 |
| IC 分析 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🟢 |
| 因子缓存管理 | ⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🟡 P1 |
| 多因子合成 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ | 🟡 P1 |
| 因子衰减监控 | ⭐ | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ | 🟡 P1 |
| 财务因子集成 | ⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🔴 P0 |
| 置信度体系 | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ | 🟢 P2 |
| 因子版本管理 | ⭐ | ⭐ | ⭐ | ⭐⭐⭐⭐⭐ | 🟢 P2 |
| 通达信因子数量 | ⭐⭐⭐⭐⭐ (独有) | ❌ | ❌ | ❌ | — |
| 因子数量 | 50+ | 5+内置 | 20+内置 | 1000+云端 | 🟢 |

**综合结论**：因子模块在**定义体系**和**IC 分析**上已有较高水平，但在 **Point-in-Time 数据管理**（P0）、**财务因子集成**（P0）、**因子衰减监控**（P1）、**多因子合成可配置化**（P1）和**因子缓存管理**（P1）上存在明显短板。

---

## 四、改进计划（Roadmap）

### Phase 1：修复 P0 问题（紧急）

| 任务 | 具体内容 | 预计工作量 |
|------|---------|-----------|
| **P0-1** 因子计算防未来函数 | 在 FactorEngine 中引入 PIT 切片，因子计算函数必须按日期滚动调用；预计算缓存增加"截至日期"标记 | 3天 |
| **P0-2** 财务因子集成 | 将 `financial_factors.py` 改造为 `FinancialFactorEngine`，集成到 `FactorEngine`；支持历史财务数据（按披露日期对齐）+ 回测中使用 | 2天 |
| **P0-3** 多因子回测可配置化 | 将 `run_multi_factor_v2.py` 封装为可配置 `MultiFactorBacktest` 类，支持 YAML 配置因子权重/合成方法/选股参数；废弃三个独立脚本 | 2天 |

### Phase 2：补齐 P1 功能（重要）

| 任务 | 具体内容 | 预计工作量 |
|------|---------|-----------|
| **P1-1** Point-in-Time 数据层 | 建立 `PITDataPortal` 统一数据访问入口，所有因子/回测通过该层访问数据；支持 `ann_date` 财务披露对齐 | 4天 |
| **P1-2** 因子衰减监控系统 | 建立 IC 定时监控（天/周/月），自动计算各因子最新 IC/ICIR，IC 衰减到阈值自动告警；可视化面板 | 3天 |
| **P1-3** 因子格式统一 | 将 tdx 因子从 dict 迁移到 `FactorDef` dataclass；统一 category 枚举 | 1天 |
| **P1-4** 因子缓存重构 | 将单体 516MB pickle 拆分为按因子分组的独立缓存；支持增量计算（只计算新增因子）；引入缓存版本号 | 3天 |

### Phase 3：架构升级（提升）

| 任务 | 具体内容 | 来源 | 预计工作量 |
|------|---------|------|-----------|
| **P2-1** 合成因子工厂 | 实现 `CompositeFactor` 声明式因子合成：`factor("ret_20d") * 0.7 + factor("ma_cross_5_20") * 0.3` | 聚宽 | 3天 |
| **P2-2** 因子可视化面板 | 在 `quant_dashboard.py` 中增加因子看板：IC 时序图、因子相关性热力图、各因子最近 N 期表现 | — | 2天 |
| **P2-3** SignalGenerator 集成 | 将 SignalGenerator 整合到回测管道；统一 `backtest_engine.py` 和 `backtest/engine.py` 的信号处理流程 | QuantConnect | 2天 |
| **P2-4** 并行因子计算 | FactorEngine 支持多线程/多进程并行计算；优先计算耗时因子（TDX 信号） | — | 2天 |
| **P2-5** 因子版本管理 | 引入因子版本号，支持因子迭代历史回溯；加入 `FactorRegistry` 管理因子生命周期 | 聚宽 | 3天 |

---

## 五、具体代码改进示例

### 5.1 P0-1：因子 PIT 计算（防未来函数）

**当前**：
```python
# FactorEngine.compute() 第86-89行
factor_vals = pd.DataFrame(index=kline.index)
for fname, fdef in factors.items():
    result = fdef["compute"](kline)  # kline是300天全量数据
```

**修复后**：
```python
# 按日期滚动计算
def compute_rolling(self, kline, fdef, window=250):
    """滚动窗口计算因子，每个时间点只用过去 window 天数据"""
    result = pd.Series(index=kline.index, dtype=float)
    for i in range(window, len(kline)):
        window_data = kline.iloc[i-window:i]  # 只能看到过去 window 天
        try:
            val = fdef["compute"](window_data)
            result.iloc[i] = val.iloc[-1] if isinstance(val, pd.Series) else val
        except:
            result.iloc[i] = np.nan
    return result
```

---

### 5.2 P0-2：财务因子集成

**新 `FinancialFactorEngine`**：
```python
class FinancialFactorEngine:
    """财务因子引擎 — 支持历史财务数据回测 + 实盘实时数据"""

    def __init__(self, cache_dir="./data/financial"):
        self._cache = FinancialDataCache(cache_dir)

    def get_factor(self, symbol, factor_name, date=None):
        """获取财务因子值（按披露日期对齐）"""
        if date:
            # 回测模式：获取 date 前最新可用的财务数据
            return self._cache.get_value(symbol, factor_name, as_of=date)
        else:
            # 实盘模式：获取最新数据
            return self._cache.get_latest(symbol, factor_name)
```

---

### 5.3 P0-3：可配置多因子回测

**新 `MultiFactorBacktest` 类**：
```python
class MultiFactorBacktest:
    """可配置多因子选股回测

    使用 YAML 配置:
    ```yaml
    universe:
      min_price: 5.0
      min_days: 500
      n_stocks: 2000
    factors:
      - name: trend_bottom
        weight: 0.40
        direction: +1
      - name: add_position
        weight: 0.25
        direction: +1
    composite:
      method: icir_weighted   # equal/ic_weighted/icir_weighted
    selection:
      top_k: 30
      rebalance: 1M
    backtest:
      initial_cash: 1_000_000
      start: 2020-01-01
      end: 2025-12-31
    """
    def __init__(self, config: dict):
        ...

    def run(self) -> BacktestResult:
        ...

    def ic_analysis(self) -> ICAnalysis:
        ...
```

---

## 六、总结评分

| 评分维度 | 潜龙当前 | QuantConnect | RiceQuant | JoinQuant | 优先级 |
|---------|:--------:|:-----------:|:---------:|:--------:|:------:|
| 因子定义体系 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🟢 |
| **Point-in-Time 数据** | ⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🔴 P0 |
| **因子预处理** | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🟢 |
| **IC 分析** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🟢 |
| **因子缓存管理** | ⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🟡 P1 |
| **多因子合成** | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ | 🟡 P1 |
| **因子衰减监控** | ⭐ | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ | 🟡 P1 |
| **财务因子集成** | ⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 🔴 P0 |
| **通达信因子数量** | ⭐⭐⭐⭐⭐ | ❌ | ❌ | ❌ | 独有优势 |
| **回测脚本架构** | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🔴 P0 |

**综合评分**：3.1/5.0（与回测模块的 3.55/5.0 相比，因子模块在数据管理方面差距更大）

**最大优势**：50+ 因子（含 20 个独有的通达信因子）、完善的 IC 分析体系、因子预处理（去极值/标准化/中性化）

**最大短板**：Point-in-Time 数据管理缺失（核心 P0）、财务因子未集成（P0）、回测脚本硬编码（P0）、缺乏因子衰减监控（P1）

---

*报告生成时间：2026-06-07 21:38*
*评审人：量化策略师 / 评审专家*
