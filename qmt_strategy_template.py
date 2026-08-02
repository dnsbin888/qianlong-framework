"""QMT策略模板 — 潜龙快速通道 v2.0
=========================================
在 QMT 策略编辑器中粘贴此代码
=========================================

双道并行:
  审核通道: 所有信号 POST 到潜龙 Flask (记录)
  快速通道: enabled=true → passorder() 直接下单 (<20ms)

接入步骤:
  1. QMT客户端 → 策略编辑器 → 新建策略
  2. 粘贴此文件全部内容
  3. 修改第 30 行的 account_id (你的资金账号)
  4. 设置运行品种为 "自定义" → 添加 auto_trade_plan.json 里的股票
  5. 周期选 "1分钟"
  6. 保存 → 运行
"""

# ═══════════════════════════════════════════════
# 配置 (改这里)
# ═══════════════════════════════════════════════
ACCOUNT_ID = ""           # ← 填你的QMT资金账号
TOTAL_ASSET = 100000      # ← 填你的账户总资产
PLAN_PATH = r"D:\quant_web\data\auto_trade_plan.json"
FLASK_URL = "http://127.0.0.1:5002/api/qmt/signal"

# ═══════════════════════════════════════════════
# 导入
# ═══════════════════════════════════════════════
import json, os, time

# ═══════════════════════════════════════════════
# 日风控
# ═══════════════════════════════════════════════
_daily = {"trades": 0, "pct": 0}
_plan = {}
_plan_mtime = 0
_prev_bars = {}  # symbol → 上一根bar


def _load_plan():
    global _plan, _plan_mtime
    if not os.path.exists(PLAN_PATH):
        return {}
    mtime = os.path.getmtime(PLAN_PATH)
    if mtime != _plan_mtime:
        try:
            with open(PLAN_PATH, "r", encoding="utf-8") as f:
                _plan = json.load(f)
            _plan_mtime = mtime
        except:
            pass
    return _plan


def _to_qmt(sym):
    s = sym.lower()
    if s.startswith('sh'): return s[2:] + '.SH'
    if s.startswith('sz'): return s[2:] + '.SZ'
    if s.startswith('bj'): return s[2:] + '.BJ'
    return sym


def _qty(pct, price):
    if price <= 0: return 100
    amount = TOTAL_ASSET * pct / 100.0
    return max(100, int(amount / price / 100) * 100)


# ═══════════════════════════════════════════════
# 策略生命周期
# ═══════════════════════════════════════════════

def init(context):
    """策略启动时调用一次"""
    plan = _load_plan()
    limits = plan.get("global_limits", {})
    enabled = sum(1 for s in plan.get("stocks", {}).values() if s.get("enabled"))
    print(f"[潜龙] 执行计划: {enabled}/{len(plan.get('stocks',{}))} 只")
    print(f"[潜龙] 日限额: {limits.get('max_daily_trades',5)}笔")
    print(f"[潜龙] 熔断: {'是' if limits.get('circuit_breaker') else '否'}")
    print(f"[潜龙] 就绪 — 双道并行 (快速+审核)")


def handle_bar(context, bar):
    """每根K线触发"""
    global _daily

    # 无成交跳过
    if bar.volume <= 0:
        return

    plan = _load_plan()
    limits = plan.get("global_limits", {})

    # 熔断
    if limits.get("circuit_breaker"):
        return

    # 日限额
    if _daily["trades"] >= limits.get("max_daily_trades", 5):
        return

    sym = bar.symbol  # QMT格式: 600519.SH
    # 转潜龙格式: sh600519
    if '.SH' in sym.upper():
        ql_sym = 'sh' + sym.split('.')[0]
    elif '.SZ' in sym.upper():
        ql_sym = 'sz' + sym.split('.')[0]
    else:
        ql_sym = sym

    stock = plan.get("stocks", {}).get(ql_sym, {})
    price = bar.close

    # 信号检测
    signal_name = None
    prev = _prev_bars.get(sym, {})

    # 竞价抢筹: 高开>2% + 量比>3
    if prev and price > prev.get('close', 0) * 1.02 and bar.volume > prev.get('volume', 0) * 3:
        signal_name = "竞价抢筹"
    # 盘中突破: 突破昨收>3% + 放量
    elif prev and price > prev.get('close', 0) * 1.03 and bar.volume > prev.get('volume', 0) * 2:
        signal_name = "盘中突破"
    # 尾盘急拉: 14:30后 + 涨>3%
    elif time.strftime('%H%M') > '1430' and prev and (price / max(prev.get('close', 0), 0.01) - 1) > 0.03:
        signal_name = "尾盘急拉"
    # 打板: 触及涨停
    elif prev and price >= round(prev.get('close', 0) * 1.10, 2):
        signal_name = "打板追封"

    # 更新前一根bar
    _prev_bars[sym] = {
        'open': bar.open, 'high': bar.high, 'low': bar.low,
        'close': bar.close, 'volume': bar.volume, 'symbol': ql_sym,
    }

    if not signal_name:
        return

    # ═══════════════════════════════════════════
    # 审核通道: 始终 POST 到潜龙
    # ═══════════════════════════════════════════
    try:
        import requests
        requests.post(FLASK_URL, json={
            "symbol": ql_sym,
            "signal_type": signal_name,
            "price": price,
            "channel": "fast" if stock.get("enabled") else "review",
            "enabled": stock.get("enabled", False),
            "qmt_code": sym,
        }, timeout=3)
    except:
        pass

    # ═══════════════════════════════════════════
    # 快速通道: enabled=true → passorder()
    # ═══════════════════════════════════════════
    if not stock.get("enabled", False):
        print(f"[审核] {ql_sym} {signal_name} @{price} — 未批准")
        return

    # ML检查 (如果有qmt_trade_config.json)
    ml_pass = True
    config_path = r"D:\quant_web\data\qmt_trade_config.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            ml = cfg.get(ql_sym, {})
            best = max(ml.get("lgbm", 0), ml.get("xgb", 0), ml.get("cb", 0))
            if best < stock.get("min_ml_score", 80):
                ml_pass = False
        except:
            pass

    if not ml_pass:
        print(f"[快速] ❌ {ql_sym} ML不达标")
        return

    # 信号类型匹配
    if signal_name not in stock.get("signal_types", []):
        print(f"[快速] ❌ {ql_sym} {signal_name} 不在白名单")
        return

    # ── 下单 ──
    pos_pct = stock.get("max_position_pct", 3)
    qty = _qty(pos_pct, price)

    try:
        # passorder(买入=23, 限价=1101, 账户, 代码, 限价=0, 价格, 数量, 策略名, 备注, 快速=2)
        passorder(23, 1101, ACCOUNT_ID, sym, 0, price, qty, "潜龙快速", ql_sym, 2)
        _daily["trades"] += 1
        _daily["pct"] += pos_pct
        print(f"[快速] ✅ {sym} BUY {qty}股@{price} {pos_pct}% ({_daily['trades']}笔/{_daily['pct']:.1f}%)")
    except Exception as e:
        print(f"[快速] ❌ {sym} passorder失败: {e}")


def on_stock_trade(context, trade):
    """成交回报"""
    pass


def on_account_status(context, account):
    """账户状态"""
    pass
