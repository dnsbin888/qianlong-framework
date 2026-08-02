# 任务卡：ml_signals.html 空数据修复

> 日期: 2026-07-07 | 优先级: P2 | 状态: 已定位, 明天修

## 问题

`http://localhost:5002/static/ml_signals.html` 显示空数据（"加载中..."后无内容）

## 根因

localStorage 存了旧状态 `ml_andMode=true` + 三模型全选 → AND 过滤只保留 LGBM+XGB+CB 都有分的股票 → 68 条里仅 1 条通过 → type 筛选再卡掉 → 0 条

## 修复

1. 打开页面 → F12 → Application → Local Storage → 删 `ml_andMode`
2. 刷新 → 默认 OR 模式 → 68 条全显示
3. 如不行，`Ctrl+Shift+R` 强制刷新

## 已加的防御

`ml_signals.html` 已加页面加载时自动清除 `ml_andMode`，默认走 OR 模式。
