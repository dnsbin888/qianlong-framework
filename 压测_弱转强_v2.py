"""弱转强 A1 补充边界测试"""
import sys
sys.path.insert(0, r"D:\quant_framework")
from signals.reversal.realtime import confirm_weak_to_strong as _cfm

tests = [
    # === 边界 ===
    ("边界: 价格=开盘价", {'lastPrice':10.1,'bidVol':20000,'askVol':10000,'volume':3000000}, 10.0, 10.1, "≥50"),
    ("边界: L2=1.0",      {'lastPrice':10.2,'bidVol':10000,'askVol':10000,'volume':3000000}, 10.0, 10.1, "≥50"),
    ("边界: L2=0.99",     {'lastPrice':10.2,'bidVol':9900,'askVol':10000,'volume':3000000}, 10.0, 10.1, "0"),
    ("边界: 高开9%",       {'lastPrice':10.2,'bidVol':20000,'askVol':10000,'volume':3000000}, 10.0, 10.9, "≥50"),
    ("边界: 最小竞价量",    {'lastPrice':10.2,'bidVol':20000,'askVol':10000,'volume':100}, 10.0, 10.1, "≥50"),
    ("边界: price=0.99*open",{'lastPrice':10.098,'bidVol':20000,'askVol':10000,'volume':3000000}, 10.0, 10.2, "0"),

    # === 异常 ===
    ("异常: lastPrice=0",  {'lastPrice':0,'bidVol':20000,'askVol':10000}, 10.0, 10.1, "0"),
    ("异常: prev_close=0", {'lastPrice':10.2,'bidVol':20000,'askVol':10000}, 0, 10.1, "0"),
    ("异常: 空tick",       {}, 10.0, 10.1, "0"),
    ("异常: bidVol缺失",   {'lastPrice':10.2,'askVol':10000}, 10.0, 10.1, "0"),
    ("异常: 负价",         {'lastPrice':-10.2,'bidVol':20000,'askVol':10000}, 10.0, 10.1, "0"),

    # === 复合 ===
    ("复合: 假诱多+低量",  {'lastPrice':10.2,'bidVol':5000,'askVol':10000,'gap_delta':0.01,'gap_vol_delta':-0.1,'volume':3000000}, 10.0, 10.1, "0"),
    ("复合: 竞价弱+高开",  {'lastPrice':10.2,'bidVol':20000,'askVol':10000,'volume':500000}, 10.0, 10.3, "≥50"),
    ("复合: 最优场景",     {'lastPrice':10.3,'bidVol':50000,'askVol':10000,'gap_delta':0.02,'gap_vol_delta':0.1,'volume':8000000}, 10.0, 10.15, "≥50"),
]

print("补充边界+异常测试\n")
ok = 0; fail = 0
for label, tick, prev, op, expected in tests:
    score = _cfm("sh600000", tick, prev, op)
    passed = (expected=="0" and score==0) or (expected=="≥50" and score>=50)
    if passed: ok += 1
    else: fail += 1
    print(f"  {'✅' if passed else '❌'} {label:25s} score={score:3d} (预期{expected})")

print(f"\n通过: {ok}/{ok+fail}")
print(f"{'✅ 边界完整' if fail==0 else '❌ 有缺口'}")
