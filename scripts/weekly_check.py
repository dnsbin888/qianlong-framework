"""每周自动检查 (蓝图 v5.0 Phase 3)

串联所有 Phase 3-5 基础设施:
  DataManager → 数据源健康
  factor_health → 因子健康扫描
  factor_pipeline → 因子发现管线
  strategy_recommender → 策略推荐更新
  dingtalk_alerts → 周报推送

建议: 每周六 09:00 自动运行 (挂到系统定时器或 EventBus timer)
用法: python scripts/weekly_check.py [--push] [--pipeline]
"""
import sys
import os
import json
import logging
from datetime import datetime

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("weekly_check")


def check_data_sources() -> dict:
    """① 数据源健康检查"""
    logger.info("=" * 50)
    logger.info("① 数据源健康检查")
    try:
        from quant_framework.data.data_manager import DataManager
        dm = DataManager()
        status = dm.status()
        issues = []
        for name, s in status["sources"].items():
            alive = s.get("alive", False)
            lag = s.get("lag", 999)
            state = "✅" if alive else ("⚠️" if name == "price_cache" else "❌")
            logger.info(f"  {state} {name}: alive={alive}, lag={lag}s")
            if not alive and name != "price_cache":
                issues.append(f"数据源{name}不可用")
        return {"status": status, "issues": issues}
    except Exception as e:
        logger.error(f"数据源检查失败: {e}")
        return {"error": str(e), "issues": ["数据源检查异常"]}


def check_factor_health() -> dict:
    """② 因子健康扫描"""
    logger.info("=" * 50)
    logger.info("② 因子健康扫描")
    try:
        from factor_health import run_health_check
        report = run_health_check()
        summary = report.get("summary", {})
        logger.info(f"  健康: {summary.get('healthy', 0)}, 观察: {summary.get('watch', 0)}, 危险: {summary.get('danger', 0)}, 退役: {summary.get('retiring', 0)}")

        issues = []
        for f in report.get("factors", []):
            if f["status"] in ("danger", "retiring"):
                logger.warning(f"  ⚠️ {f['name']}: health={f['health']}, status={f['status']}")
                issues.append(f"因子{f['name']}健康度{f['health']}→{f['status']}")
        return {"report": report, "issues": issues}
    except Exception as e:
        logger.error(f"因子健康检查失败: {e}")
        return {"error": str(e), "issues": ["因子健康检查异常"]}


def check_strategy_circuit() -> dict:
    """③ 策略熔断检查"""
    logger.info("=" * 50)
    logger.info("③ 策略熔断检查")
    try:
        from factor_health import check_strategy_circuit_breaker
        actions = check_strategy_circuit_breaker()
        if actions:
            logger.warning(f"  触发{len(actions)}项熔断:")
            for a in actions:
                logger.warning(f"    {a['strategy']}: {a['action']} — {a['reason']}")
        else:
            logger.info("  无熔断触发")
        return {"actions": actions, "issues": [f"策略{a['strategy']}熔断:{a['action']}" for a in actions]}
    except Exception as e:
        logger.error(f"策略熔断检查失败: {e}")
        return {"error": str(e), "issues": []}


def check_strategy_recommendation() -> dict:
    """④ 策略推荐更新"""
    logger.info("=" * 50)
    logger.info("④ 策略推荐更新")
    try:
        from strategy_recommender import recommend
        rec = recommend()
        logger.info(f"  市场: {rec['market_state']}")
        logger.info(f"  主推: {rec['primary_strategies']}")
        logger.info(f"  仓位: {rec['max_positions']}只, 信号≥Lv{rec['signal_level']}")
        return {"recommendation": rec, "issues": []}
    except Exception as e:
        logger.error(f"策略推荐失败: {e}")
        return {"error": str(e), "issues": []}


def run_weekly(push: bool = False, run_pipeline: bool = False) -> dict:
    """运行完整周检。"""
    logger.info("=" * 60)
    logger.info(f"潜龙系统周检 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    logger.info("=" * 60)

    results = {}
    all_issues = []

    # ① 数据源
    r = check_data_sources()
    results["data_sources"] = r
    all_issues.extend(r.get("issues", []))

    # ② 因子健康
    r = check_factor_health()
    results["factor_health"] = r
    all_issues.extend(r.get("issues", []))

    # ③ 策略熔断
    r = check_strategy_circuit()
    results["strategy_circuit"] = r
    all_issues.extend(r.get("issues", []))

    # ④ 策略推荐
    r = check_strategy_recommendation()
    results["strategy_recommendation"] = r

    # ⑤ 因子发现管线 (可选, 较慢)
    if run_pipeline:
        logger.info("=" * 50)
        logger.info("⑤ 因子发现管线")
        try:
            from factor_pipeline import run
            p_result = run(sample=500, days=60, auto=False)
            results["factor_pipeline"] = p_result
            registered = p_result.get("stages", {}).get("register", {}).get("registered", 0)
            logger.info(f"  发现{registered}个新候选因子")
        except Exception as e:
            logger.error(f"因子管线失败: {e}")
            results["factor_pipeline"] = {"error": str(e)}

    # ── 汇总 ──
    logger.info("=" * 60)
    if all_issues:
        logger.warning(f"⚠️ 发现 {len(all_issues)} 个问题:")
        for i in all_issues:
            logger.warning(f"  - {i}")
    else:
        logger.info("✅ 系统运行正常")

    # 保存报告
    report_path = r"D:\quant_framework\weekly_report.json"
    report = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M:%S"),
        "issues": all_issues,
        "results": results,
    }
    with open(report_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"报告已保存: {report_path}")

    # 钉钉推送
    if push and all_issues:
        try:
            from dingtalk_alerts import send_alert
            summary = "\n".join(f"• {i}" for i in all_issues[:5])
            send_alert("📊 潜龙周检报告", f"发现{len(all_issues)}个问题:\n{summary}", "info")
            logger.info("钉钉推送完成")
        except Exception as e:
            logger.error(f"钉钉推送失败: {e}")

    return report


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="潜龙系统每周自动检查")
    p.add_argument("--push", action="store_true", help="推送周报到钉钉")
    p.add_argument("--pipeline", action="store_true", help="运行因子发现管线 (较慢)")
    args = p.parse_args()
    run_weekly(push=args.push, run_pipeline=args.pipeline)
