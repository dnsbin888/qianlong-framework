"""量化策略桌面终端 — PyQt5 专业版。

对标 vnpy/聚宽 界面风格，包含:
  1. 策略绩效仪表盘 — KPI卡片 + 收益曲线 + 月度热图
  2. K线信号图 — 蜡烛图 + 买卖点标记 + 成交量 + MACD
  3. 市场情绪 — 涨停/跌停/涨跌比/情绪周期
  4. 交易复盘 — 逐笔交易列表 + 统计 + 筛选
  5. 策略配置 — 公式选择 + 参数调整

启动: python quant_terminal.py
"""

import sys
import os
import json

sys.path.insert(0, r"d:\quant_framework\src")

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

# ---- PyQt5 ----
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QStackedWidget,
    QFrame, QGridLayout, QSplitter, QTableWidget, QTableWidgetItem,
    QHeaderView, QComboBox, QCompleter, QSpinBox, QDoubleSpinBox, QGroupBox,
    QFormLayout, QDateEdit, QMessageBox, QProgressBar, QTabWidget,
    QTextEdit, QScrollArea, QSizePolicy, QSpacerItem,
)
from PyQt5.QtCore import Qt, QDate, QTimer, QThread, pyqtSignal, QSize
from PyQt5.QtGui import (
    QFont, QColor, QPalette, QIcon, QPixmap, QPainter, QPen,
    QLinearGradient, QBrush,
)

# ---- Matplotlib ----
import matplotlib
matplotlib.use("Qt5Agg")
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch
import mplfinance as mpf

# Chinese font
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# ---- Quant Framework ----
from quant_framework.data.providers.ths_day import THSDayDataProvider, _date_to_datetime
from quant_framework.factors.tdx_signals import factor_qlj, factor_ztxf, factor_resonance
from quant_framework.factors.tdx_signals2 import factor_xg_signal, factor_b1_structure, factor_final_pick

# ── 股票名称缓存 ────────────────────────────────────────────
_STOCK_NAME_CACHE: dict[str, str] = {}
_STOCK_NAME_FILE = r"d:\quant_framework\stock_names.json"
_STOCK_SEARCH_INDEX: list[str] = []  # 全量搜索字符串列表 (供 QCompleter 使用)

def _get_stock_names() -> dict[str, str]:
    """获取 A 股代码→名称映射 (优先从本地缓存, 缓存不存在则从 akshare 下载)。"""
    global _STOCK_NAME_CACHE
    if _STOCK_NAME_CACHE:
        return _STOCK_NAME_CACHE

    # 1. 尝试加载本地缓存
    if os.path.exists(_STOCK_NAME_FILE):
        try:
            with open(_STOCK_NAME_FILE, "r", encoding="utf-8") as f:
                _STOCK_NAME_CACHE = json.load(f)
            if len(_STOCK_NAME_CACHE) > 1000:
                return _STOCK_NAME_CACHE
        except Exception:
            pass

    # 2. 尝试从 akshare 下载
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        for _, row in df.iterrows():
            _STOCK_NAME_CACHE[str(row["code"])] = str(row["name"])
        with open(_STOCK_NAME_FILE, "w", encoding="utf-8") as f:
            json.dump(_STOCK_NAME_CACHE, f, ensure_ascii=False)
        print(f"[StockNames] 已从 akshare 下载 {len(_STOCK_NAME_CACHE)} 只股票名称, 缓存至 {_STOCK_NAME_FILE}")
    except Exception as e:
        print(f"[StockNames] akshare 下载失败: {e}")

    return _STOCK_NAME_CACHE

def _build_search_index() -> list[str]:
    """构建模糊搜索索引 — 每只股票生成多条搜索词:
    - 代码: '000001'
    - 名称: '平安银行'
    - 拼音首字母: 'PAYH'
    - 全拼: 'pinganyinhang'
    - 代码+名称: '000001 平安银行'
    """
    global _STOCK_SEARCH_INDEX
    if _STOCK_SEARCH_INDEX:
        return _STOCK_SEARCH_INDEX

    names = _get_stock_names()
    if not names:
        return []

    try:
        from pypinyin import lazy_pinyin, Style
        has_pinyin = True
    except ImportError:
        has_pinyin = False

    items = []
    for code, name in names.items():
        # 核心显示行: "000001  平安银行"
        display = f"{code}  {name}"
        items.append(display)
        # 纯代码 (方便从中间匹配)
        items.append(code)
        # 纯名称
        items.append(name)

        if has_pinyin:
            try:
                # 拼音首字母: PAYH
                initials = "".join(lazy_pinyin(name, style=Style.FIRST_LETTER)).upper()
                if initials:
                    items.append(f"{code}  {initials}")
                    items.append(initials)
                # 全拼: pinganyinhang
                full_py = "".join(lazy_pinyin(name, style=Style.NORMAL)).lower()
                if full_py:
                    items.append(f"{code}  {full_py}")
                    items.append(full_py)
            except Exception:
                pass

    # 去重并排序
    _STOCK_SEARCH_INDEX = sorted(set(items))
    return _STOCK_SEARCH_INDEX

def stock_display_name(code: str) -> str:
    """返回 '代码  名称' 格式的显示字符串。"""
    names = _get_stock_names()
    name = names.get(code, "")
    if name:
        return f"{code}  {name}"
    return code

def resolve_stock_query(query: str, provider=None) -> str | None:
    """将用户输入 (代码/中文名/拼音) 解析为股票代码。

    匹配优先级: 精确代码 > 精确名称 > 模糊名称 > 拼音 > 模糊代码
    """
    query = query.strip()
    if not query:
        return None

    names = _get_stock_names()
    query_lower = query.lower()

    # 1. 纯6位数字 → 直接返回
    if query.isdigit() and len(query) == 6:
        if query in names or provider is None:
            return query
        # 验证数据目录中存在
        syms = provider.scan_symbols()
        if query in syms:
            return query
        return None

    # 2. 精确匹配名称
    for code, name in names.items():
        if name.strip() == query or name == query:
            return code

    # 3. 名称包含匹配 (如 "茅台" → "贵州茅台")
    matches = []
    for code, name in names.items():
        if query in name:
            matches.append(code)
    if len(matches) == 1:
        return matches[0]

    # 4. 拼音首字母匹配 (如 "PAYH" → "平安银行", "gzmt" → "贵州茅台")
    if query.isalpha():
        try:
            from pypinyin import lazy_pinyin, Style
            query_upper = query.upper()
            for code, name in names.items():
                try:
                    initials = "".join(lazy_pinyin(name, style=Style.FIRST_LETTER)).upper()
                    if initials == query_upper:
                        matches.append(code)
                    # 全拼匹配
                    full_py = "".join(lazy_pinyin(name, style=Style.NORMAL)).lower()
                    if full_py == query_lower:
                        matches.append(code)
                    # 全拼包含
                    if query_lower in full_py:
                        matches.append(code)
                except Exception:
                    pass
        except ImportError:
            pass

    if len(matches) >= 1:
        return matches[0]

    # 5. 模糊代码匹配 (如 "0001" → "000001")
    if query.isdigit():
        for code in names:
            if code.startswith(query) or code.endswith(query):
                return code

    return None


# ======================================================================
# Professional Dark Theme (QSS)
# ======================================================================

DARK_THEME = """
QMainWindow {
    background-color: #0d1117;
}
QWidget {
    background-color: #0d1117;
    color: #c9d1d9;
    font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
}
QListWidget {
    background-color: #0d1117;
    border: none;
    outline: none;
    padding: 5px;
}
QListWidget::item {
    background-color: transparent;
    color: #8b949e;
    padding: 12px 15px;
    border-radius: 8px;
    margin: 2px 8px;
    font-size: 14px;
}
QListWidget::item:selected {
    background-color: #1f6feb;
    color: #ffffff;
}
QListWidget::item:hover {
    background-color: #161b22;
    color: #c9d1d9;
}
QFrame#card {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 15px;
}
QLabel#cardTitle {
    color: #8b949e;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
}
QLabel#cardValue {
    color: #c9d1d9;
    font-size: 26px;
    font-weight: bold;
}
QLabel#cardValuePositive {
    color: #3fb950;
    font-size: 26px;
    font-weight: bold;
}
QLabel#cardValueNegative {
    color: #f85149;
    font-size: 26px;
    font-weight: bold;
}
QLabel#sectionTitle {
    color: #58a6ff;
    font-size: 16px;
    font-weight: bold;
    padding: 10px 0px;
    border-bottom: 1px solid #30363d;
}
QGroupBox {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    margin-top: 15px;
    padding-top: 20px;
    color: #c9d1d9;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #58a6ff;
}
QTableWidget {
    background-color: #161b22;
    alternate-background-color: #1a2733;
    border: 1px solid #30363d;
    border-radius: 8px;
    gridline-color: #21262d;
    color: #c9d1d9;
}
QTableWidget::item {
    padding: 6px;
}
QTableWidget::item:selected {
    background-color: #1f6feb;
}
QHeaderView::section {
    background-color: #1e3a5f;
    color: #58a6ff;
    padding: 8px;
    border: none;
    font-weight: bold;
    font-size: 12px;
}
QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {
    background-color: #0d1117;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px 10px;
    min-height: 20px;
}
QComboBox::drop-down {
    border: none;
    background: #161b22;
}
QComboBox QAbstractItemView {
    background-color: #161b22;
    color: #c9d1d9;
    selection-background-color: #1f6feb;
}
QPushButton {
    background-color: #238636;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 20px;
    font-weight: bold;
    font-size: 13px;
}
QPushButton:hover {
    background-color: #2ea043;
}
QPushButton:pressed {
    background-color: #196c2e;
}
QPushButton#secondary {
    background-color: #21262d;
    color: #c9d1d9;
    border: 1px solid #30363d;
}
QPushButton#secondary:hover {
    background-color: #30363d;
}
QProgressBar {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 4px;
    text-align: center;
    color: #c9d1d9;
}
QProgressBar::chunk {
    background-color: #1f6feb;
    border-radius: 4px;
}
QScrollArea {
    border: none;
    background: transparent;
}
QTabWidget::pane {
    border: 1px solid #30363d;
    background: #0d1117;
}
QTabBar::tab {
    background: #161b22;
    color: #8b949e;
    padding: 8px 20px;
    border: 1px solid #30363d;
    border-bottom: none;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #0d1117;
    color: #58a6ff;
    border-bottom: 1px solid #0d1117;
}
QTextEdit {
    background-color: #161b22;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 6px;
}
"""


# ======================================================================
# Data loading worker (background thread)
# ======================================================================

class DataLoadWorker(QThread):
    """Background thread for loading 通达信 data."""
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.data_root = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"

    def run(self):
        data = {"equity": pd.DataFrame(), "trades": pd.DataFrame(),
                "sentiment": pd.DataFrame(), "stock_data": {}}

        # P2#6: 统一从 BacktestStore 加载回测数据
        try:
            from quant_framework.data.backtest_store import BacktestStore
            store = BacktestStore(r"d:\quant_framework")
            bt_data = store.load_latest()
            data["equity"] = bt_data["equity"]
            data["trades"] = bt_data["trades"]
            data["sentiment"] = bt_data.get("sentiment", pd.DataFrame())
            self.progress.emit(50)
        except Exception:
            # 回退到旧路径
            trade_path = r"d:\quant_framework\trade_log.csv"
            equity_path = r"d:\quant_framework\equity_curve.csv"
            sentiment_path = r"d:\quant_framework\sentiment_data.csv"
            if os.path.exists(equity_path):
                eq = pd.read_csv(equity_path)
                if "date" in eq.columns:
                    eq["date"] = pd.to_datetime(eq["date"])
                    eq.set_index("date", inplace=True)
                data["equity"] = eq
            if os.path.exists(trade_path):
                tr = pd.read_csv(trade_path)
                if "buy_date" in tr.columns:
                    tr["buy_date"] = pd.to_datetime(tr["buy_date"])
                data["trades"] = tr
            if os.path.exists(sentiment_path):
                sm = pd.read_csv(sentiment_path)
                if "date" in sm.columns:
                    sm["date"] = pd.to_datetime(sm["date"])
                    sm.set_index("date", inplace=True)
                data["sentiment"] = sm
            self.progress.emit(50)

        # Load sample stock data for K-line display
        self.status.emit("加载K线样本数据...")
        try:
            provider = THSDayDataProvider(self.data_root)
            provider.connect()
            sample_symbols = ["000001", "600000", "000002", "600519", "300750"]
            for sym in sample_symbols:
                raw = provider._read_day_file(sym)
                if not raw:
                    continue
                records = []
                dates = []
                for date_int, (o, h, l, c, amt, vol) in sorted(raw.items())[-300:]:
                    dt = _date_to_datetime(date_int)
                    if dt and o > 0 and c > 0:
                        records.append({"open": o, "high": h, "low": l,
                                        "close": c, "volume": vol})
                        dates.append(dt)
                if len(records) >= 100:
                    data["stock_data"][sym] = pd.DataFrame(records, index=dates)
        except Exception:
            pass

        self.progress.emit(100)
        self.status.emit("数据加载完成")
        self.finished.emit(data)


# ======================================================================
# Metric Card Widget
# ======================================================================

class MetricCard(QFrame):
    """Professional KPI metric card."""
    def __init__(self, title: str, value: str, color: str = "neutral"):
        super().__init__()
        self.setObjectName("card")
        self.setMinimumSize(140, 90)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        title_label = QLabel(title.upper())
        title_label.setObjectName("cardTitle")
        layout.addWidget(title_label)

        value_label = QLabel(value)
        if color == "positive":
            value_label.setObjectName("cardValuePositive")
        elif color == "negative":
            value_label.setObjectName("cardValueNegative")
        else:
            value_label.setObjectName("cardValue")
        layout.addWidget(value_label)
        self.value_label = value_label  # 保存引用供外部更新


# ======================================================================
# Matplotlib Canvas Widget
# ======================================================================

class MplCanvas(FigureCanvas):
    """Embedded matplotlib canvas for PyQt5."""
    def __init__(self, parent=None, width=8, height=5, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi, facecolor="#0d1117")
        super().__init__(self.fig)
        self.setParent(parent)
        self.setStyleSheet("background-color: #0d1117;")
        self.fig.set_tight_layout(True)


# ======================================================================
# PAGE 1: Performance Dashboard
# ======================================================================

class DashboardPage(QWidget):
    """策略绩效仪表盘 — KPI卡片 + 策略信息 + 收益曲线/回撤/月度/分布。"""

    # 信号中文名映射
    SIGNAL_LABELS = {
        "tdx2_final": "终极选股 (涨停突破牛线 + 底部反转 B1)",
        "tdx_resonance": "双信号共振 (擒龙决 + 涨停先锋)",
        "tdx2_xg": "涨停突破牛线 XG",
        "tdx2_b1": "底部反转结构 B1",
        "tdx_qlj": "擒龙决 (打板追涨)",
        "tdx_ztxf": "涨停先锋 (分歧低吸)",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(10)

        # ================================================================
        # 策略信息面板 (顶部横幅 — 说明数据来源 & 策略逻辑)
        # ================================================================
        self.info_frame = QFrame()
        self.info_frame.setObjectName("card")
        self.info_frame.setStyleSheet(
            "QFrame#card { background-color: #161b22; border: 1px solid #30363d;"
            "border-radius: 10px; padding: 10px; }"
        )
        info_layout = QVBoxLayout(self.info_frame)
        info_layout.setContentsMargins(16, 10, 16, 10)
        info_layout.setSpacing(4)

        self.info_title = QLabel("策略信息")
        self.info_title.setStyleSheet("color: #58a6ff; font-size: 14px; font-weight: bold;")
        info_layout.addWidget(self.info_title)

        self.info_content = QLabel("暂无回测数据")
        self.info_content.setStyleSheet("color: #8b949e; font-size: 12px; line-height: 1.6;")
        self.info_content.setWordWrap(True)
        info_layout.addWidget(self.info_content)

        layout.addWidget(self.info_frame)

        # ================================================================
        # KPI 卡片网格
        # ================================================================
        self.metrics_grid = QGridLayout()
        self.metrics_grid.setSpacing(10)
        self.metric_cards: dict[str, MetricCard] = {}

        metric_specs = [
            ("total_return", "总收益率", "neutral"),
            ("annual_return", "年化收益率", "neutral"),
            ("sharpe", "夏普比率", "neutral"),
            ("max_dd", "最大回撤", "negative"),
            ("calmar", "卡玛比率", "neutral"),
            ("win_rate", "胜率", "neutral"),
            ("profit_factor", "盈亏比", "neutral"),
            ("n_trades", "交易次数", "neutral"),
        ]

        for i, (key, label, color) in enumerate(metric_specs):
            row, col = divmod(i, 4)
            card = MetricCard(label, "--", color)
            self.metrics_grid.addWidget(card, row, col)
            self.metric_cards[key] = card

        layout.addLayout(self.metrics_grid)

        # ================================================================
        # 图表区域
        # ================================================================
        self.chart_tabs = QTabWidget()
        layout.addWidget(self.chart_tabs)

        self.equity_canvas = MplCanvas(self, width=12, height=5)
        self.chart_tabs.addTab(self.equity_canvas, "收益曲线")

        self.dd_canvas = MplCanvas(self, width=12, height=5)
        self.chart_tabs.addTab(self.dd_canvas, "回撤分析")

        self.monthly_canvas = MplCanvas(self, width=12, height=5)
        self.chart_tabs.addTab(self.monthly_canvas, "月度收益")

        self.dist_canvas = MplCanvas(self, width=12, height=5)
        self.chart_tabs.addTab(self.dist_canvas, "收益分布")

    def _build_info_text(self, trades: pd.DataFrame, equity: pd.DataFrame) -> str:
        """从交易数据和权益曲线中提取策略信息，生成可读摘要。"""
        parts = []

        # ── 策略信号 ──
        if not trades.empty and "signal" in trades.columns:
            sig = trades["signal"].iloc[0]
            sig_label = self.SIGNAL_LABELS.get(sig, sig)
            parts.append(f"选股公式: {sig_label}")
            # 如果有多个信号，列出所有
            sigs = trades["signal"].unique()
            if len(sigs) > 1:
                sig_labels = [self.SIGNAL_LABELS.get(s, s) for s in sigs]
                parts.append(f"组合信号: {', '.join(sig_labels)}")
        else:
            parts.append("选股公式: (未记录)")

        # ── 回测时间段 ──
        if not trades.empty and "buy_date" in trades.columns:
            t0 = pd.to_datetime(trades["buy_date"]).min()
            t1 = pd.to_datetime(trades["buy_date"]).max()
            parts.append(f"回测区间: {t0.strftime('%Y-%m-%d')} ~ {t1.strftime('%Y-%m-%d')} ({(t1 - t0).days} 天)")
        elif not equity.empty:
            if hasattr(equity.index, '__len__') and len(equity.index) > 1:
                parts.append(f"数据区间: {str(equity.index[0])[:10]} ~ {str(equity.index[-1])[:10]}")

        # ── 初始资金 ──
        if not equity.empty:
            eq_col = equity["equity"] if "equity" in equity.columns else equity.iloc[:, 0]
            if len(eq_col) > 0:
                initial = eq_col.iloc[0]
                parts.append(f"初始资金: {initial:,.0f} 元")

        # ── 仓位参数 ──
        if not trades.empty and "volume" in trades.columns and not equity.empty:
            eq_col = equity["equity"] if "equity" in equity.columns else equity.iloc[:, 0]
            if len(eq_col) > 0 and len(trades) > 0:
                avg_cost = (trades["buy_price"] * trades["volume"]).mean()
                capital = eq_col.iloc[0]
                if capital > 0:
                    avg_pct = avg_cost / capital * 100
                    parts.append(f"单笔仓位: 约 {avg_pct:.0f}% (均{avg_cost:,.0f}元)")

        # ── 最大持仓 ──
        if not equity.empty and "market_value" in equity.columns and "cash" in equity.columns:
            positions = (equity["market_value"] > 0).astype(int)
            # 简单估算: 持有天数 / 交易天数
            hold_ratio = positions.mean()
            parts.append(f"持仓占比: {hold_ratio:.1%} 交易日")

        # ── 退出方式分布 ──
        if not trades.empty and "exit_type" in trades.columns:
            exits = trades["exit_type"].value_counts()
            exit_parts = []
            for e, c in exits.items():
                labels = {"normal": "正常平仓", "stop_loss": "止损", "take_profit": "止盈"}
                exit_parts.append(f"{labels.get(e, e)} {c}笔")
            parts.append(f"退出方式: {', '.join(exit_parts)}")

        # ── 数据源 ──
        parts.append("数据源: 通达信日线 (.day 文件)")
        parts.append(f"交易笔数: {len(trades)} 笔")

        return "\n".join(parts)

    def update_data(self, data: dict):
        self.data = data
        equity = data.get("equity", pd.DataFrame())
        trades = data.get("trades", pd.DataFrame())

        if not equity.empty and "equity" in equity.columns:
            eq_series = equity["equity"]
        elif not equity.empty and len(equity.columns) > 0:
            eq_series = equity.iloc[:, 0]
        else:
            # 无回测数据时仍显示空状态
            self.info_content.setText("暂无回测数据 — 请先在策略配置面板运行回测，或运行 run_backtest_fast.py 生成结果。")
            return

        # ── 更新策略信息面板 ──
        info_text = self._build_info_text(trades, equity)
        self.info_content.setText(info_text)

        # ── 计算指标 ──
        total_ret = (eq_series.iloc[-1] / eq_series.iloc[0] - 1) if eq_series.iloc[0] > 0 else 0
        days = max((eq_series.index[-1] - eq_series.index[0]).days, 1)
        years = max(days / 365.25, 0.1)
        annual_ret = (1 + total_ret) ** (1 / years) - 1
        daily_ret = eq_series.pct_change().dropna()
        sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0
        peak = eq_series.expanding().max()
        dd = (eq_series - peak) / peak
        max_dd = dd.min()
        calmar = annual_ret / abs(max_dd) if abs(max_dd) > 1e-9 else 0

        n_trades = len(trades)
        if not trades.empty and "return_pct" in trades.columns:
            rets = trades["return_pct"].values
            win_rate = (rets > 0).mean()
            wins = rets[rets > 0]
            losses = rets[rets < 0]
            avg_win = wins.mean() if len(wins) > 0 else 0
            avg_loss = losses.mean() if len(losses) > 0 else 0
            profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        else:
            win_rate = profit_factor = 0

        # ── 更新 KPI 卡片 ──
        self.metric_cards["total_return"].value_label.setText(f"{total_ret:+.2%}")
        self.metric_cards["annual_return"].value_label.setText(f"{annual_ret:+.2%}")
        self.metric_cards["sharpe"].value_label.setText(f"{sharpe:.2f}")
        self.metric_cards["max_dd"].value_label.setText(f"{max_dd:.2%}")
        self.metric_cards["calmar"].value_label.setText(f"{calmar:.2f}")
        self.metric_cards["win_rate"].value_label.setText(f"{win_rate:.1%}")
        self.metric_cards["profit_factor"].value_label.setText(f"{profit_factor:.2f}")
        self.metric_cards["n_trades"].value_label.setText(str(n_trades))

        # ── 绘制图表 ──
        self._draw_equity(eq_series, dd)
        self._draw_drawdown(dd)
        self._draw_monthly(eq_series)
        self._draw_distribution(trades)

    def _draw_equity(self, equity: pd.Series, dd: pd.Series):
        ax = self.equity_canvas.fig.subplots()
        ax.clear()
        ax.plot(equity.index, equity.values, color="#58a6ff", linewidth=1.5)
        ax.fill_between(equity.index, equity.values, equity.iloc[0],
                        alpha=0.1, color="#58a6ff")
        ax.axhline(y=equity.iloc[0], color="#484f58", linestyle="--", linewidth=0.8,
                   label="初始资金")
        ax.set_facecolor("#0d1117")
        ax.tick_params(colors="#8b949e")
        ax.spines["bottom"].set_color("#30363d")
        ax.spines["top"].set_color("#30363d")
        ax.spines["left"].set_color("#30363d")
        ax.spines["right"].set_color("#30363d")
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x/10000:.0f}万"))
        ax.legend(loc="upper left", facecolor="#161b22", edgecolor="#30363d",
                  labelcolor="#c9d1d9", fontsize=9)
        ax.grid(True, alpha=0.2, color="#30363d")
        ax.set_title("收益曲线", color="#58a6ff", fontsize=13, fontweight="bold")
        self.equity_canvas.fig.tight_layout()
        self.equity_canvas.draw()

    def _draw_drawdown(self, dd: pd.Series):
        ax = self.dd_canvas.fig.subplots()
        ax.clear()
        ax.fill_between(dd.index, dd.values * 100, 0, color="#f85149", alpha=0.3)
        ax.plot(dd.index, dd.values * 100, color="#f85149", linewidth=1)
        ax.set_facecolor("#0d1117")
        ax.tick_params(colors="#8b949e")
        ax.spines["bottom"].set_color("#30363d")
        ax.spines["top"].set_color("#30363d")
        ax.spines["left"].set_color("#30363d")
        ax.spines["right"].set_color("#30363d")
        ax.set_ylabel("回撤 %", color="#8b949e")
        ax.grid(True, alpha=0.2, color="#30363d")
        ax.set_title("回撤分析", color="#58a6ff", fontsize=13, fontweight="bold")
        self.dd_canvas.fig.tight_layout()
        self.dd_canvas.draw()

    def _draw_monthly(self, equity: pd.Series):
        monthly = equity.resample("ME").last().pct_change().dropna()
        ax = self.monthly_canvas.fig.subplots()
        ax.clear()
        colors = ["#3fb950" if v > 0 else "#f85149" for v in monthly.values]
        ax.bar(range(len(monthly)), monthly.values * 100, color=colors, alpha=0.85)
        ax.axhline(y=0, color="#484f58", linewidth=0.5)
        ax.set_facecolor("#0d1117")
        ax.tick_params(colors="#8b949e")
        ax.spines["bottom"].set_color("#30363d")
        ax.spines["top"].set_color("#30363d")
        ax.spines["left"].set_color("#30363d")
        ax.spines["right"].set_color("#30363d")
        ax.set_ylabel("月度收益 %", color="#8b949e")
        ax.grid(True, alpha=0.2, color="#30363d", axis="y")
        ax.set_title("月度收益", color="#58a6ff", fontsize=13, fontweight="bold")
        self.monthly_canvas.fig.tight_layout()
        self.monthly_canvas.draw()

    def _draw_distribution(self, trades: pd.DataFrame):
        ax = self.dist_canvas.fig.subplots()
        ax.clear()
        if not trades.empty and "return_pct" in trades.columns:
            rets = trades["return_pct"].values * 100
            ax.hist(rets, bins=30, color="#58a6ff", alpha=0.7, edgecolor="#30363d")
            ax.axvline(x=rets.mean(), color="#d2a8ff", linestyle="--", linewidth=1.5,
                       label=f"Mean: {rets.mean():.2f}%")
            ax.axvline(x=0, color="#484f58", linewidth=0.5)
        ax.set_facecolor("#0d1117")
        ax.tick_params(colors="#8b949e")
        ax.spines["bottom"].set_color("#30363d")
        ax.spines["top"].set_color("#30363d")
        ax.spines["left"].set_color("#30363d")
        ax.spines["right"].set_color("#30363d")
        ax.set_xlabel("收益率 %", color="#8b949e")
        ax.set_ylabel("次数", color="#8b949e")
        ax.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")
        ax.grid(True, alpha=0.2, color="#30363d")
        ax.set_title("收益分布", color="#58a6ff", fontsize=13, fontweight="bold")
        self.dist_canvas.fig.tight_layout()
        self.dist_canvas.draw()


# ======================================================================
# PAGE 2: K-line Chart with Signals
# ======================================================================

class KlinePage(QWidget):
    """K-line chart with signal markers (candlestick + volume + MACD).

    支持: 下拉选择预加载股票 + 手动输入代码/名称即时查询。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.stock_data: dict[str, pd.DataFrame] = {}
        self._provider = None
        self._data_root = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
        self._setup_ui()

    def _get_provider(self):
        """懒加载 TDX 数据提供器。"""
        if self._provider is None:
            self._provider = THSDayDataProvider(self._data_root)
            self._provider.connect()
        return self._provider

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)

        # ── Row 1: 股票搜索 + 信号公式 + 查询 ──
        ctrl_layout = QHBoxLayout()
        title = QLabel("K线信号图")
        title.setObjectName("sectionTitle")
        ctrl_layout.addWidget(title)
        ctrl_layout.addStretch()

        ctrl_layout.addWidget(QLabel("股票:"))
        self.symbol_combo = QComboBox()
        self.symbol_combo.setEditable(True)
        self.symbol_combo.setMinimumWidth(180)
        self.symbol_combo.setInsertPolicy(QComboBox.NoInsert)
        self.symbol_combo.lineEdit().setPlaceholderText("代码/名称/拼音 (如 000001, 平安, PAYH)")
        self.symbol_combo.currentTextChanged.connect(self._on_symbol_changed)
        self.symbol_combo.lineEdit().returnPressed.connect(self._on_manual_input)
        self._setup_completer()
        ctrl_layout.addWidget(self.symbol_combo)

        # 信号公式中文映射
        self.SIGNAL_MAP = {
            "终极选股 (涨停突破牛线+底部反转)": "tdx2_final",
            "双信号共振 (擒龙决+涨停先锋)": "tdx_resonance",
            "涨停突破牛线 XG": "tdx2_xg",
            "底部反转结构 B1": "tdx2_b1",
            "擒龙决 (打板追涨)": "tdx_qlj",
            "涨停先锋 (分歧低吸)": "tdx_ztxf",
        }
        self.signal_combo = QComboBox()
        self.signal_combo.addItems(list(self.SIGNAL_MAP.keys()))
        self.signal_combo.currentTextChanged.connect(self._on_symbol_changed)
        ctrl_layout.addWidget(QLabel("信号:"))
        ctrl_layout.addWidget(self.signal_combo)

        self.search_btn = QPushButton("查询")
        self.search_btn.clicked.connect(self._on_manual_input)
        ctrl_layout.addWidget(self.search_btn)

        layout.addLayout(ctrl_layout)

        # ── Row 2: 起始日期 ~ 结束日期 + 快捷预设 (左对齐，仿同花顺习惯) ──
        date_layout = QHBoxLayout()
        date_layout.setSpacing(6)

        lbl_start = QLabel("起始日期:")
        lbl_start.setStyleSheet("color: #8b949e; font-size: 12px;")
        date_layout.addWidget(lbl_start)

        self.start_date = QDateEdit()
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("yyyy-MM-dd")
        self.start_date.setDate(QDate.currentDate().addYears(-1))
        self.start_date.dateChanged.connect(self._on_symbol_changed)
        self.start_date.setStyleSheet("font-size: 13px; padding: 3px 6px;")
        date_layout.addWidget(self.start_date)

        lbl_sep = QLabel("~")
        lbl_sep.setStyleSheet("color: #484f58; font-size: 14px;")
        date_layout.addWidget(lbl_sep)

        self.end_date = QDateEdit()
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("yyyy-MM-dd")
        self.end_date.setDate(QDate.currentDate())
        self.end_date.dateChanged.connect(self._on_symbol_changed)
        self.end_date.setStyleSheet("font-size: 13px; padding: 3px 6px;")
        date_layout.addWidget(self.end_date)

        date_layout.addSpacing(12)

        # 快捷预设按钮
        PRESETS = [
            ("近1月", 30),
            ("近3月", 90),
            ("近半年", 182),
            ("近1年", 365),
            ("近2年", 730),
            ("全部", -1),
        ]
        for label, days in PRESETS:
            btn = QPushButton(label)
            btn.setObjectName("secondary")
            btn.setFixedHeight(26)
            btn.setStyleSheet("QPushButton { font-size: 11px; padding: 2px 8px; }")
            btn.clicked.connect(lambda checked, d=days: self._apply_preset(d))
            date_layout.addWidget(btn)

        date_layout.addStretch()
        layout.addLayout(date_layout)

        # ── Canvas ──
        self.canvas = MplCanvas(self, width=14, height=8)
        layout.addWidget(self.canvas)

    def _setup_completer(self):
        """配置模糊搜索自动补全 — 输入时实时弹出匹配建议。"""
        from PyQt5.QtCore import QStringListModel, Qt as QtCore2
        search_list = _build_search_index()
        if not search_list:
            return
        model = QStringListModel(search_list)
        completer = QCompleter(model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)  # 模糊包含匹配
        completer.setCompletionMode(QCompleter.PopupCompletion)  # 弹出下拉建议
        completer.setMaxVisibleItems(12)
        completer.activated.connect(self._on_completer_selected)
        self.symbol_combo.setCompleter(completer)
        self._completer = completer

    def _apply_preset(self, days: int):
        """快捷日期预设 — 从今天往前推 N 天，或全部数据。"""
        end = QDate.currentDate()
        self.end_date.setDate(end)
        if days > 0:
            self.start_date.setDate(end.addDays(-days))
        else:
            # "全部": 设一个很早的日期
            self.start_date.setDate(QDate(2000, 1, 1))

    def _on_completer_selected(self, text: str):
        """用户从补全下拉中选择一项。"""
        code = self._extract_code(text)
        if code and code in self.stock_data:
            self.symbol_combo.setCurrentText(stock_display_name(code))
            self._sync_dates_to_data()
            self._draw_kline()
        elif code:
            actual = resolve_stock_query(text, self._get_provider())
            if actual and actual in self.stock_data:
                self.symbol_combo.setCurrentText(stock_display_name(actual))
                self._sync_dates_to_data()
                self._draw_kline()
            elif actual:
                self._load_single_stock(actual)

    def _sync_dates_to_data(self):
        """当加载新股时，将日期范围同步到实际数据区间。"""
        sym = self._get_current_symbol()
        if sym and sym in self.stock_data:
            df = self.stock_data[sym]
            n = len(df)
            if n > 0:
                last_idx = n - 1
                from datetime import datetime as dt_mod
                # 尝试推断数据最后日期 (DataFrame 按行索引)
                today = QDate.currentDate()
                self.end_date.setDate(today)
                # 起始设为一年前或更早
                if n >= 250:
                    self.start_date.setDate(today.addYears(-1))
                elif n >= 60:
                    self.start_date.setDate(today.addMonths(-3))
                else:
                    self.start_date.setDate(today.addDays(-n))

    def _get_current_symbol(self) -> str | None:
        """从当前选中项获取股票代码。"""
        idx = self.symbol_combo.currentIndex()
        if idx >= 0 and self.symbol_combo.itemData(idx):
            return self.symbol_combo.itemData(idx)
        return self._extract_code(self.symbol_combo.currentText()) or None

    def _extract_code(self, text: str) -> str:
        """从下拉框文本中提取6位股票代码 (支持 '000001  平安银行' 格式)。"""
        text = text.strip()
        if text and len(text) >= 6 and text[:6].isdigit():
            return text[:6]
        return text

    def update_stock_data(self, data: dict[str, pd.DataFrame]):
        self.stock_data = data
        self.symbol_combo.clear()
        for code in sorted(data.keys()):
            self.symbol_combo.addItem(stock_display_name(code), code)
        if data:
            self._draw_kline()

    def _on_manual_input(self):
        """用户手动输入代码/名称后按回车或点查询按钮时触发。"""
        text = self.symbol_combo.currentText().strip()
        if not text:
            return
        # 先从现有 stock_data 中查找
        code = self._extract_code(text)
        if code in self.stock_data:
            self._draw_kline()
            return
        # 解析输入 (支持中文名称、拼音)
        actual_symbol = self._resolve_symbol(text)
        if actual_symbol is None:
            QMessageBox.warning(self, "未找到",
                f"未找到股票: {text}\n\n请检查代码是否正确，或确认通达信数据目录中有该股票。\n\n提示: 首次使用需联网下载股票名称列表。")
            return
        # 检查是否已加载
        if actual_symbol in self.stock_data:
            idx = self.symbol_combo.findData(actual_symbol)
            if idx >= 0:
                self.symbol_combo.setCurrentIndex(idx)
            self._draw_kline()
            return
        # 从磁盘加载
        self._load_single_stock(actual_symbol)

    def _resolve_symbol(self, query: str) -> str | None:
        """将用户输入解析为股票代码 (支持代码/中文名/拼音首字母)。"""
        return resolve_stock_query(query, self._get_provider())

    def _load_single_stock(self, symbol: str):
        """从通达信数据文件加载单只股票的历史K线 (含日期索引)。"""
        try:
            provider = self._get_provider()
            raw = provider._read_day_file(symbol)
            if not raw:
                QMessageBox.warning(self, "无数据", f"股票 {stock_display_name(symbol)} 没有历史数据。")
                return
            records = []
            dates = []
            for date_int, (o, h, l, c, amt, vol) in sorted(raw.items()):
                dt_obj = _date_to_datetime(date_int)
                if dt_obj and o > 0 and c > 0:
                    records.append({"open": o, "high": h, "low": l,
                                    "close": c, "volume": vol})
                    dates.append(dt_obj)
            if len(records) < 50:
                QMessageBox.warning(self, "数据不足",
                    f"股票 {stock_display_name(symbol)} 仅有 {len(records)} 条有效K线，至少需要50条。")
                return
            df = pd.DataFrame(records, index=dates)
            df.index.name = "date"
            self.stock_data[symbol] = df
            # 更新下拉列表
            display = stock_display_name(symbol)
            existing_codes = [self.symbol_combo.itemData(i) for i in range(self.symbol_combo.count())]
            if symbol not in existing_codes:
                self.symbol_combo.addItem(display, symbol)
            idx = self.symbol_combo.findData(symbol)
            if idx >= 0:
                self.symbol_combo.setCurrentIndex(idx)
            self._sync_dates_to_data()
            self._draw_kline()
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"加载股票 {symbol} 时出错:\n{e}")

    def _on_symbol_changed(self):
        self._draw_kline()

    def _draw_kline(self):
        # 优先从 itemData 取代码 (更可靠)
        idx = self.symbol_combo.currentIndex()
        if idx >= 0 and self.symbol_combo.itemData(idx):
            sym = self.symbol_combo.itemData(idx)
        else:
            sym = self._extract_code(self.symbol_combo.currentText())
        if not sym or sym not in self.stock_data:
            return

        df = self.stock_data[sym]
        # 按日期区间筛选 (DataFrame 索引为 datetime)
        q_start = self.start_date.date().toPyDate()
        q_end = self.end_date.date().toPyDate()
        if hasattr(df.index, "date"):
            # 如果索引是 DatetimeIndex
            mask = (pd.to_datetime(df.index).date >= q_start) & (pd.to_datetime(df.index).date <= q_end)
        else:
            mask = pd.Series(True, index=df.index)
        df_plot = df[mask].copy() if mask.any() else df.iloc[-200:].copy()

        if len(df_plot) < 50:
            return

        # Compute signals — 中文标签 → 英文键 → 计算函数
        signal_label = self.signal_combo.currentText()
        signal_key = self.SIGNAL_MAP.get(signal_label, "tdx2_final")
        signal_fn = {
            "tdx2_final": factor_final_pick,
            "tdx_resonance": factor_resonance,
            "tdx2_xg": factor_xg_signal,
            "tdx2_b1": factor_b1_structure,
            "tdx_qlj": factor_qlj,
            "tdx_ztxf": factor_ztxf,
        }.get(signal_key, factor_final_pick)

        try:
            sig_result = signal_fn(df_plot)
            sig_vals = sig_result.values if isinstance(sig_result, pd.Series) else np.zeros(len(df_plot))
        except Exception:
            sig_vals = np.zeros(len(df_plot))

        # Clear and draw
        self.canvas.fig.clear()

        # 3 subplots: price + volume + MACD
        gs = self.canvas.fig.add_gridspec(3, 1, height_ratios=[3, 1, 1], hspace=0.05)

        ax1 = self.canvas.fig.add_subplot(gs[0])
        ax2 = self.canvas.fig.add_subplot(gs[1], sharex=ax1)
        ax3 = self.canvas.fig.add_subplot(gs[2], sharex=ax1)

        # Candlestick chart
        colors = ["#3fb950" if df_plot["close"].iloc[i] >= df_plot["open"].iloc[i]
                  else "#f85149" for i in range(len(df_plot))]
        x = range(len(df_plot))

        # Draw candlesticks manually
        body_width = 0.6
        for i in range(len(df_plot)):
            row = df_plot.iloc[i]
            color = colors[i]
            # Body
            body_bottom = min(row["open"], row["close"])
            body_height = abs(row["close"] - row["open"])
            ax1.add_patch(plt.Rectangle(
                (i - body_width/2, body_bottom), body_width, max(body_height, 0.01),
                facecolor=color, edgecolor=color, alpha=0.9
            ))
            # Wick
            ax1.plot([i, i], [row["low"], row["high"]], color=color, linewidth=0.8)

        # MA lines
        ma20 = df_plot["close"].rolling(20).mean()
        ma60 = df_plot["close"].rolling(60).mean()
        ax1.plot(x, ma20.values, color="#d29922", linewidth=0.8, alpha=0.7, label="MA20")
        ax1.plot(x, ma60.values, color="#d2a8ff", linewidth=0.8, alpha=0.7, label="MA60")

        # Signal markers
        buy_idx = [i for i in range(len(df_plot)) if sig_vals[i] >= 1]
        if buy_idx:
            buy_prices = df_plot["high"].iloc[buy_idx].values * 1.02
            ax1.scatter(buy_idx, buy_prices, c="#00ff00", s=100, marker="^",
                       zorder=5, edgecolors="#00aa00", linewidths=1.5,
                       label=f"买入信号 ({len(buy_idx)})", alpha=0.9)

        # Styling
        for ax in [ax1, ax2, ax3]:
            ax.set_facecolor("#0d1117")
            ax.tick_params(colors="#8b949e", labelsize=9)
            for spine in ax.spines.values():
                spine.set_color("#30363d")
            ax.grid(True, alpha=0.15, color="#30363d")

        ax1.set_ylabel("价格", color="#8b949e", fontsize=10)
        ax1.legend(loc="upper left", facecolor="#161b22", edgecolor="#30363d",
                   labelcolor="#c9d1d9", fontsize=8)
        ax1.set_title(f"{stock_display_name(sym)}  K线图  |  {signal_label}  |  {q_start} ~ {q_end}",
                      color="#58a6ff", fontsize=13, fontweight="bold")

        # Volume bars
        vol_colors = ["#3fb950" if df_plot["close"].iloc[i] >= df_plot["open"].iloc[i]
                      else "#f85149" for i in range(len(df_plot))]
        ax2.bar(x, df_plot["volume"].values, color=vol_colors, alpha=0.5, width=1.0)
        ax2.set_ylabel("成交量", color="#8b949e", fontsize=10)

        # MACD
        ema12 = df_plot["close"].ewm(span=12, adjust=False).mean()
        ema26 = df_plot["close"].ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        hist = 2 * (dif - dea)
        macd_colors = ["#3fb950" if h >= 0 else "#f85149" for h in hist]
        ax3.bar(x, hist.values, color=macd_colors, alpha=0.5, width=1.0)
        ax3.plot(x, dif.values, color="#58a6ff", linewidth=1, label="DIF")
        ax3.plot(x, dea.values, color="#d29922", linewidth=1, label="DEA")
        ax3.axhline(y=0, color="#484f58", linewidth=0.5)
        ax3.set_ylabel("MACD", color="#8b949e", fontsize=10)
        ax3.legend(loc="upper left", facecolor="#161b22", edgecolor="#30363d",
                   labelcolor="#c9d1d9", fontsize=8)

        self.canvas.fig.tight_layout()
        self.canvas.draw()


# ======================================================================
# PAGE 3: Market Sentiment
# ======================================================================

class SentimentPage(QWidget):
    """市场情绪分析 — A股专业版。"""

    PHASE_CONFIG = [
        (-100, -3.0, "极度冰点", "恐慌蔓延·跌停潮·流动性枯竭", "#8b0000", "🧊"),
        (-3.0, -1.0, "偏冷",     "市场偏弱·赚钱效应差·观望为主", "#f85149", "❄️"),
        (-1.0, 0.5,  "中性",     "多空平衡·结构性行情",         "#d29922", "⚖️"),
        (0.5,  2.0,  "偏暖",     "市场活跃·赚钱效应好·可积极操作", "#3fb950", "🔥"),
        (2.0,  100,  "狂热",     "涨停潮·情绪亢奋·注意高位风险", "#58a6ff", "🚀"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sentiment_data = pd.DataFrame()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(8)

        # ── 标题 ──
        title = QLabel("市场情绪分析")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        # ── 信息行 ──
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #484f58; font-size: 11px;")
        layout.addWidget(self.info_label)

        # ── 阶段横幅 ──
        phase_card = QFrame()
        phase_card.setObjectName("card")
        phase_card.setStyleSheet(
            "QFrame#card { background-color: #161b22; border: 1px solid #30363d;"
            "border-radius: 12px; padding: 14px; }"
        )
        pl = QHBoxLayout(phase_card)
        pl.setContentsMargins(16, 10, 16, 10)

        self.phase_icon = QLabel("⚖️")
        self.phase_icon.setStyleSheet("font-size: 36px;")
        self.phase_icon.setFixedWidth(50)
        self.phase_icon.setAlignment(Qt.AlignCenter)
        pl.addWidget(self.phase_icon)

        pm = QVBoxLayout()
        pm.setSpacing(2)
        self.phase_title = QLabel("市场阶段: --")
        self.phase_title.setStyleSheet("color: #c9d1d9; font-size: 16px; font-weight: bold;")
        pm.addWidget(self.phase_title)
        self.phase_desc = QLabel("")
        self.phase_desc.setStyleSheet("color: #8b949e; font-size: 12px;")
        pm.addWidget(self.phase_desc)

        self.temp_bar = QProgressBar()
        self.temp_bar.setRange(0, 100)
        self.temp_bar.setValue(50)
        self.temp_bar.setFormat("市场温度: %v°C")
        self.temp_bar.setFixedHeight(20)
        self.temp_bar.setStyleSheet("""
            QProgressBar { background-color: #0d1117; border: 1px solid #30363d;
            border-radius: 4px; text-align: center; color: #c9d1d9; font-size: 11px; }
            QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
            stop:0 #3fb950, stop:0.35 #3fb950, stop:0.5 #d29922, stop:0.7 #f85149, stop:1 #8b0000);
            border-radius: 4px; }
        """)
        pm.addWidget(self.temp_bar)
        pl.addLayout(pm)

        # 右侧最新数据
        pr = QVBoxLayout()
        pr.setSpacing(1)
        self.lu_label = QLabel("涨停: --")
        self.lu_label.setStyleSheet("color: #3fb950; font-size: 13px; font-weight: bold;")
        pr.addWidget(self.lu_label)
        self.ld_label = QLabel("跌停: --")
        self.ld_label.setStyleSheet("color: #f85149; font-size: 13px; font-weight: bold;")
        pr.addWidget(self.ld_label)
        self.bomb_label = QLabel("炸板率: --")
        self.bomb_label.setStyleSheet("color: #d29922; font-size: 13px;")
        pr.addWidget(self.bomb_label)
        self.streak_label = QLabel("最高连板: --")
        self.streak_label.setStyleSheet("color: #d2a8ff; font-size: 13px;")
        pr.addWidget(self.streak_label)
        pl.addLayout(pr)

        layout.addWidget(phase_card)

        # ── KPI 卡片 2行×5列 ──
        grid = QGridLayout()
        grid.setSpacing(8)
        self.cards: dict[str, MetricCard] = {}
        specs = [
            ("avg_lu",     "日均涨停",        "neutral"),
            ("max_lu",     "单日最高涨停",    "positive"),
            ("avg_ld",     "日均跌停",        "negative"),
            ("max_ld",     "单日最高跌停",    "negative"),
            ("avg_bomb",   "平均炸板率",      "neutral"),
            ("avg_ad",     "平均涨跌比",      "neutral"),
            ("max_streak", "历史最高连板",    "positive"),
            ("ice_days",   "冰点天数",        "negative"),
            ("hot_days",   "狂热天数",        "positive"),
            ("avg_amount", "日均成交额(亿)",  "neutral"),
        ]
        for i, (k, lbl, c) in enumerate(specs):
            row, col = divmod(i, 5)
            card = MetricCard(lbl, "--", c)
            grid.addWidget(card, row, col)
            self.cards[k] = card
        layout.addLayout(grid)

        # ── 今日市场速览 (表格化数据，对应图片中间的结构化表格区) ──
        table_label = QLabel("今日市场速览")
        table_label.setStyleSheet("color: #d29922; font-size: 13px; font-weight: bold; margin-top: 6px;")
        layout.addWidget(table_label)

        self.summary_table = QTableWidget()
        self.summary_table.setRowCount(3)
        self.summary_table.setColumnCount(5)
        self.summary_table.setHorizontalHeaderLabels(["指标", "数值", "较昨日", "20日均值", "历史分位"])
        self.summary_table.setMaximumHeight(110)
        self.summary_table.verticalHeader().setVisible(False)
        self.summary_table.horizontalHeader().setStretchLastSection(True)
        self.summary_table.setStyleSheet("""
            QTableWidget { background-color: #161b22; border: 1px solid #30363d;
            border-radius: 6px; gridline-color: #21262d; color: #c9d1d9; }
            QTableWidget::item { padding: 4px 8px; }
            QHeaderView::section { background-color: #1e3a5f; color: #d29922;
            padding: 5px; border: none; font-weight: bold; font-size: 11px; }
        """)
        layout.addWidget(self.summary_table)

        # ── 图表 6 Tab ──
        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(380)

        self.ch1 = MplCanvas(self, width=14, height=5)
        self.tabs.addTab(self.ch1, "涨跌停+炸板率")

        self.ch2 = MplCanvas(self, width=14, height=5)
        self.tabs.addTab(self.ch2, "情绪温度周期")

        self.ch3 = MplCanvas(self, width=14, height=5)
        self.tabs.addTab(self.ch3, "市场宽度")

        self.ch4 = MplCanvas(self, width=14, height=5)
        self.tabs.addTab(self.ch4, "成交额分析")

        self.ch5 = MplCanvas(self, width=14, height=5)
        self.tabs.addTab(self.ch5, "连板天梯")

        self.ch6 = MplCanvas(self, width=14, height=5)
        self.tabs.addTab(self.ch6, "阶段分布")

        layout.addWidget(self.tabs)

    # ==================================================================

    def update_data(self, sentiment: pd.DataFrame):
        self.sentiment_data = sentiment
        if sentiment.empty:
            self.info_label.setText("暂无情绪数据 — 请运行 run_sentiment_v2.py 生成")
            return

        n = len(sentiment)
        d0, d1 = str(sentiment.index[0])[:10], str(sentiment.index[-1])[:10]
        avg_amt = sentiment["total_amount"].mean() / 1e8 if "total_amount" in sentiment.columns else 0
        avg_stocks = sentiment["valid_stocks"].mean() if "valid_stocks" in sentiment.columns else 0
        breadth_up = (sentiment["breadth"] > 0).mean() if "breadth" in sentiment.columns else 0

        self.info_label.setText(
            f"数据源: 通达信日线 | {d0} ~ {d1} | {n}天 | 均{avg_stocks:.0f}只 | "
            f"日均成交{avg_amt:.0f}亿 | 上涨>下跌: {breadth_up:.1%}"
        )

        # ── 阶段横幅 ──
        last = sentiment.iloc[-1]
        temp = float(last.get("market_temp", 50))
        self.temp_bar.setValue(int(temp))

        phase_str = str(last.get("phase", ""))
        c, icon, desc = "#d29922", "⚖️", "多空平衡"
        for lo, hi, nm, ds, cl, ic in self.PHASE_CONFIG:
            if nm == phase_str:
                c, icon, desc = cl, ic, ds
                break
        self.phase_icon.setText(icon)
        self.phase_title.setText(f"市场阶段: {phase_str}")
        self.phase_title.setStyleSheet(f"color: {c}; font-size: 16px; font-weight: bold;")
        self.phase_desc.setText(f"{desc}  |  近20日均温: {temp:.0f}°C")

        lu_v = int(last["limit_up"]); ld_v = int(last["limit_down"])
        bomb_v = float(last.get("bomb_ratio", 0)); strk_v = int(last.get("max_streak", 0))
        self.lu_label.setText(f"涨停: {lu_v} 家")
        self.ld_label.setText(f"跌停: {ld_v} 家")
        self.bomb_label.setText(f"炸板率: {bomb_v:.1%}")
        self.streak_label.setText(f"最高连板: {strk_v} 板")

        # ── KPI ──
        max_lu_v = int(sentiment["limit_up"].max()); max_ld_v = int(sentiment["limit_down"].max())
        avg_bomb = sentiment["bomb_ratio"].mean() if "bomb_ratio" in sentiment.columns else 0
        avg_ad = sentiment["advance_decline"].mean()
        ice = int(((sentiment["limit_up"] <= 15) & (sentiment["limit_down"] > sentiment["limit_up"])).sum())
        hot = int((sentiment["limit_up"] >= 80).sum())
        max_strk = int(sentiment["max_streak"].max()) if "max_streak" in sentiment.columns else 0

        for k, v in [
            ("avg_lu", f"{sentiment['limit_up'].mean():.0f}"),
            ("max_lu", str(max_lu_v)),
            ("avg_ld", f"{sentiment['limit_down'].mean():.0f}"),
            ("max_ld", str(max_ld_v)),
            ("avg_bomb", f"{avg_bomb:.1%}"),
            ("avg_ad", f"{avg_ad:.2f}"),
            ("max_streak", f"{max_strk}板"),
            ("ice_days", str(ice)),
            ("hot_days", str(hot)),
            ("avg_amount", f"{avg_amt:.0f}"),
        ]:
            if k in self.cards:
                self.cards[k].value_label.setText(v)

        # ── 今日市场速览表格 ──
        prev = sentiment.iloc[-2] if len(sentiment) >= 2 else last
        ma20 = sentiment.iloc[-20:] if len(sentiment) >= 20 else sentiment

        def delta_str(cur, prev_val):
            d = cur - prev_val
            if d > 0: return f"+{d:.0f}" if abs(d) >= 1 else f"+{d:.2f}"
            else: return f"{d:.0f}" if abs(d) >= 1 else f"{d:.2f}"

        def pctile_str(series, val):
            pct = (series < val).mean() * 100
            if pct >= 90: return f"前{100-pct:.0f}% (高位)"
            elif pct <= 10: return f"后{pct:.0f}% (低位)"
            else: return f"{pct:.0f}%分位"

        rows_data = [
            ("涨停家数", f"{lu_v} 家", delta_str(lu_v, prev["limit_up"]),
             f"{ma20['limit_up'].mean():.0f} 家", pctile_str(sentiment["limit_up"], lu_v)),
            ("跌停家数", f"{ld_v} 家", delta_str(ld_v, prev["limit_down"]),
             f"{ma20['limit_down'].mean():.0f} 家", pctile_str(sentiment["limit_down"], ld_v)),
            ("炸板率",   f"{bomb_v:.1%}", delta_str(bomb_v, prev.get('bomb_ratio', 0)),
             f"{ma20['bomb_ratio'].mean():.1%}" if 'bomb_ratio' in ma20 else "--",
             pctile_str(sentiment.get('bomb_ratio', sentiment['limit_up']*0), bomb_v)),
        ]

        for i, (indicator, val, delta, avg, pctile) in enumerate(rows_data):
            items = [
                QTableWidgetItem(indicator),
                QTableWidgetItem(val),
                QTableWidgetItem(delta),
                QTableWidgetItem(avg),
                QTableWidgetItem(pctile),
            ]
            # 颜色
            if "涨" in indicator or (indicator == "炸板率" and bomb_v < 0.3):
                items[1].setForeground(QColor("#3fb950"))
            elif "跌" in indicator:
                items[1].setForeground(QColor("#f85149"))
            else:
                items[1].setForeground(QColor("#d29922"))

            try:
                d_val = float(delta.replace("+", ""))
                items[2].setForeground(QColor("#3fb950") if d_val > 0 else QColor("#f85149"))
            except: pass

            for j, item in enumerate(items):
                self.summary_table.setItem(i, j, item)
        self.summary_table.resizeColumnsToContents()

        # ── 图表 ──
        self._draw1(sentiment)
        self._draw2(sentiment)
        self._draw3(sentiment)
        self._draw4(sentiment)
        self._draw5(sentiment)
        self._draw6(sentiment)

    # ==================================================================

    def _draw1(self, df):
        """涨跌停 + 炸板率"""
        ax = self.ch1.fig.subplots()
        ax.clear()
        ax.fill_between(range(len(df)), df["limit_up"].values, alpha=0.4, color="#3fb950", label="涨停")
        ax.fill_between(range(len(df)), -df["limit_down"].values, alpha=0.4, color="#f85149", label="跌停")
        if "bomb_ratio" in df.columns:
            ax2 = ax.twinx()
            ax2.clear()
            ax2.plot(range(len(df)), df["bomb_ratio"].values * 100, color="#d29922", linewidth=0.6, alpha=0.6)
            ax2.set_ylabel("炸板率%", color="#d29922", fontsize=8)
            ax2.tick_params(colors="#d29922", labelsize=7)
        ax.axhline(y=0, color="#484f58", linewidth=0.5)
        ax.set_facecolor("#0d1117")
        ax.tick_params(colors="#8b949e")
        for s in ax.spines.values(): s.set_color("#30363d")
        ax.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9", fontsize=8)
        ax.grid(True, alpha=0.15, color="#30363d")
        ax.set_title("涨跌停家数 (红绿柱) + 炸板率 (黄线%)", color="#58a6ff", fontsize=11, fontweight="bold")
        self.ch1.fig.subplots_adjust(left=0.08, right=0.92, top=0.93, bottom=0.08)
        self.ch1.draw()

    def _draw2(self, df):
        """情绪温度周期"""
        ax = self.ch2.fig.subplots()
        ax.clear()
        if "market_temp" in df.columns:
            ax.fill_between(range(len(df)), df["market_temp"].values, alpha=0.3, color="#58a6ff")
            ax.plot(range(len(df)), df["market_temp"].rolling(20).mean().values, color="#d29922", linewidth=1.5, label="温度MA20")
        ax.axhline(y=50, color="#484f58", linewidth=0.5, linestyle="--")
        ax.set_facecolor("#0d1117")
        ax.tick_params(colors="#8b949e")
        for s in ax.spines.values(): s.set_color("#30363d")
        ax.set_ylim(0, 100)
        ax.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9", fontsize=8)
        ax.grid(True, alpha=0.15, color="#30363d")
        ax.set_ylabel("温度°C", color="#8b949e")
        ax.set_title("市场温度周期 (蓝区=温度, 黄线=MA20, 虚线=50°C中性)", color="#58a6ff", fontsize=11, fontweight="bold")
        self.ch2.fig.subplots_adjust(left=0.08, right=0.95, top=0.93, bottom=0.08)
        self.ch2.draw()

    def _draw3(self, df):
        """市场宽度"""
        ax = self.ch3.fig.subplots()
        ax.clear()
        breadth = df.get("breadth", (df["up_count"] - df["down_count"]) / df["valid_stocks"])
        colors = ["#3fb950" if v > 0 else "#f85149" for v in breadth]
        ax.bar(range(len(breadth)), breadth.values, color=colors, alpha=0.7, width=1.0)
        ax.axhline(y=0, color="#484f58", linewidth=0.5)
        ax.set_facecolor("#0d1117")
        ax.tick_params(colors="#8b949e")
        for s in ax.spines.values(): s.set_color("#30363d")
        ax.grid(True, alpha=0.15, color="#30363d", axis="y")
        ax.set_title("市场宽度 (红绿柱) — (上涨-下跌)/总数", color="#58a6ff", fontsize=11, fontweight="bold")
        self.ch3.fig.subplots_adjust(left=0.08, right=0.95, top=0.93, bottom=0.08)
        self.ch3.draw()

    def _draw4(self, df):
        """成交额"""
        ax = self.ch4.fig.subplots()
        ax.clear()
        if "total_amount" in df.columns:
            amt = df["total_amount"] / 1e8
            ax.fill_between(range(len(df)), amt.values, alpha=0.5, color="#58a6ff")
            ax.plot(range(len(df)), amt.rolling(20).mean().values, color="#d29922", linewidth=1.5, label="MA20")
            ax.plot(range(len(df)), amt.rolling(60).mean().values, color="#d2a8ff", linewidth=1, alpha=0.7, label="MA60")
        ax.set_facecolor("#0d1117")
        ax.tick_params(colors="#8b949e")
        for s in ax.spines.values(): s.set_color("#30363d")
        ax.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9", fontsize=8)
        ax.grid(True, alpha=0.15, color="#30363d")
        ax.set_ylabel("成交额(亿)", color="#8b949e", fontsize=8)
        ax.set_title("全市场成交额 + MA20/MA60", color="#58a6ff", fontsize=11, fontweight="bold")
        self.ch4.fig.subplots_adjust(left=0.08, right=0.95, top=0.93, bottom=0.08)
        self.ch4.draw()

    def _draw5(self, df):
        """连板天梯"""
        ax = self.ch5.fig.subplots()
        ax.clear()
        if "max_streak" in df.columns:
            stk = df["max_streak"]
            for i, v in enumerate(stk.values):
                c = "#ff6b6b" if v >= 7 else ("#d29922" if v >= 5 else ("#3fb950" if v >= 3 else "#484f58"))
                ax.scatter(i, v, c=c, s=max(5, min(80, v * 10)), alpha=0.7)
            ax.plot(range(len(stk)), stk.rolling(20).mean().values, color="#58a6ff", linewidth=1.5, label="连板MA20")
        ax.set_facecolor("#0d1117")
        ax.tick_params(colors="#8b949e")
        for s in ax.spines.values(): s.set_color("#30363d")
        ax.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9", fontsize=8)
        ax.grid(True, alpha=0.15, color="#30363d", axis="y")
        ax.set_ylabel("连板高度", color="#8b949e")
        ax.set_title("连板天梯 (绿<3 黄5-6 红7+ 散点大小=强度, 蓝线=MA20)", color="#58a6ff", fontsize=11, fontweight="bold")
        self.ch5.fig.subplots_adjust(left=0.08, right=0.95, top=0.93, bottom=0.08)
        self.ch5.draw()

    def _draw6(self, df):
        """阶段分布饼图"""
        ax = self.ch6.fig.subplots()
        ax.clear()
        if "phase" in df.columns:
            counts = df["phase"].value_counts()
            order = ["极度冰点", "偏冷", "中性", "偏暖", "狂热"]
            vals = [counts.get(p, 0) for p in order]
            colors_list = ["#8b0000", "#f85149", "#d29922", "#3fb950", "#58a6ff"]
            ax.pie(vals, labels=[f"{p}\n{d}天" for p, d in zip(order, vals) if d > 0],
                   colors=[colors_list[i] for i, d in enumerate(vals) if d > 0],
                   autopct="%1.1f%%", startangle=90, pctdistance=0.6,
                   textprops={"color": "#c9d1d9", "fontsize": 9})
        ax.set_title(f"情绪阶段分布 (共{len(df)}天)", color="#58a6ff", fontsize=11, fontweight="bold")
        self.ch6.fig.subplots_adjust(left=0.05, right=0.95, top=0.93, bottom=0.08)
        self.ch6.draw()


# ======================================================================
# PAGE 4: Trade Review
# ======================================================================

class TradeReviewPage(QWidget):
    """Trade review & analysis panel."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.trades = pd.DataFrame()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)

        title = QLabel("交易复盘分析")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        # Filter bar
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("筛选退出方式:"))
        self.exit_filter = QComboBox()
        self.exit_filter.addItems(["全部", "normal", "stop_loss", "take_profit"])
        self.exit_filter.currentTextChanged.connect(self._on_filter)
        filter_layout.addWidget(self.exit_filter)
        filter_layout.addStretch()

        self.export_btn = QPushButton("导出CSV")
        self.export_btn.setObjectName("secondary")
        filter_layout.addWidget(self.export_btn)
        layout.addLayout(filter_layout)

        # Trade stats summary
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #8b949e; font-size: 12px; padding: 5px;")
        layout.addWidget(self.stats_label)

        # Trade table
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table)

        # P&L chart
        self.pnl_canvas = MplCanvas(self, width=12, height=3)
        layout.addWidget(self.pnl_canvas)

    def update_data(self, trades: pd.DataFrame):
        self.trades = trades
        self._on_filter()

    def _on_filter(self):
        if self.trades.empty:
            self.stats_label.setText("暂无交易数据，请先运行回测。")
            return

        trades = self.trades
        exit_filter = self.exit_filter.currentText()
        if exit_filter != "全部":
            trades = trades[trades["exit_type"] == exit_filter]

        # Stats
        returns = trades["return_pct"].values
        win_rate = (returns > 0).mean()
        avg_ret = returns.mean()
        total_pnl = trades["net_profit"].sum() if "net_profit" in trades.columns else 0
        best = returns.max()
        worst = returns.min()

        self.stats_label.setText(
            f"共 {len(trades)} 笔交易 | 胜率: {win_rate:.1%} | "
            f"平均收益: {avg_ret:+.2%} | 最佳: {best:+.2%} | 最差: {worst:+.2%} | "
            f"总盈亏: {total_pnl:+,.0f} 元"
        )

        # Populate table
        columns = ["symbol", "buy_date", "sell_date", "buy_price", "sell_price",
                   "return_pct", "net_profit", "exit_type"]
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(
            ["代码", "买入日期", "卖出日期", "买入价", "卖出价",
             "收益率", "盈亏", "退出方式"]
        )
        self.table.setRowCount(min(len(trades), 200))

        for i, (_, row) in enumerate(trades.head(200).iterrows()):
            for j, col in enumerate(columns):
                val = row.get(col, "")
                if col == "return_pct":
                    val = f"{val:+.2%}"
                elif col in ("buy_price", "sell_price"):
                    val = f"{val:.2f}"
                elif col == "net_profit":
                    val = f"{val:+,.0f}"
                item = QTableWidgetItem(str(val))
                if col == "return_pct" and isinstance(row.get(col), (int, float)):
                    item.setForeground(QColor("#3fb950") if row[col] > 0 else QColor("#f85149"))
                self.table.setItem(i, j, item)

        self.table.resizeColumnsToContents()

        # Draw cumulative P&L
        self._draw_cumulative_pnl(trades)

    def _draw_cumulative_pnl(self, trades: pd.DataFrame):
        ax = self.pnl_canvas.fig.subplots()
        ax.clear()
        trades_sorted = trades.sort_values("buy_date")
        cum_pnl = trades_sorted["net_profit"].cumsum() if "net_profit" in trades_sorted.columns else \
                  (trades_sorted["return_pct"] * 10000).cumsum()
        ax.fill_between(range(len(cum_pnl)), cum_pnl.values, 0,
                        alpha=0.3, color="#58a6ff")
        ax.plot(range(len(cum_pnl)), cum_pnl.values, color="#58a6ff", linewidth=1.5)
        ax.axhline(y=0, color="#484f58", linewidth=0.5)
        ax.set_facecolor("#0d1117")
        ax.tick_params(colors="#8b949e")
        for spine in ax.spines.values():
            spine.set_color("#30363d")
        ax.grid(True, alpha=0.15, color="#30363d")
        ax.set_ylabel("累计盈亏 (元)", color="#8b949e")
        ax.set_title("逐笔累计盈亏曲线", color="#58a6ff", fontsize=12, fontweight="bold")
        self.pnl_canvas.fig.tight_layout()
        self.pnl_canvas.draw()


# ======================================================================
# PAGE 5: Strategy Config
# ======================================================================

class ConfigPage(QWidget):
    """Strategy configuration panel."""
    run_backtest_signal = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)

        title = QLabel("策略配置")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        # Signal selection
        signal_group = QGroupBox("选股公式")
        signal_layout = QFormLayout(signal_group)

        self.signal_combo = QComboBox()
        self.signal_combo.addItems([
            "tdx2_final (公式1:牛线突破+B1反转)", "tdx_resonance (公式2:双信号共振)",
            "tdx2_xg (涨停突破牛线)", "tdx2_b1 (底部反转B1)",
            "tdx_qlj (擒龙决)", "tdx_ztxf (涨停先锋)",
        ])
        signal_layout.addRow("选股公式:", self.signal_combo)

        self.max_positions = QSpinBox()
        self.max_positions.setRange(1, 10)
        self.max_positions.setValue(3)
        signal_layout.addRow("最大持仓数:", self.max_positions)

        self.position_pct = QDoubleSpinBox()
        self.position_pct.setRange(0.05, 1.0)
        self.position_pct.setSingleStep(0.05)
        self.position_pct.setValue(0.30)
        signal_layout.addRow("单票仓位 %:", self.position_pct)

        self.stop_loss = QDoubleSpinBox()
        self.stop_loss.setRange(-0.20, 0.0)
        self.stop_loss.setSingleStep(0.01)
        self.stop_loss.setValue(-0.03)
        signal_layout.addRow("止损线:", self.stop_loss)

        self.take_profit = QDoubleSpinBox()
        self.take_profit.setRange(0.01, 0.30)
        self.take_profit.setSingleStep(0.01)
        self.take_profit.setValue(0.05)
        signal_layout.addRow("止盈线:", self.take_profit)

        layout.addWidget(signal_group)

        # Date range
        date_group = QGroupBox("回测时间段")
        date_layout = QFormLayout(date_group)

        self.start_date = QDateEdit(QDate(2022, 1, 1))
        self.end_date = QDateEdit(QDate(2025, 12, 31))
        date_layout.addRow("起始日期:", self.start_date)
        date_layout.addRow("结束日期:", self.end_date)
        layout.addWidget(date_group)

        # Run button
        self.run_btn = QPushButton("开始回测")
        self.run_btn.setMinimumHeight(40)
        self.run_btn.clicked.connect(self._on_run)
        layout.addWidget(self.run_btn)

        # Progress
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # Log output
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(150)
        layout.addWidget(self.log_output)

        layout.addStretch()

    def _on_run(self):
        self.progress.setVisible(True)
        self.progress.setValue(5)
        self.log_output.append("[启动] 开始运行回测...")
        config = {
            "signal": self.signal_combo.currentText(),
            "max_positions": self.max_positions.value(),
            "position_pct": self.position_pct.value(),
            "stop_loss": self.stop_loss.value(),
            "take_profit": self.take_profit.value(),
            "start": self.start_date.date().toString("yyyy-MM-dd"),
            "end": self.end_date.date().toString("yyyy-MM-dd"),
        }
        self.log_output.append(f"[配置] {json.dumps(config, indent=2, ensure_ascii=False)}")
        # P1修复: 信号由 QuantTerminal._on_run_backtest 接收并实际执行回测
        self.run_backtest_signal.emit(config)


# ======================================================================
# MAIN WINDOW
# ======================================================================

class QuantTerminal(QMainWindow):
    """Main quant trading terminal window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("量化策略终端 — A股T+1短线策略平台")
        self.setMinimumSize(1400, 900)
        self.resize(1600, 1000)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---- Sidebar ----
        sidebar = QFrame()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet("background-color: #161b22; border-right: 1px solid #30363d;")

        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 15, 0, 15)
        sidebar_layout.setSpacing(5)

        # Logo
        logo = QLabel("  量化策略终端")
        logo.setStyleSheet("color: #58a6ff; font-size: 16px; font-weight: bold; padding: 10px;")
        sidebar_layout.addWidget(logo)

        sidebar_layout.addSpacing(10)

        # Navigation
        self.nav_list = QListWidget()
        nav_items = [
            ("  策略仪表盘", "绩效概览与收益曲线"),
            ("  K线信号图", "蜡烛图+买卖点标记"),
            ("  市场情绪", "涨停/跌停/涨跌比"),
            ("  交易复盘", "交易明细与统计分析"),
            ("  策略配置", "公式选择与参数调整"),
        ]
        for label, tooltip in nav_items:
            item = QListWidgetItem(label)
            item.setToolTip(tooltip)
            item.setSizeHint(QSize(200, 45))
            self.nav_list.addItem(item)

        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        sidebar_layout.addWidget(self.nav_list)

        sidebar_layout.addStretch()

        # Version
        version_label = QLabel("  v1.0.0 | 通达信日线数据")
        version_label.setStyleSheet("color: #484f58; font-size: 11px; padding: 10px;")
        sidebar_layout.addWidget(version_label)

        main_layout.addWidget(sidebar)

        # ---- Content Area ----
        self.stack = QStackedWidget()

        self.dashboard_page = DashboardPage()
        self.kline_page = KlinePage()
        self.sentiment_page = SentimentPage()
        self.review_page = TradeReviewPage()
        self.config_page = ConfigPage()

        # P1修复: 连接"开始回测"信号到实际执行器
        self.config_page.run_backtest_signal.connect(self._on_run_backtest)

        self.stack.addWidget(self.dashboard_page)    # 0
        self.stack.addWidget(self.kline_page)        # 1
        self.stack.addWidget(self.sentiment_page)    # 2
        self.stack.addWidget(self.review_page)       # 3
        self.stack.addWidget(self.config_page)       # 4

        main_layout.addWidget(self.stack)

        # Status bar
        self.statusBar().setStyleSheet(
            "background-color: #161b22; color: #8b949e; border-top: 1px solid #30363d;"
        )
        self.statusBar().showMessage("就绪 — 点击左侧菜单开始")

        # Data
        self.app_data: dict = {}
        self._load_data()

    def _load_data(self):
        """Start background data loading."""
        self.statusBar().showMessage("正在加载数据...")
        self.worker = DataLoadWorker()
        self.worker.status.connect(lambda msg: self.statusBar().showMessage(msg))
        self.worker.progress.connect(lambda p: None)
        self.worker.finished.connect(self._on_data_loaded)
        self.worker.start()

    def _on_data_loaded(self, data: dict):
        """Handle loaded data."""
        self.app_data = data
        self.statusBar().showMessage(
            f"数据已加载 — {len(data.get('trades', []))} 笔交易, "
            f"{len(data.get('sentiment', []))} 天情绪数据"
        )

        # Update pages
        self.dashboard_page.update_data(data)
        self.kline_page.update_stock_data(data.get("stock_data", {}))
        self.sentiment_page.update_data(data.get("sentiment", pd.DataFrame()))
        self.review_page.update_data(data.get("trades", pd.DataFrame()))

    def _on_run_backtest(self, config: dict):
        """P1修复: 接收 ConfigPage 的 run_backtest_signal，实际执行回测。"""
        import subprocess, json as _json, threading

        self.statusBar().showMessage("正在运行回测...")
        self.config_page.progress.setVisible(True)
        self.config_page.progress.setValue(0)

        # 从 UI config 提取参数
        sig_name = config.get("signal", "tdx2_final")
        if " " in sig_name:
            sig_name = sig_name.split(" ")[0]

        cli_config = {
            "strategy": sig_name,
            "max_pos": config.get("max_positions", 3),
            "position_pct": config.get("position_pct", 0.30),
            "stop_loss": config.get("stop_loss", -0.05),
            "take_profit": config.get("take_profit", 0.08),
            "start": config.get("start", "2023-01-01"),
            "end": config.get("end", "2025-06-01"),
        }

        def _run():
            try:
                cmd = [
                    sys.executable,
                    r"d:\quant_framework\run_backtest_fast.py",
                    "--config", _json.dumps(cli_config, ensure_ascii=False),
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if proc.returncode == 0:
                    self.config_page.log_output.append("[完成] 回测成功！")
                    self.config_page.progress.setValue(100)
                    # 重新加载数据刷新所有页面
                    self._load_data()
                else:
                    self.config_page.log_output.append(f"[失败] 回测退出码 {proc.returncode}")
                    if proc.stderr:
                        for line in proc.stderr.strip().split("\n")[-5:]:
                            self.config_page.log_output.append(f"  {line}")
            except subprocess.TimeoutExpired:
                self.config_page.log_output.append("[超时] 回测超过10分钟，已终止")
            except Exception as e:
                self.config_page.log_output.append(f"[错误] {e}")
            finally:
                self.config_page.progress.setVisible(False)
                self.statusBar().showMessage("就绪")

        threading.Thread(target=_run, daemon=True).start()

    def _on_nav_changed(self, index: int):
        """Switch pages."""
        self.stack.setCurrentIndex(index)
        page_names = ["策略仪表盘", "K线信号图", "市场情绪", "交易复盘", "策略配置"]
        if index < len(page_names):
            self.statusBar().showMessage(f"当前页面: {page_names[index]}")


# ======================================================================
# Main Entry
# ======================================================================

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_THEME)

    # Apply dark palette
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#0d1117"))
    palette.setColor(QPalette.WindowText, QColor("#c9d1d9"))
    palette.setColor(QPalette.Base, QColor("#161b22"))
    palette.setColor(QPalette.AlternateBase, QColor("#1a2733"))
    palette.setColor(QPalette.Text, QColor("#c9d1d9"))
    palette.setColor(QPalette.Button, QColor("#21262d"))
    palette.setColor(QPalette.ButtonText, QColor("#c9d1d9"))
    palette.setColor(QPalette.Highlight, QColor("#1f6feb"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    window = QuantTerminal()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
