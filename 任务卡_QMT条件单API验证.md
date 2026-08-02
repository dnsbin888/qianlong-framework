# 任务卡: QMT条件单 API 验证

**日期**: 2026-07-18  
**优先级**: P0 (阻塞双刀打板极速方案)  
**状态**: 待执行 (周一开盘前)

## 背景

双刀打板极速方案的核心依赖: QMT Python API 能否设置条件单(C++层监控, <1ms触发)。

- 一封 → QMT条件单直接监控触板 (<1ms)
- 二封 → Python炸板检测后设条件单等回封 (<1ms)

**如果条件单 API 不存在**, 则退回 `subscribe_quote` 推流方案 (~15ms, 仍优于当前0-60s轮询)。

## 执行步骤

### 方式A: QMT策略内运行探针 (交易日)

1. 打开 QMT 客户端 (周一9:00前可登录)
2. 策略编辑器 → 新建策略
3. 粘贴 `D:\quant_framework\qmt_strategies\test_condition_order.py`
4. 主图K线: 任意股票, 1分钟周期
5. 运行 → 复制日志全文

### 方式B: 直接读 xtquant 源码 (周末可行)

xtquant 安装路径:
- 模拟盘: `D:\国金QMT交易端模拟\bin.x64\Lib\site-packages\xtquant\`
- 实盘: `D:\国金证券QMT交易端\bin.x64\Lib\site-packages\xtquant\`

关键文件:
- `xttrader.py` — 交易接口, 找条件单相关方法
- `functions.py` — 策略函数(passorder等), 找隐藏参数
- `xtconstant.py` — 常量定义, 找条件单相关常量
- `contextinfo.py` — ContextInfo, 找条件单方法

### 方式C: QMT 帮助文档

QMT 客户端内 → 帮助 → API文档 → 搜索"条件单"

## 判定标准

| 发现 | 结论 | 后续 |
|------|------|------|
| 找到条件单设置API | ✅ 可用 | 按双刀条件单方案施工 |
| passorder有其他模式参数 | ⚠️ 半可用 | 测试参数功能 |
| 完全找不到 | ❌ 不可用 | 退回 subscribe_quote 方案 |

## 兜底方案

如果条件单 API 不存在, 不阻塞施工:

```
一封: subscribe_quote(全市场) → 筛选涨幅>9.5% → tick回调 → 轻量确认 → passorder (~15ms)
二封: subscribe_quote(3-8候选) → tick回调 → 炸板检测+confirm_board → passorder (~15ms)
```

比当前 handlebar 1分钟轮询提升 4000 倍, 完全够用。

## 关联

- [[策略ABC路线图]] — A1弱转强已交付, A2打板双刀待施工
- [[待完成任务卡]] — 基建缺口
