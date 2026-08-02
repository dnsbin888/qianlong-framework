import shutil, os, json
from datetime import datetime

DST = r"D:\quant_framework\backups\snap_20260719_FINAL"
os.makedirs(DST, exist_ok=True)

files = [
    # Core
    r"D:\quant_framework\paper_engine.py",
    r"D:\quant_web\generate_signal_table.py",
    r"D:\quant_web\data_loader.py",
    r"D:\quant_framework\live_trader.py",
    r"D:\quant_web\app.py",
    # Models
    r"D:\quant_framework\optuna_tune_xgb.py",
    r"D:\quant_framework\optuna_tune_lgbm.py",
    r"D:\quant_framework\xgb_factor_weight.py",
    r"D:\quant_framework\lgbm_strategy.py",
    r"D:\quant_framework\lgbm_weight.py",
    r"D:\quant_framework\purged_cv.py",
    r"D:\quant_framework\full_market_ic.py",
    # Frontend
    r"D:\quant_web\templates\ml_signals.html",
    r"D:\quant_web\templates\terminal.html",
    r"D:\quant_web\templates\factor_health.html",
    r"D:\quant_web\static\js\terminal_v2.js",
    r"D:\quant_web\static\js\signal_card.js",
    # Strategy
    r"D:\quant_framework\reversal_strategy.py",
    r"D:\quant_framework\signals\reversal\realtime.py",
    r"D:\quant_framework\signals\daban\realtime.py",
    r"D:\quant_framework\signals\daban\weights.py",
    r"D:\quant_framework\baostock_sync.py",
    # Rules
    r"D:\quant_framework\src\quant_framework\execution\rules\daily_limits.py",
    r"D:\quant_framework\src\quant_framework\execution\rules\engine.py",
    # Config
    r"D:\quant_framework\signal_config.json",
    r"D:\quant_framework\trade_config_master.json",
    r"D:\quant_framework\factor_registry.json",
    # Models
    r"D:\quant_framework\lgbm_model_stock.pkl",
    r"D:\quant_framework\xgb_model.json",
    r"D:\quant_framework\ridge_model.pkl",
    # Data
    r"D:\quant_web\data\signal_table.json",
    r"D:\quant_web\data\auto_trade_plan.json",
    r"D:\quant_web\data\qmt_trade_config.json",
    r"D:\quant_web\data\lgbm_importance.json",
    r"D:\quant_web\data\xgb_importance.json",
]

copied = 0
for f in files:
    if os.path.exists(f):
        dst = os.path.join(DST, os.path.basename(f))
        shutil.copy2(f, dst)
        copied += 1
        print(f"  {os.path.basename(f)}")
    else:
        print(f"  SKIP: {os.path.basename(f)}")

# Manifest
with open(os.path.join(DST, "MANIFEST.txt"), "w", encoding="utf-8") as mf:
    mf.write(f"Backup: {datetime.now()}\nFiles: {copied}\n")
    for f in files:
        mf.write(f"  {f}\n")

print(f"\nDone: {DST} ({copied} files)")
