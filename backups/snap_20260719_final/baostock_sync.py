"""Baostock 退市股数据同步 v2.0 (2026-07-18)
用途: 下载退市股+上市/退市日期 → 消除回测幸存者偏差
输出:
  1. stock_status.json — 全量股票上市/退市日期 (Point-in-Time过滤)
  2. delisted_stocks.parquet — 退市股历史K线
用法: python baostock_sync.py
"""
import sys, os, pickle, json
import numpy as np
import pandas as pd
from datetime import datetime

STATUS_PATH = r"D:\quant_web\stock_status.json"
DELISTED_PATH = r"D:\quant_framework\delisted_stocks.parquet"
LIVE_PARQUET = r"D:\quant_web\stock_data.parquet"


def _baostock_code_to_qianlong(code: str) -> str:
    """baostock格式 → 潜龙格式: sh.600000 → sh600000"""
    code = str(code).strip()
    if code.startswith('sh.'): return 'sh' + code[3:]
    if code.startswith('sz.'): return 'sz' + code[3:]
    if code.endswith('.SH'): return 'sh' + code[:-3]
    if code.endswith('.SZ'): return 'sz' + code[:-3]
    return code


def download_all_status():
    """从baostock获取全量股票状态(含上市日/退市日)"""
    try:
        import baostock as bs
    except ImportError:
        print("[B1] 请先安装: pip install baostock")
        return None

    bs.login()
    from datetime import date as _date, timedelta as _td
    _today = _date.today()
    if _today.weekday() == 5: _today -= _td(days=1)
    elif _today.weekday() == 6: _today -= _td(days=2)
    today_str = _today.strftime('%Y-%m-%d')

    # ══ Phase 1: 从baostock获取当前全量代码 ══
    print(f"[B1·1] 获取baostock全量代码 (day={today_str})...")
    rs_now = bs.query_all_stock(day=today_str)
    print(f"  error_code={rs_now.error_code}")

    baostock_codes = {}  # {qianlong_sym: tradeStatus}
    if rs_now.error_code == '0':
        while rs_now.next():
            row = rs_now.get_row_data()
            if len(row) >= 2:
                sym = _baostock_code_to_qianlong(row[0])
                if sym: baostock_codes[sym] = row[1]

    # ══ Phase 2: 读 stock_data.parquet 现存股 ══
    print("[B1·2] 对比 stock_data.parquet 找出缺失股...")
    parquet_syms = set()
    if os.path.exists(LIVE_PARQUET):
        import pandas as pd
        pf = pd.read_parquet(LIVE_PARQUET, columns=['symbol'])
        parquet_syms = set(pf['symbol'].unique())
    print(f"  baostock: {len(baostock_codes)}只 | parquet: {len(parquet_syms)}只")

    # 差集 = baostock有但parquet没有 → 退市/停牌/缺失
    missing = {s: baostock_codes[s] for s in baostock_codes if s not in parquet_syms}
    active_in_parquet = {s for s in parquet_syms if s in baostock_codes}

    n_delisted_baostock = sum(1 for v in baostock_codes.values() if str(v) != '1')

    print(f"  parquet中有baostock也有: {len(active_in_parquet)}只")
    print(f"  baostock标记退市/停牌: {n_delisted_baostock}只")
    print(f"  parquet缺失(需下载): {len(missing)}只")

    # ══ Phase 1c: 汇总 stock_status + 查退市日 ══
    stock_status = {}

    # 活跃股: parquet里有的 → active=True
    for sym in parquet_syms:
        stock_status[sym] = {
            'name': '', 'listed': '', 'delisted': None, 'active': True
        }

    # 缺失股(退市/停牌): 逐个查 basic info
    _to_query = list(missing.keys())
    print(f"[B1·3] 查询缺失/退市/停牌 {len(_to_query)} 只日期...")
    for i, sym in enumerate(_to_query):
        stock_status[sym] = {
            'name': '', 'listed': '', 'delisted': None, 'active': False
        }

        # 还原baostock格式查basic
        bs_code = ''
        if sym.startswith('sh'): bs_code = 'sh.' + sym[2:]
        elif sym.startswith('sz'): bs_code = 'sz.' + sym[2:]
        else: bs_code = sym

        if bs_code:
            try:
                rs_basic = bs.query_stock_basic(code=bs_code)
                if rs_basic.error_code == '0':
                    while rs_basic.next():
                        r = rs_basic.get_row_data()
                        stock_status[sym] = {
                            'name': str(r[1]) if len(r) > 1 else '',
                            'listed': str(r[2])[:10] if len(r) > 2 and r[2] else '',
                            'delisted': str(r[3])[:10] if len(r) > 3 and r[3] else None,
                            'active': False
                        }
                        break
            except Exception:
                pass

        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(_to_query)}...")

    bs.logout()

    # 保存
    with open(STATUS_PATH, 'w', encoding='utf-8') as f:
        json.dump(stock_status, f, ensure_ascii=False, indent=2)

    n_delisted = sum(1 for v in stock_status.values() if not v['active'])
    n_active = sum(1 for v in stock_status.values() if v['active'])
    print(f"[B1] stock_status.json 已保存: {len(stock_status)}只 (活跃{n_active}, 退市/停牌{n_delisted})")
    return stock_status


def download_delisted_kline(limit: int = 0):
    """下载退市股历史K线 (limit=0=全部, >0=限制数量)"""
    try:
        import baostock as bs
    except ImportError:
        print("[B1] 请先安装: pip install baostock")
        return None

    # 加载状态
    if not os.path.exists(STATUS_PATH):
        print("[B1] 请先运行 download_all_status()")
        return None

    with open(STATUS_PATH, 'r', encoding='utf-8') as f:
        status = json.load(f)

    # 筛选退市股 (listed可能为空字符串, 退市股默认2010年起)
    delisted = {sym: info for sym, info in status.items()
                if not info.get('active', True)}

    if not delisted:
        print("[B1·2/3] 无退市股, 跳过K线下载")
        bs.logout()
        return {}

    if limit and len(delisted) > limit:
        delisted = dict(list(delisted.items())[:limit])

    print(f"[B1·2/3] 下载 {len(delisted)} 只退市股K线...")

    bs.login()
    data = {}
    failed = 0
    for i, (sym, info) in enumerate(delisted.items()):
        # 还原baostock格式
        if sym.startswith('sh'): bs_code = 'sh.' + sym[2:]
        elif sym.startswith('sz'): bs_code = 'sz.' + sym[2:]
        else: bs_code = sym

        # baostock需要 YYYY-MM-DD 格式
        _listed = (info.get('listed') or '').strip()
        if _listed and len(_listed) >= 8:
            start_date = _listed[:4] + '-' + _listed[4:6] + '-' + _listed[6:8] if '-' not in _listed else _listed[:10]
        else:
            start_date = '2023-01-01'  # 仅近3年

        _delisted = (info.get('delisted') or '').strip()
        if _delisted and len(_delisted) >= 8:
            end_date = _delisted[:4] + '-' + _delisted[4:6] + '-' + _delisted[6:8] if '-' not in _delisted else _delisted[:10]
            # 加30天缓冲, 不超过今天
            try:
                end_dt = pd.to_datetime(end_date) + pd.Timedelta(days=30)
                today = pd.Timestamp.now()
                if end_dt > today:
                    end_dt = today
                end_date = end_dt.strftime('%Y-%m-%d')
            except:
                end_date = datetime.now().strftime('%Y-%m-%d')
        else:
            end_date = datetime.now().strftime('%Y-%m-%d')

        try:
            rs = bs.query_history_k_data_plus(
                bs_code, "date,open,high,low,close,volume,amount",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="2"
            )
            if rs.error_code != '0':
                failed += 1
                continue

            # 迭代器模式读取
            klines = []
            while rs.next():
                klines.append(rs.get_row_data())

            if len(klines) < 30:
                failed += 1
                continue

            df = pd.DataFrame(klines, columns=['date','open','high','low','close','volume','amount'])
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            # 去零 + 数据清洗
            df = df[(df['close'] > 0) & (df['open'] > 0)]
            if len(df) >= 30:
                data[sym] = df
        except Exception:
            failed += 1

        if (i+1) % 50 == 0:
            print(f"  {i+1}/{len(delisted)}... 成功{len(data)} 失败{failed}")

    bs.logout()

    # 保存为parquet (兼容主数据管线)
    if data:
        rows = []
        for sym, df in data.items():
            for date_idx, row in df.iterrows():
                rows.append({
                    'symbol': sym,
                    'date': date_idx,
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close']),
                    'volume': float(row['volume']),
                    'amount': float(row['amount']),
                })
        out_df = pd.DataFrame(rows)
        out_df.to_parquet(DELISTED_PATH, index=False)
        print(f"[B1] delisted_stocks.parquet 已保存: {len(data)}只, {len(rows)}行, {os.path.getsize(DELISTED_PATH)/1024/1024:.1f}MB")
    else:
        print("[B1] ⚠️ 无退市股数据可保存")

    print(f"[B1] 完成: {len(data)}只有效, {failed}只失败")
    return data


def merge_status_to_names():
    """将退市股名称合并到 stock_names_full.csv"""
    if not os.path.exists(STATUS_PATH):
        print("[B1] stock_status.json 不存在, 跳过名称合并")
        return

    names_csv = r"D:\quant_web\stock_names_full.csv"
    if not os.path.exists(names_csv):
        print(f"[B1] {names_csv} 不存在, 跳过")
        return

    with open(STATUS_PATH, 'r', encoding='utf-8') as f:
        status = json.load(f)

    # 读现有名称
    existing = set()
    with open(names_csv, 'r', encoding='utf-8') as f:
        for line in f:
            p = line.strip().split(',')
            if p:
                existing.add(p[0])

    # 追加退市股名称
    try:
        added = 0
        with open(names_csv, 'a', encoding='utf-8') as f:
            for sym, info in status.items():
                if sym not in existing and info.get('name', '').strip():
                    f.write(f"{sym},{info['name']}\n")
                    added += 1
        print(f"[B1] stock_names_full.csv: 追加 {added} 只退市股名称")
    except PermissionError:
        print(f"[B1] ⚠️ stock_names_full.csv 被占用, 跳过名称追加 (手动合并: {added}只)")


if __name__ == "__main__":
    print("=" * 55)
    print("  Baostock 退市股同步 v2.0")
    print("=" * 55)

    # Phase 1: 获取全量状态
    stock_status = download_all_status()
    if not stock_status:
        print("[B1] ❌ 状态获取失败, 退出")
        sys.exit(1)

    # Phase 2: 下载退市股K线
    data = download_delisted_kline(limit=0)  # 0=全部

    # Phase 3: 合并名称
    merge_status_to_names()

    print("\n✅ B1 同步完成。")
    print(f"   状态文件: {STATUS_PATH}")
    print(f"   退市K线: {DELISTED_PATH}")
    print(f"   下一步: data_loader 加载时自动合并退市股+ PIT过滤")
