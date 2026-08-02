import json, urllib.request
d = json.dumps({"symbol":"sh600089","position_pct":2,"close":19.12,"decision":"test","source":"ml"}).encode()
r = urllib.request.urlopen(urllib.request.Request(
    "http://127.0.0.1:5002/api/signal/approve",
    data=d, headers={"Content-Type":"application/json"}), timeout=5)
print(json.load(r))
