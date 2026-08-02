"""三级预警自动熔断 v2.0 — P2-08 + 峰值回撤 (2026-07-10)
对标: FIA 2024 分层预警 + 私募峰值回撤标准
规则:
  🟡 关注: 日亏>2% / 连亏2笔 / 峰值回撤>7% → 钉钉提醒
  🟠 警告: 日亏>3.5% / 连亏3笔 / 峰值回撤>10% → 关QMT快速(只推不下单)
  🔴 熔断: 日亏>5% / 连亏5笔 / 峰值回撤>15% → 关总闸(全停)
"""
import sys, os, json, time
from datetime import datetime

sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

PEAK_FILE = r"D:\quant_framework\peak_equity.json"
MONTHLY_FILE = r"D:\quant_framework\monthly_equity.json"

# 动态阈值 (牛市放宽, 熊市收紧)
# 阈值格式: (日亏%, 连亏笔, 行业%, 峰值回撤%, 月度回撤%)
THRESHOLDS = {
    "bull": {"yellow": (3, 2, 25, 10, 7), "orange": (5, 3, 28, 13, 10), "red": (8, 5, 30, 18, 15)},
    "sideways": {"yellow": (2, 2, 20, 7, 5), "orange": (3.5, 3, 23, 10, 8), "red": (5, 5, 25, 15, 12)},
    "bear": {"yellow": (1.5, 2, 15, 5, 3), "orange": (2.5, 3, 18, 8, 6), "red": (3.5, 5, 20, 12, 10)},
}

def get_market_regime():
    """获取当前市场状态"""
    try:
        from market_regime import detect_regime
        from data_loader import load_stock_data_cache
        sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=30)
        r = detect_regime(sd) if sd else {}
        return r.get("regime", "sideways")
    except:
        return "sideways"

def get_current_equity() -> float:
    """获取当前总权益 (模拟盘现金+持仓市值)"""
    eq = 0
    try:
        from paper_engine import paper
        # paper 有 cash 属性和 get_total_equity() 方法
        eq = paper.get_total_equity() if hasattr(paper, 'get_total_equity') else 0
        if eq <= 0:
            eq = getattr(paper, 'cash', 0)
            try:
                eq += paper.get_market_value()
            except Exception:
                pass
    except Exception:
        pass
    if eq <= 0:
        try:
            import json as _j
            p = r"D:\quant_framework\paper_account.json"
            if os.path.exists(p):
                d = _j.load(open(p, encoding='utf-8'))
                eq = d.get('cash', 0) + sum(
                    pos.get('market_value', 0)
                    for pos in d.get('positions', {}).values())
        except Exception:
            pass
    return eq


def _load_peak() -> dict:
    """加载历史峰值"""
    if os.path.exists(PEAK_FILE):
        try:
            with open(PEAK_FILE, encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {"peak_equity": 0, "peak_date": ""}


def _save_peak(data: dict):
    """原子写入峰值"""
    tmp = PEAK_FILE + ".tmp"
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PEAK_FILE)
    except: pass


def update_peak_equity():
    """更新峰值权益 (每日盘后调用)"""
    eq = get_current_equity()
    peak = _load_peak()
    if eq > peak.get("peak_equity", 0):
        peak["peak_equity"] = eq
        peak["peak_date"] = datetime.now().strftime("%Y-%m-%d")
        _save_peak(peak)
    return peak


def get_drawdown_pct() -> float:
    """当前相对历史峰值的回撤百分比"""
    eq = get_current_equity()
    peak = _load_peak()
    pk = peak.get("peak_equity", 0)
    if pk <= 0 or eq <= 0:
        return 0
    return round((pk - eq) / pk * 100, 2)


# ── 月度回撤追踪 (C3: 月回撤>10%→当月禁买, 对标游资铁律) ──

def _load_monthly() -> dict:
    if os.path.exists(MONTHLY_FILE):
        try:
            with open(MONTHLY_FILE, encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {"month_start_equity": 0, "month_start_date": "", "month_disabled": False}


def _save_monthly(data: dict):
    tmp = MONTHLY_FILE + ".tmp"
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, MONTHLY_FILE)
    except: pass


def update_monthly_equity():
    """月初或首次运行时更新月度起始权益"""
    eq = get_current_equity()
    if eq <= 0:
        return
    m = _load_monthly()
    now = datetime.now()
    current_month = now.strftime("%Y-%m")
    saved_month = m.get("month_start_date", "")[:7]

    # 新月 → 重置月度基准
    if current_month != saved_month:
        m["month_start_equity"] = eq
        m["month_start_date"] = now.strftime("%Y-%m-%d")
        m["month_disabled"] = False  # 新月重置禁买
        _save_monthly(m)
    # 首次运行
    elif m.get("month_start_equity", 0) <= 0:
        m["month_start_equity"] = eq
        m["month_start_date"] = now.strftime("%Y-%m-%d")
        _save_monthly(m)


def get_monthly_drawdown_pct() -> float:
    """当前相对月初权益的回撤百分比"""
    eq = get_current_equity()
    m = _load_monthly()
    ms = m.get("month_start_equity", 0)
    if ms <= 0 or eq <= 0:
        return 0
    return round((ms - eq) / ms * 100, 2)


def get_risk_metrics():
    """获取当前风险指标 (含峰值回撤)"""
    m = {"daily_loss_pct": 0, "consecutive_loss": 0,
         "max_sector_pct": 0, "peak_drawdown_pct": 0, "monthly_drawdown_pct": 0}
    # 峰值回撤
    m["peak_drawdown_pct"] = get_drawdown_pct()
    # 月度回撤 (C3)
    m["monthly_drawdown_pct"] = get_monthly_drawdown_pct()
    try:
        from live_trader import state
        m["daily_loss_pct"] = abs(getattr(state, 'risk', {}).get('daily_loss', 0) or 0)
        m["consecutive_loss"] = getattr(state, 'risk', {}).get('consecutive_loss', 0) or 0
    except: pass
    try:
        from paper_engine import paper
        from stock_names import get_industry
        sects = {}
        for sym, pos in paper.positions.items():
            ind = get_industry(sym) or "其他"
            sects[ind] = sects.get(ind, 0) + pos.get("market_value", 0)
        total = sum(sects.values()) or 1
        m["max_sector_pct"] = round(max(sects.values()) / total * 100, 1) if sects else 0
    except: pass
    return m

def check_and_act():
    """检查风险指标并自动熔断 (含峰值回撤)"""
    # 先更新峰值和月度基准 (每次检查时刷新)
    update_peak_equity()
    update_monthly_equity()

    regime = get_market_regime()
    m = get_risk_metrics()
    t = THRESHOLDS.get(regime, THRESHOLDS["sideways"])
    dl, cl, sp, dd, md = (abs(m["daily_loss_pct"]), m["consecutive_loss"],
                           m["max_sector_pct"], m["peak_drawdown_pct"],
                           m["monthly_drawdown_pct"])

    level = "green"
    reason = ""
    # 从高到低检查 (五个维度任一触发即升级, C3加月度回撤)
    if dl >= t["red"][0] or cl >= t["red"][1] or sp >= t["red"][2] or dd >= t["red"][3] or md >= t["red"][4]:
        level, reason = "red", f"日亏{dl}%/连亏{cl}/行业{sp}%/回撤{dd}%/月回撤{md}%"
    elif dl >= t["orange"][0] or cl >= t["orange"][1] or sp >= t["orange"][2] or dd >= t["orange"][3] or md >= t["orange"][4]:
        level, reason = "orange", f"日亏{dl}%/连亏{cl}/行业{sp}%/回撤{dd}%/月回撤{md}%"
    elif dl >= t["yellow"][0] or cl >= t["yellow"][1] or sp >= t["yellow"][2] or dd >= t["yellow"][3] or md >= t["yellow"][4]:
        level, reason = "yellow", f"日亏{dl}%/连亏{cl}/行业{sp}%/回撤{dd}%/月回撤{md}%"

    action_taken = None
    try:
        from master_switch import toggle, get_status, emergency_halt
        st = get_status()

        # C3: 月回撤触发 → 当月禁买
        monthly = _load_monthly()
        if md >= t["orange"][4] and not monthly.get("month_disabled", False):
            monthly["month_disabled"] = True
            _save_monthly(monthly)
            if st.get("qmt_fast_enabled", True):
                toggle("qmt_fast_enabled", False, "月回撤熔断")
            action_taken = f"🟠 月回撤{md}%→当月禁买(下月自动恢复)"

        if level == "red" and not st.get("circuit_breaker", False):
            emergency_halt("自动熔断")
            action_taken = "🔴 总闸已自动关闭"
        elif level == "orange" and st.get("qmt_fast_enabled", True) and not action_taken:
            toggle("qmt_fast_enabled", False, "自动熔断")
            action_taken = "🟠 QMT快速已自动关闭(只推不下单)"
        elif level == "yellow" and not action_taken:
            action_taken = "🟡 已推送钉钉提醒"
    except Exception as e:
        action_taken = f"自动操作失败: {e}"

    # 去重: 同级别只报一次,级别变化才重报(防60秒循环推送)
    if not hasattr(check_and_act, '_last_level'):
        check_and_act._last_level = 'green'
    if level == check_and_act._last_level and level != 'green':
        print(f"[AutoBreaker] {regime} | {level} | 已报过,跳过推送")
        return
    check_and_act._last_level = level

    msg = f"[AutoBreaker] {regime} | 日亏{dl}% 连亏{cl} 行业{sp}% 回撤{dd}% 月回撤{md}% | {level} | {action_taken or '无动作'}"
    print(msg)

    if level != "green":
        try:
            from dingtalk_alerts import send_alert
            send_alert(f"自动熔断 {level}", f"{reason}\n{action_taken}", "critical" if level=="red" else "warning")
        except: pass

    return {"regime": regime, "metrics": m, "level": level, "action": action_taken}


if __name__ == "__main__":
    r = check_and_act()
    # 显示峰值
    peak = _load_peak()
    dd = get_drawdown_pct()
    print(f"峰值权益: {peak.get('peak_equity',0):.0f} ({peak.get('peak_date','')})")
    print(f"当前回撤: {dd}%")
    print(json.dumps(r, ensure_ascii=False, indent=2))
