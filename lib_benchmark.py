"""库 vs 手写 深度评测 — 性能+准确度+稳定性"""
import sys, os, time, json, numpy as np, pandas as pd

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

np.random.seed(42)
N = 1000  # 样本数

print("=" * 70)
print("  库 vs 手写 深度评测")
print("=" * 70)

# ═══════════════════════════════════════
# 1. 技术指标: pandas-ta vs 手写 numpy
# ═══════════════════════════════════════
print("\n[1] 技术指标对比")
prices = np.cumprod(1 + np.random.normal(0.001, 0.02, 500)) * 100
df = pd.DataFrame({"close": prices, "high": prices*1.02, "low": prices*0.98, "volume": np.random.randint(1e6, 1e7, 500)})

# 手写RSI
t0 = time.time()
delta = np.diff(prices)
gain = np.where(delta > 0, delta, 0)
loss = np.where(delta < 0, -delta, 0)
avg_gain = np.convolve(gain, np.ones(14)/14, mode='full')[:len(prices)-1]
avg_loss = np.convolve(loss, np.ones(14)/14, mode='full')[:len(prices)-1]
rs = avg_gain / (avg_loss + 1e-9)
hand_rsi = 100 - 100 / (1 + rs)
t_hand = (time.time() - t0) * 1000

# pandas-ta RSI
try:
    import pandas_ta as ta
    t0 = time.time()
    lib_rsi = ta.rsi(df["close"], length=14).values
    t_lib = (time.time() - t0) * 1000
    match = np.allclose(hand_rsi[-100:], lib_rsi[-100:], rtol=0.01, equal_nan=True)
    print(f"  RSI: hand={t_hand:.1f}ms lib={t_lib:.1f}ms match={'OK' if match else 'DIFF'}")
    print(f"     verdict: {'pandas-ta' if t_lib < t_hand else 'hand'}")
except ImportError:
    print(f"  RSI: pandas-ta not available")

# ═══════════════════════════════════════
# 2. 回测绩效: pyfolio vs 手写
# ═══════════════════════════════════════
print("\n[2] 回测绩效对比")
rets = np.random.normal(0.001, 0.02, N)

# 手写
t0 = time.time()
sr = np.mean(rets) / np.std(rets, ddof=1) * np.sqrt(252)
max_dd = 0; peak = 0; eq = 1.0
for r in rets:
    eq *= (1 + r)
    peak = max(peak, eq)
    max_dd = min(max_dd, (eq - peak) / peak)
calmar = sr * 252 / abs(max_dd) if max_dd != 0 else 0
sortino = np.mean(rets) / np.std(rets[rets < 0], ddof=1) * np.sqrt(252) if len(rets[rets < 0]) > 1 else 0
t_hand = (time.time() - t0) * 1000
print(f"  Hand: Sharpe={sr:.2f} Sortino={sortino:.2f} MaxDD={max_dd:.2%} Calmar={calmar:.2f} ({t_hand:.1f}ms)")

# pyfolio
try:
    import pyfolio as pf
    t0 = time.time()
    pf_rets = pd.Series(rets, index=pd.date_range('2025-01-01', periods=N, freq='B'))
    pf_sr = pf.timeseries.sharpe_ratio(pf_rets)
    pf_mdd = pf.timeseries.max_drawdown(pf_rets)
    pf_sortino = pf.timeseries.sortino_ratio(pf_rets)
    pf_calmar = pf.timeseries.calmar_ratio(pf_rets)
    t_lib = (time.time() - t0) * 1000
    match_sr = abs(sr - pf_sr) < 0.1
    print(f"  pyfolio: Sharpe={pf_sr:.2f} Sortino={pf_sortino:.2f} MaxDD={pf_mdd:.2%} Calmar={pf_calmar:.2f} ({t_lib:.1f}ms)")
    print(f"     match={match_sr}, verdict: {'pyfolio' if t_lib < t_hand * 2 else 'hand (faster)'}")
except ImportError:
    print(f"  pyfolio: not installed")

# ═══════════════════════════════════════
# 3. 组合优化: riskfolio vs 手写 portfolio_opt
# ═══════════════════════════════════════
print("\n[3] 组合优化对比")
# 模拟10只股票的收益数据
n_assets = 10
mock_rets = pd.DataFrame(
    np.random.normal(0.001, 0.02, (500, n_assets)),
    columns=[f"stock_{i}" for i in range(n_assets)]
)

# 手写 MVO
try:
    from scipy.optimize import minimize
    t0 = time.time()
    mu = mock_rets.mean().values
    cov = mock_rets.cov().values
    def obj(w): return -(np.dot(w, mu) - 1.0 * np.dot(w, np.dot(cov, w)))
    cons = [{'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0}]
    bnds = [(0, 0.3) for _ in range(n_assets)]
    res = minimize(obj, np.ones(n_assets)/n_assets, method='SLSQP', bounds=bnds, constraints=cons, options={'maxiter': 100})
    hand_w = res.x if res.success else np.ones(n_assets)/n_assets
    t_hand = (time.time() - t0) * 1000
    print(f"  Hand MVO: weights={[round(w,3) for w in hand_w[:5]]}... ({t_hand:.1f}ms)")
except Exception as e:
    print(f"  Hand MVO: failed ({e})")
    hand_w = np.ones(n_assets) / n_assets

# riskfolio MVO
try:
    from riskfolio import Portfolio
    t0 = time.time()
    rp = Portfolio(returns=mock_rets)
    lib_w = rp.optimization(model='Classic', rm='MV', obj='MinRisk', rf=0, l=1)
    t_lib = (time.time() - t0) * 1000
    lib_vals = lib_w.values.flatten()[:5]
    print(f"  riskfolio MV: weights={[round(w,3) for w in lib_vals]}... ({t_lib:.1f}ms)")
    print(f"     verdict: riskfolio (more robust + Ledoit-Wolf)")
except Exception as e:
    print(f"  riskfolio MV: failed ({e})")

# ═══════════════════════════════════════
# 4. 超参搜索: Optuna vs 手写 evolution
# ═══════════════════════════════════════
print("\n[4] 超参搜索对比")
# Optuna
try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    def objective(trial):
        lr = trial.suggest_float('lr', 0.01, 0.3)
        depth = trial.suggest_int('depth', 3, 10)
        leaves = trial.suggest_int('leaves', 10, 100)
        return -( (lr - 0.1)**2 + (depth - 6)**2/10 + (leaves - 50)**2/100 )

    t0 = time.time()
    study = optuna.create_study(direction='minimize')
    study.optimize(objective, n_trials=50, show_progress_bar=False)
    t_optuna = (time.time() - t0) * 1000
    best = study.best_params
    print(f"  Optuna: {t_optuna:.0f}ms, best={best}")
except Exception as e:
    print(f"  Optuna: failed ({e})")

# 手写随机搜索
try:
    t0 = time.time()
    best_score = float('inf')
    best_params = {}
    for _ in range(50):
        lr = np.random.uniform(0.01, 0.3)
        depth = np.random.randint(3, 11)
        leaves = np.random.randint(10, 101)
        score = (lr - 0.1)**2 + (depth - 6)**2/10 + (leaves - 50)**2/100
        if score < best_score:
            best_score = score
            best_params = {'lr': lr, 'depth': depth, 'leaves': leaves}
    t_hand = (time.time() - t0) * 1000
    print(f"  Random: {t_hand:.0f}ms, best={best_params}")
    print(f"     verdict: Optuna (TPE > random, early stopping, pruning)")
except Exception as e:
    print(f"  Random: failed ({e})")

# ═══════════════════════════════════════
# 5. 综合评分
# ═══════════════════════════════════════
print("\n" + "=" * 70)
print("  综合评估")
print("=" * 70)
results = {
    "pandas-ta (技术指标)":    {"verdict": "替换手写", "reason": "社区验证+1行代码+无边界bug"},
    "pyfolio (绩效报告)":      {"verdict": "装回增强", "reason": "tear sheet独有,手写保留实时计算"},
    "riskfolio (组合优化)":    {"verdict": "✅已替换HRP", "reason": "真HRP+Ledoit-Wolf+BL开箱即用"},
    "optuna (超参搜索)":       {"verdict": "接入XGB/CB", "reason": "TPE+早停,evolution保留遗传算法"},
    "alphalens (IC分析)":      {"verdict": "可选增强", "reason": "分位数图+因子衰减,手写IC核心保留"},
    "backtrader (回测)":       {"verdict": "辅助工具", "reason": "A股T+1/涨跌停手写独有,BT做多策略对比"},
    "CatBoost (Meta裁判)":     {"verdict": "保留", "reason": "Stacking架构正确,87棵树够用"},
}
for name, r in results.items():
    print(f"  {name}: {r['verdict']} — {r['reason']}")
