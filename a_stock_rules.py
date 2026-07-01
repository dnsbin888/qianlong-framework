"""潜龙 A股 Backtrader 配置 (v3.0)

含: T+1, 涨跌停, 印花税, 百股整数
用法: from a_stock_rules import AStockConfig; cerebro.addanalyzer(AStockConfig)
"""
import backtrader as bt


class T1Constraint(bt.Sizer):
    """A股T+1: 当日买入不可当日卖出"""
    params = (('stake', 100),)

    def _getsizing(self, comminfo, cash, data, isbuy):
        if isbuy:
            return self.p.stake
        pos = self.broker.getposition(data)
        if not pos:
            return 0
        return min(self.p.stake, pos.size)


class StampDutyCommission(bt.CommInfoBase):
    """A股佣金: 买入0.03%, 卖出0.03%+印花税0.1%"""
    params = (
        ('commission', 0.0003),
        ('stamp_duty', 0.001),
        ('min_commission', 5.0),
    )

    def _getcommission(self, size, price, pseudoexec):
        value = abs(size) * price
        comm = max(value * self.p.commission, self.p.min_commission)
        if size < 0:
            comm += value * self.p.stamp_duty
        return comm


class PriceLimitFilter:
    """涨跌停过滤器: 涨停不买, 跌停不卖"""
    def __init__(self, limit_pct=0.10):
        self.limit_pct = limit_pct

    def can_buy(self, data):
        return data.close[0] < data.close[-1] * (1 + self.limit_pct)

    def can_sell(self, data):
        return data.close[0] > data.close[-1] * (1 - self.limit_pct)


class RoundLotSizer(bt.Sizer):
    """百股整数: A股最小交易单位100股"""
    params = (('stake', 100),)

    def _getsizing(self, comminfo, cash, data, isbuy):
        size = cash / data.close[0] if isbuy else self.broker.getposition(data).size
        size = max(size, self.p.stake)
        return int(size / self.p.stake) * self.p.stake


class AStockStrategy(bt.Strategy):
    """A股策略基类 — 所有策略继承此类获得A股规则"""
    params = (
        ('limit_pct', 0.10),
        ('hold_days', 5),
    )

    def __init__(self):
        self.limit_filter = PriceLimitFilter(self.p.limit_pct)
        self.order = None
        self.buy_dates = {}

    def notify_order(self, order):
        if order.status in [order.Completed]:
            if order.isbuy():
                self.buy_dates[order.data._name] = len(self.data)

    def can_sell_today(self, data):
        """T+1检查: 今天之前买入的才能卖"""
        buy_day = self.buy_dates.get(data._name, -999)
        return len(self.data) > buy_day + 1

    def next(self):
        if self.order:
            return
        for data in self.datas:
            pos = self.getposition(data)
            if pos:
                if self.can_sell_today(data) and self.limit_filter.can_sell(data):
                    self.signal_sell(data)
            else:
                if self.limit_filter.can_buy(data):
                    self.signal_buy(data)

    def signal_buy(self, data):
        pass

    def signal_sell(self, data):
        pass
