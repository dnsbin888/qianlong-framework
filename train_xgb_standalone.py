"""独立XGBoost训练 — 不依赖Flask，直接读数据训练"""
import sys
sys.path.insert(0, r"d:\quant_framework")
sys.path.insert(0, r"d:\quant_web")

import os, time, pickle
import numpy as np
import pandas as pd

print("[1/4] 加载 STOCK_DATA (parquet)...")
t0 = time.time()
parquet_path = r"d:\quant_web\stock_data.parquet"
df_all = pd.read_parquet(parquet_path, columns=['symbol','date','close','volume'])
stock_data = {}
for sym, group_df in df_all.groupby('symbol'):
    stock_data[sym] = group_df.drop(columns=['symbol']).set_index('date').sort_index()
print(f"  {len(stock_data)} 只股票, {time.time()-t0:.1f}s")

print("[2/4] 构建训练数据 (60天 × N只股票)...")
ACTIVE_FACTORS = ["trend_score","defensive_v2","qmt_composite","chase_v2",
                  "chip_v2","momentum_score","bull_line","fund_v2"]
TRAIN_DAYS, FORWARD_DAYS, MIN_SAMPLES = 60, 5, 5000
TOP_PCT = 0.2

# 收集所有日期
all_dates = set()
for sym, df in stock_data.items():
    if df is None or len(df) < 60:
        continue
    for d in df.index:
        ds = str(d)[:10]
        all_dates.add(ds)
dates = sorted(all_dates)
print(f"  日期范围: {dates[0]} ~ {dates[-1]}, 共{len(dates)}天")

start_date = dates[max(0, len(dates)-TRAIN_DAYS-30)]
end_date = dates[-1]

all_X, all_y = [], []
processed = 0

for i, day in enumerate(dates[:-FORWARD_DAYS]):
    if day < start_date:
        continue
    target_day = dates[i + FORWARD_DAYS]

    day_features, day_returns = [], []
    for sym, df in stock_data.items():
        if df is None or len(df) < 60:
            continue
        try:
            day_mask = df.index.astype(str).str.startswith(day)
            tgt_mask = df.index.astype(str).str.startswith(target_day)
            if not day_mask.any() or not tgt_mask.any():
                continue

            day_row = df.loc[day_mask].iloc[-1]
            tgt_row = df.loc[tgt_mask].iloc[-1]
            close = float(day_row['close'])
            if close <= 0:
                continue

            close_hist = df['close'].values
            vol_hist = df['volume'].values

            # 找当日在数组中的位置
            day_idx = -1
            for j in range(len(df)):
                if str(df.index[j])[:10] == day:
                    day_idx = j
                    break
            if day_idx < 20:
                continue

            n = day_idx + 1

            # 8因子代理
            ma20 = np.mean(close_hist[max(0,n-20):n])
            trend = (close - ma20) / ma20 if ma20 > 0 else 0

            rets_20 = np.diff(close_hist[max(0,n-21):n]) / (close_hist[max(0,n-21):n-1] + 1e-9)
            defensive = -float(np.std(rets_20)) if len(rets_20) > 1 else 0

            vol_ma5 = np.mean(vol_hist[max(0,n-5):n])
            vol_ma20 = np.mean(vol_hist[max(0,n-20):n])
            vol_ratio = vol_ma5 / (vol_ma20 + 1e-9)

            close_5d = close_hist[max(0,n-6)]
            chase = (close - close_5d) / (close_5d + 1e-9)

            close_10d = close_hist[max(0,n-11)]
            momentum = (close - close_10d) / (close_10d + 1e-9)

            ma60 = np.mean(close_hist[max(0,n-60):n])
            bull = (close - ma60) / ma60 if ma60 > 0 else 0
            fund = -vol_ratio  # 资金流出

            features = [trend, defensive, vol_ratio*0.5+chase*0.5,
                       chase, vol_ratio, momentum, bull, fund]

            fwd_close = float(tgt_row['close'])
            fwd_ret = (fwd_close - close) / close

            day_features.append(features)
            day_returns.append(fwd_ret)
        except Exception:
            continue

    if len(day_features) < 50:
        continue

    # z-score
    arr = np.array(day_features, dtype=np.float64)
    z_arr = np.zeros_like(arr)
    for j in range(arr.shape[1]):
        col = arr[:, j]
        mu, sigma = np.nanmean(col), np.nanstd(col)
        if sigma > 0:
            z_arr[:, j] = (col - mu) / sigma

    ret_arr = np.array(day_returns)
    threshold = np.percentile(ret_arr, 100*(1-TOP_PCT))
    targets = (ret_arr >= threshold).astype(int)

    all_X.extend(z_arr.tolist())
    all_y.extend(targets.tolist())
    processed += 1

    if processed % 10 == 0:
        print(f"  {processed}天, {len(all_X)}样本...")

print(f"  完成: {processed}天, {len(all_X)}样本")

if len(all_X) < MIN_SAMPLES:
    print(f"  ❌ 样本不足 ({len(all_X)} < {MIN_SAMPLES})")
    sys.exit(1)

X_train = np.array(all_X, dtype=np.float32)
y_train = np.array(all_y, dtype=np.int32)

print(f"[3/4] 训练 XGBoost...")
import xgboost as xgb
pos, neg = int(y_train.sum()), len(y_train)-int(y_train.sum())
print(f"  正样本(top20%): {pos}, 负样本: {neg}, 比例: {neg/max(pos,1):.1f}:1")

model = xgb.XGBClassifier(
    n_estimators=100, max_depth=4, learning_rate=0.05,
    subsample=0.8, colsample_bytree=0.8,
    scale_pos_weight=neg/max(pos,1),
    objective='binary:logistic', eval_metric='auc',
    random_state=42, n_jobs=1,
)
model.fit(X_train, y_train, verbose=True)

print(f"[4/4] 保存模型...")
model_path = r"d:\quant_framework\xgb_model.json"
model.save_model(model_path)
print(f"  ✅ 模型已保存: {model_path}")
print(f"  样本: {len(X_train)}, 特征: {len(ACTIVE_FACTORS)}")
print(f"  因子: {ACTIVE_FACTORS}")

# 验证加载
model2 = xgb.XGBClassifier()
model2.load_model(model_path)
print("  ✅ 模型加载验证通过")
