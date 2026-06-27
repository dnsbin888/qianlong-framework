"""潜龙多策略模块 (蓝图 v3.0 Phase 2 + V1-5)

策略 (5个):
    - chase:      追涨策略 (动量突破, 原有)
    - low_absorb: 低吸策略 (反转因子 v2, S1-1 + V1-5修正)
    - defensive:  防守策略 (低波动+高股息, S1-2)
    - chip:       筹码策略 (获利盘+集中度, V1-5 IC=+0.034)
    - fund_flow:  资金流向策略 (反转版, V1-5 IC≈+0.063)
"""

from strategies.low_absorb_strategy import generate_low_absorb_signal
from strategies.defensive_strategy import generate_defensive_signal
from strategies.chip_strategy import generate_chip_signal
from strategies.fund_flow_strategy import generate_fund_flow_signal
