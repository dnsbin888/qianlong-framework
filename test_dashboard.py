"""Quick dashboard page test"""
import urllib.request, re

for page in ["", "?page=Strategy+Analysis", "?page=Parameter+Optimization", "?page=Data+Health"]:
    try:
        url = f"http://localhost:8501/{page}"
        resp = urllib.request.urlopen(url, timeout=10)
        html = resp.read().decode()

        has_error = "Traceback" in html or "Exception" in html
        label = page or "(home)"
        print(f"[{'FAIL' if has_error else 'OK'}] {label}  status={resp.status}")

        if has_error:
            match = re.search(r"Traceback.*?</div>", html, re.DOTALL)
            if match:
                err = match.group()[:500]
                # Strip HTML tags for readability
                err = re.sub(r"<[^>]+>", " ", err)
                print(f"  {err[:400]}")
    except Exception as e:
        print(f"[FAIL] {label}  {e}")
