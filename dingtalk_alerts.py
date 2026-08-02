"""潜龙告警+钉钉指令 v2.0 (恢复版, 含钉钉指令解析)
推送通道:
  1. 钉钉机器人 → 钉钉群 (主通道)
  2. Server酱 → 微信 (可选)

配置: 在 live_trader_config.json 中设置:
  "dingtalk_webhook": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
  "dingtalk_outgoing_token": "xxx"  # 钉钉Outgoing Webhook的Token
"""
import json, os, time, threading, urllib.request, urllib.parse
from datetime import datetime

CONFIG_PATH = r"D:\quant_framework\live_trader_config.json"
ALERTS_LOG = r"D:\quant_web\data\alerts.jsonl"

SERVERCHAN_KEY = ""
DINGTALK_WEBHOOK = ""
OUTGOING_TOKEN = ""

def _load_config():
    global SERVERCHAN_KEY, DINGTALK_WEBHOOK, OUTGOING_TOKEN
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", cfg.get("serverchan_key", ""))
            DINGTALK_WEBHOOK = cfg.get("dingtalk_webhook", "")
            OUTGOING_TOKEN = cfg.get("dingtalk_outgoing_token", "")
    except: pass

_load_config()

_alert_lock = threading.Lock()
os.makedirs(os.path.dirname(ALERTS_LOG), exist_ok=True)

def _write_local(title, content, level):
    try:
        with _alert_lock:
            with open(ALERTS_LOG, 'a', encoding='utf-8') as f:
                f.write(json.dumps({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "level": level, "title": title, "content": content}, ensure_ascii=False) + "\n")
    except: pass

_DAILY_COUNT = 0; _DAILY_DATE = ""; _COOLDOWN = {}
_THROTTLE = {}; _THROTTLE_SEC = 300

def _throttled(key: str) -> bool:
    now = time.time()
    if key in _THROTTLE and now - _THROTTLE[key] < _THROTTLE_SEC: return True
    _THROTTLE[key] = now; return False

def send_alert(title, content="", level="info", symbol="", side=""):
    global _DAILY_COUNT, _DAILY_DATE
    _write_local(title, content, level)
    today = time.strftime("%Y%m%d")
    if _DAILY_DATE != today: _DAILY_COUNT = 0; _DAILY_DATE = today
    try:
        prefs = json.load(open(r"D:\quant_framework\dingtalk_prefs.json", encoding="utf-8"))
    except: prefs = {}
    max_daily = prefs.get("max_daily", 20)
    if _DAILY_COUNT >= max_daily: return
    if symbol and side:
        cooldown_min = prefs.get("cooldown", 30)
        key = f"{symbol}_{side}"
        last = _COOLDOWN.get(key, 0)
        if time.time() - last < cooldown_min * 60: return
        _COOLDOWN[key] = time.time()
    if not DINGTALK_WEBHOOK: return
    try:
        keywords = prefs.get("keywords", "交易员")
        payload = json.dumps({"msgtype": "markdown", "markdown": {"title": title, "text": f"## {title}\n\n{content}\n\n> {keywords} · 潜龙 · {time.strftime('%H:%M:%S')}"}}, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(DINGTALK_WEBHOOK, data=payload, headers={"Content-Type": "application/json; charset=utf-8"})
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode('utf-8'))
        if resp.get("errcode") == 0: _DAILY_COUNT += 1; print(f"[DingTalk] ✅ ({_DAILY_COUNT}/{max_daily}): {title}")
        else: print(f"[DingTalk] ❌: {resp.get('errmsg', '')}")
    except Exception as e: print(f"[DingTalk] 发送失败: {e}")

def trade_signal(symbol, name, side, reason, price, pnl=0, industry="", source="模拟"):
    emoji = "📈" if side == "buy" else "📉"
    pnl_str = f" 盈亏{pnl:+.0f}" if pnl else ""
    content = f"{emoji} {source} | {name}({symbol}) | {reason} | @{price}{pnl_str}"
    send_alert("交易信号", content, symbol=symbol, side=side)

def system_error(component, error_msg):
    send_alert(f"⚠ 系统异常: {component}", f"**组件**: {component}\n\n**错误**: {error_msg}", "error")

def parse_command(text: str) -> dict | None:
    text = text.strip()
    if not text: return None
    cmd = {"raw": text, "action": None, "params": {}}
    if text.startswith("买入") or text.startswith("buy"):
        parts = text.split(); cmd["action"] = "buy"
        if len(parts) >= 2: cmd["params"]["symbol"] = parts[1]
        if len(parts) >= 3:
            qty = parts[2].replace("股", "")
            try: cmd["params"]["qty"] = int(float(qty))
            except: cmd["params"]["qty"] = 100
    elif text.startswith("卖出") or text.startswith("sell"):
        parts = text.split(); cmd["action"] = "sell"
        if len(parts) >= 2: cmd["params"]["symbol"] = parts[1]
    elif text in ("持仓", "position", "positions"): cmd["action"] = "positions"
    elif text in ("状态", "status", "health"): cmd["action"] = "status"
    elif text in ("停止", "stop", "暂停"): cmd["action"] = "stop"
    elif text in ("启动", "start", "恢复"): cmd["action"] = "start"
    elif text in ("批", "批准", "同意", "apply"): cmd["action"] = "approve_evolution"
    else: cmd["action"] = "unknown"; cmd["params"]["message"] = text
    return cmd

def execute_command(cmd: dict) -> str:
    action = cmd.get("action", "unknown")
    if action == "buy":
        sym = cmd["params"].get("symbol", ""); qty = cmd["params"].get("qty", 100)
        return f"买入指令已收到: {sym} × {qty}股，待实盘引擎执行"
    elif action == "sell":
        sym = cmd["params"].get("symbol", "")
        return f"卖出指令已收到: {sym}，待实盘引擎执行"
    elif action == "positions":
        try:
            from paper_engine import paper
            pos = paper.get_positions()
            if not pos: return "当前无持仓"
            lines = [f"- {p.get('symbol','')} ×{p.get('qty',0)}股 @{p.get('price',0):.2f} (盈亏{p.get('pnl_pct',0):+.1f}%)" for p in pos[:10]]
            return "📊 当前持仓:\n" + "\n".join(lines)
        except Exception as e: return f"获取持仓失败: {e}"
    elif action == "status":
        return "潜龙系统运行中 ✅"
    elif action == "stop":
        try:
            from paper_engine import paper; paper.auto_enabled = False
            cfg = json.load(open(CONFIG_PATH, "r")); cfg["auto_trade_enabled"] = False
            json.dump(cfg, open(CONFIG_PATH, "w"), ensure_ascii=False, indent=2)
            return "✅ 已暂停自动交易"
        except Exception as e: return f"暂停失败: {e}"
    elif action == "start":
        try:
            from paper_engine import paper; paper.auto_enabled = True
            cfg = json.load(open(CONFIG_PATH, "r")); cfg["auto_trade_enabled"] = True
            json.dump(cfg, open(CONFIG_PATH, "w"), ensure_ascii=False, indent=2)
            return "✅ 已恢复自动交易"
        except Exception as e: return f"恢复失败: {e}"
    else:
        return f"指令: 查模拟盘 | 查实盘 | 买入/卖出 sh600000 | 撤单 sh600000 | 查询 sh600000 | 信号 | 市场 | 风控 | 今日 | 关实盘/暂停/恢复 | 帮助"
