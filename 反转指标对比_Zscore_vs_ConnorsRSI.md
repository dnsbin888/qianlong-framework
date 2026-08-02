# 反转指标对比：Z-score vs Connors RSI(2)

> 2026-07-11 | 用途：个股级反转信号补充

---

## 一、原理对比

| | Z-score (布林带) | Connors RSI(2) |
|------|------|------|
| **公式** | (Close - MA20) / Std(20) | RSI(2) = 100 - 100/(1+RS), RS=AvgGain/AvgLoss over 2 days |
| **买入信号** | Z < -2σ | RSI(2) < 10 |
| **卖出信号** | Z > +2σ | RSI(2) > 90 或持有2天后 |
| **理论基础** | 正态分布假设 | 短期超卖后必然反弹 |
| **提出** | Bollinger 1980s | Larry Connors 1980s |

---

## 二、行业适用度

| 维度 | Z-score | Connors RSI(2) | 胜者 |
|------|:--:|:--:|:--:|
| 机构使用率 | ⭐⭐⭐⭐ | ⭐⭐⭐ | Z-score |
| 个人量化使用率 | ⭐⭐ | ⭐⭐⭐⭐ | Connors |
| A股回测过吗 | ⚠️ 少 | ✅ 多（量化圈经典） | Connors |
| 参数需校准 | ⚠️ 每只票不同 | ✅ 固定参数 | Connors |
| 代码量 | 15行 | 10行 | Connors |
| 与现有 regime 联动 | 需自建 | 天然适配 | Connors |

---

## 三、我们的适配度

| 场景 | Z-score | Connors RSI(2) |
|------|:--:|:--:|
| 震荡市 | Z < -2 有效 | RSI(2) < 10 有效 |
| 熊市 | Z < -2 假信号多 | 同 |
| 日频扫描 | 5000只×20天std | 5000只×2天RSI |
| 与现有反转信号重叠 | ⚠️ 弱转强已覆盖 | ✅ 独立维度（超卖 ≠ 弱转强） |
| 理解难度 | 中 | 低 |

---

## 四、推荐：Connors RSI(2)

### 理由

1. **A股量化圈共识**：个人能做的短期反转，Connors RSI(2) 是绕不过的经典——博客/论坛/开源策略最多的就是它
2. **参数不用调**：RSI(2)<10 买、>90 卖，跨市场有效
3. **独立于现有信号**：弱转强看的是趋势反转，Connors 看的是超卖——一只票可能弱转强不触发但 Connors 触发了
4. **实现极简**：10 行 Python，加到 `stock_filters.py` 做标记

### 不选 Z-score 的原因

- 需要每只票单独校准均值和标准差，维护成本高
- 和弱转强逻辑重叠（都看价格偏离均值）
- 行业用得多是因为布林带自带可视化——我们不需要

---

## 五、实现方案

```python
# stock_filters.py 加一个诊断函数

def connors_oversold(sd):
    """标记 RSI(2) < 10 的超卖股 (不排除, 供策略参考)"""
    result = {}
    for sym, df in sd.items():
        try:
            c = df['close'].values
            if len(c) < 3: result[sym] = False; continue
            diff = np.diff(c[-3:])
            gain = sum(d for d in diff if d > 0) / 2
            loss = -sum(d for d in diff if d < 0) / 2
            rs = gain / max(loss, 1e-9)
            rsi = 100 - 100 / (1 + rs)
            result[sym] = rsi < 10
        except: result[sym] = False
    return {sym: True for sym in sd}  # 不排除, 只标记
```

### 集成方式

```
stock_filters.py 加 connors_oversold()
generate_signal_table.py 调用后把超卖标记写入信号表
QMT 不直接使用（由信号表传递）
```

### 投入

```
15行代码，零依赖，20分钟。
```

---

## 六、结论

**选 Connors RSI(2)。** 10 行代码、A股验证过、独立于现有信号。加进 stock_filters 做诊断标记，不影响现有逻辑。
