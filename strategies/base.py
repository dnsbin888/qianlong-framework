"""策略基类"""


class BaseStrategy:
    """所有策略的基类"""

    name = "base"
    description = "策略基类"

    def generate(self, factor_cache, stock_data) -> list:
        """生成买入信号

        Args:
            factor_cache: StockInfo列表
            stock_data: {symbol: DataFrame}

        Returns:
            list[dict]: [{"symbol": str, "price": float, "power_score": int, ...}]
        """
        raise NotImplementedError
