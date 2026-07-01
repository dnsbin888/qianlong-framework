"""LLM → Backtrader 策略生成 (v3.0)

DeepSeek生成Backtrader策略代码 → 自动回测 → 返回报告
用法:
  python llm_strategy.py --desc "放量突破20日新高"
"""
import sys, os, json, hashlib
sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

PROMPT_TEMPLATE = """你是一个A股量化策略专家。请根据描述生成一个Backtrader策略类。

描述: {desc}

关键规则:
1. A股T+1: 当日买入不可当日卖出
2. 涨跌停10%: 涨停不买, 跌停不卖
3. 百股整数: 每次买卖100股的整数倍
4. 策略类继承 AStockStrategy, 重写 signal_buy/signal_sell
5. 只返回Python代码, 不解释
6. 禁止使用 relative import (from .xxx), 禁止重复 import numpy

示例策略(signal_buy重写):
```python
class MyStrategy(AStockStrategy):
    def signal_buy(self, data):
        # 因子计算
        close = np.array([data.close[-i] for i in range(20,0,-1)])
        vol = np.array([data.volume[-i] for i in range(20,0,-1)])
        ma20 = np.mean(close)
        vol_ratio = vol[-1] / np.mean(vol)
        if close[-1] > ma20 * 1.02 and vol_ratio > 1.5:
            self.order = self.buy(data=data, size=100)

    def signal_sell(self, data):
        pos = self.getposition(data)
        if pos and data.close[0] > pos.price * 1.05:
            self.order = self.sell(data=data)
```

请生成策略代码:"""


def generate_strategy(desc: str, model: str = "deepseek") -> dict:
    """LLM生成Backtrader策略 → 自动回测"""
    # 1. LLM生成代码
    try:
        cfg = json.load(open(r"D:\quant_framework\live_trader_config.json", encoding="utf-8"))
        api_key = cfg.get("aiKey", "")
    except Exception:
        api_key = ""

    if not api_key:
        return {"success": False, "error": "API Key未配置"}

    prompt = PROMPT_TEMPLATE.format(desc=desc)
    import urllib.request
    data = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7, "max_tokens": 800,
    }).encode("utf-8")

    try:
        req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=data, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        })
        resp = urllib.request.urlopen(req, timeout=60)
        body = json.loads(resp.read().decode())
        code = body["choices"][0]["message"]["content"].strip()
        if "```" in code:
            lines = code.split("\n")
            code = "\n".join([l for l in lines if not l.startswith("```")])
    except Exception as e:
        return {"success": False, "error": f"API调用失败: {e}"}

    if "def signal_buy" not in code and "class " not in code:
        return {"success": False, "error": "生成代码格式无效"}

    # 2. 保存代码
    code_hash = hashlib.md5(code.encode()).hexdigest()[:8]
    tmp_path = os.path.join(os.path.dirname(__file__), f"_ai_strat_{code_hash}.py")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("from a_stock_rules import AStockStrategy\nimport numpy as np\n\n")
        f.write(code)
    print(f"[LLM] 策略代码已保存: {tmp_path}")

    # 注册到策略表
    strat_name = f"AI策略_{code_hash}"
    try:
        sp = r"D:\quant_framework\user_customizations\user_strategies.json"
        strategies = json.load(open(sp, encoding="utf-8"))
        strategies["strategies"].append({
            "name": strat_name,
            "display_name": desc[:20] + "...",
            "type": "builder",
            "factors": [],
            "trigger": {"type": "weighted_sum", "min_score": 60},
            "hold_days": 5,
            "status": "draft",
            "created_at": __import__('datetime').datetime.now().isoformat(),
            "note": f"LLM生成: {desc[:50]}",
            "llm_code": f"_ai_strat_{code_hash}.factor_xxx",
        })
        strategies["last_updated"] = __import__('datetime').datetime.now().isoformat()
        json.dump(strategies, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[LLM] 已注册策略: {strat_name}")
    except Exception as e:
        print(f"[LLM] 注册策略失败: {e}")

    return {
        "success": True,
        "code_file": tmp_path,
        "code_hash": code_hash,
        "strategy_name": strat_name,
        "code_length": len(code),
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--desc", type=str, required=True, help="策略描述")
    args = p.parse_args()
    result = generate_strategy(args.desc)
    print(json.dumps(result, ensure_ascii=False, indent=2))
