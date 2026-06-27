"""模拟后台线程的_fetch_sina_batch逻辑"""
import os, urllib.request, ssl, json, re
os.environ['NO_PROXY'] = '*'
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
headers = {'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'}

# 加载price_cache
with open(r'd:\quant_framework\price_cache.json') as f:
    pc = json.load(f)
print("price_cache总key数:", len(pc))

# 用修复后的正则过滤A股
stock_keys = [k for k in pc.keys() if re.match(r'^(sh[56]\d{5}|sz[0123]\d{5}|bj\d{6})$', k)]
print("A股数量:", len(stock_keys))
print("前5个:", stock_keys[:5])
print("后5个:", stock_keys[-5:])

# 取前300个，模拟后台线程
init_symbols = stock_keys[:300]
print("\n模拟后台线程: 取前%d个" % len(init_symbols))

# 逐批调用_fetch_sina_batch的逻辑
_BATCH_SIZE = 80
quotes = {}
for i in range(0, len(init_symbols), _BATCH_SIZE):
    batch = init_symbols[i:i+_BATCH_SIZE]
    # 构建新浪格式
    sina_codes = [c for c in batch]
    url = 'https://hq.sinajs.cn/list=' + ','.join(sina_codes)
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            text = resp.read().decode('gbk')
        for line in text.strip().split('\n'):
            if '="' not in line:
                continue
            sc = line.split('hq_str_')[1].split('="')[0] if 'hq_str_' in line else ''
            data_str = line.split('="')[1].rstrip('";\n')
            parts = data_str.split(',')
            if len(parts) >= 32 and parts[3]:
                price = float(parts[3]) if parts[3] else 0
                if price > 0:
                    quotes[sc] = 1
        print("  第%d批: %d只, 成功%d" % (i//_BATCH_SIZE+1, len(batch), sum(1 for l in text.split('\n') if l.strip() and '="' in l)))
    except Exception as e:
        print("  第%d批: 失败 %s" % (i//_BATCH_SIZE+1, str(e)[:50]))

print("\n总计: 请求%d, 缓存%d只" % (len(init_symbols), len(quotes)))
