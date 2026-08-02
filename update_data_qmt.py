"""QMT实时更新日线数据 v1.0
用法: python update_data_qmt.py
从QMT xtdata拉取最新日线 → 更新 stock_data.parquet
"""
import sys, os, time
sys.path.insert(0, r"D:\quant_web")

def update_daily():
    from xtquant import xtdata
    import pandas as pd
    import numpy as np

    # 1. 获取全部A股代码
    print("[QMT-Update] 获取股票列表...")
    all_codes = xtdata.get_stock_list_in_sector("沪深A股")
    all_codes = [c for c in all_codes if c.startswith(('60','00','30','688'))]
    # 加指数 (市场状态检测需要)
    all_codes += ['000300.SH', '000001.SH', '399001.SZ', '399006.SZ']
    print(f"  {len(all_codes)} 只(含指数)")

    # 2. 跳过下载(QMT本地已有缓存), 直接拉数据
    print("[QMT-Update] 拉取全量日线数据...")
    # 拉取全部历史日线 (非60天)
    count_days = 1500  # ~6年日线
    parquet_path = r"D:\quant_web\stock_data.parquet_tmp"
    rows = []
    total = len(all_codes)

    for i, code in enumerate(all_codes):
        try:
            h = xtdata.get_market_data_ex(
                field_list=['open','high','low','close','volume','amount','outstanding'],
                stock_list=[code],
                period='1d',
                count=1500
            )
            if h and code in h and 'close' in h[code] and len(h[code]['close']) > 0:
                c_arr = h[code]['close']
                o_arr = h[code]['open']
                hi_arr = h[code]['high']
                lo_arr = h[code]['low']
                v_arr = h[code]['volume'] if 'volume' in h[code] else []
                a_arr = h[code]['amount'] if 'amount' in h[code] else []
                s_arr = h[code]['outstanding'] if 'outstanding' in h[code] else []
                c_list = list(c_arr) if hasattr(c_arr,'tolist') else c_arr
                o_list = list(o_arr) if hasattr(o_arr,'tolist') else o_arr
                h_list = list(hi_arr) if hasattr(hi_arr,'tolist') else hi_arr
                l_list = list(lo_arr) if hasattr(lo_arr,'tolist') else lo_arr
                v_list = list(v_arr) if hasattr(v_arr,'tolist') else v_arr
                a_list = list(a_arr) if hasattr(a_arr,'tolist') else a_arr
                s_list = list(s_arr) if hasattr(s_arr,'tolist') else s_arr
                dates = c_arr.index if hasattr(c_arr,'index') else range(len(c_list))
                ql_code = code
                if '.SH' in code: ql_code = 'sh' + code.split('.')[0]
                elif '.SZ' in code: ql_code = 'sz' + code.split('.')[0]
                elif '.BJ' in code: ql_code = 'bj' + code.split('.')[0]
                for j in range(len(c_list)):
                    rows.append({
                        'symbol': ql_code,
                        'date': str(dates[j])[:10],
                        'open': float(o_list[j]),
                        'high': float(h_list[j]),
                        'low': float(l_list[j]),
                        'close': float(c_list[j]),
                        'volume': float(v_list[j]) if j < len(v_list) else 0,
                        'amount': float(a_list[j]) if j < len(a_list) else 0,
                        'outstanding': float(s_list[j]) if j < len(s_list) else 0,
                    })
        except: pass
        if (i+1) % 500 == 0:
            print(f"  {i+1}/{total}")

    print(f"  获取 {len(rows)} 条数据")

    # 4. 保存 + 自动校验
    if len(rows) < 500_000:
        print(f"[QMT-Update] ❌ 数据异常: 仅{len(rows)}行, 拒绝覆盖")
        return
    combined = pd.DataFrame(rows)
    _max_date = combined['date'].max()
    _expected_latest = str(pd.Timestamp.now().date() - pd.Timedelta(days=1))[:10]
    if _max_date < _expected_latest:
        print(f"[QMT-Update] ⚠️ 日期滞后: 最新{_max_date}, 期望≥{_expected_latest}")
    n_stocks = combined['symbol'].nunique()
    if n_stocks < 4500:
        print(f"[QMT-Update] ⚠️ 股票数偏少: {n_stocks}只")
    # 缺失率检查
    null_rate = combined['close'].isna().sum() / max(len(combined), 1)
    if null_rate > 0.05:
        print(f"[QMT-Update] ⚠️ close缺失率{null_rate*100:.1f}%")
    combined.to_parquet(parquet_path, index=False)
    os.replace(parquet_path, parquet_path.replace('_tmp', ''))
    print(f"[QMT-Update] ✅ 完成: {n_stocks}只, {len(rows)}行, {_max_date}")

if __name__ == "__main__":
    update_daily()
