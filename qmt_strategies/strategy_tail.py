"""尾盘急拉 check(ctx)"""
import time

def tail_rush_check(ctx):
    if time.strftime('%H%M') > '1430' and (ctx['price'] / max(ctx['prev_close'], 0.01) - 1) > 0.03:
        return "尾盘急拉"
    return None
