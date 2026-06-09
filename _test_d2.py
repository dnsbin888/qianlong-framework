import requests, json
r = requests.get("http://localhost:5000/api/dashboard2", timeout=30)
d = r.json()
print("Code:", d["code"])
print("Equity points:", len(d["equity_curve"]))
print("Trades:", len(d["trades"]))
m = d["metrics"]
print(f'Return: {m["total_return"]:.2%}  Sharpe: {m["sharpe"]}  DD: {m["max_drawdown"]:.2%}')
print(f'Trades: {m["n_trades"]}  WinRate: {m["win_rate"]:.1%}')
print("message:", d.get("message", "OK"))
