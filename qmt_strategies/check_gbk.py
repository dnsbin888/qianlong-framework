"""检查所有模块的GBK兼容性"""
for mod in ["common","init_body","signal_body","helper_body","strategy_chase","strategy_breakthrough"]:
    path = f"D:\\quant_framework\\qmt_strategies\\{mod}.py"
    data = open(path, "rb").read()
    try:
        data.decode("gbk")
        ok = "OK"
    except Exception as e:
        ok = f"FAIL: {e}"
    print(f"  {mod}.py: {ok}")
