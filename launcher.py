"""
A股量化策略分析平台 — 全能启动器 v3.0
双击 启动量化终端.bat 即可打开此菜单

v3.0 新增: 配置向导 · 策略市场 · 数据下载 · 实时行情监控
"""
import subprocess
import sys
import os

# 确保控制台正确输出中文
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_DIR = r"d:\quant_framework"
STREAMLIT_PORT = 8501

# ── 可用功能注册表 ──────────────────────────────────────────
MENU = [
    ("HEADER", "🎯 策略回测"),
    ("ITEM",  1, "快速回测 (tdx2_final)",     "涨停突破牛线 + 底部反转, 3分钟全市场扫描", "run_backtest_fast.py"),
    ("ITEM",  2, "多因子回测 v2",             "14因子组合对比 + IC排名",                "run_multi_factor_v2.py"),
    ("ITEM",  3, "全因子独立回测",            "逐个因子评估, 输出 IC/IR 结果",          "run_factor_backtest.py"),
    ("ITEM",  4, "T+1 隔日策略回测",          "买入次日开盘卖出模式",                    "run_t1_backtest.py"),
    ("ITEM",  5, "智能因子分析 (IC/IR)",      "因子有效性统计检验",                      "run_smart_factor.py"),
    ("ITEM", 15, "统一回测框架 (推荐)",       "使用 FrameworkConfig 驱动的完整回测",     "UNIFIED_BACKTEST"),
    ("HEADER", "📊 可视化终端"),
    ("ITEM",  6, "PyQt5 桌面终端 (专业版)",   "K线+信号+情绪+复盘, 对标 vnpy 风格",     "quant_terminal.py"),
    ("ITEM",  7, "Streamlit 策略仪表盘",      "浏览器打开, 收益曲线+交易分析",           "STREAMLIT:app.py"),
    ("ITEM",  8, "Streamlit 可视化面板",      "情绪监控+信号回放+深度复盘",              "STREAMLIT:visual_dashboard.py"),
    ("HEADER", "📋 报告 & 分析"),
    ("ITEM",  9, "生成 HTML 回测报告",        "独立浏览器报告, 无需服务器",              "generate_report.py"),
    ("ITEM", 10, "市场情绪分析",              "涨跌停家数 / 市场宽度 / 成交额",          "run_sentiment.py"),
    ("HEADER", "🔧 工具箱"),
    ("ITEM", 11, "检查环境依赖",              "查看 Python 包和数据文件状态",            "CHECK_ENV"),
    ("ITEM", 12, "安装/更新依赖",             "一键安装核心或完整依赖",                   "INSTALL"),
    ("ITEM", 13, "查看最近回测结果",          "读取 trade_log.csv 汇总",                "RESULTS"),
    ("ITEM", 14, "打开项目文件夹",            "在资源管理器中打开",                       "OPENDIR"),
    ("ITEM", 16, "策略市场 (浏览所有策略)",   "查看策略列表、参数、适用场景",            "STRATEGY_MARKET"),
    ("ITEM", 17, "数据下载工具",              "通过 AkShare 批量下载历史日线数据",       "DATA_DOWNLOAD"),
    ("ITEM", 18, "配置向导",                  "首次运行引导：选择数据源、模式、资金",     "CONFIG_WIZARD"),
]

# ── Rich 颜色主题 ────────────────────────────────────────────
C = {
    "title":    "bold yellow",
    "subtitle": "dim white",
    "border":   "cyan",
    "header":   "bold cyan",
    "backtest": "green",
    "visual":   "blue",
    "report":   "yellow",
    "tool":     "dim white",
    "num":      "bold green",
    "key":      "bold cyan",
    "hint":     "dim",
}

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def get_rich():
    """懒加载 rich, 未安装时降级为纯文本"""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text
        from rich.align import Align
        from rich.box import Box, DOUBLE
        return Console(), Table, Panel, Text, Align, Box, DOUBLE, True
    except ImportError:
        return None, None, None, None, None, None, None, False

def print_banner_rich(console, Panel, Text, Align, DOUBLE):
    """Rich 渲染的彩色 Banner"""
    title = Text("A 股 量 化 策 略 分 析 平 台  v 3 . 0", style="bold yellow")
    subtitle = Text("统一配置 · 策略市场 · 多因子回测 · 情绪监控 · 实时行情", style="dim white")

    py_ver = sys.version.split()[0]
    info = Text()
    info.append("Python: ", style="dim")
    info.append(sys.executable, style="green")
    info.append(f"  (v{py_ver})", style="dim")
    info.append(f"\n项目:   ", style="dim")
    info.append(PROJECT_DIR, style="dim")

    # 检查是否首次运行
    config_exists = os.path.exists(os.path.join(PROJECT_DIR, "config", "default.yaml"))
    if config_exists:
        info.append("\n配置:   ", style="dim")
        info.append("已就绪 ✓", style="green")
    else:
        info.append("\n配置:   ", style="dim")
        info.append("未配置 — 建议运行 [18] 配置向导", style="bold yellow")

    header_content = Text()
    header_content.append(title)
    header_content.append("\n")
    header_content.append(subtitle)
    header_content.append("\n\n")
    header_content.append(info)

    panel = Panel(header_content, box=DOUBLE, border_style="cyan", padding=(1, 2))
    console.print(panel)

def print_menu_rich(console, Table, Panel, Text, Align, DOUBLE):
    """Rich 渲染的彩色菜单"""
    content = Text()
    first_section = True

    for item in MENU:
        kind = item[0]
        if kind == "HEADER":
            if not first_section:
                content.append("\n")
            first_section = False
            hdr = item[1]
            section_colors = {
                "🎯 策略回测": "green", "📊 可视化终端": "blue",
                "📋 报告 & 分析": "yellow", "🔧 工具箱": "bright_black",
            }
            color = section_colors.get(hdr, "cyan")
            content.append(f"  {hdr}\n", style=f"bold {color}")
        elif kind == "ITEM":
            num, name, desc = item[1], item[2], item[3]
            num_str = f"[{num:>2}]" if num < 10 else f"[{num}]"
            content.append(f"     {num_str}  ", style="bold green")
            content.append(f"{name}", style="white")
            content.append(f"  -  {desc}\n", style="dim")

    content.append("\n     [ 0]  ", style="bold red")
    content.append("退出\n", style="dim white")
    content.append("\n  提示: 也可直接在命令行传参跳过菜单  例: 启动量化终端.bat 1\n", style="dim")
    content.append("  v3.0 新增: 配置向导 [18] · 策略市场 [16] · 数据下载 [17]\n", style="dim")

    panel = Panel(content, box=DOUBLE, border_style="cyan", padding=(1, 2))
    console.print(panel)

def print_banner_plain():
    """纯文本降级版"""
    print("""
  +----------------------------------------------------+
  |      A 股 量 化 策 略 分 析 平 台  v 3 . 0       |
  |     统一配置 · 策略市场 · 多因子回测 · 情绪监控    |
  +----------------------------------------------------+
  |  Python: {py}
  |  项目:   d:\\quant_framework
  +----------------------------------------------------+""".format(py=sys.executable))

def print_menu_plain():
    """纯文本降级菜单"""
    for item in MENU:
        kind = item[0]
        if kind == "HEADER":
            print(f"  |  {item[1]:<50} |")
        elif kind == "ITEM":
            num, name, desc = item[1], item[2], item[3]
            print(f"  |   [{num:>2}]  {name:<44} |")
            if desc:
                print(f"  |         {desc:<46} |")
    print("  +----------------------------------------------------+")
    print("  |  [ 0]  退出                                         |")
    print("  +----------------------------------------------------+")

# ── 功能执行 ──────────────────────────────────────────────

def run_script(script_name):
    path = os.path.join(PROJECT_DIR, script_name)
    if not os.path.exists(path):
        print(f"\n  [错误] 找不到脚本: {path}")
        input("\n  按回车返回...")
        return
    print(f"\n  正在运行: {script_name}")
    print("  " + "-" * 50)
    result = subprocess.run([sys.executable, "-u", path], cwd=PROJECT_DIR)
    if result.returncode != 0:
        print(f"\n  [注意] 脚本退出码: {result.returncode}")

def run_streamlit(script_name):
    try:
        import streamlit
    except ImportError:
        print("\n  正在安装 streamlit + plotly ...")
        subprocess.run([sys.executable, "-m", "pip", "install", "streamlit", "plotly", "-q"])
    try:
        result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                               encoding='gbk', errors='replace')
        for line in result.stdout.split("\n"):
            if f":{STREAMLIT_PORT}" in line and "LISTENING" in line:
                pid = line.strip().split()[-1]
                print(f"  释放端口 {STREAMLIT_PORT} (PID: {pid})...")
                subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
    except Exception:
        pass
    print(f"\n  启动 Streamlit: http://localhost:{STREAMLIT_PORT}")
    print("  按 Ctrl+C 停止服务器\n")
    subprocess.run([
        sys.executable, "-m", "streamlit", "run", script_name,
        "--server.port", str(STREAMLIT_PORT),
        "--server.headless", "true",
    ], cwd=PROJECT_DIR)

def check_env():
    print("\n  Python: " + sys.executable)
    print(f"  版本: {sys.version}")
    print("\n  关键包:")
    for pkg in ["pandas", "numpy", "pydantic", "pyyaml", "matplotlib", "rich",
                "streamlit", "PyQt5", "plotly", "pandas_ta", "sqlalchemy", "akshare"]:
        try:
            mod = __import__(pkg)
            ver = getattr(mod, "__version__", "?")
            print(f"    [已安装] {pkg}=={ver}")
        except ImportError:
            print(f"    [未安装] {pkg}")
    print("\n  数据文件:")
    for f in ["trade_log.csv", "equity_curve.csv", "sentiment_data.csv", "factor_ic_results.csv"]:
        path = os.path.join(PROJECT_DIR, f)
        if os.path.exists(path):
            print(f"    [存在] {f} ({os.path.getsize(path):,} bytes)")
        else:
            print(f"    [暂无] {f}")

    # 检查通达信数据
    print("\n  通达信数据目录:")
    from quant_framework.config.paths import get_tdx_data_root
    tdx = get_tdx_data_root()
    if tdx:
        print(f"    [找到] {tdx}")
    else:
        print(f"    [未找到] 设置 TDX_DATA_ROOT 环境变量或放置到默认路径")

    # 检查配置文件
    config_path = os.path.join(PROJECT_DIR, "config", "default.yaml")
    print(f"\n  配置文件:")
    if os.path.exists(config_path):
        print(f"    [存在] config/default.yaml")
    else:
        print(f"    [未找到] 运行 [18] 配置向导创建")

def install_deps():
    print("\n  [1] 核心依赖 (回测必需)")
    print("  [2] 完整依赖 (含 Streamlit + PyQt5)")
    print("  [3] 全部 + AkShare 数据源")
    print("  [0] 返回")
    choice = input("\n  选择: ").strip()
    core = "pandas numpy pydantic pyyaml matplotlib rich structlog python-dateutil"
    full = core + " streamlit plotly PyQt5 mplfinance sqlalchemy aiohttp pandas-ta"
    akshare_deps = full + " akshare scipy statsmodels"
    if choice == "1":
        print("  安装核心依赖...")
        subprocess.run([sys.executable, "-m", "pip", "install"] + core.split() + ["--quiet"])
    elif choice == "2":
        print("  安装完整依赖 (可能需要几分钟)...")
        subprocess.run([sys.executable, "-m", "pip", "install"] + full.split() + ["--quiet"])
    elif choice == "3":
        print("  安装全部依赖 (可能需要几分钟)...")
        subprocess.run([sys.executable, "-m", "pip", "install"] + akshare_deps.split() + ["--quiet"])
    print("  完成!")

def view_results():
    trade_path = os.path.join(PROJECT_DIR, "trade_log.csv")
    equity_path = os.path.join(PROJECT_DIR, "equity_curve.csv")
    if not os.path.exists(trade_path):
        print("\n  暂无交易数据。请先运行回测 [1-5] 或 [15]。")
        return
    import pandas as pd
    t = pd.read_csv(trade_path)
    if len(t) == 0:
        print("\n  交易记录为空。")
        return
    rets = t["return_pct"].values
    wr = (rets > 0).mean()
    pnl = t["net_profit"].sum() if "net_profit" in t.columns else 0
    print(f"\n  总交易:    {len(t)} 笔")
    print(f"  胜率:      {wr:.1%}")
    print(f"  平均收益:  {rets.mean():+.2%}")
    print(f"  最佳:      {rets.max():+.2%}")
    print(f"  最差:      {rets.min():+.2%}")
    pos = rets[rets > 0]; neg = rets[rets < 0]
    if len(pos) > 0 and len(neg) > 0:
        print(f"  盈亏比:    {abs(pos.mean() / neg.mean()):.2f}")
    print(f"  总盈亏:    {pnl:+,.0f} 元")
    if os.path.exists(equity_path):
        eq = pd.read_csv(equity_path)
        eq_col = eq["equity"] if "equity" in eq.columns else eq.iloc[:, 1]
        print(f"  资金:      {eq_col.iloc[0]:,.0f} -> {eq_col.iloc[-1]:,.0f}")
        print(f"  总收益率:  {(eq_col.iloc[-1] / eq_col.iloc[0] - 1):+.2%}")


# ── v3.0 新功能 ──────────────────────────────────────────

def unified_backtest():
    """统一回测框架入口 — 使用 FrameworkConfig 驱动."""
    script = os.path.join(PROJECT_DIR, "scripts", "run_backtest.py")
    if not os.path.exists(script):
        print("\n  [错误] 找不到统一回测脚本")
        input("\n  按回车返回...")
        return

    print("\n  === 统一回测框架 ===")
    print("  使用 FrameworkConfig 配置，支持策略市场中的所有策略。\n")
    strategy = input("  策略名称 (回车查看可用策略): ").strip()
    if not strategy:
        strategy_market()
        strategy = input("\n  请输入策略名称: ").strip()
    if not strategy:
        return

    start = input("  开始日期 (回车默认 2024-01-01): ").strip() or "2024-01-01"
    end = input("  结束日期 (回车默认今天): ").strip() or ""
    capital = input("  初始资金 (回车默认 100万): ").strip() or "1000000"
    sizer = input("  仓位算法 (回车默认 fixed_ratio): ").strip() or "fixed_ratio"

    cmd = [
        sys.executable, "-u", script,
        "-c", os.path.join(PROJECT_DIR, "config", "default.yaml"),
        "-s", strategy,
        "--start", start,
        "--capital", capital,
        "--sizer", sizer,
    ]
    if end:
        cmd.extend(["--end", end])

    print(f"\n  运行: {' '.join(cmd[1:])}")
    print("  " + "-" * 50)
    subprocess.run(cmd, cwd=PROJECT_DIR)


def strategy_market():
    """策略市场 — 浏览所有可用策略."""
    sys.path.insert(0, os.path.join(PROJECT_DIR, "src"))
    try:
        from quant_framework.strategy.registry import StrategyRegistry
        registry = StrategyRegistry.instance()
        strategies = registry.list_all()

        if not strategies:
            print("\n  未找到已注册的策略。请检查策略模块是否正确安装。")
        else:
            print(f"\n  === 策略市场 ({len(strategies)} 个策略) ===\n")
            categories: dict[str, list] = {}
            for s in strategies:
                categories.setdefault(s.category, []).append(s)

            for cat, strats in sorted(categories.items()):
                print(f"  [{cat}]")
                for s in strats:
                    risk_icon = {"低": "🟢", "中等": "🟡", "高": "🔴"}.get(s.risk_level, "⚪")
                    print(f"    {risk_icon} {s.label:<16s} ({s.name})")
                    print(f"       {s.description}")
                    print(f"       适用: {s.market} | 周期: {s.recommended_interval} | 风险: {s.risk_level}")
                    if s.params:
                        param_str = ", ".join(
                            f"{k}={v.get('default')}" for k, v in list(s.params.items())[:5]
                        )
                        print(f"       参数: {param_str}")
                    print()
    except ImportError as e:
        print(f"\n  无法加载策略注册表: {e}")
    except Exception as e:
        print(f"\n  加载策略时出错: {e}")

    input("  按回车返回...")


def data_download():
    """数据下载工具 — 通过 AkShare 批量下载历史数据."""
    print("\n  === 数据下载工具 ===\n")
    print("  此功能通过 AkShare 下载 A 股历史日线数据")
    print("  数据将保存为 .day 格式，兼容 THSDayDataProvider。\n")

    print("  [1] 下载单只股票 (如 600000)")
    print("  [2] 批量下载沪深300成分股")
    print("  [3] 下载全市场股票 (需要较长时间)")
    print("  [0] 返回")
    choice = input("\n  选择: ").strip()

    if choice == "1":
        symbol = input("  股票代码 (如 600000): ").strip()
        if not symbol:
            return
        start = input("  开始日期 (YYYYMMDD, 回车默认 20200101): ").strip() or "20200101"
        print(f"\n  正在下载 {symbol} 从 {start}...")
        _download_single(symbol, start)
    elif choice == "2":
        print("\n  正在下载沪深300成分股历史数据...")
        _download_batch("hs300")
    elif choice == "3":
        confirm = input("\n  确认下载全市场 (~5000只股票)？(y/N): ").strip().lower()
        if confirm == "y":
            print("\n  正在下载全市场数据 (这可能需要几小时)...")
            _download_batch("all")
    elif choice == "0":
        return

    input("\n  按回车返回...")


def _download_single(symbol: str, start_date: str):
    """Download a single stock's daily data using AkShare."""
    try:
        import akshare as ak
        end_date = "20251231"  # default end
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                start_date=start_date, end_date=end_date, adjust="qfq")
        if df is None or df.empty:
            print(f"  未获取到 {symbol} 的数据")
            return

        # Save as CSV in data directory
        data_dir = os.path.join(PROJECT_DIR, "data", "market", symbol)
        os.makedirs(data_dir, exist_ok=True)
        csv_path = os.path.join(data_dir, "1d.csv")
        df.to_csv(csv_path, index=False)
        print(f"  已保存 {len(df)} 条记录到: {csv_path}")
    except ImportError:
        print("  [错误] 需要安装 akshare: pip install akshare")
    except Exception as e:
        print(f"  下载失败: {e}")


def _download_batch(scope: str):
    """Download a batch of stocks."""
    try:
        import akshare as ak
    except ImportError:
        print("  [错误] 需要安装 akshare: pip install akshare")
        return

    data_dir = os.path.join(PROJECT_DIR, "data", "market")
    os.makedirs(data_dir, exist_ok=True)

    symbols: list[str] = []
    if scope == "hs300":
        try:
            df = ak.index_stock_cons_csindex(symbol="000300")
            symbols = df["成分券代码"].tolist() if "成分券代码" in df.columns else df.iloc[:, 0].tolist()
        except Exception:
            symbols = ["600000", "600036", "601318", "000858", "002415"]  # fallback
    else:
        print("  全市场下载暂请手动分批执行。")
        return

    print(f"  共 {len(symbols)} 只股票待下载...")
    for i, sym in enumerate(symbols):
        try:
            df = ak.stock_zh_a_hist(symbol=sym, period="daily",
                                    start_date="20200101", end_date="20251231", adjust="qfq")
            if df is not None and not df.empty:
                sym_dir = os.path.join(data_dir, sym)
                os.makedirs(sym_dir, exist_ok=True)
                df.to_csv(os.path.join(sym_dir, "1d.csv"), index=False)
            if (i + 1) % 50 == 0:
                print(f"  进度: {i+1}/{len(symbols)}")
        except Exception:
            pass  # Skip failed downloads
    print(f"  完成! 数据保存至: {data_dir}")


def config_wizard():
    """首次运行配置向导 — 引导用户创建 config/default.yaml."""
    print("\n  === 首次运行配置向导 ===\n")
    print("  此向导将帮您创建 config/default.yaml 配置文件。\n")

    config_dir = os.path.join(PROJECT_DIR, "config")
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "default.yaml")

    if os.path.exists(config_path):
        overwrite = input("  配置文件已存在。覆盖? (y/N): ").strip().lower()
        if overwrite != "y":
            print("  已取消。")
            return

    # Step 1: 数据源
    print("\n  [1/5] 选择数据源:")
    print("    1. AkShare (免费, 无需注册, 推荐)")
    print("    2. 通达信本地 .day 文件")
    print("    3. TuShare (需 token)")
    print("    4. 同花顺 THS (需安装 THS)")
    ds_choice = input("  选择 (1-4, 回车默认 1): ").strip() or "1"
    ds_map = {"1": "akshare", "2": "csv", "3": "tushare", "4": "ths"}
    provider = ds_map.get(ds_choice, "akshare")

    # Step 2: 交易模式
    print("\n  [2/5] 选择交易模式:")
    print("    1. 模拟交易 (paper) — 推荐入门")
    print("    2. 实盘交易 (live) — 需连接券商")
    mode_choice = input("  选择 (1-2, 回车默认 1): ").strip() or "1"
    mode = "paper" if mode_choice == "1" else "live"

    # Step 3: 初始资金
    print("\n  [3/5] 初始资金 (回测用):")
    capital = input("  金额 (回车默认 1000000): ").strip() or "1000000"

    # Step 4: 风险偏好
    print("\n  [4/5] 风险偏好:")
    print("    1. 保守 (单票20%, 最大回撤10%)")
    print("    2. 中等 (单票30%, 最大回撤20%) — 推荐")
    print("    3. 激进 (单票40%, 最大回撤30%)")
    risk_choice = input("  选择 (1-3, 回车默认 2): ").strip() or "2"
    risk_presets = {
        "1": ("0.10", "0.20", "5"),
        "2": ("0.20", "0.30", "10"),
        "3": ("0.30", "0.40", "20"),
    }
    max_dd, max_pos, max_total = risk_presets.get(risk_choice, risk_presets["2"])

    # Step 5: 通知
    print("\n  [5/5] 通知设置:")
    print("    1. 仅控制台输出 (默认)")
    print("    2. 控制台 + 钉钉通知 (需 webhook)")
    notify_choice = input("  选择 (1-2, 回车默认 1): ").strip() or "1"
    ding_enabled = "true" if notify_choice == "2" else "false"

    # 生成配置文件
    yaml_content = f"""# Quant Framework — 默认配置 (由配置向导生成)
framework:
  mode: {mode}
  engine: polling
  log_level: INFO
  log_dir: ./logs
  data_dir: ./data

data:
  provider: {provider}
  store:
    type: sqlite
    db_path: ./data/market.db
    bar_cache_days: 365

risk:
  enabled: true
  max_drawdown_pct: {max_dd}
  max_daily_loss: 50000.0
  max_single_position_pct: {max_pos}
  max_total_positions: {max_total}
  order_cooldown_seconds: 5
  blacklist: []

execution:
  broker: simulated
  default_slippage: 0.001
  commission_rate: 0.0003
  min_commission: 5.0

backtest:
  initial_cash: {capital}
  commission_rate: 0.0003
  slippage_model: fixed
  slippage_value: 0.001
  benchmark: "000300"
  risk_free_rate: 0.03

monitor:
  log_format: json
  trade_recorder:
    type: sqlite
    db_path: ./data/trades.db
  notifications:
    dingtalk:
      enabled: {ding_enabled}
      webhook_url: ""
    wecom:
      enabled: false
      webhook_url: ""
    feishu:
      enabled: false
      webhook_url: ""
    email:
      enabled: false
      smtp_host: ""
"""

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        print(f"\n  ✓ 配置文件已创建: {config_path}")
        print(f"    数据源: {provider}")
        print(f"    模式: {mode}")
        print(f"    初始资金: {int(capital):,} 元")
        print(f"    最大回撤限制: {float(max_dd):.0%}")
        print(f"    单票仓位上限: {float(max_pos):.0%}")
    except Exception as e:
        print(f"\n  [错误] 无法写入配置文件: {e}")


# ── 主循环 ──────────────────────────────────────────────────

def main():
    console, Table, Panel, Text, Align, Box, DOUBLE, has_rich = get_rich()

    # Check if config exists — if not, offer to run wizard
    config_path = os.path.join(PROJECT_DIR, "config", "default.yaml")
    if not os.path.exists(config_path):
        if has_rich:
            console.print()
            from rich.text import Text as RText
            hint = RText("  检测到首次运行！是否运行配置向导？(Y/n): ", style="bold yellow")
            console.print(hint)
        else:
            print("\n  检测到首次运行！是否运行配置向导？(Y/n): ")
        answer = input("").strip().lower()
        if answer != "n":
            clear_screen()
            config_wizard()

    while True:
        clear_screen()

        if has_rich:
            print_banner_rich(console, Panel, Text, Align, DOUBLE)
            print_menu_rich(console, Table, Panel, Text, Align, DOUBLE)
        else:
            print_banner_plain()
            print_menu_plain()

        # 提示行
        if has_rich:
            from rich.text import Text as RText
            hint = RText("  请输入数字选项 (0-18) 后回车: ", style="bold")
            console.print()
            console.print(hint)
            choice = input("").strip()
        else:
            print()
            choice = input("  请输入数字选项 (0-18) 后回车: ").strip()

        choice = choice.lstrip('﻿').strip()

        if choice == "0":
            break

        try:
            idx = int(choice)
        except ValueError:
            print(f"  无效选项 '{choice}'，请重试...")
            input("\n  按回车继续...")
            continue

        # Find the matching menu item
        matched = None
        for item in MENU:
            if item[0] == "ITEM" and item[1] == idx:
                matched = item
                break

        if matched is None:
            print(f"  无效选项 '{choice}'，请在 0-18 之间选择。")
            input("\n  按回车继续...")
            continue

        _, _, name, _, script = matched

        if script == "CHECK_ENV":
            clear_screen()
            print("=" * 55)
            print("  环境 & 依赖检查")
            print("=" * 55)
            check_env()
            input("\n  按回车返回菜单...")
        elif script == "INSTALL":
            clear_screen()
            print("=" * 55)
            print("  安装 / 更新依赖")
            print("=" * 55)
            install_deps()
            input("\n  按回车返回菜单...")
        elif script == "RESULTS":
            clear_screen()
            print("=" * 55)
            print("  最近回测结果")
            print("=" * 55)
            view_results()
            input("\n  按回车返回菜单...")
        elif script == "OPENDIR":
            os.startfile(PROJECT_DIR)
        elif script == "UNIFIED_BACKTEST":
            clear_screen()
            print("=" * 55)
            print(f"  {name}")
            print("=" * 55)
            unified_backtest()
        elif script == "STRATEGY_MARKET":
            clear_screen()
            print("=" * 55)
            print(f"  {name}")
            print("=" * 55)
            strategy_market()
        elif script == "DATA_DOWNLOAD":
            clear_screen()
            print("=" * 55)
            print(f"  {name}")
            print("=" * 55)
            data_download()
        elif script == "CONFIG_WIZARD":
            clear_screen()
            print("=" * 55)
            print(f"  {name}")
            print("=" * 55)
            config_wizard()
            input("\n  按回车返回菜单...")
        elif script.startswith("STREAMLIT:"):
            clear_screen()
            print("=" * 55)
            print(f"  {name}")
            print("=" * 55)
            run_streamlit(script.split(":", 1)[1])
        else:
            clear_screen()
            print("=" * 55)
            print(f"  {name}")
            print("=" * 55)
            run_script(script)
            input("\n  按回车返回菜单...")

    clear_screen()
    msg = "感谢使用 A 股量化策略分析平台 v3.0!"
    print(f"\n  {msg}\n")

if __name__ == "__main__":
    main()
