"""查 sh688120 实时价格"""
import json, os

# 1. QMT缓存
qmt = r"D:\quant_framework\quote_cache.json"
if os.path.exists(qmt):
    with open(qmt, "r") as f:
        data = json.load(f)
    print(f"QMT缓存: {data.get('count')}只, {data.get('updated')}")
    d = data.get("data", {})
    # 找 688120
    for code in ["688120.SH", "688120.SZ"]:
        if code in d:
            t = d[code]
            print(f"  QMT {code}: price={t.get('price')}, chg={t.get('change_pct')}%")
            break
    else:
        print("  QMT缓存中未找到 sh688120")

# 2. 模拟盘持仓
try:
    pa = r"D:\quant_framework\paper_account.json"
    with open(pa, "r") as f:
        pa_data = json.load(f)
    for sym, pos in pa_data.get("positions", {}).items():
        if "688120" in sym:
            print(f"\n持仓: {sym} 成本={pos.get('avg_cost')} 现价={pos.get('last_price')}")
except Exception as e:
    print(f"模拟盘读取失败: {e}")

# 3. 新浪实时价格
try:
    import urllib.request
    url = "https://hq.sinajs.cn/list=sh688120"
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    resp = urllib.request.urlopen(req, timeout=5)
    text = resp.read().decode("gbk")
    parts = text.split(",")
    if len(parts) > 3:
        name = parts[0].split('"')[1] if '"' in parts[0] else "?"
        price = float(parts[3])
        prev = float(parts[2])
        chg = round((price - prev) / prev * 100, 2)
        print(f"\n新浪实时: {name} ¥{price} 昨收={prev} 涨跌={chg}%")
except Exception as e:
    print(f"\n新浪失败: {e}")
