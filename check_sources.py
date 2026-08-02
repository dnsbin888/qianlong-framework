import json
st = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
print(f"总信号: {len(st)}\n")

# 来源分布
srcs = {}
for r in st:
    cons = r.get('consensus', '?')
    srcs[cons] = srcs.get(cons, 0) + 1
print("共识字段分布:")
for k, v in sorted(srcs.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}只")

# 打板/反转
daban = [r for r in st if r.get('consensus','') == '打板']
rev = [r for r in st if r.get('consensus','') == '反转']
print(f"\n打板标志: {len(daban)}只")
print(f"反转标志: {len(rev)}只")

# 查specific信号
for r in st:
    if r.get('consensus','') in ('打板', '反转'):
        print(f"\n{r['symbol']} {r.get('name','')} consensus={r['consensus']} signal={r.get('signal','')} combined={r.get('combined_score',0)}")
