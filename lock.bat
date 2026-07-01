@echo off
:: 锁住所有关键数据文件 (attrib +R)
attrib +R D:\quant_framework\paper_account.json
attrib +R D:\quant_framework\trade_log.csv
attrib +R D:\quant_framework\equity_log.json
attrib +R D:\quant_framework\live_equity_log.json
attrib +R D:\quant_framework\live_positions_track.json
attrib +R D:\quant_framework\live_trader_config.json
attrib +R D:\quant_framework\blacklist.json
attrib +R D:\quant_framework\factor_registry.json
attrib +R D:\quant_framework\user_customizations\user_factors.json
attrib +R D:\quant_framework\user_customizations\user_strategies.json
attrib +R D:\quant_framework\user_customizations\user_tdx_formulas.json
attrib +R D:\quant_framework\config\default.yaml
attrib +R D:\quant_web\stock_names_full.csv
echo LOCKED: 14 files
