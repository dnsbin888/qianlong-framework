"""盘中突破 check(ctx) — 注册表模式"""

def breakthrough_check(ctx):
    """涨>3% + 放量>2x, 反转候选需价格>VWAP"""
    if ctx['price'] > ctx['prev_close'] * 1.03 and ctx['prev_vol'] > 0 and ctx['volume'] > ctx['prev_vol'] * 2:
        if ctx.get('_is_reversal') and not ctx.get('_above_vwap', True):
            return None
        return "盘中突破"
    return None
