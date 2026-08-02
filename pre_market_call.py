"""游资竞价弱转强扫描 v1.0 — 9:24 集合竞价确认
对标游资: 9:20后不可撤单, 量是真金白银
用法: python pre_market_call.py (9:24 自动执行)
"""
import sys, os, json, time
from datetime import datetime

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

PLAN_PATH = r"D:\quant_web\data\auto_trade_plan.json"


def get_auction_data(symbols):
    """竞价数据多点密集采样 (防砸盘, 对标私募)

    9:23:00→9:24:55, 每5秒采样一次 = ~8个数据点
    防砸盘: 价格单调不降 + 无量能突增 + 最后三样本稳定
    """
    result = {}
    try:
        from xtquant import xtdata
        # 批量转换代码格式
        qmt_map = {}
        for sym in symbols:
            if sym.startswith('sh'): qmt_map[sym] = sym[2:] + '.SH'
            elif sym.startswith('sz'): qmt_map[sym] = sym[2:] + '.SZ'
            elif sym.startswith('bj'): qmt_map[sym] = sym[2:] + '.BJ'

        # 密集采样: 每次拉所有票的竞价数据
        qmt_list = list(qmt_map.values())
        sym_samples = {s: [] for s in symbols}  # sym → [(price, vol, time)]

        for attempt in range(8):  # 8次 × 5秒 = 40秒覆盖
            for qmt in qmt_list:
                try:
                    mk = xtdata.get_market_data(
                        ['lastPrice', 'volume', 'lastClose'],
                        stock_list=[qmt], period='1d', count=1)
                    if mk and qmt in mk and 'lastPrice' in mk[qmt]:
                        p = float(mk[qmt]['lastPrice'][-1])
                        v = float(mk[qmt]['volume'][-1]) if 'volume' in mk[qmt] else 0
                        yc = float(mk[qmt]['lastClose'][-1]) if 'lastClose' in mk[qmt] else 0
                        if p > 0:
                            for s, q in qmt_map.items():
                                if q == qmt:
                                    sym_samples[s].append((p, v, time.time(), yc))
                                    break
                except Exception: pass
            if attempt < 7: time.sleep(5)  # 5秒后再采样

        # 逐个验证: 8采样点 × 趋势 + 稳定性
        for sym, samples in sym_samples.items():
            if len(samples) < 4: continue  # 至少要4个有效样本
            prices = [s[0] for s in samples]
            volumes = [s[1] for s in samples]

            # 1. 价格单调不降 (排除砸盘)
            if prices[-1] < prices[0]:  # 最终价比初始价还低
                continue
            # 2. 无最后阶段突降
            if len(prices) >= 6:
                early_avg = sum(prices[:3]) / 3
                late_avg = sum(prices[-3:]) / 3
                if late_avg < early_avg * 0.995:  # 后段比前段低0.5%
                    continue
            # 3. 稳定: 最后3个的标准差 < 均值的0.3%
            last3 = prices[-3:] if len(prices) >= 3 else prices
            avg3 = sum(last3) / len(last3)
            std3 = (sum((p-avg3)**2 for p in last3) / len(last3))**0.5
            if std3 > avg3 * 0.003:  # 波动>0.3%
                continue
            # 4. 无量能突增 (大单砸盘)
            if len(volumes) >= 6:
                early_v = sum(volumes[:3]) / 3
                late_v = sum(volumes[-3:]) / 3
                if late_v > early_v * 1.5 and prices[-1] < prices[-2]:  # 尾段量突增+价跌
                    continue

            # 采样通过 → 用最后一刻数据
            last = samples[-1]
            result[sym] = {'price': last[0], 'vol': int(last[1]),
                          'yest_close': last[3] if len(last) > 3 else 0,
                          'samples': len(samples)}
    except Exception as e:
        print(f"[CallAuction] xtdata不可用: {e}")
    return result


def scan_call_auction():
    """扫描弱转强候选的竞价确认"""
    if not os.path.exists(PLAN_PATH):
        print("[CallAuction] plan不存在")
        return 0

    with open(PLAN_PATH, encoding='utf-8') as f:
        plan = json.load(f)

    # 找弱转强候选 (有time_window且未启用)
    candidates = {}
    for sym, cfg in plan.get("stocks", {}).items():
        if isinstance(cfg, dict) and cfg.get("time_window") and not cfg.get("enabled"):
            candidates[sym] = cfg

    if not candidates:
        print("[CallAuction] 无弱转强候选")
        return 0

    print(f"[CallAuction] 扫描 {len(candidates)}只候选...")
    data = get_auction_data(list(candidates.keys()))

    confirmed = 0
    for sym, cfg in candidates.items():
        ad = data.get(sym)
        if not ad or ad['price'] <= 0:
            continue

        yest_vol = cfg.get("yesterday_volume", 0)
        vol_ratio = ad['vol'] / max(yest_vol, 1) if yest_vol > 0 else 0
        chg = (ad['price'] - ad['yest_close']) / max(ad['yest_close'], 0.01)

        # 竞价确认条件: 量占昨>3% + 高开>0.5% (游资标准)
        if vol_ratio > 0.03 and chg > 0.005:
            cfg["enabled"] = True
            cfg["auction_price"] = round(ad['price'], 2)
            cfg["auto_reason"] = f"竞价确认 量{vol_ratio*100:.1f}% 高开{chg*100:.1f}%"
            cfg["signal_types"] = ["竞价弱转强", "竞价抢筹", "盘中突破"]
            cfg["max_position_pct"] = min(cfg.get("max_position_pct", 2) + 0.5, 4)
            confirmed += 1
            print(f"  ✅ {sym} 竞价确认 量{vol_ratio*100:.1f}% 高开{chg*100:.1f}%")

    if confirmed:
        plan["global_limits"]["_call_auction_time"] = datetime.now().strftime("%H:%M:%S")
        plan["global_limits"]["_call_auction_confirmed"] = confirmed
        tmp = PLAN_PATH + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PLAN_PATH)
        print(f"[CallAuction] ✅ 竞价确认 {confirmed}/{len(candidates)}只, 已启用")
    else:
        print(f"[CallAuction] 0只通过竞价确认")
    return confirmed


if __name__ == "__main__":
    print(f"[CallAuction] {datetime.now().strftime('%H:%M:%S')} 开始...")
    scan_call_auction()
    print("[CallAuction] 完成")


def scan_position_risk():
    """竞价卖出扫描 — 游资严选: 昨大涨+今竞价跳水/无人接盘 (P0)

    检查模拟盘持仓中昨涨>5%的票, 竞价不及预期 → 挂跌停排队出货
    """
    positions = {}
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from paper_engine import paper
        for sym, pos in paper.positions.items():
            if pos.get("qty", 0) > 0:
                positions[sym] = pos
    except Exception as e:
        print(f"[CallAuction] 持仓读取失败: {e}")
        return 0

    if not positions:
        print("[CallAuction] 无持仓, 跳过卖出扫描")
        return 0

    # 获取竞价数据
    syms = list(positions.keys())
    data = get_auction_data(syms)

    sold = 0
    if not os.path.exists(PLAN_PATH):
        return 0

    with open(PLAN_PATH, encoding='utf-8') as f:
        plan = json.load(f)

    for sym, pos in positions.items():
        ad = data.get(sym)
        if not ad or ad['price'] <= 0:
            continue

        yest_close = ad.get('yest_close', 0)
        if yest_close <= 0: continue

        yest_chg = (yest_close - pos.get('avg_cost', yest_close)) / max(pos.get('avg_cost', yest_close), 0.01)
        auction_chg = (ad['price'] - yest_close) / max(yest_close, 0.01)

        # 只检查昨涨>5%的票 (大涨后有回落风险)
        if yest_chg < 0.05:
            continue

        # 游资卖出四场景 (2026-07-21: +LHB机构出货)
        vol_ratio = ad['vol'] / max(plan.get("stocks", {}).get(sym, {}).get("yesterday_volume", 1), 1)
        reason = None

        # 优先检查LHB机构净卖出(优先级最高: 机构出货信号更强)
        _lhb_sell = 0
        try:
            _st = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
            for _sr in _st:
                if _sr.get("symbol") == sym:
                    _lhb_sell = _sr.get("lhb_sell", 0) or 0
                    break
        except: pass

        if _lhb_sell and auction_chg < 0.005:
            reason = f"LHB机构净卖出{_lhb_sell:.0f}万+竞价偏弱 → 减仓"
        elif auction_chg < -0.02:
            reason = f"竞价跳水{auction_chg*100:.1f}% → 清仓"
        elif vol_ratio < 0.03 and yest_chg > 0.08:
            reason = f"昨涨停+竞价量不足{vol_ratio*100:.1f}% → 减半"
        elif ad['price'] >= yest_close * 1.095 and ad['vol'] < yest_close * 0.02:
            reason = f"一字涨停+量极低 → 炸板前兆"

        if reason:
            # 写入卖出信号
            sym_cfg = plan["stocks"].get(sym, {})
            sym_cfg["sell_signal"] = True
            sym_cfg["sell_reason"] = reason
            sym_cfg["sell_qty"] = pos.get("qty", 0) // 2 if "减半" in reason else pos.get("qty", 0)
            sym_cfg["auction_price"] = round(ad['price'], 2)
            plan["stocks"][sym] = sym_cfg
            sold += 1
            print(f"  🔴 {sym} {reason}")

    if sold:
        plan["global_limits"]["_call_auction_sells"] = sold
        tmp = PLAN_PATH + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PLAN_PATH)
        print(f"[CallAuction] 🔴 卖出信号 {sold}只")
    else:
        print("[CallAuction] 持仓安全, 无卖出信号")
    return sold


if __name__ == "__main__":
    print(f"[CallAuction] {datetime.now().strftime('%H:%M:%S')} 开始...")
    scan_call_auction()
    scan_position_risk()
    print("[CallAuction] 完成")
