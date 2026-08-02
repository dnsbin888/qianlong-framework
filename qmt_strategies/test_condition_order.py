#encoding:gbk
'''
QMT 条件单 API 探针 v1.0
=========================
用法:
  1. QMT客户端 → 策略编辑器 → 新建策略 → 粘贴本文件
  2. 设主图周期=1分钟, 任意股票
  3. 运行 → 查看日志输出
  4. 把日志全文贴回给AI分析

目标: 确认 QMT xtquant Python API 是否支持条件单
'''

import json, inspect

def init(ContextInfo):
    print("[探针] ===== QMT 条件单 API 探测开始 =====")
    ContextInfo.accID = '8890695045'

    results = {"found": [], "not_found": [], "methods": {}}

    # ════ 1. 探测 passorder 签名 ════
    print("\n[1] passorder 签名:")
    try:
        sig = inspect.signature(passorder)
        print(f"    passorder{ sig }")
        results["methods"]["passorder"] = str(sig)
    except Exception as e:
        print(f"    passorder 签名读取失败: {e}")

    # ════ 2. 探测 order_stock 系列 ════
    print("\n[2] 条件单相关函数探测:")
    candidates = [
        'passorder',
        'order_stock', 'order_stock_async', 'order_stock_sync',
        'condition_order', 'create_condition_order', 'set_condition_order',
        'cond_order', 'add_cond_order',
        'xt_trader', 'Xttrader',
        'cancel_condition_order', 'get_condition_orders',
        'order_target', 'order_value', 'order_volume',
    ]
    for name in candidates:
        try:
            obj = eval(name)
            print(f"    ✅ {name} 存在, type={type(obj)}")
            results["found"].append(name)
            # 如果是函数, 打印签名
            try:
                sig = inspect.signature(obj)
                print(f"       签名: {sig}")
            except:
                pass
        except NameError:
            results["not_found"].append(name)

    # ════ 3. 探测 xtquant.xttrader ════
    print("\n[3] xtquant.xttrader 探测:")
    try:
        from xtquant import xttrader
        print(f"    xttrader 模块: { dir(xttrader) }")
        results["methods"]["xttrader"] = [x for x in dir(xttrader) if not x.startswith('_')]
    except Exception as e:
        print(f"    xttrader 导入失败: {e}")

    # ════ 4. 探测 xtdata 条件单相关 ════
    print("\n[4] xtdata 条件单相关方法:")
    try:
        from xtquant import xtdata
        all_attrs = dir(xtdata)
        cond_related = [a for a in all_attrs if any(kw in a.lower() for kw in ['cond', 'order', 'trade', 'pass'])]
        print(f"    条件/交易关键词匹配: {cond_related}")
        results["methods"]["xtdata_cond"] = cond_related
    except Exception as e:
        print(f"    失败: {e}")

    # ════ 5. 探测 ContextInfo 交易方法 ════
    print("\n[5] ContextInfo 交易相关方法:")
    try:
        ci_attrs = [a for a in dir(ContextInfo) if not a.startswith('_')]
        trade_related = [a for a in ci_attrs if any(kw in a.lower() for kw in ['order', 'cond', 'trade', 'pass', 'acc', 'pos'])]
        print(f"    交易关键词: {trade_related}")
        results["methods"]["ContextInfo_trade"] = trade_related
    except Exception as e:
        print(f"    失败: {e}")

    # ════ 6. 打印全局命名空间中所有含 order/cond 的名字 ════
    print("\n[6] 全局命名空间搜索 (order/cond/trade):")
    import __main__
    main_names = dir(__main__)
    matched = [n for n in main_names if any(kw in n.lower() for kw in ['order', 'cond', 'trade', 'pass'])]
    print(f"    匹配: {matched}")
    results["methods"]["__main__"] = matched

    # ════ 7. 汇总 ════
    print("\n" + "=" * 50)
    print("[探针] 探测完成")
    print(f"   找到: {results['found']}")
    print(f"   未找到: {results['not_found']}")

    # 判断
    has_cond_api = any('cond' in n.lower() or 'condition' in n.lower() for n in results['found'])
    if has_cond_api:
        print("\n✅ 结论: 存在条件单相关API, 可继续深挖")
    else:
        print("\n⚠️ 结论: 未发现条件单专用API")
        print("   可能: (a)条件单仅GUI可用 (b)API名称不同 (c)需通过passorder参数变体实现")

    print("\n[探针] 完整结果JSON:")
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    print("[探针] ===== END =====")


def handlebar(ContextInfo):
    # 只跑一次
    pass
