"""从同花顺stockname文件提取A股代码→名称映射"""
import re, json, os

# Read all stockname files with GBK encoding
names = {}
stockname_dir = r"D:\同花顺软件\同花顺\stockname"

for fname in os.listdir(stockname_dir):
    if not fname.endswith(".txt"):
        continue
    path = os.path.join(stockname_dir, fname)
    try:
        with open(path, "r", encoding="gbk", errors="replace") as f:
            for line in f:
                m = re.match(r"^(\d{6})=([^|]+)", line.strip())
                if m:
                    code = m.group(1)
                    name = m.group(2).strip()
                    if code[0] in "6030248":
                        names[code] = name
    except Exception:
        continue

print(f"Loaded {len(names)} A-share stock names")
for sym in ["600000", "000001", "000002", "300033", "688001", "002415"]:
    print(f"  {sym} = {names.get(sym, 'unknown')}")

with open(r"d:\quant_framework\stock_names.json", "w", encoding="utf-8") as f:
    json.dump(names, f, ensure_ascii=False)
print(f"Saved to stock_names.json ({len(names)} names)")
