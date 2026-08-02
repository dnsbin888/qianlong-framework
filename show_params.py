import json
for f,title in [
    (r"D:\quant_framework\trade_config_master.json","参数真相源"),
    (r"D:\quant_framework\strategy_registry.json","策略注册表"),
]:
    d=json.load(open(f,encoding="utf-8"))
    print(f"\n{'='*50}\n  {title}\n{'='*50}")
    print(json.dumps(d,ensure_ascii=False,indent=2))
