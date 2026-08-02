# 任务卡 — QMT快速通道修复

> 创建: 2026-07-08 | 优先级: 🔴 P0 | 状态: 待执行

---

## 背景

- **已验证能力**: 昨天(07-07)测试 QMT 快速通道，`passorder` 正常下单，延迟 <5ms
- **当前问题**: 07-08 QMT 策略启动后 `handlebar` 回调不触发，输出 `[quote]start simulation mode`，行情数据不推
- **影响范围**: 快速通道完全失效，审核通道（POST → Flask）手工测试正常
- **非代码问题**: 策略代码逻辑正确，Flask 端已验证通过（手工 POST 测试成功入库+页面显示）

---

## 根因分析

| 环节 | 状态 | 说明 |
|------|:--:|------|
| QMT 行情连接 | ❌ simulation mode | `handlebar` 依赖实盘行情推送，模拟模式不推 K 线 |
| QMT 交易连接 | ✅ | 账号已登录，昨天 `passorder` 验证通过 |
| 策略代码 | ✅ | `qmt_full_strategy.py` 逻辑正确 |
| Flask 接收 | ✅ | `/api/qmt/signal` 手工测试通过 |
| 页面展示 | ✅ | QMT 信号卡片蓝色边框正常出现 |

**根因**: QMT 客户端行情引擎处于 `simulation mode`，不向策略推送实时 K 线数据，导致 `handlebar(ContextInfo)` 从未被调用。

---

## 修复步骤

### 第一步: 恢复快速通道代码

当前 `qmt_full_strategy.py` 第264-268行快速通道被替换为 `continue`（测试时禁用）。正式上线前需恢复原始逻辑。

文件: `D:\quant_framework\qmt_strategies\qmt_full_strategy.py`

```python
# 当前 (测试版)
# 快速通道已禁用: 仅测试信号推送, 不下单
continue

# 恢复为 (正式版)
if not enabled:
    continue
if signal_name not in signal_set:
    continue
if best_ml < min_ml:
    continue

total_asset = 100000
try:
    acc = ContextInfo.get_account_info(ContextInfo.accID)
    if acc:
        total_asset = acc.get('total_asset', total_asset)
except Exception:
    pass

qty = _calc_shares(pos_pct, price, total_asset)

try:
    passorder(23, 1101, ContextInfo.accID, qmt_code, 0,
              round(price, 2), qty, u"潜龙快速", ql_sym, 2)
    _daily["trades"] += 1
    _daily["pct"] += pos_pct
    print(f"[快速] ✅ {qmt_code} BUY {qty}股@{price:.2f} 仓位{pos_pct}%")
except Exception as e:
    print(f"[快速] ❌ {qmt_code} passorder失败: {e}")
```

### 第二步: 修复 QMT 行情 simulation mode

**排查方向**（按优先级）:

1. **检查 QMT 客户端版本和授权**
   - 确认是否有实盘行情权限（国金证券 QMT 实盘账户需开通 Level-1 行情）
   - 确认行情服务器地址是否正确

2. **QMT 策略配置**
   - 运行品种: 设置为"自定义"
   - 添加至少1只股票作为行情触发器（推荐 `000300.SH`）
   - K线周期: 1分钟

3. **QMT 客户端重装/重置**
   - 退出 QMT
   - 删除 `D:\国金证券QMT交易端\python\` 下旧策略文件
   - 重新打开 QMT，新建策略，粘贴最新代码

4. **联系国金QMT技术支持**
   - 症状: 策略启动后打印 `[quote]start simulation mode`，`handlebar` 不触发
   - 确认: 交易账号已登录，账号有实盘行情权限
   - 询问: 如何切换行情为实盘模式

### 第三步: 端到端验证

恢复后逐项验证:

| # | 验证项 | 通过标准 |
|:--:|------|------|
| 1 | `init()` 启动日志 | 无 `simulation mode` 字样 |
| 2 | `handlebar` 被调用 | 输出面板出现 `[潜龙] bar:` 日志(加临时print) |
| 3 | 模式检测触发 | `[潜龙] 盘中突破 sh600xxx` 等日志 |
| 4 | 审核通道 POST | 首页出现 QMT 信号卡片(蓝色边框) |
| 5 | 快速通道 passorder | `[快速] ✅ xxx.SH BUY` 日志 + QMT 委托记录 |
| 6 | 延迟 <5ms | `passorder` 调用到返回 <5ms |

### 第四步: 安全保护

首次恢复时建议加保护:

```python
# 安全模式: 仅模拟盘账户 (正式上线前移除)
if ContextInfo.accID != '8890695045':
    print(f"[快速] ⚠️ 非授权账户, 跳过下单")
    continue

# 仓位硬上限保护 (正式上线前移除)
if pos_pct > 5:
    print(f"[快速] ⚠️ 仓位{pos_pct}%超测试上限, 跳过")
    continue
```

测试通过后再移除保护。

---

## 依赖关系

```
行情恢复 → handlebar触发 → 模式检测 → 审核通道POST + 快速通道passorder
                                              ↓                    ↓
                                        首页信号卡片           QMT委托成交
```

行情是唯一阻塞点，后续链路已验证。

---

## 验收标准

- [ ] QMT 输出面板不再出现 `start simulation mode`
- [ ] `handlebar` 每1分钟被调用
- [ ] 至少1种信号模式（竞价抢筹/盘中突破/尾盘急拉/打板追封）被触发
- [ ] 首页 `⚡ 盘中信号` 出现 QMT 实时信号卡片
- [ ] `passorder` 成功下单，延迟 <5ms
- [ ] 全链路回归: `generate_signal_table.py` → `auto_trade_plan.json` → QMT 读取 → 检测 → 下单

---

## 关联

- [[session-20260707-summary]] — QMT 快速通道 passorder 接入 (07-07)
- [[architecture-upgrade-guide]] 12.4 — QMT 执行架构
- [[task-master-20260707]] — 任务总书
