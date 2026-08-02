"""打板策略共享权重 (日线+QMT共用)"""
# 封板质量五因子权重
TIME_WEIGHT = 1.0       # 时间分权重
SEAL_WEIGHT = 1.0       # 封单分权重
BREAK_WEIGHT = 1.0      # 炸板惩罚权重
RESEAL_WEIGHT = 1.0     # 回封加分权重
SECTOR_WEIGHT = 1.0     # 板块联动权重

# 时间分 (日线用开盘代理, QMT用精确时间)
TIME_EARLY = 1.0        # 10:00前
TIME_MID = 0.7          # 10:30前
TIME_LATE = 0.4         # 11:00前
TIME_AFTERNOON = 0.1    # 午后

# 封单分
SEAL_STRONG = 1.0       # ≥3%
SEAL_MEDIUM = 0.7       # ≥2%
SEAL_WEAK = 0.4         # ≥1%

# 炸板惩罚
BREAK_NONE = 1.0        # 未炸
BREAK_ONCE = 0.7        # 1次
BREAK_TWICE = 0.3       # 2次+

# 回封加分
RESEAL_BONUS = 1.5      # 30分钟内回封

# 板块联动
SECTOR_BONUS = 1.3      # ≥3只涨停
SECTOR_BONUS_MED = 1.15 # ≥3只 (宽松)
SECTOR_BONUS_LOW = 1.05 # ≥1只

# 质量阈值
DAILY_PASS_SCORE = 30   # 日线候选最低分
REALTIME_PASS_SCORE = 0.7  # QMT实时确认最低分(默认)
# 动态确认阈值: 市场状态自适应 (对齐游资: 牛市跟势/熊市守质)
THRESHOLD = {"bull": 0.65, "sideways": 0.70, "bear": 0.80}
