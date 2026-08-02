"""超跌反弹 + 弱转强 共享阈值 (日线+QMT共用)"""
# 超跌反弹阈值
OVERSOLD_Z_MIN = -1.5       # Z-score 最低阈值
OVERSOLD_CONNORS_MAX = 15   # Connors RSI 超卖上限
OVERSOLD_STABILIZE_MAX = -0.01  # 企稳最大跌幅
OVERSOLD_VOL_MIN = 1.2      # 最低量比

# 弱转强阈值
WTS_VOL_MIN = 1.5           # 放量倍数
WTS_TURNOVER_MIN = 3        # 最低换手%
WTS_TURNOVER_MAX = 50       # 最高换手%
WTS_PRICE_MIN = 5           # 最低股价

# 评分权重
SCORE_Z_DEPTH = 6           # Z-score 深度系数
SCORE_TAPE = 30             # 盘口验证满分
SCORE_REGIME = 30           # 市场适配满分
