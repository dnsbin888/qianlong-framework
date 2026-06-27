"""测试新浪批量API能从缓存取多少只"""
import os, urllib.request, ssl, json
os.environ['NO_PROXY'] = '*'
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

with open(r'd:\quant_framework\price_cache.json') as f:
    pc = json.load(f)

# 取前300个真实股票代码（跳过指数）
stock_codes = []
for k in pc.keys():
    if any(k.startswith(p) for p in ['sh00','sh88','sz39','sz98']):
        continue
    stock_codes.append(k)
    if len(stock_codes) >= 300:
        break

print("总缓存: %d, 取前%d个股票" % (len(pc), len(stock_codes)))

# 分3批测试，每批100个
total_ok = 0
for batch_idx in range(3):
    start = batch_idx * 100
    batch = stock_codes[start:start+100]
    sina_codes = []
    for c in batch:
        clean = c.replace('sh','').replace('sz','').replace('bj','')
        if len(clean) >= 6:
            clean = clean[:6]
            prefix = 'sh' if clean[0] == '6' else 'sz'
            sina_codes.append(prefix + clean)
    
    url = 'https://hq.sinajs.cn/list=' + ','.join(sina_codes)
    headers = {'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            text = resp.read().decode('gbk')
        ok = sum(1 for l in text.split('\n') if l.strip() and '="' in l)
        total_ok += ok
        print("  第%d批: 请求%d, 成功%d" % (batch_idx+1, len(batch), ok))
    except Exception as e:
        print("  第%d批: 失败 %s" % (batch_idx+1, str(e)[:60]))

print("总计: 请求%d, 成功%d" % (len(stock_codes), total_ok))
