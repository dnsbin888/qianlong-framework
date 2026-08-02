import shutil, os
DST = r"D:\quant_framework\backups\snap_20260719_final"
os.makedirs(DST, exist_ok=True)

files = [
    r"D:\quant_framework\paper_engine.py",
    r"D:\quant_web\generate_signal_table.py",
    r"D:\quant_web\data_loader.py",
    r"D:\quant_framework\live_trader.py",
    r"D:\quant_web\app.py",
    r"D:\quant_framework\optuna_tune_xgb.py",
    r"D:\quant_framework\optuna_tune_lgbm.py",
    r"D:\quant_framework\xgb_factor_weight.py",
    r"D:\quant_framework\lgbm_strategy.py",
    r"D:\quant_framework\purged_cv.py",
    r"D:\quant_framework\baostock_sync.py",
    r"D:\quant_web\templates\ml_signals.html",
    r"D:\quant_framework\reversal_strategy.py",
    r"D:\quant_framework\signals\reversal\realtime.py",
    r"D:\quant_framework\signals\daban\realtime.py",
    r"D:\quant_framework\signals\daban\weights.py",
    r"D:\quant_framework\signal_config.json",
    r"D:\quant_framework\trade_config_master.json",
    r"D:\quant_framework\lgbm_model_stock.pkl",
    r"D:\quant_framework\xgb_model.json",
    r"D:\quant_framework\ridge_model.pkl",
]

for f in files:
    if os.path.exists(f):
        dst = os.path.join(DST, os.path.basename(f))
        shutil.copy2(f, dst)
        print(f"  {os.path.basename(f)}")

print(f"\nDone: {DST}")
