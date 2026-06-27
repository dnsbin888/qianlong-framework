"""测试实时行情"""
import sys
sys.path.insert(0, r'd:\quant_framework')
from realtime_quotes import fetch_realtime_quotes

r = fetch_realtime_quotes(['sh600519', 'sz000001', 'sh600036'])
print(f"Status: {r.get('status')}")
print(f"Count: {r.get('count')}")
print(f"Trading: {r.get('trading')}")
data = r.get('data', {})
for code, q in list(data.items())[:3]:
    print(f"  {code}: {q.get('name','?')} price={q.get('close',0)} change={q.get('change_pct',0)} source={q.get('data_source','?')}")
