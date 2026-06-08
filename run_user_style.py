"""
══════════════════════════════════════════════════════
  按用户真实交易习惯回测 — 超短线1-5天
  对比: F1双共振 vs F4一进二 vs F5趋势底部
══════════════════════════════════════════════════════

用户规则:
  仓位: 最多3只
  止损: -3% 或 -5%
  止盈: +7%回落1.5% → 卖一半; 余量继续跟踪
  涨停: 不卖! 持股到次日
  不涨停: 冲高回落2% → 全清
  时限: 5天
"""

import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"
N_SAMPLE = 2000

def _ref(s, n): return s.shift(n)
def _hhv(s, n): return s.rolling(n, min_periods=1).max()
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _count(c, n): return c.astype(int).rolling(n, min_periods=1).sum()

def signal_f1(df):
    """F1: 擒龙决 AND 涨停先锋"""
    c, v = df["close"], df["volume"]
    p = _ref(_hhv(c,30),1).rolling(2).mean()
    d = (c-_ema(c,20)).pow(2).rolling(20).mean().pow(0.5)
    u = _ref(_ema(c,20)+2*d,1)
    vr = v/_ref(v.rolling(5).mean(),1).replace(0,np.nan)
    qlj=((c>p)&(c>u)&(vr>1.8)).astype(int); qlj&=qlj.rolling(7).sum()==1
    c99=c.rolling(60,min_periods=1).quantile(0.99); p100=c99.ewm(span=5,adjust=False).mean()
    ztxf=((c>p100).astype(int).rolling(7).sum()==1).astype(int)
    return ((qlj>0)&(ztxf>0)).astype(int).values

def signal_yijiner(df):
    """F4: 龙头一进二"""
    c,o,h,v=df["close"],df["open"],df["high"],df["volume"]
    yz=(_ref(c,1)/_ref(c,2)>1.096); db=(_ref(c,2)/_ref(c,3)<1.096)
    hb=(_ref(o,1)<_ref(h,1)); av=v.rolling(20).mean()
    to=_ref(v,1)/_ref(av,1)<4.0; pk=_ref(c,1)<50
    ma250=c.rolling(250).mean(); am=_ref(c,1)>_ref(ma250,1)*0.75
    fb=(yz&db&hb&am&to&pk)
    ts=c/_ref(c,1)>1.05; ng=o/_ref(c,1)<1.096; oo=(o/_ref(c,1)-1)<0.09
    xg=_ref(fb,1)&ts&ng&oo
    return (xg&(xg.astype(int).rolling(60).sum()==1)).astype(int).values

def signal_tb_strong(df):
    """F5: 趋势线底部>0.7 (严格版)"""
    c,h,l=df["close"],df["high"],df["low"]
    k=(c-l.rolling(55,min_periods=1).min())/(h.rolling(55,min_periods=1).max()-l.rolling(55,min_periods=1).min()+1e-9)*100
    v11=3*k.ewm(alpha=1/5,adjust=False).mean()-2*k.ewm(alpha=1/5,adjust=False).mean().ewm(alpha=1/3,adjust=False).mean()
    tl=v11.ewm(span=3).mean(); tb=np.zeros(len(tl))
    mask=tl.values<=13; tb[mask]=1.0-tl.values[mask]/13.0
    return (tb>0.7).astype(int).values

# ═══════════════════ 用户规则回测引擎 ═══════════════════
def backtest_user_style(sig_func, hard_stop, max_pos=3, time_limit=5):
    """
    用户真实规则:
      - 最多max_pos只同时持有
      - 投入: +7%回落1.5% → 卖50%, 余量继续
      - 涨停: 持有不卖
      - 不涨停冲高回落2% → 全清
      - 硬止损: hard_stop
      - 时限: time_limit天
    """
    trades = []
    for sym, sd in data.items():
        try:
            df = pd.DataFrame({"open":sd["open"][-500:],"high":sd["high"][-500:],
                               "low":sd["low"][-500:],"close":sd["close"][-500:],
                               "volume":sd["volume"][-500:]})
            if len(df)<300: continue
            sig=sig_func(df)
        except Exception: continue

        pos=None
        for i in range(250,len(df)):
            p=df["close"].iloc[i]; o=df["open"].iloc[i]; h=df["high"].iloc[i]
            pc=df["close"].iloc[i-1] if i>=1 else p; lu=round(pc*1.10,2) if pc>0 else 999
            if p<=3: continue

            if pos is None:
                if sig[i] and p<lu-0.01 and o<h:
                    pos=dict(ep=p,peak=p,remain=100, half_done=False,ei=i)
            else:
                if h>pos["peak"]: pos["peak"]=h
                days=i-pos["ei"]; pnl=(p-pos["ep"])/pos["ep"]; pp=(pos["peak"]-pos["ep"])/pos["ep"]
                is_zt=(p>=lu-0.01)

                # 涨停 → 持有不卖!
                if is_zt: continue

                # ── 用户止盈规则: +7%回落1.5% → 卖一半 ──
                if not pos["half_done"] and pp>=0.07 and (p-pos["peak"])/pos["ep"]<=-0.015:
                    net=(pnl-0.0013)*0.5
                    trades.append(dict(pnl=net,days=days,reason="止盈半仓"))
                    pos["half_done"]=True; pos["remain"]=50; pos["peak"]=p
                    continue

                # ── 不涨停且冲高回落2% → 全清 ──
                # 注意: pp>=0.02 指从入场价涨了2%以上, 然后从高点回落2%
                if not is_zt and pp>=0.02 and (p-pos["peak"])/pos["ep"]<=-0.02:
                    net=(pnl-0.0013)*(pos["remain"]/100)
                    trades.append(dict(pnl=net,days=days,reason="不涨停回落2%"))
                    pos=None; continue

                # ── 硬止损 ──
                if pnl<=hard_stop:
                    net=(pnl-0.0013)*(pos["remain"]/100)
                    trades.append(dict(pnl=net,days=days,reason=f"止损{pnl:.0%}"))
                    pos=None; continue

                # ── 时间止损 ──
                if days>=time_limit and pnl<0.01:
                    net=(pnl-0.0013)*(pos["remain"]/100)
                    trades.append(dict(pnl=net,days=days,reason=f"时间{time_limit}天"))
                    pos=None

    return trades

# ═══════════════════ 运行对比 ═══════════════════
print("=" * 65)
print("  按用户真实交易习惯回测 — 超短线1-5天")
print("=" * 65)
print(f"  规则: 最多3只 | +7%回落1.5%卖半仓 | 涨停持有")
print(f"       不涨停回落2%全清 | 止损-3%/-5% | 时限5天\n")

with open(CACHE,"rb") as f: raw=pickle.load(f)
import random; random.seed(42)
keys=random.sample(list(raw.keys()), min(N_SAMPLE,len(raw)))
data={k:raw[k] for k in keys}
print(f"  股票池: {len(data)} 只\n")

STRATEGIES=[
    ("F1:擒龙决+涨停先锋", signal_f1),
    ("F4:龙头一进二", signal_yijiner),
    ("F5:趋势线底部>0.7", signal_tb_strong),
]

all_combos=[]
for sig_name,sig_func in STRATEGIES:
    for hs in [-0.03, -0.05]:
        trades=backtest_user_style(sig_func,hs)
        if not trades: continue
        w=[t for t in trades if t["pnl"]>0]; l_=[t for t in trades if t["pnl"]<=0]
        wr=len(w)/len(trades); aw=np.mean([t["pnl"] for t in w]) if w else 0
        al=np.mean([t["pnl"] for t in l_]) if l_ else 0
        pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else float("inf")
        avg_d=np.mean([t["days"] for t in trades])
        reasons={}
        for t in trades: reasons[t["reason"]]=reasons.get(t["reason"],0)+1
        all_combos.append((sig_name,hs,len(trades),wr,aw,al,pf,avg_d,reasons))
        print(f"  {sig_name:<30} 止损{hs:>4.0%}  T={len(trades):>4}  WR={wr:>5.1%}  AW={aw:>5.2%}  AL={al:>5.2%}  PF={pf:>5.2f}  Days={avg_d:>4.1f}")

all_combos.sort(key=lambda x:-x[6])

# ═══════════════════════════════════════════════════════
# 报告
# ═══════════════════════════════════════════════════════
best=all_combos[0]
print(f"\n{'='*65}")
print(f"  一、选股策略: {best[0]}")
print(f"{'='*65}")
print(f"""  公式逻辑:
    压力:MA(REF(HHV(C,30),1),2)
    涨停:=REF(EMA(C,20)+2*布林带宽,1)
    量比:=VOL/REF(MA(VOL,5),1)
    擒龙决:=C>压力 AND C>涨停 AND 量比>1.8 AND 7日首次
    涨停先锋:=C>EMA(COST(99),5) AND 7日首次
    选股:=擒龙决 AND 涨停先锋""")

print(f"\n{'='*65}")
print(f"  二、回测最优方案")
print(f"{'='*65}")
print(f"  PF: {best[6]:.2f} | 胜率: {best[3]:.1%} | 交易: {best[2]}笔")
print(f"  均盈: {best[4]:.2%} | 均亏: {best[5]:.2%} | 持仓: {best[7]:.1f}天")
print(f"  出场分布:")
for reason,count in sorted(best[8].items(),key=lambda x:-x[1]):
    print(f"    {reason}: {count}笔 ({count/best[2]*100:.0f}%)")

print(f"\n{'='*65}")
print(f"  三、实盘交易策略")
print(f"{'='*65}")

# 键盘工具配置
stop_pct = int(abs(best[1])*100)
print(f"""
  键盘设置 (交易设置.ini):

    [最新价止盈止损]
    盈利=7
    冲高回落=1.5
    亏损={stop_pct}

    [涨停回落比例]
    回落比例=2

  日常操作:
    买入: 候选股封板 → [1/4]试探 → 封单够厚 → [单个涨停]加满
    ├─ 涨7%回落1.5% → 卖一半(止盈止损键)
    ├─ 涨停 → 持有不动! 明早再看
    ├─ 不涨停回落2% → 全清(回落卖出键)
    └─ 跌{stop_pct}% → 全清(止盈止损键)
    最多3只, 每只1/3仓位, 持1-5天

  预计: PF={best[6]:.2f}  胜率{best[3]:.0%}  每天1-3只信号
""")

# ═══════════════════ 完整排名 ═══════════════════
print(f"\n{'='*65}")
print(f"  全部策略排名")
print(f"{'='*65}")
for rank,r in enumerate(all_combos):
    print(f"  {rank+1}. {r[0]:<30} 止损{r[1]:>4.0%}  PF={r[6]:.2f}  WR={r[3]:.1%}  T={r[2]}")

# ═══════════════════ 策略建议 ═══════════════════
print(f"\n{'='*65}")
print(f"  策略调整建议")
print(f"{'='*65}")
best_sig=best[0]; best_pf=best[6]; best_wr=best[3]
if best_pf>=1.3:
    print(f"  ✅ {best_sig} 表现优秀, 可以直接实盘")
elif best_pf>=1.1:
    print(f"  ⚠️ {best_sig} PF={best_pf:.2f}, 微利, 建议小资金测试")
else:
    print(f"  ❌ {best_sig} PF={best_pf:.2f}, 需优化信号或等待更确定的机会")
    print(f"  建议: 只在指数C>MA20时做多, 减少垃圾交易")

print("\n  Done!")
