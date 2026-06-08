"""同花顺持仓自动读取 — 多策略自动同步
策略1: 监控同花顺导出文件变化
策略2: 读取窗口控件文本 (pywin32)
策略3: 监控剪贴板
策略4: 同花顺本地数据库 (SQLite)
"""
import os, time, csv, io, json, threading
from datetime import datetime
from pathlib import Path

# ═══════════════════════════════════════════════
# 策略1: 文件监控 — 监控同花顺自动导出的持仓文件
# ═══════════════════════════════════════════════
class FileWatcher:
    def __init__(self, filepath):
        self.filepath = filepath
        self.last_mtime = None
        self.last_size = 0

    def has_changed(self):
        if not os.path.exists(self.filepath):
            return False
        stat = os.stat(self.filepath)
        if stat.st_mtime != self.last_mtime or stat.st_size != self.last_size:
            self.last_mtime = stat.st_mtime
            self.last_size = stat.st_size
            return True
        return False


# ═══════════════════════════════════════════════
# 策略2: 同花顺窗口抓取 (UIAutomation)
# ═══════════════════════════════════════════════
def try_read_with_uia():
    """通过 Windows UI Automation 读取同花顺持仓窗口"""
    try:
        import uiautomation as uia
    except ImportError:
        return None

    try:
        # 查找同花顺下单窗口
        windows = uia.WindowControl(Name="同花顺").GetChildren()
        for win in windows:
            if "持仓" in win.Name or "position" in win.Name.lower():
                # 尝试读取表格数据
                grids = win.GetChildren()
                for grid in grids:
                    if grid.ControlTypeName == "DataGrid" or grid.ControlTypeName == "Table":
                        rows = grid.GetChildren()
                        positions = []
                        for row in rows[1:]:  # 跳过表头
                            cells = row.GetChildren()
                            if len(cells) >= 5:
                                positions.append({
                                    "symbol": cells[0].Name if len(cells) > 0 else "",
                                    "name": cells[1].Name if len(cells) > 1 else "",
                                    "quantity": cells[2].Name if len(cells) > 2 else "0",
                                    "cost_price": cells[3].Name if len(cells) > 3 else "0",
                                    "current_price": cells[4].Name if len(cells) > 4 else "0",
                                })
                        return positions
    except Exception as e:
        print(f"[THS-UIA] Error: {e}")
    return None


# ═══════════════════════════════════════════════
# 策略3: 同花顺 SQLite 数据库读取
# ═══════════════════════════════════════════════
def try_read_sqlite():
    """尝试从同花顺本地 SQLite 数据库读取持仓"""
    import sqlite3
    candidates = [
        r"d:\同花顺软件\同花顺\xiadan-plus\database\ths_position.db",
        r"d:\同花顺软件\同花顺\data\position.db",
        r"d:\同花顺软件\同花顺\history\position.db",
    ]
    for db_path in candidates:
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                for table in tables:
                    try:
                        rows = cursor.execute(f"SELECT * FROM {table[0]} LIMIT 20").fetchall()
                        cols = [d[0] for d in cursor.description]
                        if 'code' in [c.lower() for c in cols] or 'symbol' in [c.lower() for c in cols]:
                            positions = []
                            for row in rows:
                                d = dict(zip(cols, row))
                                positions.append({
                                    "symbol": str(d.get('code', d.get('symbol', ''))),
                                    "name": str(d.get('name', '')),
                                    "quantity": int(float(str(d.get('qty', d.get('quantity', 0)))) if d.get('qty', d.get('quantity', 0)) else 0),
                                    "cost_price": float(str(d.get('cost', 0))) if d.get('cost', 0) else 0,
                                })
                            conn.close()
                            return positions
                    except: pass
                conn.close()
            except Exception as e:
                print(f"[THS-DB] Error reading {db_path}: {e}")
    return None


# ═══════════════════════════════════════════════
# 策略4: 窗口标题提取 (快速版)
# ═══════════════════════════════════════════════
def try_read_window_title():
    """从同花顺窗口标题提取总资产/盈亏摘要"""
    try:
        import pygetwindow as gw
        windows = gw.getWindowsWithTitle("同花顺")
        for w in windows:
            title = w.title
            if "总资产" in title or "盈亏" in title:
                # 解析标题: "同花顺(v9.xx) - 总资产: xxx 盈亏: xxx"
                info = {"title": title}
                import re
                assets = re.findall(r'总资产[：:]\s*([\d,.]+)', title)
                pnl = re.findall(r'盈亏[：:]\s*([+-]?[\d,.]+)', title)
                if assets: info["total_assets"] = assets[0]
                if pnl: info["pnl"] = pnl[0]
                return info
    except: pass
    return None


# ═══════════════════════════════════════════════
# 综合读取器
# ═══════════════════════════════════════════════
class THSPositionReader:
    def __init__(self):
        self.watcher = FileWatcher(r"d:\quant_framework\live_positions.csv")
        self.cached_positions = []
        self.last_read = None
        self._lock = threading.Lock()

    def read_all(self):
        """尝试所有策略，返回最可靠的持仓数据"""
        with self._lock:
            now = datetime.now()

            # 1. 文件变化检测 (最快)
            if self.watcher.has_changed():
                positions = self._read_csv(self.watcher.filepath)
                if positions:
                    self.cached_positions = positions
                    self.last_read = now
                    return {"positions": positions, "source": "file_watcher"}

            # 2. SQLite 数据库 (如果有)
            positions = try_read_sqlite()
            if positions:
                self.cached_positions = positions
                self.last_read = now
                return {"positions": positions, "source": "sqlite"}

            # 3. UIA 窗口读取
            positions = try_read_with_uia()
            if positions:
                self.cached_positions = positions
                self.last_read = now
                return {"positions": positions, "source": "uiautomation"}

            # 4. 返回缓存
            if self.cached_positions:
                return {"positions": self.cached_positions, "source": "cache"}

            # 5. 窗口标题摘要
            info = try_read_window_title()
            if info:
                return {"positions": [], "source": "window_title", "summary": info}

            return {"positions": [], "source": "none"}

    def _read_csv(self, filepath):
        positions = []
        try:
            for enc in ['utf-8-sig', 'utf-8', 'gbk']:
                try:
                    with open(filepath, 'r', encoding=enc) as f:
                        content = f.read()
                    break
                except: pass
            if not content: return None
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                try:
                    positions.append({
                        "symbol": str(row.get("代码", row.get("symbol", ""))).strip(),
                        "name": str(row.get("名称", row.get("name", ""))).strip(),
                        "quantity": int(float(row.get("数量", row.get("quantity", "0")) or 0)),
                        "cost_price": float(row.get("成本价", row.get("cost", "0")) or 0),
                        "current_price": float(row.get("现价", row.get("price", "0")) or 0),
                        "market_value": float(row.get("市值", row.get("value", "0")) or 0),
                        "profit_pct": float(row.get("盈亏%", row.get("pnl_pct", "0")) or 0),
                        "profit_amt": float(row.get("盈亏", row.get("pnl", "0")) or 0),
                    })
                except: pass
        except: pass
        return positions if positions else None


# 全局单例
ths_reader = THSPositionReader()
