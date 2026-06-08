"""提高胜率: 二次严选 + 回踩确认入场 vs 开盘无脑买"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import struct, os, numpy as np, pandas as pd, random
import warnings; warnings.filterwarnings("ignore")

ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N = 2000

def load_stock(m,f):
    code=f.replace(m,'').replace('.day','')
    if len(code)!=6 or not code.isdigit():return None
    p=os.path.join(ROOT,m,'lday',f)
    if not os.path.exists(p):return None
    with open(p,'rb') as fh:raw=fh.read()
    d,o,h,l,c,v=[],[],[],[],[],[]
    for i in range(len(raw)//32):
        vs=struct.unpack_from('<I I I I I f I I',raw,i*32)
        if 20100101<=vs[0]<=20270101 and vs[1]>0:
            d.append(vs[0]);o.append(vs[1]/100.);h.append(vs[2]/100.);l.append(vs[3]/100.);c.append(vs[4]/100.);v.append(vs[6])
    return {'code':code,'dates':d,'o':o,'h':h,'l':l,'c':c,'v':v}

def f1(df):
    c,v=df['close'].values,df['volume'].values
    hhv30=pd.Series(c).rolling(30).max().shift(1)
    pressure=hhv30.rolling(2).mean().values
    ema20=pd.Series(c).ewm(span=20,adjust=False).mean()
    dev=((pd.Series(c)-ema20)**2).rolling(20).mean()**0.5
    upper=(ema20+2*dev).shift(1).values
    vr=v/pd.Series(v).rolling(5).mean().shift(1).replace(0,np.nan)
    qlj_raw=(c>pressure)&(c>upper)&(vr>1.8)
    qlj=np.zeros(len(c),dtype=int)
    for i in range(60,len(c)):
        if qlj_raw[i] and qlj_raw[i-7:i].sum()<=1:qlj[i]=1
    cost99=pd.Series(c).rolling(60).quantile(0.99)
    profit100=cost99.ewm(span=5,adjust=False).mean()
    ztxf_raw=c>profit100.values
    ztxf=np.zeros(len(c),dtype=int)
    for i in range(60,len(c)):
        if ztxf_raw[i] and ztxf_raw[i-7:i].sum()<=1:ztxf[i]=1
    return(qlj&ztxf).astype(int)

print("Loading...")
stocks={}
for m in['sh','sz']:
    ld=os.path.join(ROOT,m,'lday')
    if not os.path.isdir(ld):continue
    for f in os.listdir(ld):
        if not f.endswith('.day'):continue
        s=load_stock(m,f)
        if s and len(s['dates'])>=300:stocks[s['code']]=s

codes=list(stocks.keys());random.seed(42)
if len(codes)>N:codes=random.sample(codes,N)
stocks={k:stocks[k] for k in codes}

# ═══════════════════ 入场方案 ═══════════════════

def user_exit(trades_for_stock):
    """统一的出场规则"""
    all_t = []
    for entry in trades_for_stock:
        code, idx, ep = entry["code"], entry["idx"], entry["ep"]
        s = stocks[code]
        peak = ep; remain = 100; dc = 0; h7 = s30 = False
        for j in range(idx+1, min(idx+30, len(s["dates"]))):
            p = s["c"][j]; h = s["h"][j]; dc += 1
            if h > peak: peak = h
            pnl = (p-ep)/ep; pp = (peak-ep)/ep
            is_zt = (j > idx and s["c"][j-1] > 0 and p >= round(s["c"][j-1]*1.10, 2) - 0.01)
            if is_zt: continue
            if not h7 and pp >= 0.07 and (p-peak)/ep <= -0.015:
                all_t.append(dict(pnl=(pnl-0.0013)*0.5, days=dc, r="+7%回1.5减半"))
                h7 = True; remain -= 50; peak = p
                if remain <= 0: break; continue
            if h7 and pp >= 0.07 and (p-peak)/ep <= -0.02:
                all_t.append(dict(pnl=(pnl-0.0013)*(remain/100), days=dc, r="+7%不涨停回2%清")); break
            if not s30 and pnl <= -0.03:
                all_t.append(dict(pnl=(pnl-0.0013)*0.5, days=dc, r="-3%减半"))
                s30 = True; remain -= 50; peak = p
                if remain <= 0: break; continue
            if pnl <= -0.05:
                all_t.append(dict(pnl=(pnl-0.0013)*(remain/100), days=dc, r="-5%全清")); break
            if not is_zt and pp >= 0.02 and (p-peak)/ep <= -0.03:
                all_t.append(dict(pnl=(pnl-0.0013)*(remain/100), days=dc, r="回落3%")); break
            if dc >= 5 and pnl < 0.01:
                all_t.append(dict(pnl=(pnl-0.0013)*(remain/100), days=dc, r="时间5天")); break
    return all_t

def stats(name, all_t):
    if not all_t: return (name,0,0,0,0,0,0,0,{})
    w=[t for t in all_t if t["pnl"]>0]; l_=[t for t in all_t if t["pnl"]<=0]
    wr=len(w)/len(all_t); aw=np.mean([t["pnl"] for t in w]) if w else 0
    al=np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    rc={};[rc.update({t["r"]:rc.get(t["r"],0)+1}) for t in all_t]
    return (name,len(all_t),wr,aw,al,pf,np.mean([t["days"] for t in all_t]),sum(t["pnl"] for t in all_t),rc)

# ═══════════════════ 方案1: 开盘无脑买(基准) ═══════════════════
entries_open = []
for code in codes:
    s = stocks[code]
    df = pd.DataFrame({"open": s["o"], "high": s["h"], "low": s["l"], "close": s["c"], "volume": s["v"]})
    try: ff = f1(df)
    except: continue
    for i in range(250, len(df)-1):
        if ff[i]:
            ni = i+1; no = s["o"][ni]; lu = round(s["c"][i]*1.10, 2)
            if no >= lu-0.01: continue
            if s["o"][ni] == s["h"][ni] == s["c"][ni]: continue
            entries_open.append({"code": code, "idx": ni, "ep": no})

# ═══════════════════ 方案2: 二次严选+回踩确认 ═══════════════════
entries_smart = []
for code in codes:
    s = stocks[code]
    df = pd.DataFrame({"open": s["o"], "high": s["h"], "low": s["l"], "close": s["c"], "volume": s["v"]})
    try: ff = f1(df)
    except: continue
    for i in range(250, len(df)-2):  # -2 for potential delay
        if not ff[i]: continue
        signal_close = s["c"][i]

        # T+1: 看这一天有没有回踩确认
        next_i = i+1
        no = s["o"][next_i]; nh = s["h"][next_i]; nl = s["l"][next_i]; nc = s["c"][next_i]
        lu = round(signal_close*1.10, 2)

        # 过滤: 一字板跳过
        if no >= lu-0.01 or s["o"][next_i]==s["h"][next_i]==nc: continue

        # 二次严选1: 量能确认 (T+1日成交量 > 5日均量)
        avg_v5 = np.mean(s["v"][max(0,next_i-5):next_i])
        if s["v"][next_i] < avg_v5 * 0.8: continue

        # 二次严选2: 回踩确认
        # T+1日最低价接近信号日收盘价(±2%), 收盘价回升到信号价上方 → 支撑确认
        retrace_to_signal = abs(nl - signal_close) / signal_close
        close_above_signal = nc > signal_close * 0.98
        high_above_signal = nh > signal_close

        if retrace_to_signal <= 0.03 and close_above_signal and high_above_signal:
            # 回踩确认 → T+1尾盘买入
            entries_smart.append({"code": code, "idx": next_i, "ep": nc})  # 尾盘收盘价买入
            continue

        # 二次严选3: 如果不回踩, 等T+2缩量回踩
        next2_i = i+2
        if next2_i >= len(s["dates"]): continue
        n2o = s["o"][next2_i]; n2l = s["l"][next2_i]; n2c = s["c"][next2_i]
        n2_lu = round(s["c"][next_i]*1.10, 2)
        if n2o >= n2_lu-0.01: continue

        retrace2 = abs(n2l - signal_close) / signal_close
        vol_shrink = s["v"][next2_i] < s["v"][next_i] * 0.8  # 缩量
        if retrace2 <= 0.03 and n2c > signal_close * 0.98 and vol_shrink:
            entries_smart.append({"code": code, "idx": next2_i, "ep": n2c})

# ═══════════════════ 跑 ═══════════════════
print(f"F1 signals: ~{sum(1 for code in codes for i in range(250,len(stocks[code]['dates'])-1) if f1(pd.DataFrame({'close':stocks[code]['c'],'volume':stocks[code]['v']}))[i] if 0==0)}")
print(f"Entries: 开盘买={len(entries_open)}  回踩确认={len(entries_smart)}\n")

r1 = stats("开盘无脑买", user_exit(entries_open))
r2 = stats("回踩确认买", user_exit(entries_smart))

print(f"  {'方案':<18} {'交易':>6} {'胜率':>7} {'均盈':>7} {'均亏':>7} {'PF':>6} {'持仓':>5} {'总盈亏':>10}")
print(f"  {'-'*18} {'-'*6} {'-'*7} {'-'*7} {'-'*7} {'-'*6} {'-'*5} {'-'*10}")
for r in [r1, r2]:
    print(f"  {r[0]:<18} {r[1]:>6} {r[2]:>6.1%} {r[3]:>6.2%} {r[4]:>6.2%} {r[5]:>6.2f} {r[6]:>5.1f} {r[7]:>+10.2f}")

print(f"\n  胜率对比: {r1[2]:.1%} → {r2[2]:.1%} ({'+' if r2[2]>r1[2] else ''}{(r2[2]-r1[2])*100:+.1f}%)")
print(f"  PF对比:   {r1[5]:.2f} → {r2[5]:.2f} ({'+' if r2[5]>r1[5] else ''}{(r2[5]-r1[5]):+.2f})")

print(f"\n  回踩确认出场分布:")
for rc, cnt in sorted(r2[8].items(), key=lambda x: -x[1])[:5]:
    print(f"    {rc}: {cnt}笔 ({cnt/r2[1]*100:.0f}%)")
print("  Done!")
