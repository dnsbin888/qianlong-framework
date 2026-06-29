"""打板键盘适配器 — 读取24键专业版 map.ini 并映射到交易引擎"""
import os, configparser, json

KEYBOARD_DIR = r"D:\1键盘\24键专业版 ID条件单"
MAP_INI = os.path.join(KEYBOARD_DIR, "map.ini")

def parse_daban_map():
    """解析打板键盘 map.ini → 返回键位布局"""
    if not os.path.exists(MAP_INI):
        return None

    config = configparser.ConfigParser(interpolation=None)
    # 使用 utf-8-sig 处理 BOM，关闭插值避免 % 号报错
    with open(MAP_INI, 'r', encoding='utf-8-sig') as f:
        config.read_file(f)

    keys = []
    for section in config.sections():
        if section.startswith('K'):
            keys.append({
                "id": section,
                "name": config.get(section, "name", fallback=""),
                "func": config.get(section, "func", fallback=""),
                "color": config.get(section, "color", fallback=""),
            })

    # 排序: K11-K16 → Row1, K21-K26 → Row2, etc.
    keys.sort(key=lambda k: k["id"])

    # 组织为 6列 x 4行 布局 (24键)
    layout = {
        "keyboard_name": "24键专业版 ID条件单",
        "layout": "6x4",
        "rows": [],
        "all_keys": keys,
        "total": len(keys),
    }

    # 按行分组 (K11→row=1, K21→row=2)
    current_row = None
    for k in keys:
        row_id = k["id"][1]  # K11→'1'(row1), K21→'2'(row2)...
        if row_id != current_row:
            current_row = row_id
            layout["rows"].append({"row": int(row_id), "keys": []})
        layout["rows"][-1]["keys"].append(k)

    return layout


def get_keyboard_status():
    """获取键盘状态"""
    layout = parse_daban_map()
    if not layout:
        return {"connected": False, "error": "map.ini not found"}

    # 检查键盘驱动是否运行
    import subprocess
    driver_running = False
    try:
        result = subprocess.run(['tasklist', '/fi', 'IMAGENAME eq kpp.exe'],
                                capture_output=True, text=True, encoding='gbk', errors='replace', timeout=3)
        driver_running = 'kpp.exe' in result.stdout
    except:
        pass

    return {
        "connected": True,
        "driver_running": driver_running,
        "keyboard_name": layout["keyboard_name"],
        "total_keys": layout["total"],
        "layout": layout,
        "map_file": MAP_INI,
    }


def get_key_action_map():
    """将打板键盘按键映射到交易动作"""
    layout = parse_daban_map()
    if not layout:
        return {}

    # 映射表: key_id → {label, action, color}
    action_map = {}
    for k in layout["all_keys"]:
        kid = k["id"]
        name = k["name"]
        func = k["func"]

        # 推断交易动作
        action = "unknown"
        if "买入" in name or "买入" in func:
            if "涨停" in name or "涨停" in func:
                action = "buy_limit_up"
            elif "跌" in name:
                action = "buy_dip"
            else:
                action = "buy_market"
        elif "卖出" in name or "卖出" in func:
            if "全仓" in name:
                action = "sell_all"
            elif "1/2" in name:
                action = "sell_half"
            elif "1/3" in name:
                action = "sell_third"
            elif "跌停" in name:
                action = "sell_limit_down"
            else:
                action = "sell_market"
        elif "撤" in name:
            action = "cancel_all"
        elif "止盈止损" in func or "止盈止损" in name:
            action = "set_sltp"
        elif "参数" in name or "设置" in name:
            action = "settings"
        elif "重启" in name:
            action = "restart"

        action_map[kid] = {
            "key_id": kid,
            "label": name,
            "func": func,
            "color": k["color"],
            "action": action,
        }

    return action_map
