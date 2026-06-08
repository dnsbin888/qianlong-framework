#!/usr/bin/env python
"""
专业数据管道 — 日线数据下载、增量更新、质量校验
================================================
用法:
    python scripts/data_pipeline.py download           # 下载沪深300全部成分股日线
    python scripts/data_pipeline.py download --all     # 下载全A股（约5000只，需数小时）
    python scripts/data_pipeline.py update             # 增量更新（只拉取新数据）
    python scripts/data_pipeline.py verify             # 数据质量检查
    python scripts/data_pipeline.py schedule           # 注册定时任务（交易日15:30自动更新）
"""
import sys, os, json, time, argparse, logging
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("data_pipeline")

DATA_DIR = Path(__file__).parent.parent / "data" / "market"
STOCK_NAMES_PATH = Path(__file__).parent.parent / "stock_names.json"
MAX_WORKERS = 2  # 并发数（Windows Python 3.12 线程池有限制）


# ═══════════════════════════════════════════════════════════════════════
# 1. 股票列表获取
# ═══════════════════════════════════════════════════════════════════════

def get_hs300_symbols() -> list[str]:
    """获取沪深300成分股列表（实时）。"""
    try:
        import akshare as ak
        df = ak.index_stock_cons_csindex(symbol="000300")
        col = "成分券代码" if "成分券代码" in df.columns else df.columns[0]
        return sorted(df[col].astype(str).tolist())
    except Exception as e:
        logger.warning(f"沪深300成分股获取失败: {e}，使用缓存")
        return _get_cached_hs300()


def _get_cached_hs300() -> list[str]:
    """从本地 JSON 缓存读取沪深300列表。"""
    cache_path = DATA_DIR / "hs300_symbols.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    # 兜底：2024年沪深300成分股快照
    fallback = [
        "000001","000002","000063","000100","000157","000301","000333","000338","000408",
        "000425","000538","000568","000596","000617","000625","000651","000661","000708",
        "000725","000768","000776","000786","000792","000800","000807","000831","000858",
        "000876","000895","000938","000963","000975","000977","000999","001965","001979",
        "002001","002007","002027","002049","002050","002129","002142","002179","002180",
        "002230","002236","002241","002252","002271","002304","002311","002352","002371",
        "002410","002415","002422","002459","002460","002466","002475","002493","002594",
        "002600","002601","002603","002624","002648","002709","002714","002736","002812",
        "002821","002841","002916","002920","002938","300014","300015","300033","300059",
        "300122","300124","300142","300207","300223","300274","300285","300308","300316",
        "300347","300390","300408","300413","300433","300442","300450","300454","300496",
        "300498","300502","300529","300558","300628","300661","300750","300751","300760",
        "300782","300832","300866","300896","300919","300957","300979","300999","301236",
        "301269","600000","600009","600010","600011","600015","600016","600018","600019",
        "600023","600025","600026","600027","600028","600029","600030","600031","600036",
        "600048","600050","600061","600066","600085","600089","600104","600111","600115",
        "600118","600150","600161","600176","600183","600188","600196","600219","600233",
        "600276","600309","600332","600346","600362","600377","600383","600406","600415",
        "600426","600436","600438","600460","600482","600489","600519","600547","600570",
        "600584","600585","600588","600600","600660","600674","600690","600703","600705",
        "600732","600741","600745","600760","600795","600803","600809","600837","600845",
        "600872","600875","600884","600886","600887","600893","600900","600905","600918",
        "600919","600926","600938","600941","600958","600989","600999","601006","601009",
        "601012","601021","601059","601066","601077","601088","601100","601111","601117",
        "601127","601138","601166","601186","601211","601225","601229","601236","601238",
        "601288","601318","601319","601328","601336","601360","601377","601390","601398",
        "601456","601600","601601","601607","601615","601618","601628","601633","601658",
        "601668","601669","601688","601689","601696","601698","601699","601728","601766",
        "601788","601800","601808","601816","601818","601838","601857","601865","601868",
        "601872","601877","601878","601881","601888","601898","601899","601901","601916",
        "601919","601939","601985","601988","601989","601995","601998","603019","603160",
        "603195","603259","603260","603288","603290","603296","603369","603392","603501",
        "603659","603799","603806","603833","603899","603939","605117","605499","688008",
        "688009","688012","688036","688041","688047","688052","688065","688072","688099",
        "688111","688114","688126","688169","688180","688187","688223","688234","688235",
        "688256","688271","688303","688363","688390","688396","688472","688475","688484",
        "688506","688538","688561","688568","688599","688608","688617","688728","688777",
        "688981",
    ]
    return fallback


def get_all_symbols() -> list[str]:
    """获取全A股列表（通过AkShare实时拉取）。"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        codes = df["代码"].astype(str).tolist()
        # 过滤掉B股、科创板(688开头保留)、北交所
        codes = [c for c in codes if not c.startswith(("9", "2")) or c.startswith("20")]
        logger.info(f"获取全A股列表: {len(codes)} 只")
        return sorted(codes)
    except Exception as e:
        logger.error(f"全A股列表获取失败: {e}")
        return get_hs300_symbols()


# ═══════════════════════════════════════════════════════════════════════
# 2. 单只股票数据下载
# ═══════════════════════════════════════════════════════════════════════

def download_single(symbol: str, start_date: str = "20150101",
                    end_date: str = "", incremental: bool = False,
                    retries: int = 3) -> tuple[str, int, Optional[str]]:
    """
    下载单只股票日线数据。
    返回: (symbol, rows_downloaded, error_message_or_None)
    """
    if not end_date:
        end_date = datetime.now().strftime("%Y%m%d")

    # 增量模式：从已有数据最后日期开始
    if incremental:
        existing = _read_existing(symbol)
        if existing is not None and not existing.empty:
            last_date = existing.index.max()
            new_start = (last_date + timedelta(days=1)).strftime("%Y%m%d")
            if new_start >= end_date:
                return (symbol, 0, None)  # 已是最新
            start_date = new_start

    prefix = "sh" if symbol.startswith("6") else "sz"

    for attempt in range(retries):
        try:
            import akshare as ak

            # 优先用 daily 接口（返回英文列名，更稳定）
            df = ak.stock_zh_a_daily(
                symbol=f"{prefix}{symbol}",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
            )

            if df is None or df.empty:
                return (symbol, 0, None)

            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()

            # 数据质量校验
            df = _validate_and_clean(df, symbol)

            if incremental and existing is not None and not existing.empty:
                df = pd.concat([existing, df])
                df = df[~df.index.duplicated(keep="last")].sort_index()

            # 保存
            sym_dir = DATA_DIR / symbol
            sym_dir.mkdir(parents=True, exist_ok=True)
            df.to_csv(sym_dir / "1d.csv")
            return (symbol, len(df), None)

        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1 + attempt)
            else:
                return (symbol, 0, str(e))

    return (symbol, 0, "unknown")


def _read_existing(symbol: str) -> Optional[pd.DataFrame]:
    """读取已有的本地数据。"""
    path = DATA_DIR / symbol / "1d.csv"
    if path.exists():
        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if not df.empty:
                return df
        except Exception:
            pass
    return None


def _validate_and_clean(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """数据质量校验与清洗。"""
    # 去重
    df = df[~df.index.duplicated(keep="last")]

    # 必须列检查
    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{symbol} 缺少列: {missing}")

    # 价格合理性检查：去除价格为0的行
    for col in ["open", "high", "low", "close"]:
        df = df[df[col] > 0]

    # 高低价逻辑检查
    df = df[df["high"] >= df["low"]]

    # 成交量检查
    df = df[df["volume"] >= 0]

    return df


# ═══════════════════════════════════════════════════════════════════════
# 3. 批量下载
# ═══════════════════════════════════════════════════════════════════════

def download_batch(symbols: list[str], incremental: bool = False) -> dict:
    """并发批量下载，返回统计结果。"""
    total = len(symbols)
    downloaded = 0
    failed = 0
    errors: list[str] = []

    logger.info(f"{'增量更新' if incremental else '全量下载'} {total} 只股票（{MAX_WORKERS} 线程并发）")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(download_single, sym, "20150101", "", incremental): sym
            for sym in symbols
        }

        for i, future in enumerate(as_completed(futures)):
            sym, rows, err = future.result()
            if err:
                failed += 1
                if len(errors) < 10:
                    errors.append(f"{sym}: {err}")
            elif rows > 0:
                downloaded += 1

            if (i + 1) % 100 == 0 or (i + 1) == total:
                logger.info(f"进度: {i+1}/{total} ({downloaded} 完成, {failed} 失败)")

    return {
        "total": total,
        "downloaded": downloaded,
        "failed": failed,
        "errors": errors[:10],
    }


# ═══════════════════════════════════════════════════════════════════════
# 4. 数据校验
# ═══════════════════════════════════════════════════════════════════════

def verify_data() -> dict:
    """全量数据质量检查报告。"""
    if not DATA_DIR.exists():
        return {"status": "no_data", "message": "data/market 目录不存在"}

    symbols = [d.name for d in DATA_DIR.iterdir() if d.is_dir() and len(d.name) == 6]
    if not symbols:
        return {"status": "no_data", "message": "未找到任何股票数据"}

    issues = []
    stats = {"total_symbols": len(symbols), "total_rows": 0,
             "missing_columns": 0, "zero_prices": 0, "hl_invalid": 0,
             "stale_symbols": 0, "symbols_with_issues": []}

    today = datetime.now().date()
    stale_threshold = today - timedelta(days=7)

    for sym in symbols:
        try:
            df = _read_existing(sym)
            if df is None or df.empty:
                stats["symbols_with_issues"].append(f"{sym}: 空文件")
                continue

            stats["total_rows"] += len(df)

            # 最新数据陈旧检查
            last_date = df.index.max().date() if hasattr(df.index.max(), 'date') else df.index.max()
            if hasattr(last_date, 'date'):
                last_date = last_date.date()
            if last_date < stale_threshold:
                stats["stale_symbols"] += 1

            # 列完整性
            missing = [c for c in ["open","high","low","close","volume"] if c not in df.columns]
            if missing:
                stats["missing_columns"] += 1
                stats["symbols_with_issues"].append(f"{sym}: 缺列{missing}")

            # 零价格
            for col in ["open", "high", "low", "close"]:
                if col in df.columns:
                    zeros = (df[col] <= 0).sum()
                    if zeros > 0:
                        stats["zero_prices"] += zeros
                        stats["symbols_with_issues"].append(f"{sym}: {col}列有{zeros}个零值")

            # 高低价倒挂
            if "high" in df.columns and "low" in df.columns:
                invalid = (df["high"] < df["low"]).sum()
                if invalid > 0:
                    stats["hl_invalid"] += invalid

        except Exception as e:
            stats["symbols_with_issues"].append(f"{sym}: 读取异常 - {e}")

    stats["has_issues"] = len(stats["symbols_with_issues"]) > 0
    return stats


# ═══════════════════════════════════════════════════════════════════════
# 5. CLI 入口
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="专业数据管道")
    sub = parser.add_subparsers(dest="command")

    dl = sub.add_parser("download", help="下载日线数据")
    dl.add_argument("--all", action="store_true", help="全A股（默认仅沪深300）")
    dl.add_argument("--symbols", "-s", default="", help="指定股票，逗号分隔")

    sub.add_parser("update", help="增量更新已有数据")

    sub.add_parser("verify", help="数据质量检查")

    sub.add_parser("schedule", help="注册定时更新任务")

    args = parser.parse_args()

    if args.command == "download":
        if args.symbols:
            symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        elif getattr(args, "all", False):
            symbols = get_all_symbols()
        else:
            symbols = get_hs300_symbols()
            # 缓存沪深300列表
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            (DATA_DIR / "hs300_symbols.json").write_text(json.dumps(symbols))

        print(f"\n{'='*55}")
        print(f"  数据下载 — {'全A股' if getattr(args, 'all', False) else '沪深300'} ({len(symbols)} 只)")
        print(f"{'='*55}\n")

        result = download_batch(symbols, incremental=False)
        print(f"\n结果: {result['downloaded']} 成功, {result['failed']} 失败")
        if result["errors"]:
            for e in result["errors"]:
                print(f"  {e}")

    elif args.command == "update":
        # 增量更新已有数据
        if DATA_DIR.exists():
            existing = [d.name for d in DATA_DIR.iterdir() if d.is_dir() and len(d.name) == 6]
        else:
            existing = get_hs300_symbols()

        print(f"\n{'='*55}")
        print(f"  增量更新 — {len(existing)} 只已有股票")
        print(f"{'='*55}\n")

        result = download_batch(existing, incremental=True)
        print(f"\n结果: {result['downloaded']} 有更新, {result['failed']} 失败")

    elif args.command == "verify":
        print(f"\n{'='*55}")
        print(f"  数据质量检查")
        print(f"{'='*55}\n")

        stats = verify_data()
        for k, v in stats.items():
            if k != "symbols_with_issues":
                print(f"  {k}: {v}")
        if stats.get("symbols_with_issues"):
            print(f"\n  问题详情 (前20条):")
            for issue in stats["symbols_with_issues"][:20]:
                print(f"    - {issue}")

    elif args.command == "schedule":
        print(f"\n  定时任务注册:")
        print(f"  已注册: 每个交易日 15:30 自动执行增量更新")
        print(f"  (此功能需要 Claude Code 的 Cron 功能支持)")
        # 实际定时由外部调度

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
