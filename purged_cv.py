"""Purged Cross-Validation v1.0 — 防标签重叠泄露
================================================
对标: Prado "Advances in Financial ML" Ch7 + sklearn兼容

问题: 5日forward标签 → train最后5天与val第1天重叠
解决: purge(清洗重叠期) + embargo(禁入期)

用法:
  from purged_cv import purged_walk_forward, stock_group_kfold
  for train_idx, val_idx in purged_walk_forward(dates, n_splits=3, purge_days=5):
      model.fit(X[train_idx], y[train_idx])
"""

import numpy as np
from collections import Counter


def purged_walk_forward(dates: list[str], n_splits: int = 3,
                        purge_days: int = 5, embargo_days: int = 0,
                        min_train_size: int = 50) -> list[tuple]:
    """Purged Walk-Forward 交叉验证 (Prado Ch7)

    原理:
      train: [T0 .......... T_purge)  ← purge后截断
      purge: [T_purge ... T_test_start)  ← 丢弃(标签可能重叠)
      embargo: [T_test_end ... T_embargo_end) ← 禁入(样本自相关)
      val:   [T_test_start ... T_test_end]

    Args:
        dates: 已排序的日期列表 ("2023-01-03", "2023-01-04", ...)
        n_splits: 折数
        purge_days: 清洗天数 (≥标签窗口, 默认5)
        embargo_days: 禁入天数 (防止序列相关, 默认0)
        min_train_size: 最少训练样本

    Returns:
        [(train_indices, val_indices), ...] 按时间递增
    """
    n = len(dates)
    if n < n_splits * min_train_size:
        raise ValueError(f"样本不足: {n} < {n_splits} × {min_train_size}")

    # 等分日期索引
    test_size = n // (n_splits + 1)
    splits = []

    for i in range(n_splits):
        # val 区间: 最后一份
        val_end = n - i * test_size
        val_start = max(0, val_end - test_size)

        # train 区间: val_start 之前, 减去purge
        train_end = val_start - purge_days
        if train_end < min_train_size:
            continue

        train_indices = list(range(0, train_end))
        val_indices = list(range(val_start, val_end))

        # embargo: val后N天不进入下一轮train (下一轮自然隔开, 但显式处理更清晰)
        if embargo_days > 0 and i < n_splits - 1:
            val_indices = val_indices[:len(val_indices) - embargo_days]

        splits.append((train_indices, val_indices))

    return splits


def stock_group_kfold(stock_ids: list[str], n_splits: int = 5,
                      random_state: int = 42) -> list[tuple]:
    """按股票分组的 K-Fold (防同股不同时泄露)

    同股票在不同日期的样本 → 分到同一折
    → 确保 train 里见过的股票, val 里不会出现

    Args:
        stock_ids: 每行对应的股票代码 (与X的样本顺序一致)
        n_splits: 折数
        random_state: 随机种子 (组间随机, 非shuffle样本)

    Returns:
        [(train_indices, val_indices), ...]
    """
    # 去重股票 → 随机分组
    unique_stocks = sorted(set(stock_ids))
    rng = np.random.RandomState(random_state)
    rng.shuffle(unique_stocks)

    # 分n_splits组
    group_size = len(unique_stocks) // n_splits
    stock_to_fold = {}
    for fold_idx in range(n_splits):
        start = fold_idx * group_size
        end = start + group_size if fold_idx < n_splits - 1 else len(unique_stocks)
        for stock in unique_stocks[start:end]:
            stock_to_fold[stock] = fold_idx

    # 将样本映射到折
    splits = []
    for fold_idx in range(n_splits):
        train_idx = [i for i, sid in enumerate(stock_ids)
                     if stock_to_fold.get(sid) != fold_idx]
        val_idx = [i for i, sid in enumerate(stock_ids)
                   if stock_to_fold.get(sid) == fold_idx]
        if len(train_idx) >= 50 and len(val_idx) >= 10:
            splits.append((train_idx, val_idx))
        else:
            splits.append(([], []))  # 跳过大小的折

    return splits


def get_stock_ids_from_rows(rows: list[dict]) -> list[str]:
    """从因子行提取股票代码 (供 stock_group_kfold 使用)"""
    return [r.get("symbol", str(i)) for i, r in enumerate(rows)]


# ═══════ 测试 ═══════
if __name__ == "__main__":
    # Test 1: purged_walk_forward
    dates = [f"2023-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
    print(f"日期: {len(dates)} 天")
    splits = purged_walk_forward(dates, n_splits=3, purge_days=5)
    for i, (train, val) in enumerate(splits):
        t_dates = [dates[j] for j in train]
        v_dates = [dates[j] for j in val]
        print(f"  Fold {i}: train={t_dates[0]}~{t_dates[-1]} ({len(train)}d) "
              f"→ val={v_dates[0]}~{v_dates[-1]} ({len(val)}d)")

    # Test 2: stock_group_kfold
    stocks = [f"{600000 + i % 100:06d}" for i in range(1000)]
    splits2 = stock_group_kfold(stocks, n_splits=5)
    for i, (train, val) in enumerate(splits2):
        train_stocks = set(stocks[j] for j in train)
        val_stocks = set(stocks[j] for j in val)
        overlap = train_stocks & val_stocks
        print(f"  GroupFold {i}: train={len(train)} val={len(val)} overlap={len(overlap)}")
