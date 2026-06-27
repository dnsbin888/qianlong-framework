"""实时行情API速度对比测试"""
import os, urllib.request, time, ssl

os.environ['NO_PROXY'] = '*'
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

codes = list(range(50))
sina_codes = ','.join('sh6005%02d' % x for x in codes)
tencent_codes = ','.join('sh6005%02d' % x for x in codes)

# 方案1: 新浪批量 HTTP
hd = {'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'}
t0 = time.time()
r = urllib.request.urlopen(urllib.request.Request('http://hq.sinajs.cn/list=' + sina_codes, headers=hd), timeout=10, context=ctx)
d = r.read().decode('gbk')
t1 = time.time()
lines = [l for l in d.split(';') if l.strip() and '="' in l]
print("方案1 新浪批量HTTP: %dms, %d条" % ((t1-t0)*1000, len(lines)))

# 方案2: 腾讯行情 API
hd2 = {'User-Agent': 'Mozilla/5.0'}
t0 = time.time()
r2 = urllib.request.urlopen(urllib.request.Request('http://qt.gtimg.cn/q=' + tencent_codes, headers=hd2), timeout=10)
d2 = r2.read().decode('gbk')
t2 = time.time()
lines2 = [l for l in d2.split(';') if l.strip() and '~' in l]
print("方案2 腾讯批量HTTP: %dms, %d条" % ((t2-t0)*1000, len(lines2)))
if lines2:
    p = lines2[0].split('~')
    print("腾讯样例: %s 现价:%s 涨跌:%s%%" % (p[1], p[3], p[32] if len(p) > 32 else '?'))
    print("返回字段数: %d" % len(p))

# 方案3: 后台缓存 + 直接返回（零延迟方案）
print("\n方案3 后台缓存: 前端直接读缓存 = <1ms (零网络延迟)")
print("方案4 WebSocket: 服务端主动推送, 无轮询开销")
