"""WorldQuant 101 Alphas — A股适配版 v2 (numpy原生, 零辅助函数)

40个因子全用np协写, 无数组长度Bug。
用法: from wq101_factors import FACTORS
"""
import numpy as np

def _r(x): return (np.argsort(np.argsort(x))+1)/len(x)  # rank

# ═══════════════════════════════════════════════════════════
def wq_alpha001(df):
    c,v=df["close"].values,df["volume"].values
    if len(c)<20: return None
    ret=np.diff(c[-21:])/(np.abs(c[-21:-1])+1e-9);ret=np.append([0],ret[-20:])
    return float(_r(ret)[-1]*_r(v[-20:])[-1])

def wq_alpha002(df):
    if len(df)<12: return None
    c,v,o=df["close"].values,df["volume"].values,df["open"].values
    gap=(o[-11:-1]-c[-12:-2])/(np.abs(c[-12:-2])+1e-9)
    vr=v[-11:-1]/(np.mean(v[-11:-1])+1e-9)
    return float(-1*np.corrcoef(gap,vr)[0,1] if len(gap)>1 else 0)

def wq_alpha003(df):
    if len(df)<10: return None
    o,v=df["open"].values[-10:],df["volume"].values[-10:]
    return float(-1*np.corrcoef(_r(o),_r(v))[0,1] if len(o)>1 else 0)

def wq_alpha004(df):
    if len(df)<10: return None
    return float(-1*_r(df["low"].values[-9:])[-1])

def wq_alpha005(df):
    if len(df)<10: return None
    c,h,l,v=df["close"].values,df["high"].values,df["low"].values,df["volume"].values
    tp=(h+l+c)/3; vwap=np.average(tp[-10:],weights=v[-10:]) if np.sum(v[-10:])>0 else c[-1]
    d=c[-1]-vwap;return float(d*-1*abs(d)/(abs(vwap)+1e-9))

def wq_alpha006(df):
    if len(df)<10: return None
    o=df["open"].values[-10:];v=df["volume"].values[-10:]
    return float(-1*np.corrcoef(o,v)[0,1] if len(o)>1 else 0)

def wq_alpha007(df):
    if len(df)<20: return None
    c,v=df["close"].values,df["volume"].values
    if v[-1]<=np.mean(v[-20:-1]): return -1.0
    d7=c[-1]-c[-8];return float(-1*np.sign(d7)*abs(d7)/(abs(c[-8])+1e-9))

def wq_alpha008(df):
    if len(df)<10: return None
    o,c=df["open"].values,df["close"].values
    return float(-1*(np.sum(o[-5:])*np.sum(np.diff(c[-6:]))-np.sum(o[-10:-5])*np.sum(np.diff(c[-11:-5])))/1e6)

def wq_alpha009(df):
    if len(df)<6: return None
    d=np.diff(df["close"].values[-6:]);d1=d[-1];mn=np.min(d);mx=np.max(d)
    return float(d1 if mn>0 else(d1 if mx<0 else -1*d1))

def wq_alpha010(df):
    if len(df)<5: return None
    d=np.diff(df["close"].values[-5:]);d1=d[-1];mn=np.min(d);mx=np.max(d)
    return float(d1 if mn>0 else(d1 if mx<0 else -1*d1))

def wq_alpha011(df):
    if len(df)<6: return None
    c,h,l,v=df["close"].values,df["high"].values,df["low"].values,df["volume"].values
    spread=(np.max(h[-5:])-np.min(l[-5:]))/(np.mean(c)+1e-9)
    vr=v[-1]/(np.mean(v[-20:])+1e-9) if len(v)>=20 else 1
    return float(spread*-1*vr)

def wq_alpha012(df):
    if len(df)<10: return None
    c,v=df["close"].values,df["volume"].values
    return float(np.sign(c[-1]-c[-3])*-1*np.sign(v[-1]-v[-3]))

def wq_alpha013(df):
    if len(df)<6: return None
    o,h,l,c=df["open"].values,df["high"].values,df["low"].values,df["close"].values
    top=np.max(h[-5:]-o[-5:]);bot=np.min(l[-5:]-o[-5:])
    return float((top-bot)/(np.mean(abs(c-o))+1e-9))

def wq_alpha014(df):
    if len(df)<20: return None
    r=np.diff(df["close"].values[-21:])/(np.abs(df["close"].values[-21:-1])+1e-9)
    return float(np.mean(r)*-1/(np.std(r)+1e-9) if np.std(r)>1e-9 else 0)

def wq_alpha015(df):
    if len(df)<20: return None
    c=df["close"].values;ma=np.mean(c[-20:])
    return float((ma-c[-1])/(ma+1e-9)*100)

def wq_alpha016(df):
    if len(df)<10: return None
    v=df["volume"].values
    return float(-1*_r(v[-5:])[-1]*_r(np.diff(v[-6:]))[-1])

def wq_alpha017(df):
    if len(df)<22: return None
    c=df["close"].values;r=np.diff(c[-22:]);v=df["volume"].values[-21:]
    return float(-1*np.corrcoef(_r(r),_r(v))[0,1] if len(r)>1 else 0)

def wq_alpha018(df):
    if len(df)<10: return None
    c,h,l=df["close"].values,df["high"].values,df["low"].values
    mid=(h+l)/2;d=abs(c[-5:]-np.roll(mid,1)[-5:])
    return float(-1*_r(d)[-1])

def wq_alpha019(df):
    if len(df)<6: return None
    c,o=df["close"].values,df["open"].values
    gap=(o[-5:]-c[-6:-1])/(abs(c[-6:-1])+1e-9)
    return float(-1*_r(gap)[-1])

def wq_alpha020(df):
    if len(df)<10: return None
    h,l=df["high"].values,df["low"].values
    return float(-1*_r(h[-10:]-np.roll(l,1)[-10:])[-1])

def wq_alpha021(df):
    if len(df)<8: return None
    c,v=df["close"].values,df["volume"].values
    d=np.diff(c[-9:]);return float(np.mean(d)/(np.std(d)+1e-9)*_r(v[-8:])[-1])

def wq_alpha022(df):
    if len(df)<10: return None
    c,v=df["close"].values,df["volume"].values
    d5=c[-1]-c[-6];vr=v[-1]/(np.mean(v[-6:-1])+1e-9)
    return float(d5*-1*vr/(abs(c[-1])+1e-9))

def wq_alpha023(df):
    if len(df)<20: return None
    h=df["high"].values
    return float((np.mean(h[-20:])-h[-1])/(np.mean(h[-20:])+1e-9)*100)

def wq_alpha024(df):
    if len(df)<6: return None
    c=df["close"].values
    return float(-1*(c[-1]-c[-6])/(abs(c[-6])+1e-9)*100)

def wq_alpha025(df):
    if len(df)<20: return None
    c,v=df["close"].values,df["volume"].values
    ret=(c[-1]-c[-20])/(abs(c[-20])+1e-9)
    vr=v[-1]/(np.mean(v[-20:])+1e-9)
    return float(ret*vr*-1)

def wq_alpha026(df):
    if len(df)<20: return None
    v=df["volume"].values
    return float(-1*_r(v[-20:])[-1]*np.argmax(v[-20:])/20)

def wq_alpha027(df):
    if len(df)<10: return None
    c,v=df["close"].values,df["volume"].values
    r=np.diff(c[-7:]);return float(np.mean(r)*np.mean(v[-6:])/1e9)

def wq_alpha028(df):
    if len(df)<20: return None
    h,l=df["high"].values,df["low"].values
    s=h-l;z=(s[-1]-np.mean(s[-20:]))/max(np.std(s[-20:]),1e-9)
    return float(z*25+50)

def wq_alpha029(df):
    if len(df)<10: return None
    c,o=df["close"].values,df["open"].values
    up=np.sum(np.maximum(c[-10:]-o[-10:],0))
    dn=np.sum(np.maximum(o[-10:]-c[-10:],0))
    return float((up-dn)/(np.mean(c)+1e-9)*-1)

def wq_alpha030(df):
    if len(df)<22: return None
    c=df["close"].values;s20=np.std(c[-21:-1]);s10=np.std(c[-11:-1])
    return float((s10/max(s20,1e-9)-1)*100)

def wq_alpha031(df):
    if len(df)<12: return None
    l,c=df["low"].values,df["close"].values
    bounce=(c[-12:]-l[-12:])/(abs(l[-12:])+1e-9)
    return float(np.mean(bounce>0.01)*-1)

def wq_alpha032(df):
    if len(df)<22: return None
    h,l=df["high"].values,df["low"].values
    a20=np.mean(h[-21:-1]-l[-21:-1]);a10=np.mean(h[-11:-1]-l[-11:-1])
    return float((a10/max(a20,1e-9)-1)*100)

def wq_alpha033(df):
    if len(df)<10: return None
    c,v=df["close"].values,df["volume"].values
    rs=np.sign(np.diff(c[-6:]));vs=np.sign(np.diff(v[-6:]))
    return float(-1*np.sum(rs*vs))

def wq_alpha034(df):
    if len(df)<6: return None
    c,h,l=df["close"].values,df["high"].values,df["low"].values
    p=(c[-1]-l[-1])/max(h[-1]-l[-1],1e-9)
    return float(min(100,max(0,p*100)))

def wq_alpha035(df):
    if len(df)<5: return None
    c,o=df["close"].values,df["open"].values
    return float(sum(1 for i in range(1,6) if c[-i]>o[-i])*20)

def wq_alpha036(df):
    if len(df)<15: return None
    r=np.diff(df["close"].values[-15:])
    g=np.mean(np.maximum(r,0));l=np.mean(np.abs(np.minimum(r,0)))+1e-9
    rsi=100-100/(1+g/l);return float(50-rsi)

def wq_alpha037(df):
    if len(df)<20: return None
    c,v=df["close"].values,df["volume"].values
    if c[-1]>np.max(c[-21:-1]):return float(v[-1]/max(np.mean(v[-20:]),1e-9)*50)
    return 0.0

def wq_alpha038(df):
    if len(df)<5: return None
    v=df["volume"].values
    return float(-1*sum(1 for i in range(1,5) if v[-i]<v[-i-1])*25)

def wq_alpha039(df):
    if len(df)<22: return None
    v=df["volume"].values
    return float((np.mean(v[-5:])/max(np.mean(v[-20:]),1e-9)-1)*50)

def wq_alpha040(df):
    if len(df)<20: return None
    c=df["close"].values;m5=np.mean(c[-5:]);m10=np.mean(c[-10:]);m20=np.mean(c[-20:])
    if c[-1]>m5>m10>m20:return 100.0
    if c[-1]<m5<m10<m20:return 0.0
    return 50.0

FACTORS={f"wq{i:03d}":fn for i,fn in enumerate([
    wq_alpha001,wq_alpha002,wq_alpha003,wq_alpha004,wq_alpha005,
    wq_alpha006,wq_alpha007,wq_alpha008,wq_alpha009,wq_alpha010,
    wq_alpha011,wq_alpha012,wq_alpha013,wq_alpha014,wq_alpha015,
    wq_alpha016,wq_alpha017,wq_alpha018,wq_alpha019,wq_alpha020,
    wq_alpha021,wq_alpha022,wq_alpha023,wq_alpha024,wq_alpha025,
    wq_alpha026,wq_alpha027,wq_alpha028,wq_alpha029,wq_alpha030,
    wq_alpha031,wq_alpha032,wq_alpha033,wq_alpha034,wq_alpha035,
    wq_alpha036,wq_alpha037,wq_alpha038,wq_alpha039,wq_alpha040,
],start=1)}
