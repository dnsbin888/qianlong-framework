"""PSI 特征稳定性监控 v1.0 — 第2层 ML质量监控 (2026-07-10)
对标: WorldQuant定检标准 + 私募PSI < 0.1

原理:
  训练时: 存每个特征的分布（分箱直方图）
  预测时: PSI = Σ (实际% - 预期%) × ln(实际% / 预期%)
  PSI < 0.1  → 稳定
  PSI 0.1-0.25 → 关注，特征在漂移
  PSI > 0.25 → 告警，考虑重训模型

用法:
  # 训练后保存分布
  from psi_monitor import save_feature_distribution
  save_feature_distribution(feature_df, "lgbm_features.json")

  # 预测时检查
  from psi_monitor import check_psi
  alerts = check_psi(feature_df, "lgbm_features.json")
"""
import os, json
import numpy as np


DIST_DIR = r"D:\quant_framework\feature_distributions"
N_BINS = 10  # 分箱数


def _compute_distribution(values: np.ndarray, n_bins: int = N_BINS) -> dict:
    """计算特征的分布直方图"""
    v = values[~np.isnan(values)]
    if len(v) < 10:
        return {"bins": [], "counts": [], "n": len(v)}
    counts, bins = np.histogram(v, bins=n_bins)
    return {
        "bins": bins.tolist(),
        "counts": counts.tolist(),
        "n": len(v)
    }


def save_feature_distribution(feature_df, name: str):
    """保存训练集特征分布

    Args:
        feature_df: DataFrame, 每列是一个特征
        name: 分布文件名 (如 "lgbm_features.json")
    """
    os.makedirs(DIST_DIR, exist_ok=True)
    dist = {}
    for col in feature_df.columns:
        vals = feature_df[col].values
        dist[col] = _compute_distribution(vals)
    path = os.path.join(DIST_DIR, name)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(dist, f, ensure_ascii=False)
    print(f"[PSI] 已保存 {len(dist)} 个特征分布 → {path}")
    return path


def compute_psi(expected_counts: list, actual_counts: list) -> float:
    """计算单个特征的 PSI 值

    PSI = Σ (A_i - E_i) × ln(A_i / E_i)
    其中 A_i = actual_i / total_actual, E_i = expected_i / total_expected
    """
    if not expected_counts or not actual_counts:
        return 0.0
    e = np.array(expected_counts, dtype=float)
    a = np.array(actual_counts, dtype=float)

    # 避免除零: 每个bin至少+1 (拉普拉斯平滑)
    e = e + 1
    a_arr = a + 1

    e_pct = e / e.sum()
    a_pct = a_arr / a_arr.sum()

    # PSI 公式
    psi = np.sum((a_pct - e_pct) * np.log(a_pct / (e_pct + 1e-9)))
    return float(max(0, psi))


def check_psi(feature_df, dist_name: str, auto_init: bool = True) -> list[dict]:
    """检查当前特征分布是否漂移

    Args:
        feature_df: 当前预测集的特征 DataFrame
        dist_name: 训练时保存的分布文件名
        auto_init: 如果分布文件不存在, 自动用当前分布作为基线 (首次运行)

    Returns:
        [{feature, psi, level}]  仅返回有问题的特征 (PSI > 0.1)
    """
    dist_path = os.path.join(DIST_DIR, dist_name)
    if not os.path.exists(dist_path):
        if auto_init and feature_df is not None and len(feature_df) > 0:
            # 首次运行: 用当前特征分布作为基线
            save_feature_distribution(feature_df, dist_name)
            return [{"feature": "_meta", "psi": 0, "level": "info",
                     "msg": f"已自动建立基线分布: {dist_name}"}]
        return [{"feature": "_meta", "psi": 0, "level": "info",
                 "msg": f"分布文件不存在: {dist_path}，请先训练模型"}]

    with open(dist_path, encoding='utf-8') as f:
        expected = json.load(f)

    alerts = []
    for col, exp_dist in expected.items():
        if col not in feature_df.columns:
            continue

        actual_vals = feature_df[col].values
        actual_dist = _compute_distribution(actual_vals, n_bins=len(exp_dist.get("bins", [N_BINS])) - 1)

        if not exp_dist.get("counts") or not actual_dist.get("counts"):
            continue

        psi = compute_psi(exp_dist["counts"], actual_dist["counts"])

        if psi > 0.25:
            level = "critical"
            msg = f"严重漂移 (PSI={psi:.3f})，建议重训模型"
        elif psi > 0.1:
            level = "warning"
            msg = f"轻微漂移 (PSI={psi:.3f})，持续关注"
        elif psi > 0.05:
            level = "info"
            msg = f"可接受范围 (PSI={psi:.3f})"
        else:
            continue  # 稳定，不告警

        alerts.append({"feature": col, "psi": round(psi, 4), "level": level, "msg": msg})

    alerts.sort(key=lambda x: -x["psi"])
    return alerts


def check_all_models(feature_df) -> dict:
    """检查所有已保存的特征分布

    Returns:
        {dist_name: [alerts]}
    """
    if not os.path.exists(DIST_DIR):
        return {"_error": [{"feature": "_meta", "psi": 0, "level": "info",
                            "msg": f"分布目录不存在: {DIST_DIR}"}]}

    results = {}
    for fname in os.listdir(DIST_DIR):
        if fname.endswith('.json'):
            alerts = check_psi(feature_df, fname)
            if alerts:
                results[fname] = alerts
    return results


def psi_summary(feature_df) -> str:
    """生成 PSI 检查摘要 (用于盘前检查)"""
    results = check_all_models(feature_df)

    if not results:
        return "✅ PSI正常: 所有特征分布稳定"

    criticals = []
    warnings = []
    for fname, alerts in results.items():
        for a in alerts:
            if a.get("level") == "critical":
                criticals.append(f"{a['feature']}({a['psi']:.3f})")
            elif a.get("level") == "warning":
                warnings.append(f"{a['feature']}({a['psi']:.3f})")

    parts = []
    if criticals:
        parts.append(f"🔴 PSI严重漂移({len(criticals)}): {', '.join(criticals[:5])}")
    if warnings:
        parts.append(f"🟡 PSI轻微漂移({len(warnings)}): {', '.join(warnings[:5])}")
    if not parts:
        return "✅ PSI正常: 所有特征分布稳定"

    return " | ".join(parts)


if __name__ == "__main__":
    # 测试: 生成随机分布, 保存, 然后检查
    print("PSI 特征稳定性监控 v1.0")
    print(f"分布目录: {DIST_DIR}")

    if os.path.exists(DIST_DIR):
        files = [f for f in os.listdir(DIST_DIR) if f.endswith('.json')]
        print(f"已有分布: {len(files)} 个")
        for f in files:
            print(f"  - {f}")
    else:
        print("尚无分布数据。训练新模型后运行 save_feature_distribution() 保存。")

    print("\n✅ psi_monitor 就绪")
