from a_stock_rules import AStockStrategy
import numpy as np

import numpy as np
import backtrader as bt
from AStockStrategy import AStockStrategy

class VolumeBreakStrategy(AStockStrategy):
    params = (
        ('lookback', 20),
        ('vol_multiplier', 3.0),
        ('high_retrace', 3),
        ('preopen_check', True),
    )

    def __init__(self):
        super().__init__()
        self.order = None

    def signal_buy(self, data):
        if self.order:
            return
        if len(data) < self.p.lookback + 5:
            return
        
        close = np.array([data.close[-i] for i in range(self.p.lookback, 0, -1)])
        high = np.array([data.high[-i] for i in range(self.p.lookback, 0, -1)])
        low = np.array([data.low[-i] for i in range(self.p.lookback, 0, -1)])
        volume = np.array([data.volume[-i] for i in range(self.p.lookback, 0, -1)])
        
        today_close = data.close[0]
        today_high = data.high[0]
        today_low = data.low[0]
        today_vol = data.volume[0]
        
        # 涨停不买
        if today_close >= data.close[-1] * 1.095:
            return
            
        # 计算成交量均值
        avg_vol = np.mean(volume[:-1])
        if avg_vol == 0:
            return
            
        # 放3倍量
        if today_vol < avg_vol * self.p.vol_multiplier:
            return
            
        # 大阳线判定: 收盘价 > 开盘价且涨幅 > 3%
        if today_close <= data.open[0] or (today_close - data.open[0]) / data.open[0] < 0.03:
            return
            
        # 计算筹码集中区域 (前N天价格波动区间)
        price_range = high.max() - low.min()
        if price_range == 0:
            return
            
        # 判断是否突破前期筹码集中区 (前N天均线附近)
        ma_close = np.mean(close)
        if today_close <= ma_close * 1.01:
            return
            
        # 收盘价从最高价回落不超过3档 (假设1档=0.01元)
        if today_high - today_close > 0.03:
            return
            
        # 次日集合竞价高开检查 (使用次日开盘价与今日收盘价比较)
        if self.p.preopen_check:
            next_open = data.open[0]  # 当前bar是次日开盘
            if next_open <= today_close * 1.005:
                return
                
        # 买入信号
        size = self.calculate_size(data, today_close)
        if size > 0:
            self.order = self.buy(data=data, size=size)

    def signal_sell(self, data):
        pos = self.getposition(data)
        if not pos:
            return
            
        current_price = data.close[0]
        buy_price = pos.price
        
        # 跌停不卖
        if current_price <= data.close[-1] * 0.905:
            return
            
        # 止盈5%
        if current_price >= buy_price * 1.05:
            self.order = self.sell(data=data, size=pos.size)
            return
            
        # 止损3%
        if current_price <= buy_price * 0.97:
            self.order = self.sell(data=data, size=pos.size)
            return

    def calculate_size(self, data, price