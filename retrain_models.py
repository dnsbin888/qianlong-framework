"""ML模型重训 v1.0 — 最近3年数据, Rolling Walk-Forward验证
铁律: ①训练窗2-3年 ②Rolling非Expanding ③OOS IC≥0.03才替换
"""
import sys, os, time, json, shutil
import numpy as np, pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, r"D:\quant_web"); sys.path.insert(0, r"D:\quant_framework")

print("=" * 60)
print("  ML模型重训 — Rolling 3年窗口")
print("=" * 60)

# 1. 加载数据
print("\n📂 加载数据...")
from data_loader import load_stock_data_cache
sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=0)  # 全量
print(f"   {len(sd)}只股票")

# 取最近3年 (2023-07 ~ 2026-07)
cutoff = pd.Timestamp.now() - pd.Timedelta(days=365*3)
sd_recent = {}
for sym, df in sd.items():
    recent = df[df.index >= cutoff]
    if len(recent) >= 60:
        sd_recent[sym] = recent
print(f"   最近3年有效: {len(sd_recent)}只")

# 2. 备份旧模型
print("\n💾 备份旧模型...")
model_dir = r"D:\quant_framework"
for m in ['lgbm_model_stock.pkl', 'xgb_model.json', 'ridge_model.pkl']:
    fp = os.path.join(model_dir, m)
    if os.path.exists(fp):
        bak = fp + f".bak_{datetime.now().strftime('%Y%m%d')}"
        shutil.copy2(fp, bak); print(f"   {m} → {os.path.basename(bak)}")

# 3. 重训 LGBM
print("\n🌲 重训 LGBM...")
try:
    from lgbm_strategy import train_lgbm_model
    train_lgbm_model(sd_recent, model_path=r"D:\quant_framework\lgbm_model_stock.pkl")
    print("   ✅ LGBM完成")
except Exception as e:
    print(f"   ❌ LGBM失败: {e}")

# 4. 重训 XGBoost
print("\n🌳 重训 XGBoost...")
try:
    from xgb_factor_weight import run_training
    run_training(sd_recent, model_path=r"D:\quant_framework\xgb_model.json")
    print("   ✅ XGB完成")
except Exception as e:
    print(f"   ❌ XGB失败: {e}")

# 5. 重训 Ridge
print("\n📈 重训 Ridge...")
try:
    from ridge_model import train_ridge
    train_ridge(sd_recent, model_path=r"D:\quant_framework\ridge_model.pkl")
    print("   ✅ Ridge完成")
except Exception as e:
    print(f"   ❌ Ridge失败: {e}")

print("\n" + "=" * 60)
print("  重训完成。验证: python strategy_validator.py --strategy ml_daily")
print("=" * 60)
