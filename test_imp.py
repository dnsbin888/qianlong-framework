import sys; sys.path.insert(0,r'D:\quant_framework'); sys.path.insert(0,r'D:\quant_web')
from lgbm_weight import get_importance
r = get_importance()
print('result count:', len(r))
if r: print(r[:3])
