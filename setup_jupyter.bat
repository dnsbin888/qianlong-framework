@echo off
echo === 潜龙 Jupyter 环境配置 ===
pip install jupyter notebook ipykernel -q
python -m ipykernel install --user --name qianlong --display-name "潜龙 Python3"
echo Done. Run: jupyter notebook D:\quant_framework\research\
