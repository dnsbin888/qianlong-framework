"""
QuantDesk Pro v3 — 用户习惯优化版
- 开盘自动扫描，无需手动点击
- 预设模式：保守/标准/激进 一键切换
- 大盘状态条 + 信号总览
- 紧凑布局，信息密度高
"""

import streamlit as st
import sys, os, pickle, json, time, numpy as np, pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, r"d:\quant_framework\src")

st.set_page_config(page_title="QuantDesk Pro", page_icon="📡", layout="wide")

# ═══════════════════════════════ CSS ═══════════════════════════════
st.markdown("""<style>
    html,body,[class*="css"]{font-family:'Inter','Microsoft YaHei',sans-serif}
    .signal-card{background:linear-gradient(135deg,#0f0f23,#1a1a35);border-radius:10px;padding:14px 12px;margin:6px 0;border-left:4px solid #00d2ff;min-height:185px;transition:.2s}
    .signal-card:hover{border-left-color:#00e676;background:#12122a}
    .signal-card.gold{border-left-color:#ffd700}.signal-card.silver{border-left-color:#c0c0c0}.signal-card.bronze{border-left-color:#cd7f32}
    .signal-card .card-name{font-size:1.05rem;font-weight:700;color:#e6f1ff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:2px}
    .signal-card .card-code{font-size:.8rem;color:#64748b;font-weight:500;margin-bottom:6px}
    .score-strong{background:rgba(0,230,118,.15);color:#00e676;padding:3px 10px;border-radius:12px;font-weight:700;font-size:.85rem}
    .score-good{background:rgba(0,210,255,.15);color:#00d2ff;padding:3px 10px;border-radius:12px;font-weight:700;font-size:.85rem}
    .section-title{font-size:1.1rem;font-weight:600;color:#ccd6f6;margin:12px 0 6px 0}
    .stButton>button{border-radius:6px;font-weight:600;transition:.2s;border:none;font-size:.85rem;padding:4px 16px}
    .stButton>button:hover{transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,160,255,.3)}
    .compact-metric label{font-size:.7rem!important;color:#8892b0!important}
    .compact-metric div{font-size:1rem!important}
    div[data-testid="stMetricValue"]{font-size:1rem!important}
    section[data-testid="stSidebar"]{background:#1e2035}
    section[data-testid="stSidebar"] *{color:#ccd6f6!important}
    section[data-testid="stSidebar"] label{color:#8892b0!important}
    section[data-testid="stSidebar"] h2{color:#e6f1ff!important}
    header[data-testid="stHeader"]{display:none!important}
    div[data-testid="stToolbar"]{display:none!important}
    div[data-testid="stDecoration"]{display:none!important}
    #MainMenu{display:none!important}
    footer{display:none!important}
    .stApp{margin-top:-4rem!important}
    .main .block-container{padding-top:0.5rem!important;padding-bottom:1rem!important}
    div[data-testid="stVerticalBlock"]>div+div{margin-top:0.5rem!important}

</style>""", unsafe_allow_html=True)

# ═══════════════════════════════ Constants ═══════════════════════════════
CACHE_FILE = r"d:\quant_framework\cache_ohlcv.pkl"
NAMES_FILE = r"d:\quant_framework\stock_names.json"

@st.cache_data(ttl=300)
def load_data():
    with open(CACHE_FILE, "rb") as f: return pickle.load(f)
@st.cache_data
def load_names():
    if os.path.exists(NAMES_FILE):
        with open(NAMES_FILE,"r",encoding="utf-8") as f: return json.load(f)
    return {}

PRESETS = {
    "🛡️ 保守": {"price": 10, "turnover": 1e8, "tb": 0.7, "add": True},
    "⚖️ 标准": {"price": 5,  "turnover": 5e7, "tb": 0.5, "add": False},
    "🚀 激进": {"price": 3,  "turnover": 2e7, "tb": 0.3, "add": False},
}

# ═══════════════════════════════ Sidebar ═══════════════════════════════
with st.sidebar:
    st.markdown("## 📡 QuantDesk Pro")
    mode = st.radio("预设模式", list(PRESETS.keys()), index=1, horizontal=True)
    preset = PRESETS[mode]

    with st.expander("⚙️ 高级参数", expanded=False):
        auto_refresh = st.checkbox("🔄 自动刷新", value=True, help="每隔N秒重新扫描")
        if auto_refresh:
            refresh_sec = st.slider("刷新间隔(秒)", 5, 120, 30, 5)
        min_price = st.slider("最低股价", 1, 50, preset["price"])
        turnover_opts = {"2000万": 2e7, "5000万": 5e7, "1亿": 1e8, "2亿": 2e8}
        turnover_label = st.selectbox("最低成交额", list(turnover_opts.keys()),
                                      index=1 if preset["turnover"]==5e7 else 0)
        min_turnover = turnover_opts[turnover_label]
        tb_min = st.slider("TB阈值", 0.0, 1.0, preset["tb"], 0.05)
        require_add = st.checkbox("要求加仓共振", preset["add"])
        top_n = st.slider("显示数量", 10, 80, 40, 5)

    st.divider()
    if st.button("🔄 刷新数据", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ═══════════════════════════════ Data ═══════════════════════════════
if not os.path.exists(CACHE_FILE):
    st.error("请先运行 build_cache.py 生成缓存"); st.stop()

data = load_data()
names = load_names()
latest_date = max(d for sd in data.values() for d in sd["dates"] if len(str(d))==8 and d<=20260601)

# Build available trading dates (last 120 days)
all_dates = sorted(set(d for sd in data.values() for d in sd["dates"] if len(str(d))==8 and 20240101<=d<=20260601))
recent_dates = all_dates[-120:]
date_labels = {d: f"{str(d)[:4]}-{str(d)[4:6]}-{str(d)[6:8]}" for d in recent_dates}
date_to_dt = {d: datetime(int(str(d)[:4]), int(str(d)[4:6]), int(str(d)[6:8])) for d in recent_dates}
latest_dt = date_to_dt[recent_dates[-1]]

# ═══════════════════════════════ Scan Engine ═══════════════════════════════
def scan_market(_data, _latest, min_price, min_turnover, tb_min, require_add):
    """全市场扫描，返回 (signals, stats)"""
    signals = []
    stats = {"scanned": 0, "price": 0, "vol": 0, "lu": 0, "tb": 0, "ok": 0}

    for sym, sd in _data.items():
        if _latest not in sd["dates"]: continue
        i = sd["dates"].index(_latest)
        if i < 250: continue
        stats["scanned"] += 1
        p = sd["close"][i]
        if p < min_price: stats["price"] += 1; continue

        vs = sd["volume"][max(0,i-20):i+1]
        avg_v = float(np.mean(vs)) if len(vs) > 0 else 0
        if avg_v * p < min_turnover: stats["vol"] += 1; continue

        if i >= 1 and sd["close"][i-1] > 0:
            if p >= round(sd["close"][i-1]*1.10,2)-0.01: stats["lu"] += 1; continue
            if sd["open"][i] == sd["high"][i] == p: stats["lu"] += 1; continue

        fv = sd["factors"]
        def g(n, d=0.0):
            a = fv.get(n,[]); return float(a[i]) if a is not None and i < len(a) and not np.isnan(float(a[i])) else d
        tb = g("trend_bottom")
        if tb < tb_min: stats["tb"] += 1; continue
        add = g("add_position")
        if require_add and add < 0.5: stats["tb"] += 1; continue

        bp = g("bull_position"); score = tb*60 + add*40 + bp*20
        chg = (p - sd["close"][i-1])/sd["close"][i-1]*100 if i>0 and sd["close"][i-1]>0 else 0
        chg5 = (p - sd["close"][i-5])/sd["close"][i-5]*100 if i>=5 and sd["close"][i-5]>0 else 0

        atr_v = 0.01
        if i >= 14:
            h=np.array(sd["high"][i-14:i+1]); l=np.array(sd["low"][i-14:i+1])
            c_=np.array([sd["close"][i-15]]+sd["close"][i-14:i])
            tr=np.maximum(h-l,np.maximum(np.abs(h-c_),np.abs(l-c_)))
            atr_v=float(np.mean(tr))

        stars = sum([tb>.7, tb>.9, add>.5, atr_v/p<.05]) + 1
        signals.append(dict(
            sym=sym, name=names.get(sym,""), price=round(p,2),
            chg=round(chg,2), chg5=round(chg5,2), score=round(score,1),
            tb=round(tb,3), add=round(add,2), atr_pct=round(atr_v/p*100,1),
            turnover=round(avg_v*p/1e4,0), stars="⭐"*stars,
            stop=round(p*.92,2), tp1=round(p*1.05,2), tp2=round(p*1.10,2),
            trend="📈" if bp>0 else "📉", rr=round(.05/(atr_v*2/p),1) if atr_v>0 else 0,
            shares=max(int(1e6*.02/(atr_v*2)/p/100)*100,100) if atr_v>0 else 100,
        ))
        stats["ok"] += 1

    signals.sort(key=lambda x:x["score"], reverse=True)
    return signals, stats

# ═══════════════════════════════ Header Bar ═══════════════════════════════
h1, h2, h3, h4, h5 = st.columns([2.5, 1.2, .8, .8, .8])

with h1:
    live_dot = "🟢实时" if auto_refresh else "📅"
    c1, c2 = st.columns([3, 1])
    with c1:
        picked_dt = st.date_input(f"{live_dot} 交易日", value=latest_dt,
                                   min_value=date_to_dt[recent_dates[0]],
                                   max_value=latest_dt,
                                   label_visibility="visible")
    with c2:
        if auto_refresh:
            st.caption(f"⏱️ {refresh_sec}s刷新")
    pick_date = int(picked_dt.strftime("%Y%m%d"))
    # Find nearest available trading date
    selected_date = min(recent_dates, key=lambda d: abs(d - pick_date))

signals, stats = scan_market(data, selected_date, min_price, min_turnover, tb_min, require_add)
sel_str = date_labels[selected_date]

# Today indicator
today_str = datetime.now().strftime("%Y-%m-%d")
is_today_signal = (selected_date != int(datetime.now().strftime("%Y%m%d")))

with h2: st.metric("股票池", f"{len(data):,}")
with h3: st.metric("扫描日", sel_str)
with h4: st.metric("通过✅", stats["ok"])
with h5:
    st.metric("通过率", f"{stats['ok']/max(stats['scanned'],1)*100:.0f}%")

# ═══════════════════════════════ Today Banner ═══════════════════════════════
if is_today_signal and selected_date == recent_dates[-1]:
    st.info(f"📌 以下信号基于 **{sel_str}收盘数据**，用于 **今日({today_str})盘中交易参考**。"
            f"在同花顺中运行 `realtime_scan.py` 查看实时价格变化。")

# ═══════════════════════════════ Alert Row ═══════════════════════════════
if signals:
    strong = [s for s in signals if s["score"] >= 80]
    medium = [s for s in signals if 60 <= s["score"] < 80]
    alert_color = "#ff5252" if len(strong) > 5 else "#ffd700" if len(strong) > 0 else "#64b5f6"
    st.markdown(f"""
    <div style="background:{alert_color}15;border-left:3px solid {alert_color};border-radius:6px;padding:8px 14px;margin:6px 0;">
    📅 {sel_str} · <b>{len(signals)}</b> 个买入候选 ·
    <span style="color:#00e676">强信号(≥80):{len(strong)}</span> ·
    <span style="color:#ffd700">中信({len(medium)})</span> ·
    过滤: 价{stats['price']}+量{stats['vol']}+涨停{stats['lu']}+TB{stats['tb']}
    </div>""", unsafe_allow_html=True)

# ═══════════════════════════════ Top 3 Cards ═══════════════════════════════
if signals:
    st.markdown('<p class="section-title">🏆 精选推荐</p>', unsafe_allow_html=True)
    cols = st.columns(3)
    for idx, (s, style, medal) in enumerate(zip(signals[:3], ["gold","silver","bronze"], ["🥇","🥈","🥉"])):
        with cols[idx]:
            sc = "score-strong" if s["score"]>=80 else "score-good"
            st.markdown(f"""
            <div class="signal-card {style}">
            <div class="card-name">{medal} {s['name'] or s['sym']}</div>
            <div class="card-code">{s['sym']}</div>
            <div style="font-size:1.6rem;font-weight:700;color:#00d2ff;margin:4px 0;">¥{s['price']}<span style="font-size:.8rem;color:{'#00e676' if s['chg']>0 else '#ff5252'};margin-left:8px;">{s['chg']:+.1f}%</span></div>
            <div style="font-size:.8rem;color:#8892b0;line-height:1.6;">
            TB:{s['tb']:.3f} | 波动:{s['atr_pct']}%<br>
            🛑 止损 <b style="color:#ff5252">¥{s['stop']}</b><br>
            🎯 止盈 <b style="color:#00e676">¥{s['tp1']}</b> / <b style="color:#00e676">¥{s['tp2']}</b>
            </div>
            <span class="{sc}">{s['score']:.0f}分 {s['stars']}</span>
            </div>""", unsafe_allow_html=True)

# ═══════════════════════════════ Signal Table ═══════════════════════════════
    st.markdown('<p class="section-title">📋 信号列表</p>', unsafe_allow_html=True)

    df = pd.DataFrame(signals[:top_n])
    display_cols = ["sym","name","price","chg","score","tb","add","atr_pct","turnover","stop","tp1","tp2","stars"]
    display_df = df[display_cols].set_index("sym")

    st.dataframe(display_df, use_container_width=True, height=min(520, 28*len(display_df)+38),
        column_config={
            "sym": st.column_config.TextColumn("代码", width="small"),
            "name": st.column_config.TextColumn("名称", width="medium"),
            "price": st.column_config.NumberColumn("现价", format="¥%.2f"),
            "chg": st.column_config.NumberColumn("涨跌", format="%+.2f%%"),
            "score": st.column_config.ProgressColumn("评分", min_value=0, max_value=120, format="%.0f", width="medium"),
            "tb": st.column_config.NumberColumn("TB", format="%.3f"),
            "add": st.column_config.NumberColumn("加仓", format="%.2f"),
            "atr_pct": st.column_config.NumberColumn("ATR%", format="%.1f%%"),
            "turnover": st.column_config.NumberColumn("成交(万)", format="%.0f"),
            "stop": st.column_config.NumberColumn("止损", format="¥%.2f"),
            "tp1": st.column_config.NumberColumn("止盈1", format="¥%.2f"),
            "stars": st.column_config.TextColumn("评级", width="small"),
        })

    # ═══════════════════════ K-line ═══════════════════════
    with st.expander("📈 最强信号技术面 — " + signals[0]["sym"] + " " + (signals[0]["name"] or ""), expanded=False):
        s_top = signals[0]; sd = data[s_top["sym"]]
        idx = sd["dates"].index(latest_date); lb = min(60, idx+1)
        dk = [str(d) for d in sd["dates"][idx-lb+1:idx+1]]
        o = sd["open"][idx-lb+1:idx+1]; h = sd["high"][idx-lb+1:idx+1]
        l = sd["low"][idx-lb+1:idx+1]; c = sd["close"][idx-lb+1:idx+1]
        v = sd["volume"][idx-lb+1:idx+1]

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[.7,.3], vertical_spacing=.03)
        fig.add_trace(go.Candlestick(x=dk, open=o, high=h, low=l, close=c, name=s_top["sym"],
                     increasing_line_color="#00e676", decreasing_line_color="#ff5252"), row=1, col=1)
        fig.add_trace(go.Scatter(x=[dk[-1]], y=[c[-1]], mode="markers+text",
                     marker=dict(size=12,color="#00d2ff",symbol="star"),
                     text=[f"TB:{s_top['tb']:.2f}"], textposition="top center"), row=1, col=1)
        colors_v = ["#00e676" if c[i]>=o[i] else "#ff5252" for i in range(len(c))]
        fig.add_trace(go.Bar(x=dk, y=v, marker_color=colors_v, opacity=.5), row=2, col=1)
        fig.update_layout(height=380, template="plotly_dark", showlegend=False,
            title=f"{s_top['sym']} {s_top.get('name','')} · {lb}日 · 评分{s_top['score']:.0f}",
            margin=dict(l=10,r=10,t=40,b=10), xaxis_rangeslider_visible=False,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)

    # ═══════════════════════ Export ═══════════════════════
    csv = display_df.to_csv().encode("utf-8-sig")
    st.download_button("📥 导出CSV", csv, f"signals_{sel_str}.csv", "text/csv",
                       use_container_width=True, key="export")

else:
    st.info("😕 当前过滤条件下无信号，请尝试切换为「激进」模式或放宽参数")

# ═══════════════════════════════ Auto-Refresh ═══════════════════════════════
if auto_refresh:
    time.sleep(refresh_sec)
    st.rerun()

st.caption("⚠️ 仅供研究参考，不构成投资建议。交易有风险，入市需谨慎。")
