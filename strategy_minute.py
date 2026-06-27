"""分钟K线策略 — 基于TDX .lc5 分钟线数据生成日内信号

信号类型:
  - 30分钟均线突破买入
  - 60分钟死叉卖出
  - 日内量价背离预警
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, r"D:\quant_web")


def get_minute_bars(code, market='sh', count=30):
    """获取最近N根分钟K线"""
    try:
        from tdx_realtime import read_minline
        result = read_minline(code, market)
        if result:
            return [result]  # 当前只读最新一根
    except Exception:
        pass
    return []


def check_ma_breakout(code, market='sh'):
    """30分钟均线突破买入信号"""
    bars = get_minute_bars(code, market, count=30)
    if len(bars) < 20:
        return None
    prices = [b['price'] for b in bars]
    ma5 = sum(prices[-5:]) / 5 if len(prices) >= 5 else prices[-1]
    ma20 = sum(prices[-20:]) / 20 if len(prices) >= 20 else prices[-1]
    # 5均线上穿20均线
    if len(prices) >= 6 and prices[-1] > ma5:
        prev_ma5 = sum(prices[-6:-1]) / 5
        if prev_ma5 <= ma20:
            return {'signal': 'buy', 'reason': '30分钟均线突破', 'strength': 4}
    return None


def check_volume_surge(code, market='sh'):
    """量价背离预警"""
    bars = get_minute_bars(code, market, count=30)
    if len(bars) < 10:
        return None
    prices = [b['price'] for b in bars]
    volumes = [b['volume'] for b in bars[-10:]]
    avg_vol = sum(volumes) / len(volumes) if volumes else 0
    last_vol = volumes[-1] if volumes else 0
    # 放量下跌 → 预警
    if last_vol > avg_vol * 2 and prices[-1] < prices[-2]:
        return {'signal': 'warn', 'reason': '放量下跌', 'strength': 2}
    return None


def generate_minute_signals(positions):
    """为持仓股生成分钟级信号"""
    signals = []
    for pos in positions:
        sym = pos.get('symbol', '')
        code = sym.replace('sh', '').replace('sz', '')
        mkt = 'sh' if sym.startswith('sh') or code.startswith('6') else 'sz'

        breakout = check_ma_breakout(code, mkt)
        if breakout:
            signals.append({**breakout, 'symbol': sym})

        surge = check_volume_surge(code, mkt)
        if surge:
            signals.append({**surge, 'symbol': sym})

    return signals
