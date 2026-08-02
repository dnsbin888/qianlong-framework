"""Fix ml_signals.html JS errors"""
import re

path = r'D:\quant_web\templates\ml_signals.html'
text = open(path, encoding='utf-8').read()

# Find the refresh() function and check for issues
start = text.index('function refresh(){')
end = text.index('document.getElementById', start + 1000)
# find the actual end of function
end = text.index('\n', end) + 1
# rough check
refresh_fn = text[start:start+8000]

# Check for common issues
issues = []
# 1. Check for undefined ridgeHtml references
ridge_count = refresh_fn.count('ridgeHtml')
ridge_var = refresh_fn.count('var ridgeHtml') + refresh_fn.count(',ridgeHtml=')
if ridge_count > ridge_var:
    issues.append(f'ridgeHtml used {ridge_count}x but declared {ridge_var}x')

# 2. Check for cbHtml without declaration
cbHtml_count = refresh_fn.count('cbHtml')
cbHtml_var = refresh_fn.count('var cbHtml') + refresh_fn.count(',cbHtml=')
if cbHtml_count > 0:
    issues.append(f'cbHtml still present ({cbHtml_count}x)')

# 3. Check for unmatched quotes in key areas
# 4. Check for escaped quote issues
escaped = refresh_fn.count("\\\"")
if escaped > 0:
    issues.append(f'escaped quotes found ({escaped}x)')

# 5. Check parenthesis balance
opens = refresh_fn.count('(')
closes = refresh_fn.count(')')
if opens != closes:
    issues.append(f'Unbalanced () : {opens} vs {closes}')

# 6. Check brace balance within this function
ob = refresh_fn.count('{')
cb = refresh_fn.count('}')
if ob != cb:
    issues.append(f'Unbalanced {{}} : {ob} vs {cb}')

if issues:
    print('ISSUES FOUND:')
    for i in issues:
        print(f'  ❌ {i}')
else:
    print('No obvious JS issues found in refresh()')

# Print key variable declarations for debugging
for kw in ['var ridgeHtml','var cbHtml','var cbRaw','var qsHtml','var dots']:
    count = text.count(kw)
    print(f'{kw}: {count} declarations')
