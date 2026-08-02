"""竞价抢筹 check(ctx)"""

def auction_check(ctx):
    if ctx['open_price'] > ctx['prev_close'] * 1.02 and ctx['prev_vol'] > 0 and ctx['volume'] > ctx['prev_vol'] * 3:
        if ctx.get('_is_reversal') and not ctx.get('_vol_ok'):
            return None
        return "竞价抢筹"
    return None
