"""验证akShare API - 修正API名 + 绕过代理"""
import os
# 临时清除代理
for k in ['http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','all_proxy']:
    os.environ.pop(k, None)

import akshare as ak

print("=== 1. 北向资金 ===")
try:
    df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
    print(f"  OK: {len(df)} rows")
except Exception as e1:
    try:
        df = ak.stock_hsgt_hist_em(symbol="沪股通")
        print(f"  stock_hsgt_hist_em OK: {len(df)} rows, cols={list(df.columns)[:5]}")
    except Exception as e2:
        print(f"  ❌ {e1}")
        print(f"  ❌ fallback: {e2}")

print("\n=== 2. 龙虎榜 ===")
try:
    df = ak.stock_lhb_detail_em()
    print(f"  OK: {len(df)} rows, cols={list(df.columns)[:5]}")
except Exception as e1:
    try:
        import akshare
        funcs = [f for f in dir(akshare) if 'lhb' in f.lower()]
        print(f"  可用龙虎榜API: {funcs}")
        if funcs:
            df = getattr(akshare, funcs[0])()
            print(f"  {funcs[0]} OK: {len(df)} rows")
    except Exception as e2:
        print(f"  ❌ {e1}")

print("\n=== 3. 个股资金流 ===")
try:
    df = ak.stock_individual_fund_flow(stock="600519", market="sh")
    print(f"  OK: {len(df)} rows")
except Exception as e:
    print(f"  ❌ {e}")

print("\n=== 4. 行业资金流 ===")
try:
    funcs = [f for f in dir(ak) if 'sector' in f.lower() and 'fund' in f.lower()]
    print(f"  可用API: {funcs}")
    if funcs:
        df = getattr(ak, funcs[0])(indicator="今日")
        print(f"  {funcs[0]} OK: {len(df)} rows")
except Exception as e:
    print(f"  ❌ {e}")

print("\n✅ 完成")
