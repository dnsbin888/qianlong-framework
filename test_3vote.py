import sys
sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")
from triple_vote import generate_consensus_signals
from data_loader import load_stock_data_cache

sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=30)
sigs = generate_consensus_signals(sd, top_k=10)
print(f"3-Model Consensus: {len(sigs)} signals")
for s in sigs[:10]:
    print(f"  {s['symbol']} Lv{s['buy_signal']} score={s['score']:.0f} {len(s['models'])}models: {'|'.join(s['models'])}")
