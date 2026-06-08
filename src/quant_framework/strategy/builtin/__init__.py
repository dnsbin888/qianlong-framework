"""Built-in strategies migrated from 同花顺 classic strategies.

Each strategy is a BaseStrategy subclass with a Pydantic/dataclass config.
Configurations can be loaded from YAML files in config/strategies/.
"""

from quant_framework.strategy.builtin.board_break import BoardBreakConfig, BoardBreakStrategy
from quant_framework.strategy.builtin.bounce_buy import BounceBuyConfig, BounceBuyStrategy
from quant_framework.strategy.builtin.grid_trading import GridTradingConfig, GridTradingStrategy
from quant_framework.strategy.builtin.intraday_change import IntradayChangeConfig, IntradayChangeStrategy
from quant_framework.strategy.builtin.limit_up_chase import LimitUpChaseConfig, LimitUpChaseStrategy
from quant_framework.strategy.builtin.ma_condition import MAConditionConfig, MAConditionStrategy
from quant_framework.strategy.builtin.macd_cross import MACDCrossConfig, MACDCrossStrategy
from quant_framework.strategy.builtin.price_condition import PriceConditionConfig, PriceConditionStrategy
from quant_framework.strategy.builtin.scheduled_trade import ScheduledTradeConfig, ScheduledTradeStrategy
from quant_framework.strategy.builtin.stop_profit_loss import StopProfitLossConfig, StopProfitLossStrategy
from quant_framework.strategy.builtin.tdx_t1_scalp import T1ScalpConfig, T1ScalpStrategy
from quant_framework.strategy.builtin.tdx_t1_scalp_v2 import T1ScalpV2Config, T1ScalpV2Strategy
from quant_framework.strategy.builtin.tdx_t1_scalp_v3 import T1ScalpV3Config, T1ScalpV3Strategy

__all__ = [
    "MACDCrossStrategy", "MACDCrossConfig",
    "GridTradingStrategy", "GridTradingConfig",
    "StopProfitLossStrategy", "StopProfitLossConfig",
    "LimitUpChaseStrategy", "LimitUpChaseConfig",
    "BoardBreakStrategy", "BoardBreakConfig",
    "BounceBuyStrategy", "BounceBuyConfig",
    "MAConditionStrategy", "MAConditionConfig",
    "PriceConditionStrategy", "PriceConditionConfig",
    "ScheduledTradeStrategy", "ScheduledTradeConfig",
    "IntradayChangeStrategy", "IntradayChangeConfig",
    "T1ScalpStrategy", "T1ScalpConfig",
    "T1ScalpV2Strategy", "T1ScalpV2Config",
    "T1ScalpV3Strategy", "T1ScalpV3Config",
]
