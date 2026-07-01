from a_stock_rules import AStockStrategy
import numpy as np

class BreakthroughMa20Strategy(AStockStrategy):
    def signal_buy(self, data):
        close_list = [data.close[-i] for i in range(20, 0, -1)]
        vol_list = [data.volume[-i] for i in range(20, 0, -1)]
        if len(close_list) < 20:
            return
        close = np.array(close_list)
        vol = np.array(vol_list)
        ma20 = np.mean(close)
        vol_ratio = vol[-1] / (np.mean(vol[:-1]) + 1e-8)
        if close[-1] > ma20 and vol_ratio > 2.0:
            self.order = self.buy(data=data, size=100)

    def signal_sell(self, data):
        pos = self.getposition(data)
        if pos and data.close[0] > pos.price * 1.05:
            self.order = self.sell(data=data)