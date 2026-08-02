import json, urllib.request
d = json.loads(urllib.request.urlopen('http://127.0.0.1:5002/api/signal-table', timeout=5).read())
s = d[0]
print('字段:', [k for k in s.keys() if 'current' in k or 'float' in k or 'change' in k])
print('current_change_pct:', s.get('current_change_pct'))
print('change_pct:', s.get('change_pct'))
print('current_price:', s.get('current_price'))
print('close:', s.get('close'))
print('float_pnl_pct:', s.get('float_pnl_pct'))
