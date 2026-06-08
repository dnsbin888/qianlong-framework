"""验证 P0-因子-02 修复 — 财务因子集成 + 披露日期对齐。

测试覆盖:
  1. 披露日期规则: Q1(4/30), Q2(8/31), Q3(10/31), Q4(次年4/30)
  2. 核心场景: 2022-05-01 时 2022Q1 已可用, 2022Q2 不可用
  3. FinancialDataLoader 基本功能
  4. FactorEngine 集成 (financial factor routing)
"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
from datetime import datetime
import numpy as np
import pandas as pd
import warnings; warnings.filterwarnings("ignore")

from financial_factors import (
    FinancialDataLoader,
    get_disclosure_deadline,
    make_financial_factor_compute,
)
from quant_framework.factors.engine import FactorEngine

PASS = 0
FAIL = 0

def check(condition, description):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {description}")
    else:
        FAIL += 1
        print(f"  ✗ FAIL: {description}")

print("=" * 70)
print("  P0-因子-02 验证: 财务因子集成 + 披露日期对齐")
print("=" * 70)

# ======================================================================
# Test 1: 披露日期规则
# ======================================================================
print("\n── Test 1: Disclosure Deadline Rules ──")

# Q1 report (ends 3/31) -> deadline 4/30 same year
deadline_q1 = get_disclosure_deadline(datetime(2022, 3, 31))
check(deadline_q1 == datetime(2022, 4, 30),
      f"Q1 deadline = {deadline_q1.strftime('%Y-%m-%d')} (expected 2022-04-30)")

# Q2 report (ends 6/30) -> deadline 8/31 same year
deadline_q2 = get_disclosure_deadline(datetime(2022, 6, 30))
check(deadline_q2 == datetime(2022, 8, 31),
      f"Q2 deadline = {deadline_q2.strftime('%Y-%m-%d')} (expected 2022-08-31)")

# Q3 report (ends 9/30) -> deadline 10/31 same year
deadline_q3 = get_disclosure_deadline(datetime(2022, 9, 30))
check(deadline_q3 == datetime(2022, 10, 31),
      f"Q3 deadline = {deadline_q3.strftime('%Y-%m-%d')} (expected 2022-10-31)")

# Q4 report (ends 12/31) -> deadline 4/30 NEXT year
deadline_q4 = get_disclosure_deadline(datetime(2022, 12, 31))
check(deadline_q4 == datetime(2023, 4, 30),
      f"Q4 deadline = {deadline_q4.strftime('%Y-%m-%d')} (expected 2023-04-30)")

# ======================================================================
# Test 2: FinancialDataLoader 基本功能
# ======================================================================
print("\n── Test 2: FinancialDataLoader Basic Operations ──")

loader = FinancialDataLoader()

# 模拟一只股票的财报数据 (000001, 平安银行)
# 报告期 → (report_period, disclosure_date, {factors})
records = [
    # 2021 Q4 (年报) — 2022-03-15 提前披露
    (datetime(2021, 12, 31), datetime(2022, 3, 15),
     {"pe_ttm": 8.5, "pb": 0.9, "roe": 12.3, "profit_growth": 25.0}),
    # 2022 Q1 — 2022-04-28 披露
    (datetime(2022, 3, 31), datetime(2022, 4, 28),
     {"pe_ttm": 7.8, "pb": 0.85, "roe": 13.1, "profit_growth": 30.0}),
    # 2022 Q2 (中报) — 2022-08-20 披露
    (datetime(2022, 6, 30), datetime(2022, 8, 20),
     {"pe_ttm": 7.2, "pb": 0.80, "roe": 13.5, "profit_growth": 28.0}),
    # 2022 Q3 — 2022-10-25 披露
    (datetime(2022, 9, 30), datetime(2022, 10, 25),
     {"pe_ttm": 6.9, "pb": 0.78, "roe": 14.0, "profit_growth": 26.0}),
    # 2022 Q4 (年报) — 2023-03-20 披露
    (datetime(2022, 12, 31), datetime(2023, 3, 20),
     {"pe_ttm": 6.5, "pb": 0.75, "roe": 14.5, "profit_growth": 22.0}),
]

for rp, disc_dt, factors in records:
    loader.add_manual_record("000001", rp, factors, disc_dt)

check(len(loader) == 5, f"Total records = {len(loader)} (expected 5)")
check(loader.has_symbol("000001"), "Symbol '000001' exists")
check("000001" in loader, "Symbol '000001' in loader (__contains__)")
check(loader.symbol_report_count("000001") == 5, f"Report count = {loader.symbol_report_count('000001')} (expected 5)")
check("pe_ttm" in loader.factor_names, "Factor 'pe_ttm' available")
check("roe" in loader.factor_names, "Factor 'roe' available")

# ======================================================================
# Test 3: 核心验证 — 披露日期对齐 (用户的验证方法)
# ======================================================================
print("\n── Test 3: Disclosure Date Alignment (Core) ──")
print("  Scenario: Query financial factors on 2022-05-01")

date_test = 20220501

# 2022-05-01: 2021Q4 已披露 (3/15), 2022Q1 已披露 (4/28), 2022Q2 未披露 (要到8/20)
# → 应该返回 2022Q1 的值

pe = loader.get_financial_factor("000001", date_test, "pe_ttm")
check(pe == 7.8, f"PE on 2022-05-01 = {pe} (expected 7.8 from 2022Q1)")

pb = loader.get_financial_factor("000001", date_test, "pb")
check(pb == 0.85, f"PB on 2022-05-01 = {pb} (expected 0.85 from 2022Q1)")

roe = loader.get_financial_factor("000001", date_test, "roe")
check(roe == 13.1, f"ROE on 2022-05-01 = {roe} (expected 13.1 from 2022Q1)")

pg = loader.get_financial_factor("000001", date_test, "profit_growth")
check(pg == 30.0, f"Profit_growth on 2022-05-01 = {pg} (expected 30.0 from 2022Q1)")

# 验证 2022Q2 确实不可用 (即不会返回 Q2 的值)
# Q2 PE = 7.2, Q1 PE = 7.8 → 如果返回 7.2 说明未来函数
check(pe != 7.2, "PE is NOT 7.2 (2022Q2 value NOT leaked!)")

print("\n  ── Step-by-step availability check ──")

# Before 2022Q1 disclosure (e.g., 2022-04-01)
pe_before_q1 = loader.get_financial_factor("000001", 20220401, "pe_ttm")
check(pe_before_q1 == 8.5,
      f"PE on 2022-04-01 = {pe_before_q1} (expected 8.5 from 2021Q4 — 2022Q1 not yet disclosed)")

# After 2022Q1 disclosure (e.g., 2022-04-30)
pe_after_q1 = loader.get_financial_factor("000001", 20220430, "pe_ttm")
check(pe_after_q1 == 7.8,
      f"PE on 2022-04-30 = {pe_after_q1} (expected 7.8 from 2022Q1 — just disclosed on 4/28)")

# Before 2022Q2 disclosure (e.g., 2022-07-15)
pe_before_q2 = loader.get_financial_factor("000001", 20220715, "pe_ttm")
check(pe_before_q2 == 7.8,
      f"PE on 2022-07-15 = {pe_before_q2} (expected 7.8 from 2022Q1 — Q2 not yet disclosed)")

# After 2022Q2 disclosure (e.g., 2022-09-01)
pe_after_q2 = loader.get_financial_factor("000001", 20220901, "pe_ttm")
check(pe_after_q2 == 7.2,
      f"PE on 2022-09-01 = {pe_after_q2} (expected 7.2 from 2022Q2 — disclosed on 8/20)")

# After 2022Q3 disclosure
pe_after_q3 = loader.get_financial_factor("000001", 20221101, "pe_ttm")
check(pe_after_q3 == 6.9,
      f"PE on 2022-11-01 = {pe_after_q3} (expected 6.9 from 2022Q3 — disclosed on 10/25)")

# Before 2022Q4 disclosure (early 2023)
pe_before_q4 = loader.get_financial_factor("000001", 20230115, "pe_ttm")
check(pe_before_q4 == 6.9,
      f"PE on 2023-01-15 = {pe_before_q4} (expected 6.9 from 2022Q3 — Q4 not yet disclosed)")

# After 2022Q4 disclosure
pe_after_q4 = loader.get_financial_factor("000001", 20230401, "pe_ttm")
check(pe_after_q4 == 6.5,
      f"PE on 2023-04-01 = {pe_after_q4} (expected 6.5 from 2022Q4 — disclosed on 3/20)")

# ======================================================================
# Test 4: 缺失数据时返回默认值
# ======================================================================
print("\n── Test 4: Default Value on Missing Data ──")

# 无记录股票
unknown_pe = loader.get_financial_factor("999999", 20220501, "pe_ttm")
check(unknown_pe == 0.0, f"Unknown symbol returns default 0.0 (got {unknown_pe})")

unknown_pe_custom = loader.get_financial_factor("999999", 20220501, "pe_ttm", default=-999)
check(unknown_pe_custom == -999, f"Unknown symbol with custom default = -999 (got {unknown_pe_custom})")

# 股票存在但报告的披露日期在查询日期之后
early_pe = loader.get_financial_factor("000001", 20100101, "pe_ttm")
check(early_pe == 0.0, f"Date before any disclosure returns default 0.0 (got {early_pe})")

# ======================================================================
# Test 5: get_factor_series 批量查询
# ======================================================================
print("\n── Test 5: Batch Query (get_factor_series) ──")

dates = [20220101, 20220401, 20220501, 20220901, 20221101, 20230401]
pe_series = loader.get_factor_series("000001", dates, "pe_ttm")
expected = [0.0, 8.5, 7.8, 7.2, 6.9, 6.5]  # Jan1=no data, Apr1=Q4, May1=Q1, Sep1=Q2, Nov1=Q3, Apr1=Q4
# Wait, on 2022-01-01: no reports disclosed yet (2021Q4 not until 3/15)
# Actually, 2021Q4 disclosure is 3/15/2022, so on 2022-01-01, NO report is available.
# Hmm, that means the earliest available data starts on 2022-03-15.
check(np.allclose(pe_series, expected, rtol=1e-9),
      f"Factor series = {pe_series.tolist()} (expected {expected})")

# ======================================================================
# Test 6: 自动披露截止日 (无实际披露日期时使用法定截止日)
# ======================================================================
print("\n── Test 6: Auto Deadline (legal deadline fallback) ──")

loader2 = FinancialDataLoader()
# 仅提供 report_period，不提供 disclosure_date → 自动使用法定截止日
loader2.add_manual_record("000002", datetime(2022, 3, 31),
                          {"pe_ttm": 15.0})  # 无披露日期 → 截止日=2022-04-30
loader2.add_manual_record("000002", datetime(2022, 6, 30),
                          {"pe_ttm": 14.0})  # 无披露日期 → 截止日=2022-08-31

# 2022-04-15: Q1未到截止日 → 不可用
pe_auto1 = loader2.get_financial_factor("000002", 20220415, "pe_ttm")
check(pe_auto1 == 0.0,
      f"Auto-deadline: PE on 2022-04-15 = {pe_auto1} (expected 0.0 — Q1 deadline is 4/30)")

# 2022-05-01: Q1 已过截止日 → 可用
pe_auto2 = loader2.get_financial_factor("000002", 20220501, "pe_ttm")
check(pe_auto2 == 15.0,
      f"Auto-deadline: PE on 2022-05-01 = {pe_auto2} (expected 15.0 — Q1 now available)")

# 2022-07-15: Q2 未到截止日 → 仍用 Q1
pe_auto3 = loader2.get_financial_factor("000002", 20220715, "pe_ttm")
check(pe_auto3 == 15.0,
      f"Auto-deadline: PE on 2022-07-15 = {pe_auto3} (expected 15.0 — Q2 deadline is 8/31, still using Q1)")

# 2022-09-01: Q2 已过截止日 → 用 Q2
pe_auto4 = loader2.get_financial_factor("000002", 20220901, "pe_ttm")
check(pe_auto4 == 14.0,
      f"Auto-deadline: PE on 2022-09-01 = {pe_auto4} (expected 14.0 — Q2 now available)")

# ======================================================================
# Test 7: FactorEngine 集成
# ======================================================================
print("\n── Test 7: FactorEngine Integration ──")

# Create a mock data provider that returns simple kline data (matches engine's _get_kline interface)
class MockDataProvider:
    def get_kline_dataframe(self, symbols, period, count):
        dates = pd.date_range("2022-01-01", "2022-12-31", freq="B")
        # Return MultiIndex (symbol, datetime) as expected by engine
        dfs = []
        for sym in symbols:
            df = pd.DataFrame({
                "open": np.random.randn(len(dates)).cumsum() + 100,
                "high": np.random.randn(len(dates)).cumsum() + 102,
                "low": np.random.randn(len(dates)).cumsum() + 98,
                "close": np.random.randn(len(dates)).cumsum() + 100,
                "volume": np.random.randint(1000000, 10000000, len(dates)),
            }, index=dates)
            df["symbol"] = sym
            dfs.append(df.set_index("symbol", append=True))
        return pd.concat(dfs) if dfs else None

provider = MockDataProvider()
engine = FactorEngine(data_provider=provider, financial_loader=loader)

# Check that engine recognizes financial factors
factors = engine._resolve_factors(["pe_ttm", "ret_20d", "roe"])
check("pe_ttm" in factors, "FactorEngine resolves 'pe_ttm'")
check("ret_20d" in factors, "FactorEngine resolves 'ret_20d'")
check("roe" in factors, "FactorEngine resolves 'roe'")

pe_def = factors.get("pe_ttm", {})
ret_def = factors.get("ret_20d", {})
check(pe_def.get("factor_type") == "financial",
      f"pe_ttm factor_type = '{pe_def.get('factor_type')}' (expected 'financial')")
check(ret_def.get("factor_type", "kline") == "kline",
      f"ret_20d factor_type = '{ret_def.get('factor_type', 'kline')}' (expected 'kline')")

# Test compute with financial loader
print("\n  Computing factors...")
try:
    result = engine.compute(
        symbols=["000001"],
        factor_names=["pe_ttm", "ret_20d"],
        start_date="2022-03-01",
        end_date="2022-06-30",
        progress=False,
    )
    check(result is not None and not result.empty, f"Compute returned DataFrame with {len(result)} rows")
    if result is not None and not result.empty:
        check("pe_ttm" in result.columns, "'pe_ttm' column present in result")
        check("ret_20d" in result.columns, "'ret_20d' column present in result")

        # Check that PE values transition at the right date (Q1 disclosure on 4/28)
        # Before 4/28: PE should be 8.5 (from 2021Q4)
        # After 4/28: PE should be 7.8 (from 2022Q1)
        # result has MultiIndex (date, symbol) — use level 0 for date
        date_idx = result.index.get_level_values(0)
        before_mask = date_idx < "2022-04-28"
        after_mask = date_idx >= "2022-04-28"
        if before_mask.any():
            pe_before = result.loc[before_mask, "pe_ttm"].iloc[-1]
            check(abs(pe_before - 8.5) < 0.01,
                  f"PE before 4/28 = {pe_before:.1f} (expected ~8.5 from 2021Q4)")
        if after_mask.any():
            pe_after = result.loc[after_mask, "pe_ttm"].iloc[0]
            check(abs(pe_after - 7.8) < 0.01,
                  f"PE after 4/28 = {pe_after:.1f} (expected ~7.8 from 2022Q1)")
except Exception as e:
    check(False, f"FactorEngine.compute() raised: {e}")

# ======================================================================
# Test 8: 多股票 + 多因子
# ======================================================================
print("\n── Test 8: Multi-Symbol, Multi-Factor ──")

# Add another stock
loader.add_manual_record("000002", datetime(2021, 12, 31),
                         {"pe_ttm": 20.0, "pb": 2.0, "roe": 8.0}, datetime(2022, 4, 15))
loader.add_manual_record("000002", datetime(2022, 3, 31),
                         {"pe_ttm": 18.0, "pb": 1.8, "roe": 9.0}, datetime(2022, 4, 25))

# Both stocks' PE on 2022-05-01
pe_001 = loader.get_financial_factor("000001", 20220501, "pe_ttm")
pe_002 = loader.get_financial_factor("000002", 20220501, "pe_ttm")
check(pe_001 == 7.8, f"000001 PE = {pe_001} (expected 7.8)")
check(pe_002 == 18.0, f"000002 PE = {pe_002} (expected 18.0 — using Q1 on 4/25)")

# Both ROE
roe_001 = loader.get_financial_factor("000001", 20220501, "roe")
roe_002 = loader.get_financial_factor("000002", 20220501, "roe")
check(roe_001 == 13.1, f"000001 ROE = {roe_001} (expected 13.1)")
check(roe_002 == 9.0, f"000002 ROE = {roe_002} (expected 9.0)")

# ======================================================================
# Summary
# ======================================================================
print(f"\n{'=' * 70}")
print(f"  Results: {PASS} passed, {FAIL} failed, {PASS + FAIL} total")
if FAIL == 0:
    print(f"  ✓ All tests passed! Financial factor integration is correct.")
else:
    print(f"  ✗ {FAIL} test(s) FAILED — review above.")
print(f"{'=' * 70}")
