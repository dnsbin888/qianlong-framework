"""分类模型 v1.0 — 预测涨跌方向 (替代CSRank排名)
标签: 5日收益>0→1(涨), ≤0→0(跌)
用法: python train_classifier.py
"""
import sys, os, pickle, numpy as np, pandas as pd
from collections import defaultdict

sys.path.insert(0, r"D:\quant_web"); sys.path.insert(0, r"D:\quant_framework")

print("📂 加载数据...")
from data_loader import load_stock_data_cache
sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=500)
_skip = ('sh000','sh11','sh12','sh13','sh14','sh15','sh2','sh5','sz399','sz11','sz12','sz13','sz15','sz16','sz18','sz5','bj')
sd = {k:v for k,v in sd.items() if not k.startswith(_skip)}
print(f"  {len(sd)}只股票")

from factor_registry import get_all_compute_fns
compute_fns = get_all_compute_fns()
factor_names = sorted(compute_fns.keys())
print(f"  {len(factor_names)}个因子")

# 构建训练集: 按日期分组收集 (跟CSRank一样)
dates = sorted(set(str(d)[:10] for df in sd.values() for d in df.index))[-250:]
dates = dates[20:]
date_samples = defaultdict(list)
import random; random.seed(42)

for d in dates[-90:]:
    syms = list(sd.keys()); random.shuffle(syms)
    for sym in syms[:500]:
        df = sd.get(sym)
        if df is None or len(df) < 21: continue
        try: idx = list(df.index).index(next(ts for ts in df.index if str(ts)[:10] >= d))
        except StopIteration: continue
        if idx < 20 or idx + 5 >= len(df): continue
        past = df.iloc[max(0,idx-60):idx+1]
        fwd_ret = (float(df.iloc[idx+5]["close"])-float(df.iloc[idx]["close"]))/max(float(df.iloc[idx]["close"]),0.01)
        label = 1 if fwd_ret > 0 else 0  # 涨=1, 跌=0
        row = []
        for fn in factor_names:
            func = compute_fns.get(fn)
            if not func: row.append(0.0); continue
            try: val = func(past)
            except: val = None
            row.append(float(np.clip(val,-100,100)) if val is not None and np.isfinite(val) else 0.0)
        date_samples[d].append((row, label))

X, y = [], []
for d, samples in date_samples.items():
    if len(samples) < 30: continue
    for row, label in samples:
        X.append(row); y.append(label)

X, y = np.array(X), np.array(y)
print(f"\n📊 {len(X)}样本, 正样本{y.sum()/len(y)*100:.1f}%")

# 时序分割: 前80%训练, 后20%验证
split = int(len(X)*0.8)
X_train, X_val = X[:split], X[split:]
y_train, y_val = y[:split], y[split:]

# 训练 LGBM 分类器
import lightgbm as lgb
pos = int(y_train.sum()); neg = len(y_train)-pos
print(f"  训练: {len(X_train)}样本 正{pos}({pos/len(y_train)*100:.1f}%) 负{neg}")
model = lgb.LGBMClassifier(
    n_estimators=100, max_depth=5, learning_rate=0.05,
    subsample=0.8, colsample_bytree=0.8,
    class_weight='balanced', random_state=42, verbose=-1)
model.fit(X_train, y_train)

# 验证
from sklearn.metrics import accuracy_score
y_pred = model.predict(X_val)
y_prob = model.predict_proba(X_val)[:,1]
acc = accuracy_score(y_val, y_pred)
print(f"  验证准确率: {acc*100:.1f}%")

# 只看高置信度预测
high_conf = y_prob > 0.55
if high_conf.sum() > 0:
    hc_acc = accuracy_score(y_val[high_conf], y_pred[high_conf])
    print(f"  高置信度(>0.55)准确率: {hc_acc*100:.1f}% (样本{high_conf.sum()})")

# 保存
path = r"D:\quant_framework\classifier_model.pkl"
imp = [{"factor":fn, "importance":round(float(model.feature_importances_[i]),4)} for i,fn in enumerate(factor_names)]
imp.sort(key=lambda x:-x["importance"])
pickle.dump({"model":model, "factors":factor_names, "importance":imp}, open(path,"wb"))
print(f"\n📄 模型已保存: {path}")
print(f"  Top5特征: {[i['factor'] for i in imp[:5]]}")
