"""诊断扫描条件分布"""
import sys, numpy as np
sys.path.insert(0,'D:/quant_web'); sys.path.insert(0,'D:/quant_framework')
from data_loader import load_stock_data_cache
sd=load_stock_data_cache('D:/quant_web/stock_data.parquet',keep_days=30)
drop=0; vol=0; bull=0; turn=0; all_ok=0
for sym,df in list(sd.items())[:3000]:
    try:
        c=df['close'].values;v=df['volume'].values;o=df['open'].values
        if len(c)<21: continue
        if 'ST' in sym.upper(): continue
        chg=(c[-1]-c[-2])/max(c[-2],0.01)
        volr=v[-1]/max(np.mean(v[-6:-1]),1)
        bullish=c[-1]>o[-1]
        turnover=999.0
        if 'outstanding' in df.columns:
            out=float(df['outstanding'].values[-1])
            if out>0: turnover=v[-1]/out*100
        if chg<-0.03: drop+=1
        if chg<-0.03 and volr>1.5: vol+=1
        if chg<-0.03 and bullish: bull+=1
        if chg<-0.03 and turnover>3 and turnover<50: turn+=1
        if chg<-0.03 and volr>1.5 and bullish and 3<turnover<50: all_ok+=1
    except:pass
print(f'跌>3%: {drop} → +放量: {vol} → +阳线: {bull} → +换手3-50%: {turn} → =全满足: {all_ok}')
