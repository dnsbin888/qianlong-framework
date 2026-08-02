"""A1弱转强改前改后对比"""
import json
st = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
wts = [r for r in st if r.get('consensus','') == '反转']
print(f"A1弱转强信号: {len(wts)}只\n")

for r in wts[:5]:
    print(f"  {r['symbol']:12s} {r.get('name',''):8s} "
          f"hold={r.get('hold_days','?')}天 "
          f"止损={r.get('stop_loss','?')} "
          f"止盈={r.get('take_profit','?')} "
          f"auto={r.get('auto_enabled',False)}")

# 对比改前: hold=2, 止损=close*(1-0.03~0.05), 止盈=close*(1+0.075~0.125)
print("\n改前→改后:")
print("  hold_days: 2 → 5")
print("  追踪止盈: 无 → TP1=+5%保本, TP2=+10%跟2%回落")
print("  止损: ATR自适应 (不变)")
