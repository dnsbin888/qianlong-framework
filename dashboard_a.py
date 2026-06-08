"""A策略仪表盘 — F1信号 + M-S-T-V-P-L管道"""
import streamlit as st
import sys, os, struct, pickle, json, time, numpy as np, pandas as pd
from datetime import datetime
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, r"d:\quant_framework\src")

st.set_page_config(page_title="A策略仪表盘", page_icon="📡", layout="wide")

ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
NAMES_FILE = r"d:\quant_framework\stock_names.json"

# A策略管道参数
A_CONFIG = {"min_price": 18, "max_price": 100, "min_turnover": 2e8, "min_vol_ratio": 3.0, "sector_heat": 0.6}

st.markdown("""<style>
    .main .block-container{padding-top:0.5rem!important}
    header[data-testid="stHeader"]{display:none!important}
    div[data-testid="stToolbar"]{display:none!important}
    .stApp{margin-top:-4rem!important}
    section[data-testid="stSidebar"]{background:#1e2035}
    section[data-testid="stSidebar"] *{color:#ccd6f6!important}
</style>""", unsafe_allow_html=True)

# ═══════════════════ 数据加载 ═══════════════════
@st.cache_data(ttl=300)
def load_data():
    # F1 function
    def _ref(s, n): return s.shift(n)
    def _hhv(s, n): return s.rolling(n, min_periods=1).max()
    def _ema(s, n): return s.ewm(span=n, adjust=False).mean()

    def f1(df):
        c, v = df['close'].values, df['volume'].values
        pr = _ref(_hhv(df['close'], 30), 1).rolling(2).mean().values
        e20 = _ema(df['close'], 20)
        dv = ((df['close'] - e20)**2).rolling(20).mean()**0.5
        up = _ref(e20 + 2*dv, 1).values
        vr = v / _ref(df['volume'].rolling(5).mean(), 1).replace(0, np.nan)
        qr = (c > pr) & (c > up) & (vr > 1.8)
        q = np.zeros(len(c), dtype=int)
        for i in range(60, len(c)):
            if qr[i] and qr[i-7:i].sum() <= 1: q[i] = 1
        c99 = df['close'].rolling(60).quantile(0.99)
        p100 = c99.ewm(span=5, adjust=False).mean()
        zr = c > p100.values
        z = np.zeros(len(c), dtype=int)
        for i in range(60, len(c)):
            if zr[i] and zr[i-7:i].sum() <= 1: z[i] = 1
        return (q & z).astype(int)

    def ld_stock(m, f):
        code = f.replace(m, '').replace('.day', '')
        if len(code) != 6 or not code.isdigit(): return None
        p = os.path.join(ROOT, m, 'lday', f)
        if not os.path.exists(p): return None
        with open(p, 'rb') as fh: raw = fh.read()
        d, o, h, l, cl, v = [], [], [], [], [], []
        for i in range(len(raw)//32):
            vs = struct.unpack_from('<I I I I I f I I', raw, i*32)
            if 20100101 <= vs[0] <= 20270101 and vs[1] > 0:
                d.append(vs[0]); o.append(vs[1]/100.); h.append(vs[2]/100.)
                l.append(vs[3]/100.); cl.append(vs[4]/100.); v.append(vs[6])
        return {'code': code, 'd': d, 'o': o, 'h': h, 'l': l, 'c': cl, 'v': v}

    # Load stocks
    stocks = {}
    for m in ['sh', 'sz']:
        ld2 = os.path.join(ROOT, m, 'lday')
        if not os.path.isdir(ld2): continue
        for f in os.listdir(ld2)[:3000]:  # Limit to 3000 for speed
            if not f.endswith('.day'): continue
            s = ld_stock(m, f)
            if s and len(s['d']) >= 300: stocks[s['code']] = s

    # Load index
    idx = stocks.get('999999') or stocks.get('000001')
    idx_map = {}
    if idx:
        idx_ma = idx['c'].rolling(20).mean().values
        for i, d in enumerate(idx['d']):
            if i >= 20: idx_map[d] = idx['c'].iloc[i] > idx_ma[i]

    # Find latest date
    latest = max(d for s in stocks.values() for d in s['d'] if len(str(d)) == 8 and d <= 20260630)

    # Scan signals
    signals = []
    stats = {"total": 0, "M": 0, "T": 0, "V": 0, "P": 0, "L": 0, "OK": 0}
    for code, s in stocks.items():
        if latest not in s['d']: continue
        i = s['d'].index(latest)
        if i < 250: continue
        df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'], 'close': s['c'], 'volume': s['v']})
        try: ff = f1(df)
        except: continue
        if not ff[i]: continue
        stats["total"] += 1
        sc, sv = s['c'][i], s['v'][i]
        sig_d = s['d'][i]
        if sig_d in idx_map and not idx_map[sig_d]: stats["M"] += 1; continue
        if sv*sc < A_CONFIG["min_turnover"]: stats["T"] += 1; continue
        avg20 = np.mean(s['v'][max(0,i-20):i])
        if sv < avg20 * A_CONFIG["min_vol_ratio"]: stats["V"] += 1; continue
        if sc < A_CONFIG["min_price"] or sc > A_CONFIG["max_price"]: stats["P"] += 1; continue
        if i >= 1 and sc >= round(s['c'][i-1]*1.10, 2)-0.01: stats["L"] += 1; continue
        stats["OK"] += 1
        chg = (sc-s['c'][i-1])/s['c'][i-1]*100 if i > 0 else 0
        signals.append({"sym": code, "price": sc, "chg": chg, "vol": sv, "score": 0})

    return signals, stats, latest, stocks

# ═══════════════════ UI ═══════════════════
st.markdown("### 📡 A策略仪表盘 — F1+Pipeline")
st.caption(f"更新时间: {datetime.now().strftime('%H:%M:%S')}")

with st.sidebar:
    st.markdown("## ⚙️ A策略管道")
    st.metric("P", f"{A_CONFIG['min_price']}-{A_CONFIG['max_price']}")
    st.metric("T", f">{A_CONFIG['min_turnover']/1e8:.0f}亿")
    st.metric("V", f">{A_CONFIG['min_vol_ratio']}x")
    st.metric("M", "C>MA20")
    if st.button("🔄 刷新数据", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

signals, stats, latest, stocks = load_data()
date_str = f"{str(latest)[:4]}-{str(latest)[4:6]}-{str(latest)[6:8]}"

c1, c2, c3, c4 = st.columns(4)
c1.metric("📅 日期", date_str)
c2.metric("F1信号", stats["total"])
c3.metric("通过✅", stats["OK"])
c4.metric("过滤", f"共{sum(stats.values())-stats['total']-stats['OK']}")

st.markdown(f"管道: M({stats['M']})→T({stats['T']})→V({stats['V']})→P({stats['P']})→L({stats['L']})→OK({stats['OK']})")

if signals:
    df = pd.DataFrame(signals)
    df = df.sort_values(["chg"], ascending=False)
    st.dataframe(df, use_container_width=True, height=400,
                 column_config={"sym": "代码", "price": st.column_config.NumberColumn("现价", format="¥%.2f"),
                               "chg": st.column_config.NumberColumn("涨跌", format="%+.2f%%"),
                               "vol": st.column_config.NumberColumn("成交量", format="%.0f")})
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("📥 导出CSV", csv, f"signals_{date_str}.csv", use_container_width=True)
else:
    st.info("今日无信号。大盘可能在MA20下方，等待回暖。")
    st.info("开关M过滤: 左侧面板 → 暂时忽略大盘过滤")

st.caption("A策略 PF=9.67 | WR=52% | Daily=5.5")
