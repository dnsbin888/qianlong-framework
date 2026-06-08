# 潜龙量化平台 · 优化模块评审报告
## 对标 TOP3 量化平台参数优化架构对比分析

> **评审时间**: 2026-06-07
> **评审对象**: scripts/optimizer.py + backtest_engine.py walk_forward + 11个独立优化脚本 + 可视化页面
> **对标平台**: QuantConnect Lean · RiceQuant RQAlpha · JoinQuant 聚宽

---

## 一、TOP3 量化平台优化架构深度研究

### 1.1 QuantConnect Lean（机构级 · Optimization Framework）

| 维度 | 设计要点 |
|------|---------|
| **云端优化** | QuantConnect Cloud 提供大规模并行参数优化，支持数千组合同时跑 |
| **本地 CLI** | `lean optimize` 本地命令行，支持自定义 Optimization Strategy |
| **优化策略接口** | `IOptimizationStrategy` 接口：GridSearch / BayesianSearch / RandomSearch 可插拔 |
| **参数类型** | 支持 int/float/categorical 三种参数类型，支持约束条件（如 fast < slow） |
| **目标函数** | 可设置任意 PerformanceMetrics 为目标，默认使用 Sharpe Ratio |
| **Walk-Forward 集成** | 内置 RollForward 回调，支持滚动窗口验证 |

**核心借鉴**：优化策略接口化、3种搜索方法、多参数类型支持

### 1.2 RiceQuant RQAlpha（Mod 插件化 · 参数搜索）

| 维度 | 设计要点 |
|------|---------|
| **model 参数** | 通过 `context.model.param` 在策略中引用可优化参数 |
| **网格搜索** | 支持在回测外运行参数网格扫描 |
| **参数管理** | 策略参数在 `init()` 中定义，优化器自动发现参数 |
| **无内置优化器** | RQAlpha 本身无内置优化引擎，需自行编写脚本 |

### 1.3 JoinQuant 聚宽（云端 · 参数优化服务）

| 维度 | 设计要点 |
|------|---------|
| **云优化服务** | 在线参数优化平台，可视化配置参数范围 |
| **参数类型** | int/float/categorical + 约束条件 |
| **并行搜索** | 云端分布式并行，免费用户有限额 |
| **结果可视化** | 自动生成优化报告：参数重要性排名、等值线图、平行坐标图 |
| **Walk-Forward** | 内置时间序列分割，自动训练/测试集划分 |
| **保存/加载** | 优化结果云端保存，支持版本管理和回放 |

**核心借鉴**：参数重要性排名、平行坐标可视化、云端并行

---

## 二、潜龙优化模块现状分析

### 2.1 文件结构梳理（26个文件）

| 层次 | 文件 | 行数 | 职责 | 评分 |
|:----:|:----|:----:|:-----|:----:|
| **核心引擎** | `scripts/optimizer.py` | 669 | 网格搜索 + Walk-Forward + 过拟合检测，5种策略 | ⭐⭐⭐⭐ |
| **引擎集成** | `backtest_engine.py` | 230行 | Walk-Forward：参数稳定性 + 结论建议 | ⭐⭐⭐⭐ |
| **专项脚本** | `run_param_search.py` | 218 | 12组预设配置在300只上对比 | ⭐⭐⭐ |
| **专项脚本** | `run_full_grid_search.py` | 177 | 6维x144种，F1/F5双信号 | ⭐⭐⭐ |
| **专项脚本** | `run_grid_simple.py` | 112 | 4维x54种，F1双共振 | ⭐⭐⭐ |
| **专项脚本** | `run_final_optimize.py` | 190 | 8种配置+DMI趋势过滤 | ⭐⭐⭐ |
| **专项脚本** | `run_opt_v2.py` | 254 | 多信号+大盘择时+ATR | ⭐⭐⭐ |
| **专项脚本** | `run_stop_grid.py` | 153 | 10种止损方案 | ⭐⭐⭐ |
| **专项脚本** | `run_tp_grid.py` | 120 | 25种止盈回落组合 | ⭐⭐⭐ |
| **专项脚本** | `run_yijiner_grid.py` | 167 | 龙头一进二策略 | ⭐⭐⭐ |
| **专项脚本** | `run_final_tune.py` | 204 | 入场确认+诊断 | ⭐⭐⭐ |
| **专项脚本** | `run_optimal_test.py` | 110 | 回落比例微调 | ⭐⭐ |
| **专项脚本** | `run_opt_demo.py` | 25 | 优化引擎快速验证 | ⭐⭐ |
| **专项脚本** | `scripts/optimize_scalper.py` | 204 | 超短线参数优化 | ⭐⭐⭐ |
| **可视化** | `quant_dashboard.py` | — | 侧边栏有止损/止盈/仓位滑条 | ⭐ |
| **组合优化** | `scripts/portfolio_backtest.py` | 304 | 多股票组合优化 | ⭐⭐⭐ |

### 2.2 核心引擎能力

**scripts/optimizer.py（主力引擎）**:
- 规则：通用网格搜索 + Walk-Forward + 过拟合检测
- 方法：`grid_search()` 暴力网格 → `walk_forward_optimize()` 滚动窗口 → `_check_overfit()` 过拟合检测
- 支持5种策略：macd_cross / grid_trading / ma_condition / bull_line_breakout / dragon_tiger
- 输出：终端表格 + JSON报告

**backtest_engine.py Walk-Forward**:
- 方法：`walk_forward()` 滚动窗口参数稳定性检验
- 特色：`_assess_param_stability()` 参数一致性评分 + `_wf_conclusion()` 自动结论

### 2.3 现有优势

1. **Walk-Forward 两套实现**：通用引擎 + 集成到回测引擎，双保险
2. **过拟合检测完善**：Top1 vs Top10 Sharpe差距 + Walk-Forward训练/测试差距
3. **策略覆盖广**：支持5种内置策略 + 通达信信号策略的参数优化
4. **专项脚本针对性强**：11个专项脚本各自覆盖不同策略的深度搜索
5. **大规模验证**：专项脚本都在 1500-5000 只股票上验证

---

## 三、页面布局与可视化评估

### 3.1 现有优化相关UI

| 页面 | 优化相关功能 |
|:----|:------------|
| `quant_dashboard.py` | 侧边栏有止损/止盈/仓位滑条 + 开始回测按钮 → 手动调参，无自动优化 |
| `dashboard.py` | 无优化功能 |
| `app.py` | 无优化功能 |
| `quant_terminal.py` | 无优化功能 |
| **优化结果可视化** | **完全没有** |

### 3.2 页面布局存在的问题

#### 🔴 问题 1：优化结果完全没有可视化
- `scripts/optimizer.py` 只输出终端表格 + JSON文件
- 没有任何图表展示优化结果：参数重要性排名、等值线图、平行坐标图、Top N参数对比
- 用户只能看数字表格，无法直观理解参数间的交互影响

#### 🔴 问题 2：优化入口在 CLI 不在 UI
- 用户要运行 `python scripts/optimizer.py macd_cross` 或 `python run_full_grid_search.py`
- `quant_dashboard.py` 只有手动调参滑条，没有"自动优化"按钮
- 用户无法在界面上配置参数网格、启动优化

#### 🟡 问题 3：11个专项脚本碎片化
| 脚本 | 策略 | 参数维度 | 优化范围 |
|:----|:----|:--------:|:--------:|
| run_param_search.py | 多策略 | 12组预设 | 300只 |
| run_full_grid_search.py | F1/F5 | 6维x144 | 1500只 |
| run_grid_simple.py | F1 | 4维x54 | 1500只 |
| run_final_optimize.py | F1+DMI | 8种 | 2000只 |
| run_opt_v2.py | 多信号 | 3组 | 2000只 |
| run_stop_grid.py | F1 | 10种 | 2000只 |
| run_tp_grid.py | F1 | 25种 | 2000只 |
| run_yijiner_grid.py | 龙头 | 4维x54 | 2000只 |
| run_final_tune.py | F1 | 3种 | 5000只 |

每个脚本独立维护相似的代码（信号计算/回测循环/指标统计），修改一下逻辑要改11个文件。

#### 🟡 问题 4：参数配置不统一
`run_*` 脚本的参数硬编码在代码中：
```python
# run_param_search.py 第10行
PARAM_GRID = [
    ("baseline", 5, -0.05, 0.3, 2.0, 0.05, 0.10, 5),
    ("T10", 10, -0.05, 0.3, 2.0, 0.05, 0.10, 5),
    ...
]
```
无法通过配置文件或命令行传入参数网格。

---

## 四、对比 TOP3 平台

| 评价维度 | 潜龙当前 | QuantConnect | RiceQuant | JoinQuant |
|:---------|:--------:|:-----------:|:---------:|:--------:|
| 网格搜索 | ✅ | ✅ | ⚠️ 需自行编写 | ✅ |
| Walk-Forward | ✅ 双实现 | ✅ RollForward | ❌ | ✅ 内置 |
| 贝叶斯优化 | ❌ | ✅ BayesianSearch | ❌ | ❌ |
| 随机搜索 | ❌ | ✅ RandomSearch | ❌ | ❌ |
| 参数约束 | ✅ | ✅ | ❌ | ✅ |
| 并行计算 | ⚠️ ProcessPool | ✅ 云端并行 | ❌ | ✅ 云端 |
| 参数重要性排名 | ❌ | ❌ | ❌ | ✅ |
| 等值线图 | ❌ | ❌ | ❌ | ✅ |
| 平行坐标图 | ❌ | ❌ | ❌ | ✅ |
| 结果保存/加载 | ✅ JSON文件 | ✅ JSON | ❌ | ✅ 云端 |
| 优化UI入口 | ❌ CLI-only | ✅ 云端Web UI | ❌ | ✅ 云端Web UI |
| 参数配置文件 | ❌ 硬编码 | ✅ YAML | ✅ mod_config | ✅ 界面 |

**综合评分**：3.0/5.0

---

## 五、改进计划

### Phase 1：优化结果可视化（P0，2天）

1. **在 quant_dashboard.py 增加优化Tab**：
   - 参数重要性排名条形图
   - 参数与目标函数的热力图（二维切片）
   - Top N 参数组合对比表
   - Walk-Forward 各窗口的Sharpe对比图

2. **增加"自动优化"按钮**：用户在UI上选择参数范围 → 启动优化 → 实时显示进度

### Phase 2：引擎升级（P1，3天）

3. **优化引擎支持 YAML 配置**：
   ```yaml
   optimizer:
     method: grid_search  # grid / random / bayesian
     max_evals: 1000
     target: sharpe
     params:
       stop_loss: [-0.03, -0.05, -0.08]
       take_profit: [0.05, 0.07, 0.10]
       hold_days: [1, 2, 3, 5]
   ```

4. **废弃 11 个重复脚本**：统一为 config 驱动的优化器
5. **增加并行优化**：多进程并行搜索，进度条反馈

### Phase 3：搜索算法扩展（P2，3天）

6. **增加随机搜索 RandomSearch** — 参数空间大时比网格搜索更高效
7. **增加贝叶斯优化 BayesianOptimization** — 用高斯过程代理模型加速搜索
8. **遗传算法 GA Optimizer** — 高维参数空间的进化搜索

---

## 六、总结评分

| 评分维度 | 当前评分 | 目标评分 | 差距 |
|:---------|:-------:|:-------:|:----:|
| 网格搜索能力 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ✅ 够用 |
| Walk-Forward | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ✅ 双实现 |
| 结果可视化 | ⭐ | ⭐⭐⭐⭐⭐ | 🔴 最大短板 |
| UI入口 | ⭐(CLI) | ⭐⭐⭐⭐ | 🔴 无Web UI |
| 资源配置 | ⭐(硬编码) | ⭐⭐⭐⭐ | 🟡 需YAML化 |
| 搜索算法多样性 | ⭐(仅网格) | ⭐⭐⭐⭐ | 🟡 需增加 |
| 并行计算 | ⭐⭐(多进程) | ⭐⭐⭐⭐ | 🟡 可优化 |
| 脚本架构 | ⭐⭐(11重复) | ⭐⭐⭐⭐⭐ | 🔴 P1 |

**综合评分**：3.0/5.0

**核心结论**：优化引擎逻辑扎实（网格搜索+Walk-Forward+过拟合检测），但 **完全没有可视化呈现优化结果**，这是与聚宽最大的差距。聚宽的参数重要性排名、等值线图、平行坐标图在潜龙上全部不存在。用户只能在终端看数字表格。

---

*报告生成时间：2026-06-07 22:32*
*评审人：量化策略师 / 评审专家*
