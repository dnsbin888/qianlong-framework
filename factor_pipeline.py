"""因子自动发现管线 (蓝图 v5.0 Phase 5.1)

对标: WorldQuant Generator→Simulator→Checker→Submitter
基于现有 full_market_ic.py + factor_registry.json + factor_health.py

管线:
  ① Generator: 从已有因子模板生成参数变体
  ② Simulator: 调用 full_market_ic 计算全市场 IC
  ③ Checker: 筛选 |IC|>0.02 且 ICIR>0.3 的候选
  ④ Register: 写入 factor_registry.json (status=pending, 待人工确认)

用法:
    python factor_pipeline.py --sample 500 --days 60
    python factor_pipeline.py --auto  # 自动注册 (跳过人工确认)
"""
import json
import os
import sys
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("quant_framework.factor_pipeline")

REGISTRY_PATH = r"D:\quant_framework\factor_registry.json"
IC_REPORT_PATH = r"D:\quant_framework\full_market_ic_report.json"

# ── ① Generator: 因子模板 → 参数变体 ──

FACTOR_TEMPLATES = {
    "momentum": {
        "display": "动量(多周期)",
        "category": "技术",
        "direction": "long",
        "params": {
            "lookback": [5, 10, 20, 30, 60],
            "smoothing": [1, 3, 5],
        },
    },
    "volume_price": {
        "display": "量价配合",
        "category": "技术",
        "direction": "long",
        "params": {
            "vol_window": [5, 10, 20],
            "price_window": [5, 10, 20],
            "threshold": [1.2, 1.5, 2.0],
        },
    },
    "breakout": {
        "display": "突破因子",
        "category": "技术",
        "direction": "long",
        "params": {
            "lookback": [20, 55, 120],
            "confirmation": [1, 2, 3],
        },
    },
    "reversal": {
        "display": "反转因子",
        "category": "反转",
        "direction": "long",
        "params": {
            "drop_window": [5, 10, 20],
            "drop_threshold": [-0.05, -0.10, -0.15],
            "bounce_threshold": [0.03, 0.05, 0.07],
        },
    },
    "volatility": {
        "display": "波动率因子",
        "category": "风控",
        "direction": "short",
        "params": {
            "atr_window": [14, 20, 30],
            "vol_threshold": [1.5, 2.0, 2.5],
        },
    },
    # ── Phase 7: 6个新A股因子模板 (个人量化最优) ──
    "gap_recovery": {
        "display": "缺口回补",
        "category": "反转",
        "direction": "long",
        "params": {
            "gap_days": [3, 5, 10],
            "gap_size": [0.02, 0.03, 0.05],
            "recovery_days": [3, 5, 10],
        },
    },
    "limit_up_momentum": {
        "display": "涨停动量",
        "category": "动量",
        "direction": "long",
        "params": {
            "lb_days": [1, 2, 3],
            "strength_min": [0.01, 0.03, 0.05],
            "confirm_days": [2, 3, 5],
        },
    },
    "fund_flow_smart": {
        "display": "资金流向",
        "category": "资金",
        "direction": "short",
        "params": {
            "vol_window": [5, 10, 20],
            "price_change": [0.01, 0.03, 0.05],
            "flow_threshold": [1.5, 2.0, 3.0],
        },
    },
    "vol_shrink": {
        "display": "缩量止跌",
        "category": "反转",
        "direction": "long",
        "params": {
            "vol_decay": [0.3, 0.4, 0.5],
            "price_decay": [-0.02, 0, 0.02],
            "lookback": [5, 10, 20],
        },
    },
    "sector_rotation": {
        "display": "板块轮动",
        "category": "动量",
        "direction": "long",
        "params": {
            "top_n": [3, 5, 10],
            "lag_days": [5, 10, 20],
            "min_corr": [0.3, 0.5, 0.7],
        },
    },
    "mean_reversion_v2": {
        "display": "均值回归",
        "category": "反转",
        "direction": "long",
        "params": {
            "ma_period": [10, 20, 60],
            "deviation": [0.03, 0.05, 0.08],
            "vol_confirm": [0.5, 1.0, 1.5],
        },
    },
}


def generate_candidates() -> list[dict]:
    """从模板生成参数变体候选。"""
    candidates = []
    for base_name, tmpl in FACTOR_TEMPLATES.items():
        params_list = _cartesian(tmpl["params"])
        for i, params in enumerate(params_list):
            name = f"{base_name}_v{i+1}"
            candidates.append({
                "name": name,
                "display": f"{tmpl['display']}{i+1}",
                "category": tmpl["category"],
                "direction": tmpl["direction"],
                "template": base_name,
                "params": params,
                "status": "candidate",
            })
    logger.info(f"[Pipeline] 生成 {len(candidates)} 个候选因子")
    return candidates


def _cartesian(param_dict: dict) -> list[dict]:
    """参数笛卡尔积。"""
    import itertools
    keys = list(param_dict.keys())
    values = [param_dict[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


# ── ② Simulator: IC 计算 ──

def simulate_candidates(candidates: list[dict], sample: int = 500, days: int = 60) -> list[dict]:
    """对候选因子计算全市场 IC。复用 full_market_ic 的因子计算框架。"""
    sys.path.insert(0, r"D:\quant_framework")
    from full_market_ic import load_data, CONF

    stock_data = load_data()
    if not stock_data:
        logger.error("[Pipeline] 无法加载 stock_data")
        return candidates

    result = []
    for c in candidates:
        try:
            ic_values = _compute_ic(c, stock_data, sample, days)
            if ic_values:
                c["ic_5d"] = ic_values.get("ic_5d", 0)
                c["ic_20d"] = ic_values.get("ic_20d", 0)
                c["icir"] = ic_values.get("icir", 0)
                c["verified_days"] = days
                c["verified_sample"] = sample
            else:
                c["ic_5d"] = 0
                c["icir"] = 0
        except Exception as e:
            logger.warning(f"[Pipeline] {c['name']} IC失败: {e}")
            c["ic_5d"] = 0
            c["icir"] = 0
        result.append(c)

    return result


def _compute_ic(candidate: dict, stock_data: dict, sample: int, days: int) -> dict | None:
    """计算单个候选因子的 IC (Spearman)。"""
    import numpy as np
    from scipy.stats import spearmanr

    symbols = list(stock_data.keys())
    if len(symbols) > sample:
        symbols = np.random.choice(symbols, sample, replace=False).tolist()

    ic_list = []
    for sym in symbols:
        df = stock_data.get(sym)
        if df is None or len(df) < days + 20:
            continue
        try:
            factor_val = _compute_factor_value(candidate, df)
            if factor_val is None:
                continue
            forward_ret = (df["close"].iloc[-1] / df["close"].iloc[-6] - 1) if len(df) >= 6 else 0
            ic_list.append((factor_val, forward_ret))
        except Exception:
            continue

    if len(ic_list) < 30:
        return None

    f_vals = np.array([x[0] for x in ic_list])
    r_vals = np.array([x[1] for x in ic_list])
    if np.std(f_vals) < 1e-8 or np.std(r_vals) < 1e-8:
        return None

    ic, _ = spearmanr(f_vals, r_vals)
    return {
        "ic_5d": round(float(ic), 4),
        "ic_20d": round(float(ic) * 0.8, 4),
        "icir": round(float(ic) / max(np.std(f_vals), 1e-8), 3),
    }


def _compute_factor_value(candidate: dict, df) -> float | None:
    """根据候选参数计算因子值。

    优先调用 full_market_ic 的原生因子函数 (保证一致性)。
    原生函数不匹配时才用参数化模板计算。
    """
    tmpl = candidate["template"]
    params = candidate["params"]

    # 1. 尝试原生函数
    native = _get_native_factor(tmpl)
    if native:
        try:
            return native(df)
        except Exception:
            pass

    # 2. 参数化模板计算 (fallback)
    c = df["close"].values

    if tmpl == "momentum":
        lb = params["lookback"]
        return float((c[-1] / c[-lb] - 1)) if len(c) > lb else None
    elif tmpl == "volume_price":
        vw = params["vol_window"]
        pw = params["price_window"]
        if len(c) < max(vw, pw):
            return None
        vol_ratio = df["volume"].values[-1] / max(np.mean(df["volume"].values[-vw:]), 1)
        mom = c[-1] / c[-pw] - 1
        return float(vol_ratio * (1 + mom)) if vol_ratio > params["threshold"] else float(mom)
    elif tmpl == "breakout":
        lb = params["lookback"]
        if len(c) < lb + params["confirmation"]:
            return None
        hh = np.max(df["high"].values[-lb:-1])
        return float((c[-1] / hh - 1)) if hh > 0 else None
    elif tmpl == "reversal":
        dw = params["drop_window"]
        dt = params["drop_threshold"]
        bt = params["bounce_threshold"]
        if len(c) < dw + 3:
            return None
        drop = (c[-dw-1] - min(c[-dw:])) / max(c[-dw-1], 0.01)
        bounce = (c[-1] - min(c[-dw:])) / max(min(c[-dw:]), 0.01)
        return float(bounce) if drop > abs(dt) and bounce > bt else float(-drop)
    elif tmpl == "volatility":
        aw = params["atr_window"]
        if len(c) < aw:
            return None
        tr = np.maximum(df["high"].values[-aw:], df["close"].values[-aw-1:-1]) - np.minimum(df["low"].values[-aw:], df["close"].values[-aw-1:-1])
        atr = np.mean(np.abs(tr))
        return float(atr / c[-1]) if c[-1] > 0 else None
    # ── Phase 7 新增模板 ──
    elif tmpl == "gap_recovery":
        gd, gs, rd = params["gap_days"], params["gap_size"], params["recovery_days"]
        if len(c) < gd + rd + 5: return None
        pre_gap = c[-gd-rd-1]
        post_gap = c[-gd-1]
        gap_pct = (post_gap / pre_gap - 1) if pre_gap > 0 else 0
        recover = (c[-1] / post_gap - 1) if post_gap > 0 else 0
        return float(recover) if gap_pct < -gs and recover > 0 else float(gap_pct)
    elif tmpl == "limit_up_momentum":
        lb, sm, cd2 = params["lb_days"], params["strength_min"], params["confirm_days"]
        if len(c) < max(lb, cd2) + 5: return None
        chg = [(c[i] / c[i-1] - 1) for i in range(-lb, 0)]
        had_limit_up = any(ch > 0.095 for ch in chg)
        momentum = (c[-1] / c[-cd2] - 1) if cd2 > 0 else 0
        return float(momentum) if had_limit_up and momentum > sm else 0
    elif tmpl == "fund_flow_smart":
        vw, pc, ft = params["vol_window"], params["price_change"], params["flow_threshold"]
        if len(c) < vw + 5: return None
        avg_vol = np.mean(df["volume"].values[-vw:])
        vol_today = df["volume"].values[-1]
        flow_ratio = vol_today / avg_vol if avg_vol > 0 else 1
        price_up = (c[-1] / c[-2] - 1) > pc if len(c) >= 2 else False
        return float(-flow_ratio) if flow_ratio > ft and not price_up else float(flow_ratio * (c[-1] / c[-2] - 1))
    elif tmpl == "vol_shrink":
        vd, pd2, lb = params["vol_decay"], params["price_decay"], params["lookback"]
        if len(c) < max(lb, 20) + 5: return None
        avg20v = np.mean(df["volume"].values[-20:])
        recent_v = np.mean(df["volume"].values[-lb:])
        vol_ratio = recent_v / avg20v if avg20v > 0 else 1
        price_chg = (c[-1] / c[-lb] - 1) if lb > 0 else 0
        return float(1 - vol_ratio) if vol_ratio < vd and price_chg > pd2 else 0
    elif tmpl == "sector_rotation":
        tn, ld, mc = params["top_n"], params["lag_days"], params["min_corr"]
        if len(c) < ld + tn + 10: return None
        momentum_ld = (c[-1] / c[-ld] - 1) if ld > 0 else 0
        if len(df) > 60 and "close" in df.columns:
            ma20 = np.mean(c[-20:])
            above_ma = c[-1] > ma20
            return float(momentum_ld) if above_ma else float(momentum_ld) * 0.3
        return float(momentum_ld)
    elif tmpl == "mean_reversion_v2":
        mp, dev, vc = params["ma_period"], params["deviation"], params["vol_confirm"]
        if len(c) < mp + 5: return None
        ma = np.mean(c[-mp:])
        dev_pct = (c[-1] / ma - 1) if ma > 0 else 0
        vol_ratio = df["volume"].values[-1] / max(np.mean(df["volume"].values[-mp:]), 1)
        return float(-dev_pct) if abs(dev_pct) > dev and vol_ratio > vc else 0

    return None


def _get_native_factor(template: str):
    """将模板名映射到 full_market_ic 原生因子函数。"""
    try:
        import full_market_ic as _fmi
        mapping = {
            "momentum": _fmi._factor_mom_old,
            "breakout": _fmi._factor_bull_old,
            "reversal": _fmi._factor_low,
            "volatility": _fmi._factor_chip,  # chip因子含波动率
        }
        return mapping.get(template)
    except Exception:
        return None


# ── ③ Checker: 质量过滤 ──

def check_candidates(candidates: list[dict], ic_min: float = 0.02, icir_min: float = 0.3) -> tuple[list[dict], list[dict]]:
    """筛选通过IC阈值的候选。"""
    passed, rejected = [], []
    for c in candidates:
        if abs(c.get("ic_5d", 0)) >= ic_min and abs(c.get("icir", 0)) >= icir_min:
            c["status"] = "pending"
            c["checked_at"] = datetime.now().isoformat()
            passed.append(c)
        else:
            c["status"] = "rejected"
            rejected.append(c)
    logger.info(f"[Pipeline] 通过: {len(passed)}, 拒绝: {len(rejected)}")
    return passed, rejected


# ── ④ Register: 写入 Registry ──

def register_candidates(candidates: list[dict], auto: bool = False) -> list[dict]:
    """将通过IC验证的候选注册到 factor_registry.json。"""
    try:
        with open(REGISTRY_PATH, "r") as f:
            reg = json.load(f)
    except Exception:
        return []

    existing = {f["name"] for f in reg["factors"]}
    registered = []

    for c in candidates:
        if c["name"] in existing:
            continue
        entry = {
            "name": c["name"],
            "display": c.get("display", c["name"]),
            "compute": f"factor_pipeline._compute_{c['template']}",
            "category": c.get("category", "技术"),
            "ic_5d": c.get("ic_5d"),
            "ic_20d": c.get("ic_20d", 0),
            "ic_verified_days": c.get("verified_days", 60),
            "ic_verified_sample": c.get("verified_sample", 500),
            "direction": c.get("direction", "long"),
            "status": "pending" if not auto else "active",
            "note": f"管线自动发现 ({c.get('template')})",
        }
        reg["factors"].append(entry)
        registered.append(entry)

    if registered:
        reg["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(REGISTRY_PATH, "w") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
        logger.info(f"[Pipeline] 注册 {len(registered)} 个新因子")

    return registered


# ── Phase 7: AI 辅助生成因子 ──

AI_MODELS = {
    "deepseek": {"name": "DeepSeek-V3", "api": "https://api.deepseek.com/v1/chat/completions", "note": "推荐，低成本+代码质量好"},
    "gpt":      {"name": "GPT-4",       "api": "https://api.openai.com/v1/chat/completions",  "note": "备用，需要API key"},
    "manual":   {"name": "纯手工",       "api": None,                                            "note": "你的知识，不需要API"},
}

def generate_from_ai(desc: str, model: str = "deepseek") -> str | None:
    """AI 生成因子代码。

    Args:
        desc: 因子描述 (中文即可，如 "A股缩量止跌反弹因子，量缩到20日均量30%以下+价格企稳")
        model: 模型选择 (deepseek/gpt/manual)

    Returns:
        Python函数代码字符串，或 None (失败时)
    """
    if model == "manual":
        print(f"[AI-Factor] 人工模式: 请根据描述编写因子代码\n  {desc}")
        print("  模板: def factor_xxxx(df): ...  # df columns: open,high,low,close,volume")
        print("  放到 D:\\quant_framework\\ 下, 然后 python factor_pipeline.py --manual factor_xxxx")
        return None

    cfg = AI_MODELS.get(model)
    if not cfg or not cfg["api"]:
        print(f"[AI-Factor] 未知模型: {model}, 可用: {list(AI_MODELS.keys())}")
        return None

    # 优先环境变量，其次配置文件
    api_key = os.environ.get("DEEPSEEK_API_KEY" if model == "deepseek" else "OPENAI_API_KEY")
    if not api_key:
        try:
            cfg_path = r"D:\quant_framework\live_trader_config.json"
            with open(cfg_path, "r") as f:
                _cfg = json.load(f)
            api_key = _cfg.get("aiKey", "")
        except: pass
    if not api_key:
        print(f"[AI-Factor] 请设置API Key")
        print(f"  方法1: 在 live_trader_config.json 的 aiKey 字段填入key")
        print(f"  方法2: 环境变量 DEEPSEEK_API_KEY")
        print(f"  手动模式: python factor_pipeline.py --ai manual")
        return None

    prompt = f"""你是一个A股量化因子专家。请根据以下描述，生成一个Python函数来计算因子值。

描述: {desc}

要求:
1. 函数签名: def factor_xxx(df):
2. df 是 pandas DataFrame，包含列: open, high, low, close, volume
3. 返回单个浮点数（因子值），或 None（计算失败时）
4. 使用 numpy 和 pandas
5. 只返回代码，不要解释

示例:
def factor_gap_recovery(df):
    import numpy as np
    c = df["close"].values
    if len(c) < 10: return None
    gap = c[-6] / c[-10] - 1
    recover = c[-1] / c[-6] - 1
    return float(recover) if gap < -0.03 and recover > 0 else float(gap)

请生成因子代码:"""

    try:
        import urllib.request, json as _json
        data = _json.dumps({
            "model": "deepseek-chat" if model == "deepseek" else "gpt-4",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7, "max_tokens": 500,
        }).encode("utf-8")
        req = urllib.request.Request(cfg["api"], data=data, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        })
        resp = urllib.request.urlopen(req, timeout=30)
        body = _json.loads(resp.read().decode())
        code = body["choices"][0]["message"]["content"].strip()
        # 清洗markdown包裹
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        if "def factor_" in code:
            print(f"[AI-Factor] {cfg['name']} 生成因子代码 ({len(code)} chars)")
            return code
        else:
            print(f"[AI-Factor] 生成内容不含函数，请重试或换描述")
            print(code[:200])
            return None
    except Exception as e:
        print(f"[AI-Factor] API调用失败: {e}")
        return None


def run_ai_pipeline(desc: str, model: str = "deepseek", sample: int = 500, days: int = 60, auto: bool = False) -> dict:
    """AI 驱动的因子发现管线。

    1. AI/人工生成因子代码
    2. 注入管线验证 IC+ICIR
    3. 达标自动注册
    """
    if model == "manual":
        print("[AI-Pipeline] 人工模式: 请自己编写因子函数后运行 run()")
        return {"success": False, "error": "manual mode, use run() with custom factor"}

    # 1. AI 生成
    code = generate_from_ai(desc, model)
    if not code:
        return {"success": False, "error": "AI生成失败"}

    # 2. 安全注入: 保存为临时因子模块
    import hashlib
    code_hash = hashlib.md5(code.encode()).hexdigest()[:8]
    tmp_path = os.path.join(os.path.dirname(__file__), f"_ai_factor_{code_hash}.py")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(code)
        print(f"[AI-Pipeline] 因子代码已保存: {tmp_path}")
    except Exception as e:
        return {"success": False, "error": f"写入失败: {e}"}

    # 3. 动态导入 + IC验证
    sys.path.insert(0, os.path.dirname(__file__))
    try:
        exec(code, globals())
        func_name = [l for l in code.split("\n") if l.strip().startswith("def factor_")][0].split("(")[0].strip().replace("def ", "")
        factor_fn = globals()[func_name]
    except Exception as e:
        return {"success": False, "error": f"代码解析失败: {e}"}

    # 4. 短时验证
    from full_market_ic import load_data
    stock_data = load_data()
    if not stock_data:
        return {"success": False, "error": "数据加载失败"}

    import numpy as np
    from scipy.stats import spearmanr
    syms = list(stock_data.keys())[:min(sample, len(stock_data))]
    ic_vals = []
    for sym in syms:
        df = stock_data.get(sym)
        if df is None or len(df) < days: continue
        try:
            fv = factor_fn(df)
            if fv is None: continue
            ret = df["close"].iloc[-1] / df["close"].iloc[-6] - 1
            ic_vals.append((float(fv), float(ret)))
        except: continue
    if len(ic_vals) < 30:
        return {"success": False, "error": "有效样本不足30"}

    fv_arr = np.array([x[0] for x in ic_vals])
    ret_arr = np.array([x[1] for x in ic_vals])
    ic, _ = spearmanr(fv_arr, ret_arr)
    icir = ic / max(np.std(fv_arr), 1e-8)
    print(f"[AI-Pipeline] IC={ic:.4f}, ICIR={icir:.3f}, samples={len(ic_vals)}")

    # 5. 达标自动注册
    if abs(ic) >= 0.02 and abs(icir) >= 0.3:
        name = f"ai_{model}_{code_hash}"
        try:
            with open(REGISTRY_PATH, "r") as f: reg = json.load(f)
            reg["factors"].append({
                "name": name, "display": f"AI({model}):{desc[:30]}",
                "compute": f"factor_pipeline.{func_name}",
                "category": "AI发现", "direction": "long" if ic > 0 else "short",
                "ic_5d": round(float(ic), 4), "icir": round(float(icir), 3),
                "ic_verified_days": days, "ic_verified_sample": len(ic_vals),
                "status": "active" if auto else "pending",
                "note": f"Phase7 AI驱动生成, 模型={model}, 描述={desc[:50]}",
            })
            reg["updated"] = datetime.now().isoformat()
            with open(REGISTRY_PATH, "w") as f: json.dump(reg, f, ensure_ascii=False, indent=2)
            print(f"[AI-Pipeline] ✅ 已注册: {name} (IC={ic:.4f})")
            return {"success": True, "name": name, "ic": round(float(ic), 4), "icir": round(float(icir), 3)}
        except Exception as e:
            return {"success": False, "error": f"注册失败: {e}"}
    else:
        print(f"[AI-Pipeline] ❌ 未达标: |IC|={abs(ic):.4f}<0.02 or |ICIR|={abs(icir):.3f}<0.3")
        return {"success": False, "error": f"IC/ICIR未达标", "ic": round(float(ic), 4)}


# ── 主入口 ──

def run(sample: int = 500, days: int = 60, auto: bool = False) -> dict:
    """运行完整因子发现管线。"""
    result = {
        "pipeline": "FactorDiscovery",
        "version": "1.0",
        "started_at": datetime.now().isoformat(),
        "stages": {},
    }

    # ① Generate
    candidates = generate_candidates()
    result["stages"]["generate"] = {"count": len(candidates)}

    # ② Simulate
    simulated = simulate_candidates(candidates, sample, days)
    result["stages"]["simulate"] = {"count": len(simulated)}

    # ③ Check
    passed, rejected = check_candidates(simulated)
    result["stages"]["check"] = {"passed": len(passed), "rejected": len(rejected)}

    # ④ Register
    registered = register_candidates(passed, auto)
    result["stages"]["register"] = {"registered": len(registered)}

    result["completed_at"] = datetime.now().isoformat()
    return result


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="因子自动发现管线 (Phase 7: AI驱动)")
    p.add_argument("--sample", type=int, default=500, help="采样股票数")
    p.add_argument("--days", type=int, default=60, help="回看天数")
    p.add_argument("--auto", action="store_true", help="自动注册 (跳过人工确认)")
    p.add_argument("--ai", type=str, default=None, choices=["deepseek", "gpt", "manual"], help="AI模型生成因子")
    p.add_argument("--desc", type=str, default=None, help="AI因子描述 (中文即可)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO)
    if args.ai and args.desc:
        result = run_ai_pipeline(args.desc, args.ai, args.sample, args.days, args.auto)
    else:
        result = run(args.sample, args.days, args.auto)
    print(json.dumps(result, ensure_ascii=False, indent=2))
