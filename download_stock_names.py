"""预下载 A 股股票名称映射表 (代码 -> 名称)"""
import json
import os

OUTPUT = r"d:\quant_framework\stock_names.json"

print("正在从 akshare 下载 A 股全量股票列表...")
try:
    import akshare as ak
    df = ak.stock_info_a_code_name()
    names = {}
    for _, row in df.iterrows():
        code = str(row["code"])
        name = str(row["name"])
        names[code] = name
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(names, f, ensure_ascii=False)
    print(f"成功! {len(names)} 只股票名称已缓存到 {OUTPUT}")
    # 展示几个示例
    samples = [("000001", "平安银行"), ("600519", "贵州茅台"), ("000002", "万科A"), ("300750", "宁德时代")]
    for code, expected in samples:
        actual = names.get(code, "?")
        match = "✓" if actual == expected else f"(expected {expected})"
        print(f"  {code} = {actual} {match}")
except Exception as e:
    print(f"下载失败: {e}")
    print("K线图功能仍可正常使用 (仅不支持中文名称搜索)")
