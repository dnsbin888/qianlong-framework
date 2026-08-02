"""板后接力 check(ctx)"""

def continuation_check(ctx):
    if ctx['_lp'] != 0.10: return None
    try:
        # QMT get_market_data(fields, stock_list, start, end, skip, period, dividend, count)
        mk3 = ctx['ContextInfo'].get_market_data(
            ['close','open','volume'], [ctx['qmt_code']],
            '', '', True, '', '', 3)
        if mk3 and ctx['qmt_code'] in mk3:
            d = mk3[ctx['qmt_code']]
            closes = list(d.get('close', [])) if hasattr(d,'get') else []
            opens = list(d.get('open', [])) if hasattr(d,'get') else []
            vols = list(d.get('volume', [])) if hasattr(d,'get') else []
            if len(closes) >= 2:
                yest_close = float(closes[-1])
                yest_open = float(opens[-1])
                if yest_close >= round(yest_open * 1.095, 2):
                    if float(opens[0]) < yest_close and ctx['price'] > float(opens[0]) and ctx['price'] > yest_close * 0.98:
                        avg_vol = sum(vols) / len(vols) if vols else ctx['volume']
                        if ctx['volume'] > avg_vol * 1.3:
                            return "板后接力"
    except: pass
    return None
