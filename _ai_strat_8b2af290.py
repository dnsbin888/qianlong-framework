from a_stock_rules import AStockStrategy
import numpy as np

import numpy as np
import backtrader as bt
from AStockStrategy import AStockStrategy

class VolumeBreakStrategy(AStockStrategy):
    params = (
        ('volume_multiplier', 3.0),
        ('lookback', 20),
        ('close_retrace', 0.03),
        ('pre_open_volume_ratio', 1.5),
    )

    def signal_buy(self, data):
        if len(data) < self.p.lookback + 1:
            return

        close = np.array([data.close[-i] for i in range(self.p.lookback, 0, -1)])
        volume = np.array([data.volume[-i] for i in range(self.p.lookback, 0, -1)])
        high = np.array([data.high[-i] for i in range(self.p.lookback, 0, -1)])

        avg_volume = np.mean(volume[:-1])
        current_volume = volume[-1]
        current_close = close[-1]
        current_high = high[-1]

        # 放3倍量
        if current_volume < avg_volume * self.p.volume_multiplier:
            return

        # 大阳线: close > open (简单处理)
        if data.close[0] <= data.open[0]:
            return

        # 突破前期筹码集中区: 用20日均线近似
        ma20 = np.mean(close)
        if current_close <= ma20:
            return

        # 收盘价从最高价回落于3%以内
        if (current_high - current_close) / current_high > self.p.close_retrace:
            return

        # 次日集合竞价抢筹: 用前一日最后一根K线成交量放大判断
        pre_volume = data.volume[-1] if len(data) > 1 else 0
        avg_pre_volume = np.mean(volume) if len(volume) > 0 else 1
        if pre_volume < avg_pre_volume * self.p.pre_open_volume_ratio:
            return

        # 出现高开: 当日开盘价高于前日收盘价
        if data.open[0] <= data.close[-1]:
            return

        # 涨停不买
        if data.close[0] >= data.close[-1] * 1.095:
            return

        # 计算买入数量（100股整数倍）
        cash = self.broker.getcash()
        price = data.open[0]
        size = int(cash / price / 100) * 100
        if size > 0:
            self.order = self.buy(data=data, size=size)

    def signal_sell(self, data):
        pos = self.getposition(data)
        if not pos:
            return

        # 跌停不卖
        if data.close[0] <= data.close[-1] * 0.905:
            return

        # 止盈5%
        if data.close[0] >= pos.price * 1.05:
            self.order = self.sell(data=data, size=pos.size)
            return

        # 止损3%
        if data.close[0] <= pos.price * 0.97:
            self.order = self.sell(data=data, size=pos.size)