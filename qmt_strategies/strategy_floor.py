"""撬板战法 check(ctx)"""

def floor_check(ctx):
    if ctx['_lp'] != 0.10: return None
    limit_down = round(ctx['prev_close'] * (1 - ctx['_lp']), 2)
    if ctx['price'] <= limit_down * 1.01 and ctx['prev_vol'] > 0 and ctx['volume'] > ctx['prev_vol'] * 5:
        if ctx['open_price'] <= limit_down * 1.005 and ctx['price'] > limit_down * 1.005:
            return "撬板战法"
    return None
