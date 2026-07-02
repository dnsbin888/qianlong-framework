import json
d=json.load(open(r'D:\quant_framework\equity_log.json','r',encoding='utf-8'))
for e in d['log'][-3:]:
    print(e)
