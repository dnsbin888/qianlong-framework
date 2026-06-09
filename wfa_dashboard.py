"""WFA 分析看板 — Walk-Forward Analysis 可视化。

运行: streamlit run wfa_dashboard.py --server.port 8503
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import json, os, sys, subprocess
from datetime import datetime

st.set_page_config(page_title="WFA分析", layout="wide")

# ═══════════════════ CSS ═══════════════════
st.markdown("""
<style>
    .stApp { background: #0d1117; }
    .metric-card {
        background: #161b22; border: 1px solid #30363d; border-radius: 8px;
        padding: 14px; text-align: center;
    }
    .metric-card .value { font-size: 24px; font-weight: 700; color: #58a6ff; }
    .metric-card .label { font-size: 11px; color: #8b949e; margin-top: 2px; }
</style>
""", unsafe_allow_html=True)

st.markdown("### 🔬 Walk-Forward 分析")

# ═══════════════════ SIDEBAR ═══════════════════
with st.sidebar:
    st.markdown("### WFA 参数")
    wfa_stock = st.text_input("股票代码", "600519")
    wfa_train = st.number_input("训练窗口 (交易日)", 60, 504, 252, 21)
    wfa_test = st.number_input("测试窗口 (交易日)", 21, 252, 63, 21)
    wfa_folds = st.slider("折叠数", 2, 8, 4)
    wfa_pool = st.slider("股票池大小", 30, 200, 80)

    st.markdown("**参数网格**")
    stop_losses = st.multiselect("止损%", [-0.03,-0.05,-0.07,-0.10], default=[-0.05,-0.07], key="wsl")
    take_profits = st.multiselect("止盈%", [0.05,0.08,0.10,0.15], default=[0.05,0.08], key="wtp")

    if st.button("开始 WFA 分析", type="primary", use_container_width=True):
        with st.spinner("WFA运行中..."):
            params = json.dumps({"stop_loss": stop_losses, "take_profit": take_profits})
            cmd = [sys.executable, r"d:\quant_framework\run_wfa.py",
                   "--stock", wfa_stock, "--train", str(wfa_train), "--test", str(wfa_test),
                   "--n-folds", str(wfa_folds), "--pool-size", str(wfa_pool),
                   "--param-grid", params,
                   "--output", r"d:\quant_framework\wfa_result.json"]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if r.returncode == 0:
                    st.success("WFA 完成")
                else:
                    st.error(f"WFA 失败 (exit {r.returncode})")
                    if r.stderr: st.text(r.stderr[-300:])
            except subprocess.TimeoutExpired:
                st.error("WFA 超时")
            except Exception as e:
                st.error(str(e))

# ═══════════════════ MAIN ═══════════════════
wfa_path = r"d:\quant_framework\wfa_result.json"
if not os.path.exists(wfa_path):
    st.info("尚未运行 WFA。请在左侧配置参数后点击「开始 WFA 分析」。")
    st.stop()

try:
    with open(wfa_path, "r", encoding="utf-8") as f:
        wfa = json.load(f)
except Exception:
    st.error("WFA 结果文件损坏，请重新运行。")
    st.stop()

if "error" in wfa:
    st.error(f"上次 WFA 失败: {wfa['error']}")
    st.stop()

folds = wfa.get("folds", [])
summary = wfa.get("summary", {})
params = wfa.get("params", {})

st.caption(f"{wfa.get('stock','?')} · {params.get('n_folds','?')} folds · train={params.get('train_days','?')}d · test={params.get('test_days','?')}d · {params.get('elapsed_seconds','?')}s")

if not folds:
    st.warning("WFA 结果为空。")
    st.stop()

# ═══════════════════ 指标卡 ═══════════════════
c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(f'<div class="metric-card"><div class="value">{summary.get("avg_test_sharpe",0):.2f}</div><div class="label">平均测试Sharpe</div></div>', unsafe_allow_html=True)
with c2:
    st.markdown(f'<div class="metric-card"><div class="value">{summary.get("avg_sharpe_decay",0):.2f}</div><div class="label">Sharpe衰减</div></div>', unsafe_allow_html=True)
with c3:
    stab = summary.get("param_stability", {})
    st.markdown(f'<div class="metric-card"><div class="value">{stab.get("overall_grade","?")}</div><div class="label">参数稳定性</div></div>', unsafe_allow_html=True)
with c4:
    st.markdown(f'<div class="metric-card"><div class="value">{len(folds)}</div><div class="label">完成折叠</div></div>', unsafe_allow_html=True)

# ═══════════════════ 图表 ═══════════════════
col1, col2 = st.columns(2)

with col1:
    st.subheader("训练 vs 测试 Sharpe")
    fold_labels = [f"Fold {f['fold']}" for f in folds]
    train_s = [f.get("train_sharpe", 0) for f in folds]
    test_s = [f.get("test_sharpe", 0) for f in folds]

    fig1 = go.Figure()
    fig1.add_trace(go.Bar(name="训练", x=fold_labels, y=train_s, marker_color="#58a6ff",
                           text=[f"{v:.2f}" for v in train_s], textposition="outside"))
    fig1.add_trace(go.Bar(name="测试", x=fold_labels, y=test_s, marker_color="#FF4051",
                           text=[f"{v:.2f}" for v in test_s], textposition="outside"))
    fig1.add_hline(y=0, line_color="#30363d")
    fig1.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        height=350, barmode="group", yaxis=dict(gridcolor="#21262d"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02))
    st.plotly_chart(fig1, width="stretch")

with col2:
    st.subheader("Sharpe 衰减")
    decays = [f.get("sharpe_decay", 0) for f in folds]
    avg_d = summary.get("avg_sharpe_decay", 0)

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=fold_labels, y=decays, mode="lines+markers",
        line=dict(color="#F1A100", width=2), marker=dict(size=10),
        fill="tozeroy", fillcolor="rgba(241,161,0,0.1)"))
    fig2.add_hline(y=0, line_dash="dash", line_color="#30363d")
    fig2.add_hline(y=avg_d, line_dash="dot", line_color="#FF4051",
                   annotation_text=f"均值: {avg_d:.2f}")
    fig2.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        height=350, yaxis=dict(gridcolor="#21262d"))
    st.plotly_chart(fig2, width="stretch")

# ═══════════════════ 折叠详情表 ═══════════════════
st.subheader("各折叠详情")
rows = []
for f in folds:
    bp = f.get("best_params", {})
    rows.append({
        "Fold": f["fold"], "训练期": f.get("train_period",""), "测试期": f.get("test_period",""),
        "最优参数": ", ".join(f"{k}={v}" for k,v in bp.items()),
        "训练Sharpe": f.get("train_sharpe",0), "测试Sharpe": f.get("test_sharpe",0),
        "衰减": f.get("sharpe_decay",0), "测试收益": f"{f.get('test_return',0):.2%}",
    })
st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
    column_config={"训练Sharpe": st.column_config.NumberColumn(format="%.2f"),
                   "测试Sharpe": st.column_config.NumberColumn(format="%.2f"),
                   "衰减": st.column_config.NumberColumn(format="%.2f")})

# ═══════════════════ 结论 ═══════════════════
conclusion = summary.get("conclusion", "")
avg_decay = summary.get("avg_sharpe_decay", 0)
overall = stab.get("overall_score", 0)
is_overfit = abs(avg_decay) > 1.0 or overall < 0.4

st.markdown("---")
if is_overfit:
    st.error(f"⚠️ 过拟合风险: {conclusion}")
else:
    st.success(f"✅ 参数稳健: {conclusion}")

st.caption(f"更新于 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
