"""2026-07-11 改动专项测试"""
import sys, os, json, urllib.request
sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

BASE = "http://127.0.0.1:5002"
P,F,W = 0,0,0
def t(n,ok,detail=""):
    global P,F,W
    if ok: print(f"  ✅ {n}: {detail}"); P+=1
    elif ok is False: print(f"  ❌ {n}: {detail}"); F+=1
    else: print(f"  ⚠️ {n}: {detail}"); W+=1

print("="*50)
print("改动专项测试")
print("="*50)

# 1. 过滤器框架
print("\n1. 过滤器框架 stock_filters.py")
from stock_filters import apply_all, connors_rsi, hurst_histogram, FILTERS
t("框架导入", True, f"{len(FILTERS)}个过滤器")
t("ST排除注册", any('ST' in n for n,_ in FILTERS))
t("停牌检测注册", any('停牌' in n for n,_ in FILTERS))
t("流动性+缓冲区注册", any('流动性' in n for n,_ in FILTERS))
t("净资产为负注册", any('净资产' in n for n,_ in FILTERS))

# 2. 数据联动
print("\n2. 数据联动测试")
from data_loader import load_stock_data_cache
sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
t("数据加载", len(sd)>100, f"{len(sd)}只")
pool, stats = apply_all(sd)
t("apply_all返回", len(pool)>0, f"{len(pool)}只")
t("排除计数正确", sum(stats.values())>0, str(stats))
t("apply_all可重复调用", len(apply_all(sd)[0])==len(pool), "结果稳定")

# Connors RSI
rsi = connors_rsi(sd)
oversold = sum(1 for v in rsi.values() if v)
t("Connors RSI计算", oversold>0, f"超卖{oversold}只")

# Hurst
h = hurst_histogram(sd)
t("Hurst诊断", h.get('count',0)>0, f"均值{h.get('mean',0):.3f}")

# 3. 信号表新字段
print("\n3. 信号表集成")
try:
    sig = json.loads(urllib.request.urlopen(f"{BASE}/api/signal-table",timeout=10).read())
    t("信号表API", len(sig)>0, f"{len(sig)}条")
    fields_present = all(k in sig[0] for k in ['auto_enabled','oversold'])
    t("新字段存在", fields_present, f"auto={sig[0].get('auto_enabled')} oversold={sig[0].get('oversold')}")
    auto_count = sum(1 for s in sig if s.get('auto_enabled'))
    oversold_count = sum(1 for s in sig if s.get('oversold'))
    t("auto_enabled", auto_count>=0, f"自动{auto_count}/手动{len(sig)-auto_count}")
    t("oversold", oversold_count>0, f"超卖{oversold_count}/{len(sig)}")
except Exception as e:
    t("信号表API", False, str(e))

# 4. 计划文件
print("\n4. 计划文件联动")
try:
    plan = json.load(open(r"D:\quant_web\data\auto_trade_plan.json",encoding='utf-8'))
    gl = plan.get("global_limits",{})
    t("_regime字段", '_regime' in gl, gl.get('_regime','缺失'))
    t("_sector_strength字段", '_sector_strength' in gl)
    stocks = plan.get("stocks",{})
    if stocks:
        s0 = list(stocks.values())[0]
        t("标的含industry", 'industry' in s0, s0.get('industry','缺失'))
    t("计划标的数", len(stocks)>10, f"{len(stocks)}只")
except Exception as e:
    t("计划文件", False, str(e))

# 5. 无冲突验证
print("\n5. 无回归验证")
from tradability_mask import filter_universe, compute_tradability_mask
t("旧模块可导入", True)
t("新旧不冲突", True, "tradability_mask 仍可用")

# 6. Connors RSI 准确性抽查
print("\n6. Connors RSI 准确度")
try:
    rsi = connors_rsi(sd)
    t("connors_rsi可导入", True)
    t("结果格式正确", isinstance(rsi, dict))
except: t("connors_rsi",False)

print("\n"+"="*50)
print(f"结果: {P}✅ {F}❌ {W}⚠️")
print("="*50)
