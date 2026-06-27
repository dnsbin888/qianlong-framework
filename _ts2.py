import requests, time
B='http://localhost:5000'
tests = [
    ("page1/50", "/api/stocks?page=1&page_size=50"),
    ("page5/50", "/api/stocks?page=5&page_size=50"),
    ("共振信号", "/api/stocks?page=1&page_size=50&signal=signal_resonance"),
    ("缓存命中", "/api/stocks?page=1&page_size=50"),
]
for n, p in tests:
    t0=time.time(); r=requests.get(f'{B}{p}',timeout=15); ms=(time.time()-t0)*1000
    d=r.json(); print(f'{n}: {ms:.0f}ms | {len(d.get("result",[]))}/{d.get("total",0)} items')
