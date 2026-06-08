"""用户规则微调对比 — 只改回落比例,其他不变"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")

CACHE,N= r"d:\quant_framework\cache_ohlcv.pkl", 2000

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

def bt(df,sig,hs,dp):
    trades=[]
    for i in range(250,len(df)):
        p=df["close"].iloc[i];o=df["open"].iloc[i];h=df["high"].iloc[i]
        pc=df["close"].iloc[i-1] if i>=1 else p;lu=round(pc*1.10,2) if pc>0 else 999
        if p<=3:continue
        if not hasattr(bt,"pos"):bt.pos=None
        if bt.pos is None:
            if sig[i] and p<lu-0.01 and o<h:
                bt.pos=dict(ep=p,peak=p,remain=100,hd=False,ei=i)
        else:
            pos=bt.pos; pos["peak"]=max(pos["peak"],h)
            days=i-pos["ei"];pnl=(p-pos["ep"])/pos["ep"];pp=(pos["peak"]-pos["ep"])/pos["ep"]
            if p>=lu-0.01:continue  # 涨停持有
            # +7%回落1.5%卖一半
            if not pos["hd"] and pp>=0.07 and (p-pos["peak"])/pos["ep"]<=-0.015:
                trades.append(dict(pnl=(pnl-0.0013)*0.5,days=days,r="止盈半仓"))
                pos["hd"]=True;pos["remain"]=50;pos["peak"]=p;continue
            # 不涨停回落dp全清
            if pp>=0.02 and (p-pos["peak"])/pos["ep"]<=-dp:
                trades.append(dict(pnl=(pnl-0.0013)*(pos["remain"]/100),days=days,r=f"回落{dp*100:.0f}%"))
                bt.pos=None;continue
            if pnl<=hs:
                trades.append(dict(pnl=(pnl-0.0013)*(pos["remain"]/100),days=days,r=f"止损{hs:.0%}"))
                bt.pos=None;continue
            if days>=5 and pnl<0.01:
                trades.append(dict(pnl=(pnl-0.0013)*(pos["remain"]/100),days=days,r="时间5天"))
                bt.pos=None
    return trades

print("="*60)
print("  最优参数测试 — 只改回落比例")
print("="*60)

with open(CACHE,"rb") as f: raw=pickle.load(f)
import random;random.seed(42)
keys=random.sample(list(raw.keys()),min(N,len(raw)))
data={k:raw[k] for k in keys}
print(f"  {len(data)} stocks\n")

# Test: stop-5%, different drop% + also test stop-3%
tests=[
    ("你的规则",-0.05,0.02),
    ("回落2.5%",-0.05,0.025),
    ("回落3%",-0.05,0.03),
    ("回落3.5%",-0.05,0.035),
    ("回落4%",-0.05,0.04),
    ("-3%+回落3%",-0.03,0.03),
]

results=[]
for name,hs,dp in tests:
    all_t=[]
    for sym,sd in data.items():
        try:
            df=pd.DataFrame({"open":sd["open"][-500:],"high":sd["high"][-500:],
                             "low":sd["low"][-500:],"close":sd["close"][-500:],
                             "volume":sd["volume"][-500:]})
            if len(df)<300:continue
            s=sig_f1(df);bt.pos=None;t=bt(df,s,hs,dp);all_t.extend(t)
        except:continue

    if not all_t:continue
    w=[t for t in all_t if t["pnl"]>0];l_=[t for t in all_t if t["pnl"]<=0]
    wr=len(w)/len(all_t);aw=np.mean([t["pnl"] for t in w]) if w else 0
    al=np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    avg_d=np.mean([t["days"] for t in all_t])
    rc={}
    for t in all_t:rc[t["r"]]=rc.get(t["r"],0)+1
    results.append((name,len(all_t),wr,aw,al,pf,avg_d,rc))
    print(f"  {name:<15} T={len(all_t):>4} WR={wr:>5.1%} AW={aw:>5.2%} AL={al:>5.2%} PF={pf:>5.2f} D={avg_d:>4.1f}")

results.sort(key=lambda x:-x[5])
best=results[0]

print(f"\n{'='*60}")
print(f"  最优: {best[0]} PF={best[5]:.2f} WR={best[1]:.1%}")
print(f"{'='*60}")
print(f"\n  全部排名:")
for r in results:
    print(f"  {r[0]:<15} PF={r[5]:.2f} WR={r[2]:.1%} T={r[1]}")

print(f"\n  🏆 建议采用: {best[0]}")
print(f"  键盘设置: 亏损=5 冲高回落=1.5 盈利=7")
if best[0]!="你的规则":
    dp_val=best[0].replace("回落","").replace("%","")
    print(f"  回落卖出比例改为: {dp_val}")
print("  Done!")
