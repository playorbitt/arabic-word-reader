import difflib, json, re, unicodedata
from pathlib import Path
from html import unescape
source=Path('work/kutub.html').read_text(encoding='utf8')
start=source.index('قال العلامة حجة الإسلام')
end=source.index('\nــ',start)
matn=unescape(source[start:end])
parts=[p.strip() for p in re.split(r'(?<=:)|(?<=،)|(?<=الكوفي)|(?<=الأنصاري)|(?<=الشيباني)',matn) if p.strip()]
parts=[p for p in parts if p!='،']
tokens=json.loads(Path('work/tilawa-tokens.json').read_text())['tokens']
table=str.maketrans('أإآٱىةؤئءڇ','اااايهوياب')
def chars(text):
    return ''.join(c for c in text.translate(table) if 'ا'<=c<='ي' and c!='ـ')
def squash(pairs):
    out=[]
    for c,k in pairs:
        if not out or out[-1][0]!=c:
            out.append((c,k))
    return out
ref=squash((c,i) for i,p in enumerate(parts) for c in chars(p))
hyp=squash((c,t['frame']*.04) for t in tokens for c in chars(t['sym']))
a=''.join(c for c,i in ref)
b=''.join(c for c,t in hyp)
print(b)
hits=[[] for p in parts]
for i,j,n in difflib.SequenceMatcher(None,a,b,autojunk=False).get_matching_blocks():
    for offset in range(n):
        hits[ref[i+offset][1]].append(hyp[j+offset][1])
for i,(p,h) in enumerate(zip(parts,hits)):
    print(i,p,len(h),len([c for c,k in ref if k==i]),h[:3],h[-3:])
print(tokens[-1])

