"""统一参数入口 v1.0 — 所有交易参数从 trade_config_master.json 读取
铁律#14: 参数三层体系, master为唯一基线

用法:
  from config_loader import get_param
  stop_loss = get_param("hard_stop_loss")          # 共享参数
  max_pos = get_param("max_positions", channel="real")  # 通道特有
"""
import json, os

_MASTER_PATH = r"D:\quant_framework\trade_config_master.json"
_cache = None
_cache_mtime = 0


def _load_master():
    global _cache, _cache_mtime
    if not os.path.exists(_MASTER_PATH):
        return {}
    mtime = os.path.getmtime(_MASTER_PATH)
    if _cache is None or mtime != _cache_mtime:
        with open(_MASTER_PATH, "r", encoding="utf-8") as f:
            _cache = json.load(f)
        _cache_mtime = mtime
    return _cache


def get_param(key, channel="sim", default=None):
    """统一读取参数

    Args:
        key: 参数名 (如 "hard_stop_loss", "tp1_profit_pct")
        channel: "sim"=模拟盘, "real"=实盘/QMT
        default: 兜底值

    通道差异自动处理:
      "max_positions" → master.sim.max_positions 或 master.real.max_positions
      "min_cash_reserve" → master.sim.min_cash_reserve 或 master.real.min_cash_reserve
    """
    m = _load_master()
    if not m:
        return default

    # ── 通道映射: 共享参数取顶层, 通道特有取 sim/real 段 ──
    _channel_keys = {
        "max_positions": ("sim", "real"),
        "min_cash_reserve": ("sim", "real"),
        "initial_cash": ("sim", None),  # 模拟盘独有
        "live_total_asset": ("real", None),  # 实盘独有
        "live_cash": ("real", None),
    }

    if key in _channel_keys:
        sources = _channel_keys[key]
        for src in sources:
            if src and src == channel and src in m:
                val = m[src].get(key)
                if val is not None:
                    return val
        return default

    # ── TP参数 ──
    tp_map = {
        "tp1_profit_pct":  ("tp1", "profit_pct"),
        "tp1_trail_pct":   ("tp1", "trail_pct"),
        "tp1_sell_ratio":  ("tp1", "sell_ratio"),
        "tp1_stop_loss":   ("tp1", "stop_loss"),
        "tp2_profit_pct":  ("tp2", "profit_pct"),
        "tp2_trail_pct":   ("tp2", "trail_pct"),
        "tp2_sell_ratio":  ("tp2", "sell_ratio"),
        "tp2_stop_loss":   ("tp2", "stop_loss"),
        "tp3_profit_pct":  ("tp3", "profit_pct"),
        "tp3_trail_pct":   ("tp3", "trail_pct"),
        "tp3_sell_ratio":  ("tp3", "sell_ratio"),
        "tp3_stop_loss":   ("tp3", "stop_loss"),
    }
    if key in tp_map:
        tier, field = tp_map[key]
        val = m.get("take_profit", {}).get(tier, {}).get(field)
        return val if val is not None else default

    # ── 止损参数 ──
    sl_map = {
        "hard_stop_loss":  ("hard", -0.055),
        "soft_stop_loss":  ("soft", -0.03),
        "hard_stop_action": ("hard_action", "清仓"),
        "soft_stop_action": ("soft_action", "卖半仓"),
    }
    if key in sl_map:
        field, fallback = sl_map[key]
        return m.get("stop_loss", {}).get(field, fallback)

    # ── 仓位参数 ──
    ps_map = {
        "max_single_pct": 0.20,
        "max_single_hard": 0.25,
        "max_sector_pct": 0.25,
        "max_hold_days": 7,
        "min_cash_reserve_pct": 0.15,
        "lv5_pct": 0.50,
        "lv4_pct": 0.33,
        "lv3_pct": 0.25,
        "lv2_pct": 0.20,
        "lv1_pct": 0.05,
    }
    if key in ps_map:
        return m.get("position_sizing", {}).get(key, ps_map[key])

    # ── 日风控参数 ──
    dr_map = {
        "level1_loss_pct": -0.03,
        "level2_loss_pct": -0.05,
        "max_daily_trades": 5,
        "max_consecutive_loss": 3,
    }
    if key in dr_map:
        return m.get("daily_risk", {}).get(key.replace("level1_loss_pct", "level1_pct")
                                            .replace("level2_loss_pct", "level2_pct")
                                            .replace("max_daily_trades", "max_trades"), dr_map[key])

    # ── 自动交易参数 ──
    if key == "min_ml_score":
        return m.get("auto_trade", {}).get("min_ml_score", 70)
    if key == "max_auto_position":
        return m.get("auto_trade", {}).get("max_auto_position", 12)

    # ── 信号过滤 ──
    sf_map = {"min_strength": 3, "vol_ratio_threshold": 1.2}
    if key in sf_map:
        return m.get("signal_filter", {}).get(key, sf_map[key])

    # ── 兜底: 直接在顶层找 ──
    return m.get(key, default)


# ═══════════════════════════════════
# 批量加载 (供 live_trader 等需要多个参数的地方)
# ═══════════════════════════════════

def load_tp_params():
    """返回完整的三级止盈参数 dict (共享)"""
    return {
        "tp1_profit_pct": get_param("tp1_profit_pct"),
        "tp1_trail_pct": get_param("tp1_trail_pct"),
        "tp1_sell_ratio": get_param("tp1_sell_ratio"),
        "tp1_stop_loss": get_param("tp1_stop_loss"),
        "tp2_profit_pct": get_param("tp2_profit_pct"),
        "tp2_trail_pct": get_param("tp2_trail_pct"),
        "tp2_sell_ratio": get_param("tp2_sell_ratio"),
        "tp2_stop_loss": get_param("tp2_stop_loss"),
        "tp3_profit_pct": get_param("tp3_profit_pct"),
        "tp3_trail_pct": get_param("tp3_trail_pct"),
        "tp3_sell_ratio": get_param("tp3_sell_ratio"),
        "tp3_stop_loss": get_param("tp3_stop_loss"),
        "hard_stop_loss": get_param("hard_stop_loss"),
        "soft_stop_loss": get_param("soft_stop_loss"),
    }


def load_risk_params(channel="sim"):
    """返回日风控参数"""
    return {
        "level1_loss_pct": get_param("level1_loss_pct"),
        "level2_loss_pct": get_param("level2_loss_pct"),
        "max_daily_trades": get_param("max_daily_trades"),
        "max_consecutive_loss": get_param("max_consecutive_loss"),
        "max_positions": get_param("max_positions", channel=channel),
        "max_single_pct": get_param("max_single_pct"),
        "max_sector_pct": get_param("max_sector_pct"),
        "max_hold_days": get_param("max_hold_days"),
    }


if __name__ == "__main__":
    print("=" * 50)
    print("  config_loader v1.0 — 统一参数入口")
    print("=" * 50)
    print(f"  实盘 max_positions: {get_param('max_positions', channel='real')}")
    print(f"  模拟 max_positions: {get_param('max_positions', channel='sim')}")
    print(f"  TP1 profit: {get_param('tp1_profit_pct')}")
    print(f"  TP3 sell: {get_param('tp3_sell_ratio')}")
    print(f"  硬止损: {get_param('hard_stop_loss')}")
    print(f"  软止损: {get_param('soft_stop_loss')}")
    print(f"  ML门槛: {get_param('min_ml_score')}")
    print(f"  日交易上限: {get_param('max_daily_trades')}")
    print(f"  连亏熔断: {get_param('max_consecutive_loss')}")
    print(f"  单票上限: {get_param('max_single_pct')}")
    print(f"  行业上限: {get_param('max_sector_pct')}")
    print(f"  持仓天数: {get_param('max_hold_days')}")
    print(f"\n  三级止盈: {json.dumps(load_tp_params(), indent=2)}")
    print("\n✅ config_loader 就绪")
