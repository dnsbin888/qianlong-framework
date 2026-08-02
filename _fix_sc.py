"""Fix signal_card.js: add quality score + Ridge dots"""
import re

path = r'D:\quant_web\static\js\signal_card.js'
text = open(path, encoding='utf-8').read()

# 1. Add quality score variable after scColor
old1 = "var scColor = cs>80?GOLD:cs>60?'#e3e3e3':'#888';"
new1 = "var scColor = cs>80?GOLD:cs>60?'#e3e3e3':'#888';var qs=s.quality_score||0,qsColor=qs>=65?'#00b96b':qs>=45?GOLD:'#FF4051',qsHtml=qs>0?' <span style=\"font-size:9px;color:'+qsColor+';font-weight:600\">Q'+qs+'</span>':'';"
text = text.replace(old1, new1)

# 2. Add quality score to the name line (after name, before signal level)
# Find: '</span>'+  followed by signal level
old2 = "+'</span>'+"
# We want to insert qsHtml after the name span, before the signal level span
# The name is in: nameFallback+'</span>'+ ... lvLabel+'</span>'
# Let's add qsHtml right after nameFallback
old2 = "nameFallback+'</span>'"
new2 = "nameFallback+'</span>'+qsHtml"
text = text.replace(old2, new2)

# 3. Add Ridge to model dots (add R dot)
old3 = "var dots = '<span style=\"letter-spacing:2px;font-size:10px\">'+\n      '<span style=\"color:'+(lValid?BLUE:'#444')+'\">●</span>'+\n      '<span style=\"color:'+(xValid?GOLD:'#444')+'\">●</span>'+\n      '<span style=\"color:'+(cValid?'#00b96b':'#444')+'\">●</span></span>';"
new3 = "var hasR=!!(s.ridge_score),rValid=hasR&&(s.ridge_score||0)>0;var dots='<span style=\"letter-spacing:2px;font-size:10px\">'+'<span style=\"color:'+(lValid?BLUE:'#444')+'\">●</span>'+'<span style=\"color:'+(xValid?GOLD:'#444')+'\">●</span>'+'<span style=\"color:'+(rValid?'#e040fb':'#444')+'\">●</span></span>';"
text = text.replace(old3, new3)

# 4. Add strategy label for ML signals (show "ML" / "反转" / "打板")
# The decShort already extracts from decision field which has emoji markers
# No change needed - decision already includes 📊/🔄/🎯

open(path, 'w', encoding='utf-8').write(text)
print("signal_card.js patched successfully")
print(f"qsHtml refs: {text.count('qsHtml')}")
print(f"hasR refs: {text.count('hasR')}")
