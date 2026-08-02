快照: 2026-07-07 审计修复前
修改清单:
  P0 卡#1: live_trader.py — 崩溃恢复持久化
  P0 卡#2: paper_engine.py — 接入 decision_adapter
  P0 卡#2: sector_limit.py — 30%→25%对齐E372
  P1 卡#3: lgbm_weight.py — 训练加验证+原子写
  P1 卡#3: xgb_factor_weight.py — 训练加验证+原子写
  P1 卡#3: train_catboost.py — 训练加验证+原子写
  P1 卡#4: lgbm_strategy.py — 加 model_path 参数
  P1 卡#4: generate_signal_table.py — 删 os.rename 竞态

恢复: 从 backups/daily_20260706/ 或 backups/snap_20260706_*/ 取原始文件
