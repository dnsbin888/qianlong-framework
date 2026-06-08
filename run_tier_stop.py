"""分层止损测试: -3%减半仓, -5%全清"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")

CACHE=r"d:\quant_framework\cache_ohlcv.pkl"; N=2000

def _ref(s,n):return s.shift(n)
def _hhv(s,n):return s.rolling(n,min_periods=1).max()
def _ema(s,n):return s.ewm(span=n,adjust=False).mean()

def sig_f1(df):
    c,v=df["close"],df["volume"]
    p=_ref(_hhv(c,30),1).rolling(2).mean();d=(c-_ema(c,20)).pow(2).rolling(20).mean().pow(0.5)
    u=_ref(_ema(c,20)+2*d,1);vr=v/_ref(v.rolling(5).mean(),1).replace(0,np.nan)
    qlj=((c>p)&(c>u)&(vr>1.8)).astype(int);qlj&=qlj.rolling(7).sum()==1
    c99=c.rolling(60,min_periods=1).quantile(0.99);p100=c99.ewm(span=5,adjust=False).mean()
    ztxf=((c>p100).astype(int).rolling(7).sum()==1).astype(int)
    return((qlj>0)&(ztxf>0)).astype(int).values

def bt_stock(df,sig):
    trades=[]
    pos=None
    for i in range(250,len(df)):
        p=df["close"].iloc[i];o=df["open"].iloc[i];h=df["high"].iloc[i]
        pc=df["close"].iloc[i-1] if i>=1 else p;lu=round(pc*1.10,2) if pc>0 else 999
        if p<=3:continue
        if pos is None:
            if sig[i] and p<lu-0.01 and o<h:
                pos=dict(ep=p,peak=p,remain=100,half3=False,half7=False,ei=i)
        else:
            if h>pos["peak"]:pos["peak"]=h
            days=i-pos["ei"];pnl=(p-pos["ep"])/pos["ep"];pp=(pos["peak"]-pos["ep"])/pos["ep"]
            if p>=lu-0.01:continue   # 涨停持有

            # ── +7%回落1.5%卖一半 ──
            if not pos["half7"] and pp>=0.07 and (p-pos["peak"])/pos["ep"]<=-0.015:
                trades.append(dict(pnl=(pnl-0.0013)*0.5,days=days,r="止盈半仓"))
                pos["half7"]=True;pos["remain"]-=50;pos["peak"]=p;continue

            # ── -3%减半 (只触发一次) ──
            if not pos["half3"] and pnl<=-0.03:
                trades.append(dict(pnl=(pnl-0.0013)*0.5,days=days,r="-3%减半"))
                pos["half3"]=True;pos["remain"]-=50;pos["peak"]=p;continue

            # ── -5%全清 ──
            if pnl<=-0.05:
                trades.append(dict(pnl=(pnl-0.0013)*(pos["remain"]/100),days=days,r="-5%全清"))
                pos=None;continue

            # ── 不涨停回落3%全清 ──
            if pp>=0.02 and (p-pos["peak"])/pos["ep"]<=-0.03:
                trades.append(dict(pnl=(pnl-0.0013)*(pos["remain"]/100),days=days,r="回落3%"))
                pos=None;continue

            # ── 时间5天 ──
            if days>=5 and pnl<0.01:
                trades.append(dict(pnl=(pnl-0.0013)*(pos["remain"]/100),days=days,r="时间5天"))
                pos=None
    return trades

print("="*60)
print("  分层止损: -3%减半仓, -5%全清")
print("="*60)

with open(CACHE,"rb") as f:raw=pickle.load(f)
import random;random.seed(42)
keys=random.sample(list(raw.keys()),min(N,len(raw)))
data={k:raw[k] for k in keys}

# 跑两版: 分层止损 vs 原来-5%直接全清
all_new=[];all_old=[]
for sym,sd in data.items():
    try:
        df=pd.DataFrame({"open":sd["open"][-500:],"high":sd["high"][-500:],
                         "low":sd["low"][-500:],"close":sd["close"][-500:],
                         "volume":sd["volume"][-500:]})
        if len(df)<300:continue
        s=sig_f1(df);all_new.extend(bt_stock(df,s))
    except:continue

# 对比: 原版(-5%直接全清,回落3%)
def bt_old(df,sig):
    trades=[];pos=None
    for i in range(250,len(df)):
        p=df["close"].iloc[i];o=df["open"].iloc[i];h=df["high"].iloc[i]
        pc=df["close"].iloc[i-1] if i>=1 else p;lu=round(pc*1.10,2) if pc>0 else 999
        if p<=3:continue
        if pos is None:
            if sig[i] and p<lu-0.01 and o<h:
                pos=dict(ep=p,peak=p,remain=100,half7=False,ei=i)
        else:
            if h>pos["peak"]:pos["peak"]=h
            days=i-pos["ei"];pnl=(p-pos["ep"])/pos["ep"];pp=(pos["peak"]-pos["ep"])/pos["ep"]
            if p>=lu-0.01:continue
            if not pos["half7"] and pp>=0.07 and (p-pos["peak"])/pos["ep"]<=-0.015:
                trades.append(dict(pnl=(pnl-0.0013)*0.5,days=days,r="止盈半仓"))
                pos["half7"]=True;pos["remain"]-=50;pos["peak"]=p;continue
            if pnl<=-0.05:
                trades.append(dict(pnl=(pnl-0.0013),days=days,r="-5%全清"))
                pos=None;continue
            if pp>=0.02 and (p-pos["peak"])/pos["ep"]<=-0.03:
                trades.append(dict(pnl=(pnl-0.0013)*(pos["remain"]/100),days=days,r="回落3%"))
                pos=None;continue
            if days>=5 and pnl<0.01:
                trades.append(dict(pnl=(pnl-0.0013)*(pos["remain"]/100),days=days,r="时间5天"))
                pos=None
    return trades

for sym,sd in data.items():
    try:
        df=pd.DataFrame({"open":sd["open"][-500:],"high":sd["high"][-500:],
                         "low":sd["low"][-500:],"close":sd["close"][-500:],
                         "volume":sd["volume"][-500:]})
        if len(df)<300:continue
        s=sig_f1(df);all_old.extend(bt_old(df,s))
    except:continue

def stats(ts,name):
    if not ts:return
    w=[t for t in ts if t["pnl"]>0];l_=[t for t in ts if t["pnl"]<=0]
    wr=len(w)/len(ts);aw=np.mean([t["pnl"] for t in w]) if w else 0
    al=np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    avg_d=np.mean([t["days"] for t in ts])
    rc={};[rc.update({t["r"]:rc.get(t["r"],0)+1}) for t in ts]
    print(f"  {name}: T={len(ts)} WR={wr:.1%} AW={aw:.2%} AL={al:.2%} PF={pf:.2f} D={avg_d:.1f}")
    for r,c in sorted(rc.items(),key=lambda x:-x[1])[:5]:
        print(f"    {r}: {c}笔 ({c/len(ts)*100:.0f}%)")
    return pf

print()
pf_new=stats(all_new, "新:-3%减半+-5%全清")
pf_old=stats(all_old, "旧:-5%直接全清")

print(f"\n  对比: 新PF={pf_new:.2f} vs 旧PF={pf_old:.2f}")
if pf_new>pf_old:print("  ✅ 分层止损更好!")
else:print("  ❌ 旧版更好, 保持原来")
print("  Done!")
