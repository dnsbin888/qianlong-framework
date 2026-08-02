"""打板·一封 check(ctx) — 注册表模式"""
import time

def chase_check(ctx):
    """触板检测: bar.high预筛 + 实时价确认 + 时间/情绪/宽度过滤"""
    _lim = round(ctx['prev_close'] * (1 + ctx['_lp']), 2)
    if ctx['bar_high'] >= _lim * 0.995 and ctx['prev_vol'] > 0 and ctx['volume'] > ctx['prev_vol'] * 1.5:
        try:
            _ft = ctx['ContextInfo'].get_full_tick(ctx['qmt_code'])
            _rt = float(_ft.lastPrice) if hasattr(_ft, 'lastPrice') else ctx['price']
        except:
            _rt = ctx['price']
        if _rt >= _lim * 0.995:
            if time.strftime('%H%M') < '1400':
                if ctx.get('_sentiment_stage','ferment') != "retreat":
                    if ctx.get('_advance_ratio',0.5) >= 0.5 and ctx.get('_limit_down_count',0) <= 50:
                        return "打板·一封"
    return None
