import json
st = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))

print("打板/反转信号详情:\n")
for r in st:
    if r.get('consensus','') in ('打板', '反转'):
        print(f"{r['symbol']:12s} {r.get('name',''):8s} {r['consensus']:4s} "
              f"combined={r.get('combined_score',0):.0f} "
              f"auto={r.get('auto_enabled', False)} "
              f"n_models={r.get('n_models',0)} "
              f"L={r.get('lgbm_score','-')} X={r.get('xgb_score','-')} R={r.get('ridge_score','-')}")

print(f"\n打板/反转 auto_enabled: {sum(1 for r in st if r.get('consensus','') in ('打板','反转') and r.get('auto_enabled'))}/{sum(1 for r in st if r.get('consensus','') in ('打板','反转'))}")

# 门槛值
import json as _j
m = _j.load(open(r"D:\quant_framework\trade_config_master.json", encoding="utf-8"))
ad = m.get("auto_trade", {}).get("market_adaptive", {}).get("sideways", {})
cg = m.get("confidence_gate", {})
print(f"\n当前门槛(sideways): ML>{ad.get('min_ml_score',75)} pos≤{ad.get('max_auto_position',10)}%")
print(f"置信度: combined>{cg.get('min_combined_score_for_auto',65)}")
