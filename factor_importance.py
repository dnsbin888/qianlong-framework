"""因子重要性追踪 v1.0 - 2026-07-09
从LGBM模型提取特征重要性, 追踪因子权重变化
"""
import os, sys, json, pickle
from datetime import datetime

sys.path.insert(0, r"D:\quant_web")

OUTPUT = r"D:\quant_web\data\factor_importance.json"
HISTORY = r"D:\quant_web\data\factor_importance_history.json"


def extract(model_path=None, factor_names=None):
    """提取LGBM特征重要性"""
    result = {"date": datetime.now().strftime("%Y-%m-%d"), "importance": [], "warnings": []}

    # 1. 加载模型
    path = model_path or r"D:\quant_framework\lgbm_model.pkl"
    if not os.path.exists(path):
        result["warnings"].append("模型文件不存在")
        return result

    try:
        with open(path, "rb") as f:
            data = pickle.load(f)
        model = data.get("model")
        factors = factor_names or data.get("factors", [])
        if model is None:
            result["warnings"].append("模型对象为空")
            return result
    except Exception as e:
        result["warnings"].append(f"模型加载失败: {e}")
        return result

    # 2. 提取重要性
    try:
        imp = model.feature_importances_
        if imp is None or len(imp) == 0:
            result["warnings"].append("模型无特征重要性")
            return result

        # 映射到因子名称
        total_imp = sum(imp)
        items = []
        for i, val in enumerate(imp):
            name = factors[i] if i < len(factors) else f"f{i}"
            pct = round(float(val) / max(total_imp, 1e-9) * 100, 1)
            items.append({"rank": i + 1, "name": name, "importance": pct})

        items.sort(key=lambda x: -x["importance"])
        for i, item in enumerate(items):
            item["rank"] = i + 1
        result["importance"] = items[:15]  # Top 15
        result["total_factors"] = len(imp)
    except Exception as e:
        result["warnings"].append(f"重要性提取失败: {e}")
        return result

    # 3. 对比历史, 检测变化
    try:
        if os.path.exists(OUTPUT):
            old = json.load(open(OUTPUT, encoding="utf-8"))
            old_map = {f["name"]: f["importance"] for f in old.get("importance", [])}
            for item in result["importance"]:
                name = item["name"]
                if name in old_map:
                    delta = item["importance"] - old_map[name]
                    item["delta"] = round(delta, 1)
                    if abs(delta) > 5:
                        direction = "↑" if delta > 0 else "↓"
                        result["warnings"].append(f"{name} 重要性变化 {direction}{abs(delta):.1f}%")
    except: pass

    # 4. 保存当前
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 5. 追加历史
    try:
        hist = []
        if os.path.exists(HISTORY):
            hist = json.load(open(HISTORY, encoding="utf-8"))
        hist.append({"date": result["date"], "top5": [item["name"] for item in result["importance"][:5]]})
        if len(hist) > 60:
            hist = hist[-60:]
        with open(HISTORY, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
    except: pass

    print(f"[FactorImp] {len(result['importance'])}因子, {len(result['warnings'])}告警 → {OUTPUT}")
    return result


if __name__ == "__main__":
    r = extract()
    for item in r.get("importance", [])[:5]:
        delta_str = f" ({item['delta']:+.1f}%)" if "delta" in item else ""
        print(f"  {item['rank']}. {item['name']}: {item['importance']:.1f}%{delta_str}")
    for w in r.get("warnings", []):
        print(f"  ⚠️ {w}")
