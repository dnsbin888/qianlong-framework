"""A股涨跌停幅度公共工具 — 单一维护点.

A股各板块涨跌停幅度:
  - 沪深主板: 10%
  - ST / *ST: 5%
  - 创业板 (30xxxx): 20%
  - 科创板 (688xxx): 20%
  - 北交所 (8xxxxx / 4xxxxx): 30%

Usage::

    from quant_framework.core.market_limits import get_limit_pct, get_limit_price

    pct = get_limit_pct("300001")        # → 0.20
    up, down = get_limit_price("600519", 10.0)  # → (11.0, 9.0)

所有需要涨跌停判断的地方 (策略/脚本/回测/研究工具) 统一调用此模块,
禁止各自硬编码 ``0.10`` / ``1.10``.
"""

from __future__ import annotations

# ══════════════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════════════

LIMIT_PCT_MAP: dict[str, float] = {
    "main": 0.10,   # 沪深主板 (60xxxx / 00xxxx)
    "st":   0.05,   # ST / *ST
    "gem":  0.20,   # 创业板 (30xxxx)
    "star": 0.20,   # 科创板 (688xxx)
    "bse":  0.30,   # 北交所 (8xxxxx / 4xxxxx)
}

# ══════════════════════════════════════════════════════════════════════
# 公共函数
# ══════════════════════════════════════════════════════════════════════


def get_limit_pct(code: str) -> float:
    """根据股票代码返回涨跌停幅度.

    Args:
        code: 6位股票代码 (纯数字), 如 ``'600519'``, ``'300001'``,
              ``'688001'``, ``'830799'``.  也接受带后缀的如 ``'600519.day'``.

    Returns:
        涨跌停幅度, 如 ``0.10`` (10%), ``0.20`` (20%), ``0.30`` (30%).

    Examples::

        >>> get_limit_pct("600519")
        0.1
        >>> get_limit_pct("300001")
        0.2
        >>> get_limit_pct("688001")
        0.2
        >>> get_limit_pct("830799")
        0.3
    """
    digits = "".join(c for c in str(code) if c.isdigit())
    if len(digits) < 6:
        return LIMIT_PCT_MAP["main"]

    prefix = digits[:3]
    if prefix == "688":
        return LIMIT_PCT_MAP["star"]
    if prefix == "300":
        return LIMIT_PCT_MAP["gem"]
    if digits[0] in ("8", "4"):
        return LIMIT_PCT_MAP["bse"]
    return LIMIT_PCT_MAP["main"]


def get_limit_price(code: str, prev_close: float) -> tuple[float, float]:
    """返回涨停价和跌停价.

    Args:
        code: 股票代码.
        prev_close: 前收盘价.

    Returns:
        ``(limit_up, limit_down)`` 元组, 精确到分 (2位小数).
    """
    if prev_close <= 0:
        return (999.0, 0.0)
    pct = get_limit_pct(code)
    return (
        round(prev_close * (1 + pct), 2),
        round(prev_close * (1 - pct), 2),
    )


def is_limit_up(code: str, price: float, prev_close: float, tolerance: float = 0.01) -> bool:
    """判断是否涨停封死.

    Args:
        code: 股票代码.
        price: 当前/最新价.
        prev_close: 前收盘价.
        tolerance: 判定容差 (默认 0.01 元).
    """
    limit_up, _ = get_limit_price(code, prev_close)
    return price >= limit_up - tolerance


def is_limit_down(code: str, price: float, prev_close: float, tolerance: float = 0.01) -> bool:
    """判断是否跌停封死.

    Args:
        code: 股票代码.
        price: 当前/最新价.
        prev_close: 前收盘价.
        tolerance: 判定容差 (默认 0.01 元).
    """
    _, limit_down = get_limit_price(code, prev_close)
    return price <= limit_down + tolerance
