"""验证信号表数据新鲜度"""
import json, urllib.request

print("="*50)
print("  数据新鲜度验证")
print("="*50)

# 1. 信号表生成时间
t = json.load(open(r"D:\quant_web\data\signal_table.json", encoding='utf-8'))
gen_time = t[0].get('_generated_at', '?')
s0 = t[0]
print(f"\n[1] 信号表")
print(f"  生成时间: {gen_time}")
print(f"  首条: {s0['symbol']} close={s0['close']}")

# 2. QMT 最新日线 vs 信号表close
print(f"\n[2] QMT 最新日线 (最近3天)")
try:
    from xtquant import xtdata
    for sym in ['688120.SH','603986.SH','605358.SH']:
        d = xtdata.get_market_data(stock_list=[sym], period='1d', count=3)
        if d and 'close' in d:
            df = d['close']
            dates = list(df.columns)
            vals = [float(df.iloc[0, i]) for i in range(len(dates))]
            print(f"  {sym}: {list(zip(dates, vals))}")
except Exception as e:
    print(f"  ❌ {e}")

# 3. API返回的current_price
print(f"\n[3] API信号表现价")
r = json.loads(urllib.request.urlopen('http://127.0.0.1:5002/api/signal-table',timeout=5).read())
for s in r[:3]:
    print(f"  {s['symbol']} close={s['close']} current={s.get('current_price')}")
